# 异常处理规范（R2-7）

## 原则

1. **捕获具体的异常类型。**
   - 类型转换：`int()`、`float()` 用 `(TypeError, ValueError, OverflowError)`；`json.loads()` 用 `(TypeError, ValueError, RecursionError)`（`JSONDecodeError` 是 `ValueError` 的子类）。
   - 读写文件：`OSError`（包括 `FileNotFoundError`、`PermissionError`）。
   - 解析数据：`ValueError`（`UnicodeDecodeError` 也是它的子类）。
   - 浏览器操作：`app.core.driver` 的驱动异常，包括 `ContextLost`（页面刷新或导航，稍后重试即可）、`DriverDisconnected`、`DriverTimeout`、`ScriptError`，基类是 `DriverError`。
2. **确有必要的宽泛捕获，在 except 行注明理由**：`except Exception:  # broad-except: 理由`。典型场景：
   - 回滚或清理之后原样重新抛出；
   - 尽力而为的收尾操作（关闭连接、清理缓存）；
   - 接口层把任何内部错误转换为 HTTP 错误码；
   - 指标抓取、心跳这类绝不能让调用方失败的路径。
3. **不要静默吞掉异常。** 至少记一条 debug 日志（`logger.debug(..., exc_info=True)`），排查问题时才有线索。

## 收窄时的注意事项

- `try` 块里的**每一个表达式**都可能抛出异常，而不只是你关心的那个调用。`int(data["x"])` 会先抛 `KeyError`，`float(cfg.get("a"))` 在 `cfg` 为 `None` 时会抛 `AttributeError`。收窄前要确认 `try` 块里没有别的可能出错的表达式；如有，要么一并列出它们的异常类型，要么把它们移出 `try`。
- 驱动层只翻译 DrissionPage 自身的异常，Python 内置异常（`TypeError`、`OSError` 等）原样抛出。所以依赖 `except TypeError` 做兼容退回的代码不受影响。

## 棘轮

`tests/test_exception_ratchet.py` 按文件统计宽泛捕获的数量，**只减不增**，基线在 `tests/fixtures/broad_except_ratchet.json`。收窄之后运行 `python tests/test_exception_ratchet.py --update`，把进度锁定。

- 起点：1511 处。
- R2-7 做了两件事：一是机械收窄 50 处「`try` 里只有一句类型转换、参数是变量或常量」的写法；二是对新模块逐个审阅，收窄或注明了 28 处。现为 1433 处。
