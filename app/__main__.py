"""``python -m app`` 入口。

行为与 ``python main.py`` 完全一致：``main.py`` 仍在 ``__main__`` 守卫里完成
环境引导（``bootstrap_runtime``）与 uvicorn 启动，这里只负责把包入口转过去，
因此不会重复执行 bootstrap，也不需要维护第二份启动逻辑。

约定：必须在仓库根目录执行（与 ``uv run python main.py`` 相同），
``sys.path[0]`` 即为仓库根目录，``main.py`` 的相对路径行为保持不变。
"""

from __future__ import annotations

import runpy
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    runpy.run_path(str(PROJECT_ROOT / "main.py"), run_name="__main__")


if __name__ == "__main__":
    main()
