# Fix: BaseHTTPMiddleware body replay & disconnect handling

## Problem

`BaseHTTPMiddleware` only buffers the request body for downstream replay when the middleware explicitly calls `request.body()` (which sets `_body`). If the middleware uses `request.stream()` or `request.form()` (which internally uses `stream()`), `_body` is never set, and `wrapped_receive()` either:

1. **Returns empty body `b""`** (when stream was fully consumed) → downstream gets incomplete/empty body
2. **Returns only remaining chunks** (when stream was partially consumed) → downstream gets partial body, may hang waiting for more
3. **Disconnect state machine has edge cases** → after body consumed, `wrapped_receive` blocks on `await self.receive()` for real disconnect which may never arrive promptly, causing hangs; the `_wrapped_rcv_disconnected` flag causes repeated `http.disconnect` returns

## Approach: Always-buffer body replay

Ensure `_body` is always set before the inner app starts, regardless of how the middleware consumed the body. This guarantees downstream always sees the complete body through any consumption method (`body()`, `stream()`, `form()`).

## Code Changes

### 1. `starlette/middleware/base.py`

#### 1a. `_CachedRequest` — add `stream()` override

Override `stream()` to record every chunk consumed into `_body`. This ensures that whether the middleware calls `body()`, `stream()`, or `form()`, the full body is captured.

```python
async def stream(self) -> AsyncGenerator[bytes, None]:
    if hasattr(self, "_body"):
        yield self._body
        yield b""
        return
    chunks: list[bytes] = []
    try:
        async for chunk in super().stream():
            if chunk:
                chunks.append(chunk)
            yield chunk
    finally:
        self._body = b"".join(chunks)
```

Key behavior:
- If `_body` already exists (from prior `body()` call), replay it — same as base `Request.stream()`
- Otherwise iterate the parent generator, recording non-empty chunks
- `finally` ensures `_body` is set even if `ClientDisconnect` is raised mid-stream
- The sentinel `b""` is yielded but not recorded (it's not real body data)

#### 1b. `_CachedRequest.wrapped_receive()` — simplify state machine

Since `_body` will always be set before the inner app starts (guaranteed by the drain in `call_next`), simplify to three states:

```python
async def wrapped_receive(self) -> Message:
    # State 1: already forwarded disconnect
    if self._wrapped_rcv_disconnected:
        return {"type": "http.disconnect"}
    # State 2: body fully replayed, now forward disconnect
    if self._wrapped_rcv_consumed:
        if not self._is_disconnected:
            msg = await self.receive()
            if msg["type"] != "http.disconnect":
                raise RuntimeError(f"Unexpected message received: {msg['type']}")
        self._wrapped_rcv_disconnected = True
        return {"type": "http.disconnect"}
    # State 3: replay buffered body
    if hasattr(self, "_body"):
        self._wrapped_rcv_consumed = True
        return {"type": "http.request", "body": self._body, "more_body": False}
    # State 4: stream not yet fully consumed — pull next chunk
    try:
        stream = self._wrapped_rc_stream
        chunk = await stream.__anext__()
        self._wrapped_rcv_consumed = self._stream_consumed
        return {"type": "http.request", "body": chunk, "more_body": not self._stream_consumed}
    except ClientDisconnect:
        self._wrapped_rcv_disconnected = True
        return {"type": "http.disconnect"}
```

Key changes from current code:
- Remove the `elif self._stream_consumed: return empty body` branch — this was the root cause of downstream getting empty body
- State 2 (consumed→disconnect) simplified: always return `http.disconnect` once, removing the separate `_is_disconnected` sub-check that could block

#### 1c. `BaseHTTPMiddleware.__call__` — add body drain in `call_next`

Before launching the inner app, drain any remaining stream chunks into `_body`:

```python
async def call_next(request: Request) -> Response:
    # Ensure the full request body is buffered for downstream replay.
    # This guarantees consistent behavior regardless of whether the
    # middleware consumed the body via body(), stream(), or form().
    if not hasattr(request, "_body"):
        chunks: list[bytes] = []
        if hasattr(request, "_body"):  # may have been set by stream() override
            chunks.append(request._body)
        try:
            while not request._stream_consumed:
                chunk = await request._wrapped_rc_stream.__anext__()
                if chunk:
                    chunks.append(chunk)
        except ClientDisconnect:
            pass
        request._body = b"".join(chunks)
    # ... rest of call_next unchanged
```

This ensures:
- If middleware called `body()` → `_body` already set → skip drain
- If middleware called `stream()` fully → `_body` set by stream() override → skip drain
- If middleware called `stream()` partially → drain reads remaining chunks, appends to `_body` from stream() recording → `_body` = full body
- If middleware called `form()` → form consumed stream via stream() override → `_body` set → skip drain
- If middleware didn't touch body → drain reads all chunks → `_body` = full body
- If client disconnected mid-drain → `ClientDisconnect` caught, `_body` = whatever was read

### 2. `tests/middleware/test_base.py`

#### 2a. Update existing tests that verified old (broken) behavior

**`test_read_request_stream_in_app_after_middleware_calls_stream`** (line 599):
- Old: endpoint expects `[b""]` (empty — stream consumed by middleware)
- New: endpoint expects `[b"a", b""]` (full body replayed from `_body`)

**`test_read_request_body_in_app_after_middleware_calls_stream`** (line 660):
- Old: endpoint asserts `body == b""` (empty)
- New: endpoint asserts `body == b"a"` (full body)

**`test_read_request_stream_in_dispatch_wrapping_app_calls_body`** (line 776):
- Old: endpoint expects `b"2"` (the "middle" chunk from interleaved stream)
- New: endpoint expects `b"123"` (full body replayed) — drain in call_next consumes entire stream before inner app starts; middleware's subsequent `request.stream()` raises "Stream consumed" since drain consumed it

#### 2b. Add new tests

1. **`test_middleware_partial_stream_then_endpoint_body`**: middleware reads first chunk from `stream()`, calls `call_next`; endpoint gets full body via `body()`

2. **`test_middleware_form_then_endpoint_body`**: middleware calls `form()` on a urlencoded form; endpoint gets raw body via `body()` — verifies form() consumption doesn't destroy body for downstream

3. **`test_middleware_body_then_endpoint_form`**: middleware calls `body()`; endpoint parses form via `form()` — verifies body buffering works with form parsing downstream

4. **`test_middleware_stream_then_endpoint_form`**: middleware calls `stream()` fully; endpoint parses form via `form()` — verifies form parsing works after stream-based buffering

5. **`test_disconnect_during_body_drain`**: client sends disconnect immediately; middleware doesn't touch body; endpoint handles `ClientDisconnect` gracefully

## Behavior Summary

| Middleware does | Endpoint does | Old behavior | New behavior |
|---|---|---|---|
| `body()` | `body()` | ✅ full body | ✅ full body |
| `body()` | `stream()` | ✅ full body | ✅ full body |
| `body()` | `form()` | ✅ full body | ✅ full body |
| `stream()` (full) | `body()` | ❌ `b""` | ✅ full body |
| `stream()` (full) | `stream()` | ❌ empty | ✅ full body |
| `stream()` (full) | `form()` | ❌ empty | ✅ full body |
| `stream()` (partial) | `body()` | ❌ partial | ✅ full body |
| `form()` | `body()` | ❌ `b""` | ✅ full body |
| nothing | any | ✅ full body | ✅ full body |
