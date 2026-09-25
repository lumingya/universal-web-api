#!/usr/bin/env python3
"""生成 / 校验 config/sites/index.json（R1-2 / R1-3 共用的内置适配器清单）。

用法：
  python scripts/build_sites_index.py                      # 重新生成 index.json
  python scripts/build_sites_index.py --check              # 只校验，过期则返回 1（测试/CI 使用）
  python scripts/build_sites_index.py --bump chat.deepseek.com [...]   # 把指定站点的 adapter_version 设为今天
  python scripts/build_sites_index.py --bump-changed       # 自动给相对 index 内容有变化的站点 bump 版本

index.json 的每一项：file、adapter_version、min_app_version、config_sha256（规范化 config 文本的摘要，
与格式/键顺序无关，用于判断用户是否改过预设）、file_sha256（原始字节摘要，用于下载校验）。
输出不含时间戳，内容只由站点文件决定，便于测试比对。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITES_DIR = ROOT / "config" / "sites"
INDEX_PATH = SITES_DIR / "index.json"
INDEX_SCHEMA_VERSION = 1


def _canonical(config) -> str:
    # 与 app/services/config/site_store.py 的 canonical_config_text 保持一致（测试会校验）
    return json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def site_files(sites_dir: Path = SITES_DIR):
    return sorted(p for p in sites_dir.glob("*.json") if p.name != INDEX_PATH.name and not p.name.startswith("."))


def build_index(sites_dir: Path = SITES_DIR) -> dict:
    entries = {}
    for path in site_files(sites_dir):
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8-sig"))
        site = payload["site"]
        if site in entries:
            raise SystemExit(f"重复声明站点 {site}: {entries[site]['file']} 与 {path.name}")
        entries[site] = {
            "file": path.name,
            "adapter_version": payload.get("adapter_version", ""),
            "min_app_version": payload.get("min_app_version", ""),
            "config_sha256": _sha256(_canonical(payload["config"]).encode("utf-8")),
            "file_sha256": _sha256(raw),
        }
    return {"schema_version": INDEX_SCHEMA_VERSION, "sites": dict(sorted(entries.items()))}


def render_index(index: dict) -> str:
    return json.dumps(index, indent=2, ensure_ascii=False) + "\n"


def _bump(paths, version: str) -> None:
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if payload.get("adapter_version") == version:
            continue
        rebuilt = {}
        for key, value in payload.items():
            if key == "config" and "adapter_version" not in rebuilt:
                rebuilt["adapter_version"] = version
            rebuilt[key] = value
        rebuilt["adapter_version"] = version
        order = ["schema_version", "site", "adapter_version", "min_app_version", "last_verified"]
        ordered = {k: rebuilt[k] for k in order if k in rebuilt}
        ordered.update({k: v for k, v in rebuilt.items() if k not in ordered and k != "config"})
        ordered["config"] = rebuilt["config"]
        path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print(f"bump {payload['site']} -> {version}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--bump", nargs="+", metavar="SITE")
    parser.add_argument("--bump-changed", action="store_true")
    parser.add_argument("--version", default=time.strftime("%Y.%m.%d"), help="bump 使用的版本号，默认今天")
    args = parser.parse_args(argv)

    if args.bump or args.bump_changed:
        by_site = {json.loads(p.read_text(encoding="utf-8-sig"))["site"]: p for p in site_files()}
        targets = []
        if args.bump:
            missing = [s for s in args.bump if s not in by_site]
            if missing:
                raise SystemExit(f"未知站点: {', '.join(missing)}")
            targets += [by_site[s] for s in args.bump]
        if args.bump_changed and INDEX_PATH.exists():
            old = json.loads(INDEX_PATH.read_text(encoding="utf-8")).get("sites", {})
            new = build_index()["sites"]
            targets += [by_site[s] for s, e in new.items() if old.get(s, {}).get("config_sha256") != e["config_sha256"]]
        _bump(sorted(set(targets)), args.version)

    rendered = render_index(build_index())
    if args.check:
        current = INDEX_PATH.read_text(encoding="utf-8") if INDEX_PATH.exists() else ""
        if current != rendered:
            print("config/sites/index.json 已过期：请运行 python scripts/build_sites_index.py", file=sys.stderr)
            return 1
        print("index.json 与站点文件一致")
        return 0
    INDEX_PATH.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"已写入 {INDEX_PATH.relative_to(ROOT)}（{len(json.loads(rendered)['sites'])} 个站点）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
