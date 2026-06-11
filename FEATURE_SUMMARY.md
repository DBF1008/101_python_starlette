# StaticFiles 自定义 HTML 索引和回退页面功能

## 概述

为 Starlette 的 `StaticFiles` 新增了两个可配置参数，允许用户自定义目录索引文件名和 HTML 回退文件名，解决了原有 `html=True` 模式下只能硬编码使用 `index.html` 和 `404.html` 的限制。

## 新增参数

### `html_index: str = "index.html"`
- 指定目录索引文件名
- 仅在 `html=True` 时生效
- 默认值保持向后兼容

### `html_fallback: str = "404.html"`
- 指定 HTML 回退页面文件名（用于 404 响应）
- 仅在 `html=True` 时生效
- 默认值保持向后兼容

## 使用示例

### 1. 传统 HTML 模式（向后兼容）
```python
from starlette.staticfiles import StaticFiles

# 默认行为，使用 index.html 和 404.html
app = StaticFiles(directory="static", html=True)
```

### 2. SPA 应用部署
```python
# SPA 应用通常使用同一个入口文件处理所有路由
app = StaticFiles(
    directory="dist",
    html=True,
    html_index="app.html",      # 目录索引使用 app.html
    html_fallback="app.html"    # 404 也返回 app.html（客户端路由）
)
```

### 3. 文档站点
```python
# 文档站点可能使用不同的命名约定
app = StaticFiles(
    directory="docs",
    html=True,
    html_index="home.html",         # 每个目录的首页
    html_fallback="not-found.html"  # 自定义 404 页面
)
```

## 功能特性

### ✅ 保留的行为
- **目录尾斜杠重定向**：访问 `/docs` 会自动重定向到 `/docs/`
- **条件缓存**：ETag 和 Last-Modified 头部检查仍然有效
- **304 Not Modified 响应**：缓存验证机制完全保留
- **路径安全检查**：防止目录遍历攻击的保护机制不变

### ✅ 新增能力
- **自定义索引文件名**：支持 `home.html`、`main.html`、`app.html` 等任意命名
- **自定义 404 回退**：支持 `not-found.html`、`error.html` 等任意命名
- **完全向后兼容**：不指定参数时行为与原版完全一致

## 实现细节

### 代码修改

#### 1. 初始化参数（`__init__`）
```python
def __init__(
    self,
    *,
    directory: PathLike | None = None,
    packages: list[str | tuple[str, str]] | None = None,
    html: bool = False,
    check_dir: bool = True,
    follow_symlink: bool = False,
    html_index: str = "index.html",      # 新增
    html_fallback: str = "404.html",     # 新增
) -> None:
    # ...
    self.html_index = html_index          # 新增
    self.html_fallback = html_fallback    # 新增
```

#### 2. 目录索引查找（`get_response`）
```python
# 原代码：
index_path = os.path.join(path, "index.html")

# 新代码：
index_path = os.path.join(path, self.html_index)
```

#### 3. 404 回退查找（`get_response`）
```python
# 原代码：
full_path, stat_result = await anyio.to_thread.run_sync(
    self.lookup_path, "404.html"
)

# 新代码：
full_path, stat_result = await anyio.to_thread.run_sync(
    self.lookup_path, self.html_fallback
)
```

## 测试覆盖

### 新增测试用例（10 个测试，覆盖 asyncio 和 trio）

1. **`test_staticfiles_html_custom_index`**
   - 验证自定义索引文件名正常工作
   - 验证目录重定向行为
   - 验证 404 回退仍使用默认 404.html

2. **`test_staticfiles_html_custom_fallback`**
   - 验证自定义 404 回退文件名正常工作
   - 验证索引仍使用默认 index.html

3. **`test_staticfiles_html_custom_index_and_fallback`**
   - 验证同时自定义索引和回退文件名
   - 验证 SPA 场景（两者指向同一文件）

4. **`test_staticfiles_html_custom_index_missing`**
   - 验证自定义索引不存在时正确回退到 404 页面

5. **`test_staticfiles_html_custom_params_ignored_when_html_false`**
   - 验证 `html=False` 时自定义参数被忽略
   - 验证不会意外启用 HTML 模式行为

### 测试结果
```
============================== 76 passed in 0.20s ==============================
```
- 所有原有测试通过（66 个）
- 所有新增测试通过（10 个）
- 向后兼容性 100% 保证

## 应用场景

### 场景 1：SPA 单页应用
```python
# Vue/React/Angular 构建产物通常使用 index.html
# 但也可以自定义为 app.html 或 main.html
app = StaticFiles(
    directory="dist",
    html=True,
    html_index="index.html",
    html_fallback="index.html"  # 所有路由交给前端处理
)
```

### 场景 2：静态文档生成器
```python
# MkDocs、Docusaurus 等工具可能使用不同的文件名约定
app = StaticFiles(
    directory="site",
    html=True,
    html_index="index.html",
    html_fallback="404.html"
)
```

### 场景 3：多语言站点
```python
# 不同语言目录可能使用不同的索引文件名
# 例如：en/home.html, zh/home.html
app = StaticFiles(
    directory="content",
    html=True,
    html_index="home.html"
)
```

### 场景 4：遗留系统集成
```python
# 集成使用非标准命名的旧系统
app = StaticFiles(
    directory="legacy",
    html=True,
    html_index="default.htm",
    html_fallback="error.htm"
)
```

## 性能影响

- **零性能开销**：仅在初始化时存储两个字符串，运行时查找逻辑完全相同
- **无额外 I/O**：不增加文件系统操作
- **内存占用**：增加约 50 字节（两个字符串引用）

## 安全性

- **路径遍历保护**：所有原有的安全检查保持不变
- **绝对路径拒绝**：仍然拒绝 `/etc/passwd` 等绝对路径
- **符号链接控制**：`follow_symlink` 参数行为不变
- **配置验证**：`check_dir` 参数行为不变

## 迁移指南

### 从硬编码迁移到可配置

**之前（仍然有效）**：
```python
app = StaticFiles(directory="static", html=True)
# 必须使用 index.html 和 404.html
```

**现在（可选）**：
```python
app = StaticFiles(
    directory="static",
    html=True,
    html_index="home.html",      # 可选
    html_fallback="error.html"   # 可选
)
```

### 无破坏性变更
- 现有代码无需任何修改
- 默认行为完全一致
- 新功能完全可选

## 文件变更清单

### 修改的文件
1. `starlette/staticfiles.py` (+6 行)
   - 新增 `html_index` 和 `html_fallback` 参数
   - 存储为实例变量
   - 在 `get_response` 中使用变量替代硬编码字符串

2. `tests/test_staticfiles.py` (+143 行)
   - 新增 5 个测试函数（10 个测试用例）
   - 覆盖自定义索引、自定义回退、组合使用、边界情况

### 新增的文件（演示用）
1. `demo_custom_html.py` - 功能演示脚本
2. `FEATURE_SUMMARY.md` - 本文档

## 总结

此功能以最小的代码改动（6 行核心代码）解决了实际部署中的灵活性问题，同时保持了 100% 的向后兼容性和零性能开销。所有现有的安全检查、缓存机制和重定向行为均得到完整保留。
