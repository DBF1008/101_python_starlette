# StaticFiles 自定义 HTML 索引和回退 - 快速参考

## 基本用法

```python
from starlette.staticfiles import StaticFiles

# 自定义索引和回退文件名
app = StaticFiles(
    directory="static",
    html=True,
    html_index="home.html",      # 默认: "index.html"
    html_fallback="404.html"     # 默认: "404.html"
)
```

## 常见场景

### 1. SPA 单页应用（Vue/React/Angular）

```python
# 所有路由都返回同一个 HTML 文件，由前端处理路由
app = StaticFiles(
    directory="dist",
    html=True,
    html_index="index.html",
    html_fallback="index.html"  # 关键：404 也返回 index.html
)
```

**效果：**
- `GET /` → `index.html` ✅
- `GET /users/123` → `index.html` ✅（前端路由）
- `GET /static/app.js` → 静态文件 ✅

### 2. 文档站点

```python
# 使用自定义命名约定
app = StaticFiles(
    directory="docs",
    html=True,
    html_index="home.html",
    html_fallback="not-found.html"
)
```

**目录结构：**
```
docs/
├── home.html              # 首页
├── not-found.html         # 404 页面
├── api/
│   └── home.html          # API 文档首页
└── guide/
    └── home.html          # 指南首页
```

### 3. 传统 HTML 站点（向后兼容）

```python
# 默认行为，无需指定参数
app = StaticFiles(directory="static", html=True)
```

**目录结构：**
```
static/
├── index.html             # 首页
├── 404.html               # 404 页面
└── about/
    └── index.html         # 关于页面
```

## 行为对照表

| 请求路径 | html_index | html_fallback | 结果 |
|---------|-----------|---------------|------|
| `/` | `index.html` | `404.html` | 返回 `index.html` (200) |
| `/docs/` | `index.html` | `404.html` | 返回 `docs/index.html` (200) |
| `/docs` | `index.html` | `404.html` | 重定向到 `/docs/` (307) |
| `/missing` | `index.html` | `404.html` | 返回 `404.html` (404) |
| `/missing` | `index.html` | `index.html` | 返回 `index.html` (404) |
| `/missing` | `home.html` | `home.html` | 返回 `home.html` (404) |

## 重要提示

### ✅ 参数仅在 `html=True` 时生效

```python
# ❌ 错误：html=False 时参数被忽略
app = StaticFiles(directory="static", html=False, html_index="home.html")

# ✅ 正确：html=True 时参数生效
app = StaticFiles(directory="static", html=True, html_index="home.html")
```

### ✅ 目录必须包含索引文件

```python
# 如果目录没有索引文件，会返回 404 回退页面
# 例如：/docs/ 目录没有 home.html → 返回 404.html
```

### ✅ 保留的行为

- **尾斜杠重定向**：`/docs` → `/docs/`
- **条件缓存**：ETag 和 Last-Modified 仍然有效
- **304 响应**：缓存验证机制不变
- **安全检查**：路径遍历保护完整

## 完整示例

```python
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

# 创建静态文件应用
static_files = StaticFiles(
    directory="dist",
    html=True,
    html_index="app.html",
    html_fallback="app.html"
)

# 挂载到路由
app = Starlette(
    routes=[
        Mount("/static", static_files, name="static"),
    ]
)
```

## 参数速查

| 参数 | 类型 | 默认值 | 说明 |
|-----|------|--------|------|
| `directory` | `PathLike \| None` | `None` | 静态文件目录 |
| `packages` | `list[str \| tuple[str, str]] \| None` | `None` | 包静态文件 |
| `html` | `bool` | `False` | 启用 HTML 模式 |
| `check_dir` | `bool` | `True` | 启动时检查目录 |
| `follow_symlink` | `bool` | `False` | 跟随符号链接 |
| **`html_index`** | `str` | `"index.html"` | **目录索引文件名** |
| **`html_fallback`** | `str` | `"404.html"` | **404 回退文件名** |

## 故障排除

### 问题：访问目录返回 404

**原因：** 目录中没有配置的索引文件

**解决：**
```python
# 确保目录包含 html_index 指定的文件
# 例如：html_index="home.html" → 目录中必须有 home.html
```

### 问题：自定义参数不生效

**原因：** `html=False`

**解决：**
```python
# 必须设置 html=True
app = StaticFiles(directory="static", html=True, html_index="home.html")
```

### 问题：404 页面不显示

**原因：** 回退文件不存在

**解决：**
```python
# 确保目录根包含 html_fallback 指定的文件
# 例如：html_fallback="not-found.html" → 根目录必须有 not-found.html
```

## 性能

- **零性能开销**：仅在初始化时存储字符串
- **无额外 I/O**：文件查找逻辑相同
- **内存占用**：增加约 50 字节

## 安全性

- ✅ 路径遍历保护完整
- ✅ 绝对路径拒绝机制不变
- ✅ 符号链接控制不受影响
- ✅ 所有安全检查保持不变
