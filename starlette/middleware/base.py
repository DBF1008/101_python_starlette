from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterable, Awaitable, Callable, Mapping, MutableMapping
from typing import Any, TypeVar

import anyio

from starlette._utils import create_collapsing_task_group
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

RequestResponseEndpoint = Callable[[Request], Awaitable[Response]]
DispatchFunction = Callable[[Request, RequestResponseEndpoint], Awaitable[Response]]
BodyStreamGenerator = AsyncGenerator[bytes | MutableMapping[str, Any], None]
AsyncContentStream = AsyncIterable[str | bytes | memoryview | MutableMapping[str, Any]]
T = TypeVar("T")


class _CachedRequestStream:
    """Non-generator async iterator for _CachedRequest.stream().

    Using a class instead of an async generator avoids PytestUnraisableExceptionWarning
    when consumers break out of iteration without explicitly closing the stream.
    """

    __slots__ = ("_request", "_replay_index", "_replaying")

    def __init__(self, request: _CachedRequest) -> None:
        self._request = request
        self._replay_index = 0
        # If _body is set and stream is exhausted, we'll replay from cache.
        self._replaying = hasattr(request, "_body") and request._rcv_stream_exhausted

    def __aiter__(self) -> _CachedRequestStream:
        return self

    async def __anext__(self) -> bytes:
        request = self._request
        if self._replaying:
            # Replay mode: yield _body then sentinel
            if self._replay_index == 0:
                self._replay_index = 1
                return request._body
            elif self._replay_index == 1:
                self._replay_index = 2
                return b""
            else:
                raise StopAsyncIteration
        # Replay already-recorded chunks from previous stream() calls
        if self._replay_index < len(request._rcv_chunks):
            chunk = request._rcv_chunks[self._replay_index]
            self._replay_index += 1
            return chunk
        # Then read new chunks from the ASGI channel
        if not request._rcv_stream_exhausted:
            chunk = await request._read_next_chunk()
            if chunk is not None:
                # _read_next_chunk appended to _rcv_chunks; advance past it
                self._replay_index = len(request._rcv_chunks)
                return chunk
        raise StopAsyncIteration


class _CachedRequest(Request):
    """
    Wraps the request so that the body is always available to the downstream
    app, regardless of how the middleware consumed it (body(), stream(), or
    form()).

    The stream() override records every chunk into _body as it is consumed.
    A drain step in BaseHTTPMiddleware's call_next() ensures the stream is
    fully consumed and _body is complete before the inner app starts.

    wrapped_receive() then replays _body as a single http.request message,
    giving the downstream app a consistent, complete view of the request body.
    """

    def __init__(self, scope: Scope, receive: Receive):
        super().__init__(scope, receive)
        self._wrapped_rcv_disconnected = False
        self._wrapped_rcv_consumed = False
        self._rcv_chunks: list[bytes] = []
        self._rcv_stream_gen: AsyncGenerator[bytes, None] | None = None
        self._rcv_stream_exhausted = False

    async def _read_next_chunk(self) -> bytes | None:
        """Read the next chunk from the ASGI receive channel, recording it.
        Returns None when the stream is fully exhausted."""
        if self._rcv_stream_exhausted:
            return None
        if self._rcv_stream_gen is None:
            self._rcv_stream_gen = super().stream()
        try:
            chunk = await self._rcv_stream_gen.__anext__()
        except StopAsyncIteration:
            self._rcv_stream_exhausted = True
            await self._rcv_stream_gen.aclose()
            return None
        # Record all chunks (including the b"" sentinel from base stream)
        self._rcv_chunks.append(chunk)
        self._body = b"".join(self._rcv_chunks)
        return chunk

    async def _close_stream_gen(self) -> None:
        if self._rcv_stream_gen is not None and not self._rcv_stream_exhausted:
            await self._rcv_stream_gen.aclose()
            self._rcv_stream_exhausted = True

    def stream(self) -> _CachedRequestStream:
        """Return a non-generator async iterator to avoid GC cleanup warnings."""
        return _CachedRequestStream(self)

    async def wrapped_receive(self) -> Message:
        # State 1: we've already forwarded a disconnect to the downstream app.
        if self._wrapped_rcv_disconnected:
            return {"type": "http.disconnect"}

        # State 2: body has been fully replayed; now forward disconnect.
        if self._wrapped_rcv_consumed:
            if not self._is_disconnected:
                msg = await self.receive()
                if msg["type"] != "http.disconnect":  # pragma: no cover
                    raise RuntimeError(f"Unexpected message received: {msg['type']}")
            self._wrapped_rcv_disconnected = True
            return {"type": "http.disconnect"}

        # State 3: client disconnected — if there's a partial body, replay it
        # first (the disconnect will follow on the next receive call).
        # If no body was read at all, return disconnect immediately.
        if self._is_disconnected:
            if not hasattr(self, "_body") or self._body == b"":
                self._wrapped_rcv_disconnected = True
                return {"type": "http.disconnect"}
            # Partial body available — replay it, then disconnect will follow.
            self._wrapped_rcv_consumed = True
            return {
                "type": "http.request",
                "body": self._body,
                "more_body": False,
            }

        # State 4: replay the buffered body as a single message.
        if hasattr(self, "_body"):
            self._wrapped_rcv_consumed = True
            return {
                "type": "http.request",
                "body": self._body,
                "more_body": False,
            }

        # State 5: stream not yet fully consumed — pull the next chunk.
        # This path is only reached if call_next's drain hasn't run yet
        # (e.g. interleaved stream/call_next usage).
        try:
            chunk = await self._read_next_chunk()
            if chunk is None:
                # Stream exhausted, no body was read
                self._wrapped_rcv_consumed = True
                return {
                    "type": "http.request",
                    "body": b"",
                    "more_body": False,
                }
            self._wrapped_rcv_consumed = self._rcv_stream_exhausted
            return {
                "type": "http.request",
                "body": chunk,
                "more_body": not self._rcv_stream_exhausted,
            }
        except ClientDisconnect:
            self._wrapped_rcv_disconnected = True
            return {"type": "http.disconnect"}


