"""测试共用：读取随仓库分发的站点配置。

R1-2 起内置站点配置位于 config/sites/（每站点一个文件），这里拼回与旧 config/sites.json 相同的结构，
测试代码无需关心存储布局。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.services.config.site_store import load_sites_dict

ROOT = Path(__file__).resolve().parents[1]
SITES_DIR = ROOT / "config" / "sites"


def shipped_sites() -> dict:
    return load_sites_dict(SITES_DIR)


def shipped_sites_at(rev: str) -> dict:
    """某个 git 版本中的内置站点配置；兼容拆分前（单文件）与拆分后（目录）两种布局。"""
    listing = subprocess.run(
        ["git", "ls-tree", "--name-only", f"{rev}:config/sites"], cwd=ROOT, capture_output=True, text=True
    )
    if listing.returncode != 0:
        return json.loads(subprocess.check_output(["git", "show", f"{rev}:config/sites.json"], cwd=ROOT))
    sites = {}
    for name in listing.stdout.split():
        if not name.endswith(".json") or name == "index.json":
            continue
        payload = json.loads(subprocess.check_output(["git", "show", f"{rev}:config/sites/{name}"], cwd=ROOT))
        sites[payload["site"]] = payload["config"]
    return sites
