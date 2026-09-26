#!/usr/bin/env python3
"""根据 app/core/settings_registry.py 生成 .env.example（R2-4）。

用法：
  python scripts/build_env_example.py          # 重新生成 .env.example
  python scripts/build_env_example.py --check  # 只校验，过期则返回 1（测试/CI 使用）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / ".env.example"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    from app.core.settings_registry import SETTINGS, render_env_example

    rendered = render_env_example()
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != rendered:
            print(".env.example 已过期：请运行 python scripts/build_env_example.py", file=sys.stderr)
            return 1
        print(f".env.example 与登记表一致（{len(SETTINGS)} 项）")
        return 0
    TARGET.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"已写入 .env.example（{len(SETTINGS)} 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