class BaseHTTPMiddleware:
    def __init__(self, app: ASGIApp, dispatch: DispatchFunction | None = None) -> None:
        self.app = app
        self.dispatch_func = self.dispatch if dispatch is None else dispatch

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = _CachedRequest(scope, receive)
        wrapped_receive = request.wrapped_receive
        response_sent = anyio.Event()
        app_exc: Exception | None = None
        exception_already_raised = False

        async def call_next(request: Request) -> Response:
            # Ensure the full request body is buffered for downstream replay.
            # This guarantees consistent behavior regardless of whether the
            # middleware consumed the body via body(), stream(), or form().
            if not request._rcv_stream_exhausted:
                try:
                    while not request._rcv_stream_exhausted:
                        await request._read_next_chunk()
                except ClientDisconnect:
                    request._is_disconnected = True
                finally:
                    # Close the base stream generator to avoid GC warnings
                    if request._rcv_stream_gen is not None:
                        await request._rcv_stream_gen.aclose()

            async def receive_or_disconnect() -> Message:
                if response_sent.is_set():
                    return {"type": "http.disconnect"}

                async with anyio.create_task_group() as task_group:

                    async def wrap(func: Callable[[], Awaitable[T]]) -> T:
                        result = await func()
                        task_group.cancel_scope.cancel()
                        return result

                    task_group.start_soon(wrap, response_sent.wait)
                    message = await wrap(wrapped_receive)

                if response_sent.is_set():
                    return {"type": "http.disconnect"}

                return message

            async def send_no_error(message: Message) -> None:
                try:
                    await send_stream.send(message)
                except anyio.BrokenResourceError:
                    # recv_stream has been closed, i.e. response_sent has been set.
                    return

            async def coro() -> None:
                nonlocal app_exc

                with send_stream:
                    try:
                        await self.app(scope, receive_or_disconnect, send_no_error)
                    except Exception as exc:
                        app_exc = exc

            task_group.start_soon(coro)

            try:
                message = await recv_stream.receive()
                info = message.get("info", None)
                if message["type"] == "http.response.debug" and info is not None:
                    message = await recv_stream.receive()
            except anyio.EndOfStream:
                if app_exc is not None:
                    nonlocal exception_already_raised
                    exception_already_raised = True
                    # Prevent `anyio.EndOfStream` from polluting app exception context.
                    # If both cause and context are None then the context is suppressed
                    # and `anyio.EndOfStream` is not present in the exception traceback.
                    # If exception cause is not None then it is propagated with
                    # reraising here.
                    # If exception has no cause but has context set then the context is
                    # propagated as a cause with the reraise. This is necessary in order
                    # to prevent `anyio.EndOfStream` from polluting the exception
                    # context.
                    raise app_exc from app_exc.__cause__ or app_exc.__context__
                raise RuntimeError("No response returned.")

            assert message["type"] == "http.response.start"

            async def body_stream() -> BodyStreamGenerator:
                async for message in recv_stream:
                    if message["type"] == "http.response.pathsend":
                        yield message
                        break
                    assert message["type"] == "http.response.body", f"Unexpected message: {message}"
                    body = message.get("body", b"")
                    if body:
                        yield body
                    if not message.get("more_body", False):
                        break

            response = _StreamingResponse(status_code=message["status"], content=body_stream(), info=info)
            response.raw_headers = message["headers"]
            return response

        streams: anyio.create_memory_object_stream[Message] = anyio.create_memory_object_stream()
        send_stream, recv_stream = streams
        with recv_stream, send_stream:
            async with create_collapsing_task_group() as task_group:
                response = await self.dispatch_func(request, call_next)
                await response(scope, wrapped_receive, send)
                response_sent.set()
                recv_stream.close()
        if app_exc is not None and not exception_already_raised:
            raise app_exc

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        raise NotImplementedError()  # pragma: no cover


class _StreamingResponse(Response):
    def __init__(
        self,
        content: AsyncContentStream,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
        info: Mapping[str, Any] | None = None,
    ) -> None:
        self.info = info
        self.body_iterator = content
        self.status_code = status_code
        self.media_type = media_type
        self.init_headers(headers)
        self.background = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.info is not None:
            await send({"type": "http.response.debug", "info": self.info})
        await send(
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": self.raw_headers,
            }
        )

        should_close_body = True
        async for chunk in self.body_iterator:
            if isinstance(chunk, dict):
                # We got an ASGI message which is not response body (eg: pathsend)
                should_close_body = False
                await send(chunk)
                continue
            await send({"type": "http.response.body", "body": chunk, "more_body": True})

        if should_close_body:
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        if self.background:
            await self.background()
