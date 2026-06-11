# Plan: 为 request.form() 添加可配置的 spool_max_size 参数

## 目标

让用户通过 `request.form(spool_max_size=...)` 自定义 multipart 上传中 `SpooledTemporaryFile` 从内存切换到磁盘临时文件的阈值，减少不必要的磁盘 I/O。

## 改动概览

| 文件 | 改动 |
|------|------|
| `starlette/formparsers.py` | `MultiPartParser.__init__` 增加 `spool_max_size` 参数 |
| `starlette/requests.py` | `_get_form()` 和 `form()` 增加 `spool_max_size` 参数，透传至 `MultiPartParser` |
| `tests/test_formparsers.py` | 新增测试：验证不同阈值下文件是否正确留在内存或落盘 |

## 详细设计

### 1. `starlette/formparsers.py`

**新增哨兵对象**（模块级）：
```python
_UNSET: Any = object()
```

**修改 `MultiPartParser.__init__`**，在现有参数后添加 `spool_max_size`：
```python
def __init__(
    self,
    headers: Headers,
    stream: AsyncGenerator[bytes, None],
    *,
    max_files: int | float = 1000,
    max_fields: int | float = 1000,
    max_part_size: int = 1024 * 1024,
    spool_max_size: int | None = _UNSET,   # 新增
) -> None:
    # ... 现有赋值不变 ...
    self.max_part_size = max_part_size
    # 新增：解析 spool_max_size
    if spool_max_size is not _UNSET:
        self.spool_max_size = spool_max_size
```

**设计要点**：
- 默认值为哨兵 `_UNSET`（而非 `1024*1024`），这样当用户不传参时，类属性 `spool_max_size = 1024 * 1024` 继续生效 → 子类/monkey-patch 完全兼容
- `None` 语义：传递给 `SpooledTemporaryFile(max_size=None)` 时，其行为是"不限制"（内部 `_max_size=0`），文件永远留在内存 → 符合直觉
- `on_headers_finished()` 中 `SpooledTemporaryFile(max_size=self.spool_max_size)` **无需改动**，自动使用实例属性

### 2. `starlette/requests.py`

**导入哨兵**：
```python
from starlette.formparsers import _UNSET, FormParser, MultiPartException, MultiPartParser
```

**修改 `_get_form()`**：
```python
async def _get_form(
    self,
    *,
    max_files: int | float = 1000,
    max_fields: int | float = 1000,
    max_part_size: int = 1024 * 1024,
    spool_max_size: int | None = _UNSET,   # 新增
) -> FormData:
```
在创建 `MultiPartParser` 时透传：
```python
multipart_parser = MultiPartParser(
    self.headers,
    self.stream(),
    max_files=max_files,
    max_fields=max_fields,
    max_part_size=max_part_size,
    spool_max_size=spool_max_size,   # 新增
)
```

**修改 `form()`**：同样添加参数并透传给 `_get_form()`。

### 3. 测试（`tests/test_formparsers.py`）

新增一个辅助 app 工厂和 4 个测试：

```python
def make_app_spool_max_size(spool_max_size: int | None) -> ASGIApp:
    async def app(scope, receive, send):
        request = Request(scope, receive)
        data = await request.form(spool_max_size=spool_max_size)
        # 返回每个 UploadFile 的 _in_memory 状态
        output = {}
        for key, value in data.items():
            if isinstance(value, UploadFile):
                output[key] = {"in_memory": value._in_memory, "size": value.size}
        await request.close()
        response = JSONResponse(output)
        await response(scope, receive, send)
    return app
```

| 测试 | 验证内容 |
|------|----------|
| `test_spool_max_size_default` | 不传参数，上传 >1MB 文件 → `_in_memory=False`（落盘） |
| `test_spool_max_size_high_keeps_in_memory` | `spool_max_size=10MB`，上传 2MB 文件 → `_in_memory=True`（留内存） |
| `test_spool_max_size_zero_rolls_immediately` | `spool_max_size=0`，上传任意文件 → `_in_memory=False`（立即落盘） |
| `test_spool_max_size_none_keeps_in_memory` | `spool_max_size=None`，上传较大文件 → `_in_memory=True`（永不落盘） |
| `test_spool_max_size_does_not_affect_limits` | 设置 `spool_max_size` 后 `max_files`/`max_fields`/`max_part_size` 仍正常生效 |

## 兼容性保障

- 类属性 `MultiPartParser.spool_max_size = 1024 * 1024` 不变
- 不传 `spool_max_size` 时，行为与改动前 100% 一致
- `UploadFile` 的 `_in_memory`、`_will_roll()`、异步 I/O 逻辑无需改动
- 现有所有测试（包括 rollover-in-thread、OSError cleanup）无需修改
