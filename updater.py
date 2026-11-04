"""根目录兼容壳 —— 实现已迁移到 :mod:`app.update.engine`。

保留原因：旧文档、外部脚本与既有测试仍按根目录模块使用它，且会 monkeypatch
模块级常量（如 ``MAX_UPDATE_MEMBER_COUNT``），因此这里用实现模块**顶替**本模块，
保证全仓库只有一份实现与一份模块级状态。

新代码请直接 ``from app.update.engine import ...``。
"""

import sys

from app.update import engine as _impl

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
