#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Arena 隐藏但可用（userSelectable: true）暗池模型导出脚本 - 现代化 HTML 仪表盘严谨版
层级架构：
1. 顶级：模态类别（Category: 文本对话、图像生成、代码增强、视频生成等）
2. 次级：月份归档（Month: 2026年08月、2026年07月、2026年06月）
3. 末级：高精度时间倒序列表（入库时间越晚越靠前）

输出：
- C:\\Users\\QIU\\Desktop\\arena_hidden_selectable_models.html (交互式现代化深色仪表盘，带即时搜索与一键复制UUID)
- C:\\Users\\QIU\\Desktop\\arena_hidden_selectable_models.md
- C:\\Users\\QIU\\Desktop\\arena_hidden_selectable_models.json
"""

import html
import json
import os
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

DESKTOP_PATH = Path(os.path.expanduser("~")) / "Desktop"
OUTPUT_HTML_PATH = DESKTOP_PATH / "arena_hidden_selectable_models.html"
OUTPUT_MD_PATH = DESKTOP_PATH / "arena_hidden_selectable_models.md"
OUTPUT_JSON_PATH = DESKTOP_PATH / "arena_hidden_selectable_models.json"

LOCAL_CACHE_PATH = Path(__file__).resolve().parent / "arena_models_cache.json"

ARENA_URL = "https://arena.ai/text/direct"

# 统一使用本地时区计算 2026-06-01 00:00:00 时间戳（毫秒）
FILTER_TIMESTAMP_START = datetime(2026, 6, 1, 0, 0, 0).timestamp() * 1000


def parse_uuidv7_timestamp(uuid_str: str) -> float | None:
    """
    根据 RFC 9562 规范严格校验并从 UUIDv7 提取创建时间戳（毫秒）
    UUIDv7 格式：xxxxxxxx-xxxx-7xxx-yxxx-xxxxxxxxxxxx
    其中第 13 位（1-indexed 对应 clean_hex[12]）固定为 '7'
    """
    if not uuid_str:
        return None
    clean_hex = uuid_str.replace("-", "").strip().lower()
    if len(clean_hex) != 32:
        return None

    # 严格校验第 13 位版本号是否为 '7'
    if clean_hex[12] != "7":
        return None

    try:
        ts_ms = int(clean_hex[:12], 16)
        # 合理性区间校验：2020年至2030年之间
        if 1577836800000 <= ts_ms <= 1893456000000:
            return ts_ms
    except Exception:
        pass
    return None


def get_model_time_info(model: dict) -> tuple[float | None, str, str]:
    """获取入库时间戳、显示字符串以及所属年月（统一时区处理）"""
    mid = model.get("id") or ""
    ts = parse_uuidv7_timestamp(mid)
    name_str = f"{model.get('name', '')} {model.get('displayName', '')} {model.get('publicName', '')}"

    if ts is not None:
        dt = datetime.fromtimestamp(ts / 1000.0)
        return ts, dt.strftime("%Y-%m-%d %H:%M"), dt.strftime("%Y年%m月")

    # 备选：从名称匹配 202606xx, 202607xx, 202608xx 等
    m = re.search(r"2026[_-]?(0[1-9]|1[0-2])[_-]?([0-3][0-9])", name_str)
    if m:
        try:
            year = 2026
            month = int(m.group(1))
            day = int(m.group(2))
            dt = datetime(year, month, day)
            return dt.timestamp() * 1000, dt.strftime("%Y-%m-%d"), f"{year}年{month:02d}月"
        except Exception:
            pass

    return None, "未知", "更早时期"


def extract_initial_models_from_html(html_text: str):
    """从 Next.js SSR HTML 中提取 initialModels JSON 数组"""
    marker = '"initialModels":'
    pos = html_text.find(marker)
    if pos == -1:
        return None
    start = html_text.find("[", pos + len(marker))
    if start == -1:
        return None

    depth = 0
    quoted = False
    escaped = False

    for i in range(start, len(html_text)):
        c = html_text[i]
        if quoted:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                quoted = False
            continue
        if c == '"':
            quoted = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                json_str = html_text[start : i + 1]
                try:
                    return json.loads(json_str)
                except Exception:
                    return None
    return None


def fetch_arena_models():
    """获取全量模型列表，支持本地持久化缓存"""
    req = urllib.request.Request(
        ARENA_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html_text = resp.read().decode("utf-8", errors="ignore")
            models = extract_initial_models_from_html(html_text)
            if models:
                try:
                    LOCAL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                    with open(LOCAL_CACHE_PATH, "w", encoding="utf-8") as f:
                        json.dump(models, f, ensure_ascii=False)
                except Exception:
                    pass
                return models
    except Exception:
        pass

    # 尝试从本地持久化缓存读取
    if LOCAL_CACHE_PATH.exists():
        try:
            with open(LOCAL_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    return None


def is_frontend_searchable(model: dict) -> bool:
    """判断是否在前端 UI 搜索框中可搜到"""
    name = (model.get("name") or "").lower()
    disp = (model.get("displayName") or "").lower()
    pub = (model.get("publicName") or "").lower()
    all_names = f"{name} {disp} {pub}"

    rank = model.get("rank")
    rank_modality = model.get("rankByModality") or {}
    chat_rank = rank_modality.get("chat")

    hidden_keywords = [
        "anonymous",
        "chatbot",
        "internal-test",
        "dlp-test",
        "smoke-test",
        "autoeval-test",
        "happy-friday",
        "segesta",
        "tianyi",
        "jebel_",
        "artemis_",
        "tetra-",
        "hofburg",
        "pisces-",
        "whisperfall",
        "viper",
        "redwood",
        "pulse",
        "ember",
        "spark",
        "hearth",
        "scooter",
        "rover",
        "mivan",
        "pakson",
        "luxor",
        "globe_",
        "beacon-",
        "tulip",
        "spider",
        "mammoth-newt",
        "pancetta",
        "victoria",
        "myrion",
        "maymo-",
        "maylynx-",
        "blue-forge",
        "rijks",
        "tetragonia-",
        "saga-decima",
        "tragopogon-",
        "marcus",
        "scorch",
        "beluga-",
        "monster",
        "camellia-",
        "dahlia-",
        "monterey",
        "neon",
        "steed-",
        "kiteki-beta",
        "mizar-beta",
        "may-beta",
        "cpqiang",
        "nightride-",
        "ring-1t",
        "frickin-router",
        "deep-octo",
        "leepwal",
        "blackhawk",
        "miles",
        "cloud-buddy",
        "tibouchina-",
        "solar-open2",
        "june-alpha",
        "hcx-lm",
    ]

    for kw in hidden_keywords:
        if kw in all_names:
            return False

    if (rank is None or rank >= 9000000000000000) and (
        chat_rank is None or chat_rank >= 9000000000000000
    ):
        well_known = [
            "gpt-4",
            "gpt-5",
            "claude-",
            "gemini-",
            "glm-",
            "qwen",
            "deepseek-",
            "kimi-",
            "grok-",
            "mistral-",
            "minimax-",
            "llama-",
        ]
        if not any(w in all_names for w in well_known):
            return False

    return True


def categorize_model(model: dict) -> tuple[str, str, str]:
    """返回 (分类ID, 分类中文名, 图标Emoji)"""
    caps = model.get("capabilities") or {}
    out_caps = caps.get("outputCapabilities") or {}

    if out_caps.get("video"):
        return "video", "Video（视频生成）", "🎬"
    elif out_caps.get("image"):
        return "image", "Image（图像生成与编辑）", "🎨"
    elif out_caps.get("search"):
        return "search", "Search（联网搜索增强）", "🌐"
    elif "code" in (model.get("name") or "").lower():
        return "code", "Code（代码专项增强）", "💻"
    else:
        return "chat", "Chat / Reasoning（文本对话与深度思考）", "💬"


def build_nested_structure(models: list) -> dict:
    """构建 类别 -> 月份 -> 模型的层级字典，内部按时间倒序"""
    cat_order = [
        ("chat", "Chat / Reasoning（文本对话与深度思考）", "💬"),
        ("image", "Image（图像生成与编辑）", "🎨"),
        ("code", "Code（代码专项增强）", "💻"),
        ("video", "Video（视频生成）", "🎬"),
        ("search", "Search（联网搜索增强）", "🌐"),
    ]

    tree = {}
    for cid, cname, icon in cat_order:
        tree[cid] = {"name": cname, "icon": icon, "months": {}, "count": 0}

    for m in models:
        cid, cname, icon = categorize_model(m)
        if cid not in tree:
            tree[cid] = {"name": cname, "icon": icon, "months": {}, "count": 0}

        month_key = m["_month_str"]
        if month_key not in tree[cid]["months"]:
            tree[cid]["months"][month_key] = []

        tree[cid]["months"][month_key].append(m)
        tree[cid]["count"] += 1

    for cid in tree:
        for month_key in tree[cid]["months"]:
            tree[cid]["months"][month_key].sort(
                key=lambda x: (-(x.get("_timestamp") or 0), x.get("displayName") or "")
            )

        sorted_months = dict(
            sorted(tree[cid]["months"].items(), key=lambda item: item[0], reverse=True)
        )
        tree[cid]["months"] = sorted_months

    return tree


def generate_html_report(tree: dict, total_count: int, filtered_count: int) -> str:
    """生成具备现代化 UI、完整 HTML 转义与事件代理复制的精美 HTML 页面"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    categories_html = []

    for cid, cat in tree.items():
        if cat["count"] == 0:
            continue

        months_html = []
        for month_name, m_list in cat["months"].items():
            if not m_list:
                continue

            rows_html = []
            for m in m_list:
                raw_mid = m.get("id") or ""
                raw_name = m.get("displayName") or m.get("name") or m.get("publicName") or "Unknown"
                raw_org = m.get("organization") or m.get("provider") or "Arena.ai (Blind)"
                raw_provider = m.get("provider") or ""
                time_str = m.get("_time_str") or "2026-06+"

                # 全面进行 HTML 安全转义
                safe_mid = html.escape(raw_mid)
                safe_name = html.escape(raw_name)
                safe_org = html.escape(raw_org)
                safe_provider = html.escape(raw_provider)
                safe_search_meta = html.escape(f"{raw_name} {raw_mid} {raw_org} {raw_provider}".lower())

                caps = m.get("capabilities") or {}
                in_caps = caps.get("inputCapabilities") or {}
                out_caps = caps.get("outputCapabilities") or {}

                badges = []
                if in_caps.get("image"):
                    badges.append('<span class="badge badge-vision">📷 Vision</span>')
                if in_caps.get("file"):
                    badges.append('<span class="badge badge-file">📁 File</span>')
                if out_caps.get("web"):
                    badges.append('<span class="badge badge-web">🌐 Web</span>')
                if out_caps.get("search"):
                    badges.append('<span class="badge badge-search">🔍 Search</span>')

                badge_str = " ".join(badges) if badges else '<span class="badge badge-text">💬 Text</span>'
                org_display = f"{safe_org} <span class='provider-tag'>{safe_provider}</span>" if safe_provider else safe_org

                rows_html.append(f"""
                <tr class="model-row" data-meta="{safe_search_meta}">
                    <td class="time-cell"><code>{time_str}</code></td>
                    <td class="name-cell">
                        <div class="model-name">{safe_name}</div>
                    </td>
                    <td class="uuid-cell">
                        <div class="uuid-wrapper">
                            <code class="uuid-text">{safe_mid}</code>
                            <button class="copy-btn" data-uuid="{safe_mid}" title="一键复制 UUID">
                                <svg class="copy-icon" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                                <span>复制</span>
                            </button>
                        </div>
                    </td>
                    <td class="org-cell">{org_display}</td>
                    <td class="badges-cell">{badge_str}</td>
                </tr>
                """)

            months_html.append(f"""
            <div class="month-section">
                <div class="month-header">
                    <div class="month-title">
                        <span class="calendar-icon">📅</span>
                        <span>{html.escape(month_name)}</span>
                        <span class="month-counter">{len(m_list)} 个模型</span>
                    </div>
                </div>
                <div class="table-responsive">
                    <table class="model-table">
                        <thead>
                            <tr>
                                <th style="width: 140px;">入库精确时间</th>
                                <th style="width: 220px;">模型代号 / 名称</th>
                                <th style="width: 380px;">模型 UUID (万能直连 targetModelId)</th>
                                <th style="width: 200px;">所属组织 / 节点</th>
                                <th>特性与支持能力</th>
                            </tr>
                        </thead>
                        <tbody>
                            {"".join(rows_html)}
                        </tbody>
                    </table>
                </div>
            </div>
            """)

        categories_html.append(f"""
        <section class="category-card" id="cat-{cid}">
            <div class="category-header">
                <div class="category-title">
                    <span class="cat-icon">{cat['icon']}</span>
                    <h2>{html.escape(cat['name'])}</h2>
                </div>
                <span class="category-badge">{cat['count']} 个暗池模型</span>
            </div>
            <div class="category-body">
                {"".join(months_html)}
            </div>
        </section>
        """)

    html_template = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Arena 最新隐藏暗池模型清单 (2026年6月及以后)</title>
    <style>
        :root {{
            --bg-base: #0f172a;
            --bg-card: #1e293b;
            --bg-card-hover: #334155;
            --bg-card-inner: #0b1120;
            --border-color: #334155;
            --border-light: #475569;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --text-dim: #64748b;
            --accent-primary: #38bdf8;
            --accent-glow: rgba(56, 189, 248, 0.15);
            --accent-success: #34d399;
            --accent-purple: #c084fc;
            --accent-amber: #fbbf24;
            --radius-lg: 16px;
            --radius-md: 10px;
            --radius-sm: 6px;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
            background-color: var(--bg-base);
            color: var(--text-main);
            line-height: 1.5;
            padding: 32px 24px;
            min-height: 100vh;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}

        /* 顶部 Header */
        .header {{
            background: linear-gradient(135deg, rgba(30, 41, 59, 0.9) 0%, rgba(15, 23, 42, 0.9) 100%);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-lg);
            padding: 32px;
            margin-bottom: 28px;
            backdrop-filter: blur(12px);
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3);
            position: relative;
            overflow: hidden;
        }}

        .header::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(90deg, #38bdf8, #818cf8, #c084fc);
        }}

        .header-top {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            flex-wrap: wrap;
            gap: 20px;
            margin-bottom: 20px;
        }}

        .title-group h1 {{
            font-size: 28px;
            font-weight: 700;
            background: linear-gradient(90deg, #ffffff, #94a3b8);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 12px;
        }}

        .title-group p {{
            color: var(--text-muted);
            font-size: 14px;
        }}

        /* 统计卡片面板 */
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-top: 24px;
        }}

        .stat-card {{
            background: rgba(11, 17, 32, 0.6);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
            padding: 16px 20px;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}

        .stat-label {{
            font-size: 13px;
            color: var(--text-dim);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .stat-val {{
            font-size: 24px;
            font-weight: 700;
            color: var(--accent-primary);
        }}

        /* 交互工具条（搜索与过滤） */
        .toolbar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
            margin-bottom: 24px;
            position: sticky;
            top: 16px;
            z-index: 50;
            background: rgba(15, 23, 42, 0.85);
            backdrop-filter: blur(16px);
            padding: 12px 16px;
            border-radius: var(--radius-md);
            border: 1px solid var(--border-color);
        }}

        .search-box {{
            flex: 1;
            min-width: 280px;
            max-width: 480px;
            position: relative;
        }}

        .search-input {{
            width: 100%;
            background: var(--bg-card-inner);
            border: 1px solid var(--border-light);
            border-radius: var(--radius-sm);
            padding: 10px 16px 10px 38px;
            color: var(--text-main);
            font-size: 14px;
            outline: none;
            transition: all 0.2s;
        }}

        .search-input:focus {{
            border-color: var(--accent-primary);
            box-shadow: 0 0 0 2px var(--accent-glow);
        }}

        .search-icon {{
            position: absolute;
            left: 12px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-dim);
            pointer-events: none;
        }}

        .nav-tabs {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }}

        .nav-btn {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            color: var(--text-muted);
            padding: 8px 14px;
            border-radius: var(--radius-sm);
            font-size: 13px;
            cursor: pointer;
            transition: all 0.2s;
            text-decoration: none;
        }}

        .nav-btn:hover, .nav-btn.active {{
            background: var(--border-light);
            color: var(--text-main);
            border-color: var(--accent-primary);
        }}

        /* 类别卡片 */
        .category-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-lg);
            margin-bottom: 32px;
            overflow: hidden;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
        }}

        .category-header {{
            background: rgba(11, 17, 32, 0.7);
            border-bottom: 1px solid var(--border-color);
            padding: 20px 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        .category-title {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}

        .cat-icon {{
            font-size: 22px;
        }}

        .category-title h2 {{
            font-size: 18px;
            font-weight: 600;
            color: var(--text-main);
        }}

        .category-badge {{
            background: rgba(56, 189, 248, 0.15);
            color: var(--accent-primary);
            border: 1px solid rgba(56, 189, 248, 0.3);
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }}

        /* 月份区块 */
        .month-section {{
            padding: 20px 24px 28px;
            border-bottom: 1px dashed var(--border-color);
        }}

        .month-section:last-child {{
            border-bottom: none;
        }}

        .month-header {{
            margin-bottom: 14px;
            display: flex;
            align-items: center;
        }}

        .month-title {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 15px;
            font-weight: 600;
            color: var(--accent-purple);
        }}

        .month-counter {{
            background: rgba(192, 132, 252, 0.15);
            color: var(--accent-purple);
            font-size: 11px;
            padding: 2px 8px;
            border-radius: 12px;
            font-weight: normal;
        }}

        /* 表格样式 */
        .table-responsive {{
            overflow-x: auto;
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
            background: var(--bg-card-inner);
        }}

        .model-table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 13px;
        }}

        .model-table th {{
            background: rgba(30, 41, 59, 0.8);
            color: var(--text-muted);
            padding: 12px 16px;
            font-weight: 600;
            border-bottom: 1px solid var(--border-color);
            white-space: nowrap;
        }}

        .model-table td {{
            padding: 12px 16px;
            border-bottom: 1px solid rgba(51, 65, 85, 0.4);
            color: var(--text-main);
        }}

        .model-row:hover td {{
            background: rgba(56, 189, 248, 0.04);
        }}

        .model-row:last-child td {{
            border-bottom: none;
        }}

        .is-hidden {{
            display: none !important;
        }}

        .time-cell code {{
            color: var(--text-muted);
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 12px;
        }}

        .model-name {{
            font-weight: 600;
            color: #ffffff;
            font-size: 14px;
        }}

        .uuid-wrapper {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .uuid-text {{
            background: rgba(15, 23, 42, 0.8);
            border: 1px solid var(--border-color);
            padding: 4px 8px;
            border-radius: 4px;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 12px;
            color: var(--accent-primary);
            user-select: all;
        }}

        .copy-btn {{
            background: rgba(51, 65, 85, 0.6);
            border: 1px solid var(--border-light);
            color: var(--text-muted);
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 4px;
            transition: all 0.2s;
        }}

        .copy-btn:hover {{
            background: var(--accent-primary);
            color: #0f172a;
            border-color: var(--accent-primary);
        }}

        .copy-btn.copied {{
            background: var(--accent-success) !important;
            color: #0f172a !important;
            border-color: var(--accent-success) !important;
        }}

        .provider-tag {{
            font-size: 11px;
            color: var(--text-dim);
            background: rgba(30, 41, 59, 0.6);
            padding: 2px 6px;
            border-radius: 4px;
            margin-left: 4px;
        }}

        /* 徽章 Badge */
        .badge {{
            display: inline-block;
            font-size: 11px;
            padding: 2px 8px;
            border-radius: 4px;
            font-weight: 500;
            white-space: nowrap;
        }}

        .badge-vision {{ background: rgba(52, 211, 153, 0.15); color: #34d399; border: 1px solid rgba(52, 211, 153, 0.3); }}
        .badge-web {{ background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3); }}
        .badge-file {{ background: rgba(251, 191, 36, 0.15); color: #fbbf24; border: 1px solid rgba(251, 191, 36, 0.3); }}
        .badge-search {{ background: rgba(192, 132, 252, 0.15); color: #c084fc; border: 1px solid rgba(192, 132, 252, 0.3); }}
        .badge-text {{ background: rgba(148, 163, 184, 0.15); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.3); }}

        .footer {{
            text-align: center;
            color: var(--text-dim);
            font-size: 13px;
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid var(--border-color);
        }}
    </style>
</head>
<body>
    <div class="container">
        <header class="header">
            <div class="header-top">
                <div class="title-group">
                    <h1><span>🏛️</span> Arena 最新隐藏暗池模型仪表盘</h1>
                    <p>专供「万能直连」预设使用 · 前端 UI 完全隐藏但底层权限开放（userSelectable: true）</p>
                </div>
            </div>

            <div class="stats-grid">
                <div class="stat-card">
                    <span class="stat-label">最新暗池模型总计 (>= 6月)</span>
                    <span class="stat-val">{filtered_count} 个</span>
                </div>
                <div class="stat-card">
                    <span class="stat-label">全量模型库基数</span>
                    <span class="stat-val">{total_count} 个</span>
                </div>
                <div class="stat-card">
                    <span class="stat-label">生成时间</span>
                    <span class="stat-val" style="font-size: 16px; color: var(--text-muted); font-weight: normal;">{now_str}</span>
                </div>
                <div class="stat-card">
                    <span class="stat-label">时间范围</span>
                    <span class="stat-val" style="font-size: 16px; color: var(--accent-success);">2026-06-01 ~ 至今</span>
                </div>
            </div>
        </header>

        <div class="toolbar">
            <div class="search-box">
                <svg class="search-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
                <input type="text" id="searchInput" class="search-input" placeholder="输入模型名称、代号或 UUID 即时过滤..." oninput="handleSearch(this.value)">
            </div>
            <div class="nav-tabs">
                <a href="#cat-chat" class="nav-btn">💬 文本对话 ({tree['chat']['count']})</a>
                <a href="#cat-image" class="nav-btn">🎨 图像生成 ({tree['image']['count']})</a>
                <a href="#cat-code" class="nav-btn">💻 代码增强 ({tree['code']['count']})</a>
                <a href="#cat-video" class="nav-btn">🎬 视频生成 ({tree['video']['count']})</a>
                <a href="#cat-search" class="nav-btn">🌐 搜索增强 ({tree['search']['count']})</a>
            </div>
        </div>

        <main id="contentContainer">
            {"".join(categories_html)}
        </main>

        <footer class="footer">
            <p>💡 使用提示：点击表格中的【复制】按钮即可直接复制模型 UUID，填入预设脚本的 <code>override_model</code> 即可实现万能直连。</p>
        </footer>
    </div>

    <script>
        // 事件代理绑定全局复制
        document.addEventListener('click', function(e) {{
            const copyBtn = e.target.closest('.copy-btn');
            if (!copyBtn) return;
            const uuid = copyBtn.getAttribute('data-uuid');
            if (!uuid) return;

            navigator.clipboard.writeText(uuid).then(() => {{
                const originalText = copyBtn.innerHTML;
                copyBtn.classList.add('copied');
                copyBtn.innerHTML = '<span>已复制!</span>';
                setTimeout(() => {{
                    copyBtn.classList.remove('copied');
                    copyBtn.innerHTML = originalText;
                }}, 1500);
            }}).catch(err => {{
                console.error('复制失败:', err);
            }});
        }});

        // 即时前端模糊搜索
        function handleSearch(query) {{
            const q = query.trim().toLowerCase();
            const rows = document.querySelectorAll('.model-row');
            
            rows.forEach(row => {{
                const meta = row.getAttribute('data-meta') || '';
                if (!q || meta.includes(q)) {{
                    row.classList.remove('is-hidden');
                }} else {{
                    row.classList.add('is-hidden');
                }}
            }});

            document.querySelectorAll('.month-section').forEach(sec => {{
                const visibleRows = sec.querySelectorAll('.model-row:not(.is-hidden)');
                if (visibleRows.length > 0) {{
                    sec.classList.remove('is-hidden');
                }} else {{
                    sec.classList.add('is-hidden');
                }}
            }});

            document.querySelectorAll('.category-card').forEach(card => {{
                const visibleSections = card.querySelectorAll('.month-section:not(.is-hidden)');
                if (visibleSections.length > 0) {{
                    card.classList.remove('is-hidden');
                }} else {{
                    card.classList.add('is-hidden');
                }}
            }});
        }}
    </script>
</body>
</html>
"""
    return html_template


def generate_markdown_report(tree: dict, total_count: int, filtered_count: int) -> str:
    """生成具备三级层级（类别 -> 月份 -> 时间倒序）并转义管道符的 Markdown 报告"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    md_lines = [
        "# Arena 最新隐藏暗池模型清单（2026年6月及以后）",
        f"> **生成时间**：`{now_str}`",
        f"> **时间范围**：`>= 2026-06-01` 至当前",
        f"> **全量模型总数**：`{total_count}` 个 | **2026年6月后隐藏可用暗池模型**：共 `{filtered_count}` 个",
        "> **使用说明**：本清单中所有模型在 Arena 前端 UI 搜索框中**均已被隐藏（搜不到）**，但底层配置为 `userSelectable: true`。在「万能直连」拦截脚本中直接将 `override_model` 设置为对应的 **UUID** 即可直接调用。",
        "",
        "---",
        "",
    ]

    for cid, cat in tree.items():
        if cat["count"] == 0:
            continue

        md_lines.append(f"# {cat['icon']} {cat['name']} (共 {cat['count']} 个)")
        md_lines.append("")

        for month_name, m_list in cat["months"].items():
            if not m_list:
                continue

            md_lines.append(f"### 📅 {month_name} (共 {len(m_list)} 个)")
            md_lines.append("")
            md_lines.append(
                "| 入库精确时间 | 模型名称 / 公开代号 | 模型 UUID (`override_model`) | 所属组织/Provider | 详细特性与能力 |"
            )
            md_lines.append("| :--- | :--- | :--- | :--- | :--- |")

            for m in m_list:
                mid = (m.get("id") or "").replace("|", "\\|")
                name = (m.get("displayName") or m.get("name") or m.get("publicName") or "Unknown").replace("|", "\\|")
                time_str = m.get("_time_str") or "2026-06+"
                org = (m.get("organization") or m.get("provider") or "Arena.ai (Blind)").replace("|", "\\|")
                provider = (m.get("provider") or "").replace("|", "\\|")

                caps = m.get("capabilities") or {}
                in_caps = caps.get("inputCapabilities") or {}
                out_caps = caps.get("outputCapabilities") or {}

                features = []
                if in_caps.get("image"):
                    features.append("支持传图(Vision)")
                if in_caps.get("file"):
                    features.append("支持文件")
                if out_caps.get("web"):
                    features.append("支持Web输出")
                if out_caps.get("search"):
                    features.append("支持搜索")

                feat_str = "、".join(features) if features else "纯文本对话"
                org_str = f"{org} ({provider})" if provider else org

                md_lines.append(f"| `{time_str}` | **`{name}`** | `{mid}` | {org_str} | {feat_str} |")

            md_lines.append("")
        md_lines.append("---")
        md_lines.append("")

    return "\n".join(md_lines)


def main():
    print("[1/3] 正在抓取 Arena 最新的模型元数据...")
    models = fetch_arena_models()

    if not models:
        print("[Error] 无法获取模型列表，请确保网络通畅或本地存在缓存！")
        sys.exit(1)

    print(f"[2/3] 正在解析时间戳与层级分组 (全量 {len(models)} 个模型)...")
    hidden_selectable = []

    for m in models:
        if m.get("userSelectable") is not True:
            continue
        if is_frontend_searchable(m):
            continue

        ts, time_str, month_str = get_model_time_info(m)
        m["_timestamp"] = ts
        m["_time_str"] = time_str
        m["_month_str"] = month_str

        if ts is not None and ts >= FILTER_TIMESTAMP_START:
            hidden_selectable.append(m)

    tree = build_nested_structure(hidden_selectable)

    print(f"[3/3] 正在生成三级层级 Markdown 与现代化 HTML 仪表盘...")
    html_content = generate_html_report(tree, len(models), len(hidden_selectable))
    with open(OUTPUT_HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html_content)

    md_content = generate_markdown_report(tree, len(models), len(hidden_selectable))
    with open(OUTPUT_MD_PATH, "w", encoding="utf-8") as f:
        f.write(md_content)

    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(hidden_selectable, f, ensure_ascii=False, indent=2)

    print(f"[Success] 成功导出 2026年6月之后的隐藏暗池模型共 {len(hidden_selectable)} 个！")
    print(f"[File] 🌟 现代化 HTML 报告: {OUTPUT_HTML_PATH}")
    print(f"[File] 📄 Markdown 文档: {OUTPUT_MD_PATH}")
    print(f"[File] 📊 JSON 原始数据: {OUTPUT_JSON_PATH}")


if __name__ == "__main__":
    main()
