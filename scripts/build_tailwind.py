#!/usr/bin/env python3
"""R2-8：生成预编译的 Tailwind 样式表 static/css/tailwind.css（需要 Node.js，首次运行会通过 npx 下载 CLI）。

版本固定为 3.4.17，与原先页面运行时（Play CDN）一致；配置见仓库根目录的 tailwind.config.js。
修改模板里的类名后重新生成，并运行 tests/test_tailwind_precompiled.py 做等价性核对。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "3.4.17"


def main() -> int:
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        print("需要 Node.js（npx）才能生成 Tailwind 样式表", file=sys.stderr)
        return 2
    cmd = [npx, "-y", f"tailwindcss@{VERSION}", "-c", "tailwind.config.js",
           "-i", "static/css/tailwind.input.css", "-o", "static/css/tailwind.css", "--minify"]
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
