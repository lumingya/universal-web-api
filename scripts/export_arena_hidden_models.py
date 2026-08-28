#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
Arena 全模态模型全景导出与现代化可视化仪表盘生成器 (模块化重构优化版)
================================================================================
【核心特性与架构】
1. 模块化组件渲染引擎 (Modular HTML Dashboard Renderer)：
   - CSS Design Tokens 现代主题体系 (深色玻璃拟态 Dark Glassmorphism)
   - 组件化渲染：KPI Cards、快速导航 Pills、厂商卡片、月份分组、数据表格、能力徽章
   - 原生高性能客户端交互：模态平滑切换、防抖实时搜索、池类型过滤、一键复制、键盘快捷键 (/ 与 Esc)
2. 多模态与时间窗口精准过滤：
   - 💬 纯文本对话板块：严格排除生图/视频/音频，暗池 >= 2026-06-01，明池 >= 2026-04-01
   - 🎨 图像生图板块：图像生成能力，暗池 >= 2026-06-01，明池 >= 2026-04-01
3. 灵活导出与 Demo 原型支持：
   - 支持全量多模态数据导出
   - 支持最小化 Demo 原型导出（纯文本与图像模态各自仅保留 1 个代表性模型）
================================================================================
"""

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ==============================================================================
# 1. 全局路径与时间常量配置
# ==============================================================================
DESKTOP_PATH = Path(os.path.expanduser("~")) / "Desktop"
OUTPUT_HTML_PATH = DESKTOP_PATH / "arena_hidden_selectable_models.html"
OUTPUT_DEMO_HTML_PATH = DESKTOP_PATH / "arena_hidden_selectable_models_demo.html"
LOCAL_CACHE_PATH = Path(__file__).resolve().parent / "arena_models_cache.json"

# 时间截断点（毫秒时间戳）
# 明池公开模型：2026年4月1日
# 暗池直连模型：2026年6月1日
FILTER_TIMESTAMP_PUBLIC_START = int(datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
FILTER_TIMESTAMP_DARK_START = int(datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

# ==============================================================================
# 2. Arena 线上真实前端 DOM 公开可见模型列表 (用于精确判定暗池 vs 明池)
# ==============================================================================
ONLINE_PUBLIC_VISIBLE_MODELS = {
    "gemini-3-flash", "glm-5.1", "qwen3.5-397b-a17b", "claude-sonnet-4-5-20250929",
    "gemini-3.1-pro-preview", "qwen3.7-plus", "minimax-m3", "claude-haiku-4-5-20251001",
    "gemini-2.5-pro", "glm-5v-turbo", "grok-4.20-beta-0309-reasoning", "gpt-5.2-high",
    "gpt-5.5-instant", "gpt-5.1", "gpt-5.2", "gemini-3.6-flash", "claude-sonnet-4-6",
    "grok-4.20-multi-agent-beta-0309", "qwen3.5-max-preview", "gemini-3.5-flash-lite",
    "glm-5", "claude-sonnet-4-5-20250929-thinking-32k", "gpt-5.1-high", "gpt-5.4-mini-high",
    "glm-4.7", "qwen3-max-preview", "gpt-5-high", "kimi-k2.5-instant", "o3-2025-04-16",
    "kimi-k2-thinking-turbo", "gpt-5-chat", "qwen3-max-2025-09-23", "qwen3-235b-a22b-instruct-2507",
    "kimi-k2-0711-preview", "kimi-k2-0905-preview", "qwen3.5-122b-a10b", "minimax-m2.7",
    "qwen3-vl-235b-a22b-instruct", "mistral-large-3", "gpt-4.1-2025-04-14", "gemini-2.5-flash",
    "mistral-medium-2508", "qwen3.5-27b", "inkling-small", "qwen3-235b-a22b-no-thinking",
    "gpt-5.4-nano-high", "longcat-flash-chat", "qwen3-next-80b-a3b-instruct",
    "claude-sonnet-4-20250514-thinking-32k", "qwen3-235b-a22b-thinking-2507", "qwen3.5-flash",
    "qwen3.5-35b-a3b", "hunyuan-vision-1.5-thinking", "qwen3-vl-235b-a22b-thinking",
    "step-3.5-flash", "minimax-m2.5", "o4-mini-2025-04-16", "gpt-5-mini-high",
    "claude-sonnet-4-20250514", "qwen3-coder-480b-a35b-instruct", "minimax-m2.1-preview",
    "qwen3-30b-a3b-instruct-2507", "gpt-4.1-mini-2025-04-14", "trinity-large-preview",
    "qwen3-235b-a22b", "trinity-large-thinking", "qwen3-next-80b-a3b-thinking",
    "gemma-3-27b-it", "minimax-m1", "gemini-2.0-flash-001", "intellect-3",
    "gemma-3-12b-it", "o3-mini-high", "gemma-3-4b-it", "mistral-small-3.2",
    "qwen3-vl-30b-a3b-instruct", "gemini-2.5-flash-lite", "qwen3-30b-a3b-thinking-2507",
    "qwen3-30b-a3b", "deepseek-v3.2", "gemma-3-1b-it", "deepseek-v4-flash",
    "o3-mini-2025-01-31", "glm-4-flash", "gemini-2.0-flash-thinking-exp-01-21",
    "qwen2.5-max-preview", "claude-3.5-haiku-20241022", "claude-3.7-sonnet",
    "step-2-16k-exp", "claude-3.7-sonnet-thinking", "deepseek-r1-distill-qwen-32b",
    "deepseek-v3.1", "deepseek-v4", "claude-3.5-sonnet-20241022", "gpt-4o-2024-11-20",
    "deepseek-v4-pro-max", "claude-sonnet-5-high", "gpt-5.4-mini", "gemini-3.7-pro",
    "gemini-3.7-pro-high", "glm-5.2 (max)", "deepseek-v4-flash-20260731",
    "gpt-5.4-high", "claude-sonnet-5-search", "gemini-3.5-pro-high",
    "gemini-3.5-flash-high", "deepseek-v4-flash-high", "gemini-3.5-flash",
    "gpt-5.4-turbo", "gpt-5.4", "gemini-3.5-pro", "gemini-2.5-pro-high",
    "gemini-3-flash-high", "gemini-3-pro-high", "gemini-3-flash-search",
    "gemini-2.5-ultra", "gemini-3-pro", "gemini-2.5-flash-thinking",
    "gemini-2.5-flash-high", "gemini-3.7-flash-high", "qwen3.5-122b-a10b-thinking",
    "qwen3.5-35b-a3b-thinking", "claude-opus-4.5", "claude-opus-4.5-thinking",
    "claude-opus-4.6", "claude-opus-4.6-thinking", "claude-opus-4.1",
    "claude-opus-4.1-thinking", "claude-opus-4", "claude-opus-4-thinking",
    "o4-mini-high", "o4-mini", "o4-max", "o4-high", "o3-pro-max", "o3-pro",
    "gpt-5.3-high", "gpt-5.3-mini", "gpt-5.3", "gpt-5.2-mini", "gpt-5.2-turbo",
    "gpt-5.1-turbo", "gpt-5.1-mini", "gpt-5.1-codex-max", "gpt-5-turbo", "gpt-5-mini",
    "inkling-low", "qwen3-omni-flash", "inkling-small-low", "inkling-medium",
    "gemini-3.7-flash", "mistral-medium-3.5", "gemini-3-flash (thinking-minimal)",
    "qwen3.7-plus-preview", "grok-4.6-medium", "grok-4.5"
}

# ==============================================================================
# 3. 厂商体系定义常量 (文本大厂 + 盲测暗池全收录；生图统一聚合不拆厂商)
# ==============================================================================
TEXT_VENDOR_DEFS: List[Tuple[str, str, str, str, str]] = [
    ("deepseek", "DeepSeek（深度求索）", "🐳", "#38bdf8", "DS"),
    ("kimi", "Kimi（月之暗面 Moonshot）", "🌙", "#c084fc", "KM"),
    ("glm", "GLM（智谱 AI / Z.ai）", "⚡", "#fbbf24", "GL"),
    ("openai", "OpenAI（GPT / o 系列）", "🟢", "#34d399", "OA"),
    ("claude", "Claude（Anthropic）", "🟠", "#fb923c", "CL"),
    ("gemini", "Gemini（Google）", "🔵", "#60a5fa", "GM"),
    ("qwen", "Qwen（阿里通义千问）", "🟣", "#a855f7", "QW"),
    ("stepfun", "StepFun（阶跃星辰）", "🚀", "#38bdf8", "SF"),
    ("minimax", "MiniMax（稀宇科技）", "⭐", "#f59e0b", "MM"),
    ("grok", "xAI（Grok 系列）", "🌌", "#e2e8f0", "GK"),
    ("mistral", "Mistral AI", "🌪️", "#f97316", "MS"),
    ("other_vendors", "Other Labs（混元/零一/百度/知名厂商）", "🏢", "#34d399", "OT"),
    ("blind", "Blind & Anonymous（盲测暗池与神秘代号）", "🎭", "#c084fc", "BL"),
]

IMAGE_VENDOR_DEFS: List[Tuple[str, str, str, str, str]] = [
    ("all_images", "全量图像生图直连模型（不区分厂商·统一收录）", "🎨", "#f472b6", "IMG"),
]

# ==============================================================================
# 4. 时间与元数据解析工具
# ==============================================================================
def parse_uuidv7_timestamp(uuid_str: str) -> Optional[float]:
    """从 UUIDv7 中解析毫秒级时间戳"""
    if not uuid_str or not isinstance(uuid_str, str):
        return None
    clean_hex = uuid_str.replace("-", "").strip().lower()
    if len(clean_hex) != 32 or clean_hex[12] != "7":
        return None
    try:
        ts_ms = int(clean_hex[:12], 16)
        if 1577836800000 <= ts_ms <= 1893456000000:
            return float(ts_ms)
    except Exception:
        pass
    return None

def get_model_time_info(model: Dict[str, Any]) -> Tuple[Optional[float], str, str]:
    """获取模型入库时间 (时间戳ms, 格式化时间串, 月份分组串)"""
    mid = model.get("id") or ""
    ts = parse_uuidv7_timestamp(mid)
    name_str = f"{model.get('name', '')} {model.get('displayName', '')} {model.get('publicName', '')}"

    if ts is not None:
        dt = datetime.fromtimestamp(ts / 1000.0, tz=timezone(timedelta(hours=8)))
        return ts, dt.strftime("%Y-%m-%d %H:%M"), dt.strftime("%Y-%m")

    m = re.search(r"2026[_-]?(0[1-9]|1[0-2])[_-]?([0-3][0-9])", name_str)
    if m:
        try:
            year, month, day = 2026, int(m.group(1)), int(m.group(2))
            dt = datetime(year, month, day, 0, 0, 0, tzinfo=timezone.utc)
            dt_local = dt.astimezone(timezone(timedelta(hours=8)))
            return dt.timestamp() * 1000, dt_local.strftime("%Y-%m-%d"), f"{year}-{month:02d}"
        except Exception:
            pass

    return None, "未知", "更早时期"

def extract_models_from_rsc_html(html_text: str) -> Optional[List[Dict[str, Any]]]:
    """从 Next.js RSC HTML 中提取 initialModels 数组"""
    pattern = re.compile(r"<script[^>]*>(.*?)</script>", re.DOTALL)
    for match in pattern.finditer(html_text):
        script_content = match.group(1)
        if "initialModels" not in script_content:
            continue

        push_pattern = re.compile(r'self\.__next_f\.push\(\[\d+,\s*"(.*?)"\]\)', re.DOTALL)
        for p_match in push_pattern.finditer(script_content):
            chunk_raw = p_match.group(1)
            if "initialModels" not in chunk_raw:
                continue

            try:
                decoded_chunk = json.loads(f'"{chunk_raw}"')
                colon_idx = decoded_chunk.find(":")
                if colon_idx != -1:
                    json_payload = decoded_chunk[colon_idx + 1:]
                    parsed_rsc = json.loads(json_payload)

                    def find_models(obj):
                        if not obj or not isinstance(obj, (dict, list)):
                            return None
                        if isinstance(obj, dict):
                            if "initialModels" in obj and isinstance(obj["initialModels"], list):
                                return obj["initialModels"]
                            for v in obj.values():
                                res = find_models(v)
                                if res:
                                    return res
                        elif isinstance(obj, list):
                            for item in obj:
                                res = find_models(item)
                                if res:
                                    return res
                        return None

                    models = find_models(parsed_rsc)
                    if models and len(models) > 0:
                        return models
            except Exception:
                pass
    return None

def fetch_arena_models_online() -> Optional[List[Dict[str, Any]]]:
    """通过 curl_cffi 模拟真实浏览器直接从 Arena 官网实时抓取全量最新模型列表 (支持自适应重试与代理容错)"""
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        return None

    url = "https://arena.ai/text/direct"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    
    # 候选连接策略列表 (直连 + 常用本地代理通道)
    candidate_proxies = [None]
    for env_k in ["https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"]:
        if os.environ.get(env_k):
            candidate_proxies.append(os.environ[env_k])
            break
    for p in ["http://127.0.0.1:7890", "http://127.0.0.1:10809", "http://127.0.0.1:30627"]:
        if p not in candidate_proxies:
            candidate_proxies.append(p)

    for proxy in candidate_proxies:
        try:
            req_kwargs = {"headers": headers, "impersonate": "chrome124", "timeout": 8}
            if proxy:
                req_kwargs["proxies"] = {"http": proxy, "https": proxy}
            resp = cffi_requests.get(url, **req_kwargs)
            if resp.status_code == 200 and resp.text:
                models = extract_models_from_rsc_html(resp.text)
                if models and len(models) > 0:
                    try:
                        ensure_parent_dir(LOCAL_CACHE_PATH)
                        with open(LOCAL_CACHE_PATH, "w", encoding="utf-8") as f:
                            json.dump(models, f, ensure_ascii=False, indent=2)
                    except Exception:
                        pass
                    return models
        except Exception:
            continue
    return None

def fetch_arena_models(prefer_online: bool = True) -> Optional[List[Dict[str, Any]]]:
    """获取全量 Arena 模型元数据：优先在线实时抓取，失败时自动安全降级至本地缓存"""
    if prefer_online:
        print("      -> 正在尝试联网实时抓取 Arena 最新模型列表 (curl_cffi)...")
        online_models = fetch_arena_models_online()
        if online_models:
            print(f"      -> 🌐 实时联网抓取成功！已获取官方最新模型共：{len(online_models)} 个 (已自动同步到本地缓存)")
            return online_models
        else:
            print("      -> ⚠️ 在线拉取未能完成（可能离线或网络受限），自动切换至本地缓存...")

    if LOCAL_CACHE_PATH.exists():
        try:
            with open(LOCAL_CACHE_PATH, "r", encoding="utf-8") as f:
                models = json.load(f)
                print(f"      -> 📦 成功加载本地缓存模型记录：{len(models)} 个")
                return models
        except Exception as e:
            print(f"[Error] 读取模型缓存文件失败: {e}")
    return None

# ==============================================================================
# 5. 模态与分类过滤判定
# ==============================================================================
def is_strictly_text_modality(model: Dict[str, Any]) -> bool:
    """严格纯文本对话模型判定（排除生图、视频、音频能力）"""
    name = (model.get("displayName") or model.get("name") or model.get("publicName") or "").lower()
    caps = model.get("capabilities") or {}
    out_caps = caps.get("outputCapabilities") or {}

    # 1. 严格排除视频能力
    if out_caps.get("video") or any(k in name for k in ["video", "seedance", "sora", "veo", "kling", "runway", "wan", "vidu"]):
        return False
    # 2. 严格排除音频能力
    if out_caps.get("audio") or any(k in name for k in ["audio", "voice", "tts", "music", "speech"]):
        return False
    # 3. 严格排除图像生成能力
    is_img_name = any(k in name for k in [
        "gpt-image", "mona-lisa", "luna-lisa", "lina-alpha", "lina-f-alpha", "silver_halide",
        "flux", "seedream", "seededit", "imagine", "imagen", "z-image", "midjourney", "dall-e", "recraft", "krea"
    ])
    if is_img_name or out_caps.get("image"):
        return False
    # 4. 必须具备文本或 Web 对话输出能力
    return bool(out_caps.get("text") or out_caps.get("web"))

def is_image_modality(model: Dict[str, Any]) -> bool:
    """图像生图模型判定（排除纯视频、纯音频）"""
    name = (model.get("displayName") or model.get("name") or model.get("publicName") or "").lower()
    caps = model.get("capabilities") or {}
    out_caps = caps.get("outputCapabilities") or {}

    if out_caps.get("video") or out_caps.get("audio"):
        return False
    is_img_name = any(k in name for k in [
        "image", "gpt-image", "mona-lisa", "luna-lisa", "lina-alpha", "lina-f-alpha", "silver_halide",
        "flux", "seedream", "seededit", "imagine", "imagen", "z-image", "midjourney", "dall-e", "recraft", "krea", "wan2.7-image"
    ])
    return bool(out_caps.get("image") or is_img_name)

def is_hidden_from_frontend_picker(model: Dict[str, Any]) -> bool:
    """判定是否属于暗池 (在前端下拉列表中隐藏)"""
    disp = (model.get("displayName") or "").strip().lower()
    name = (model.get("name") or "").strip().lower()
    pub = (model.get("publicName") or "").strip().lower()

    for pub_name in ONLINE_PUBLIC_VISIBLE_MODELS:
        pub_lower = pub_name.lower()
        if disp == pub_lower or pub == pub_lower or name == pub_lower:
            if not any(k in f"{disp} {name} {pub}" for k in ["internal", "test", "dlp", "fireworks", "node"]):
                return False
    return True

def match_text_vendor(model: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    """纯文本大厂分类 + 盲测暗池兜底保护 (100% 覆盖所有纯文本模型，不漏掉任何暗池)"""
    disp = (model.get("displayName") or "").strip().lower()
    name = (model.get("name") or "").strip().lower()
    pub = (model.get("publicName") or "").strip().lower()
    org = (model.get("organization") or "").strip().lower()
    prov = (model.get("provider") or "").strip().lower()
    all_text = f"{name} {disp} {pub} {org} {prov}"

    if "deepseek" in all_text:
        return "deepseek", "DeepSeek（深度求索）", "🐳", "#38bdf8", "DS"
    if "kimi" in all_text or "moonshot" in all_text:
        return "kimi", "Kimi（月之暗面 Moonshot）", "🌙", "#c084fc", "KM"
    if "glm" in all_text or "zai" in all_text or "zhipu" in all_text or "siliconflow" in prov:
        return "glm", "GLM（智谱 AI / Z.ai）", "⚡", "#fbbf24", "GL"
    if re.search(r"(openai|gpt[-_]?\d|o3[-_]|o4[-_]|\bo3\b|\bo4\b)", all_text):
        return "openai", "OpenAI（GPT / o 系列）", "🟢", "#34d399", "OA"
    if "claude" in all_text or "anthropic" in all_text:
        return "claude", "Claude（Anthropic）", "🟠", "#fb923c", "CL"
    if "gemini" in all_text or "google" in all_text or "gemma" in all_text:
        return "gemini", "Gemini（Google）", "🔵", "#60a5fa", "GM"
    if "qwen" in all_text or "alibaba" in all_text:
        return "qwen", "Qwen（阿里通义千问）", "🟣", "#a855f7", "QW"
    if "step" in all_text or "stepfun" in all_text:
        return "stepfun", "StepFun（阶跃星辰）", "🚀", "#38bdf8", "SF"
    if "minimax" in all_text:
        return "minimax", "MiniMax（稀宇科技）", "⭐", "#f59e0b", "MM"
    if "grok" in all_text or "xai" in all_text:
        return "grok", "xAI（Grok 系列）", "🌌", "#e2e8f0", "GK"
    if "mistral" in all_text:
        return "mistral", "Mistral AI", "🌪️", "#f97316", "MS"
    if any(k in all_text for k in ["hunyuan", "tencent", "yi-", "01-ai", "ernie", "baidu", "baichuan", "sensetime", "solar", "hcx", "nova"]):
        return "other_vendors", "Other Labs（混元/零一/百度/知名厂商）", "🏢", "#34d399", "OT"
    
    # 若为前端公开可见的小众实验室模型，归入 Other Labs
    if not is_hidden_from_frontend_picker(model):
        return "other_vendors", "Other Labs（混元/零一/百度/知名厂商）", "🏢", "#34d399", "OT"

    # 若为暗池未知代号模型，归入盲测暗池
    return "blind", "Blind & Anonymous（盲测暗池与神秘代号）", "🎭", "#c084fc", "BL"

def match_image_vendor(m: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    """图像生图模型：统一收录为一个完整大类，不区分厂商"""
    return "all_images", "全量图像生图直连模型（不区分厂商·统一收录）", "🎨", "#f472b6", "IMG"

# ==============================================================================
# 6. 数据树构建
# ==============================================================================
def build_modality_tree(models: List[Dict[str, Any]], vendor_defs: List[Tuple[str, str, str, str, str]], match_fn) -> Dict[str, Any]:
    """构建数据树：厂商 -> 月份 -> 模型列表（入库时间倒序）"""
    tree: Dict[str, Any] = {}
    for vid, vname, vicon, vcolor, vcode in vendor_defs:
        tree[vid] = {
            "id": vid,
            "name": vname,
            "icon": vicon,
            "color": vcolor,
            "code": vcode,
            "months": {},
            "count": 0,
            "dark_count": 0,
            "public_count": 0
        }

    for m in models:
        v_info = match_fn(m)
        if not v_info:
            continue
        vid, vname, vicon, vcolor, vcode = v_info
        if vid not in tree:
            tree[vid] = {
                "id": vid,
                "name": vname,
                "icon": vicon,
                "color": vcolor,
                "code": vcode,
                "months": {},
                "count": 0,
                "dark_count": 0,
                "public_count": 0
            }

        month_key = m.get("_month_str", "其他")
        if month_key not in tree[vid]["months"]:
            tree[vid]["months"][month_key] = []

        tree[vid]["months"][month_key].append(m)
        tree[vid]["count"] += 1
        if m.get("_is_hidden"):
            tree[vid]["dark_count"] += 1
        else:
            tree[vid]["public_count"] += 1

    # 排序：月份倒序，月份内部时间戳倒序
    for vid in tree:
        for month_key in tree[vid]["months"]:
            tree[vid]["months"][month_key].sort(
                key=lambda x: (-(x.get("_timestamp") or 0), x.get("displayName") or "")
            )
        tree[vid]["months"] = dict(sorted(tree[vid]["months"].items(), key=lambda item: item[0], reverse=True))

    return tree

# ==============================================================================
# 7. 模块化 HTML 渲染引擎 (Modular HTML Dashboard Renderer)
# ==============================================================================
class ArenaDashboardRenderer:
    """Arena 仪表盘 HTML 生成引擎，负责样式、脚本与 DOM 组件渲染"""

    @staticmethod
    def render_css() -> str:
        return """
        /* Design Tokens: 现代深色玻璃拟态 & 科技渐变 */
        :root {
            --bg-base: #060911;
            --bg-mesh: radial-gradient(at 0% 0%, rgba(56, 189, 248, 0.08) 0px, transparent 50%),
                       radial-gradient(at 100% 0%, rgba(168, 85, 247, 0.08) 0px, transparent 50%),
                       radial-gradient(at 50% 100%, rgba(244, 114, 182, 0.06) 0px, transparent 50%);
            --bg-glass-card: rgba(13, 20, 38, 0.96);
            --bg-glass-header: rgba(10, 16, 32, 0.98);
            --bg-glass-inner: rgba(7, 12, 24, 0.92);
            --bg-glass-hover: rgba(22, 32, 58, 0.96);
            --border-glass: rgba(255, 255, 255, 0.08);
            --border-glass-subtle: rgba(255, 255, 255, 0.04);
            --border-glass-glow: rgba(56, 189, 248, 0.35);
            --border-glass-img: rgba(244, 114, 182, 0.35);
            --text-primary: #f8fafc;
            --text-secondary: #cbd5e1;
            --text-muted: #94a3b8;
            --text-dim: #64748b;
            --accent-cyan: #38bdf8;
            --accent-indigo: #818cf8;
            --accent-purple: #c084fc;
            --accent-pink: #f472b6;
            --accent-emerald: #34d399;
            --accent-amber: #fbbf24;
            --glow-cyan: 0 0 16px rgba(56, 189, 248, 0.15);
            --glow-pink: 0 0 16px rgba(244, 114, 182, 0.15);
            --glow-card: 0 4px 16px rgba(0, 0, 0, 0.3);
            --radius-xl: 16px;
            --radius-lg: 12px;
            --radius-md: 8px;
            --radius-sm: 6px;
            --radius-pill: 9999px;
            --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
            --font-mono: "JetBrains Mono", "Fira Code", Consolas, Menlo, monospace;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; -webkit-font-smoothing: antialiased; }
        body {
            font-family: var(--font-sans);
            background-color: var(--bg-base);
            color: var(--text-primary);
            line-height: 1.5;
            padding: 24px 20px 48px;
            min-height: 100vh;
            overflow-x: clip;
        }
        .dashboard-container { max-width: 1440px; margin: 0 auto; }
        a { color: inherit; text-decoration: none; }
        code { font-family: var(--font-mono); }

        /* Master Header & Segment Switcher */
        .master-header {
            background: var(--bg-glass-card);
            border: 1px solid var(--border-glass);
            border-radius: var(--radius-xl);
            padding: 28px;
            margin-bottom: 24px;
            box-shadow: var(--glow-card);
            position: relative;
            overflow: hidden;
            transform: translateZ(0);
        }
        .master-header::before {
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0; height: 3px;
            background: linear-gradient(90deg, #38bdf8, #818cf8, #c084fc, #f472b6, #fbbf24);
        }
        .master-top-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
            margin-bottom: 20px;
        }
        .master-title-flex { display: flex; align-items: center; gap: 16px; }
        .master-logo {
            width: 44px;
            height: 44px;
            border-radius: var(--radius-lg);
            background: linear-gradient(135deg, rgba(56, 189, 248, 0.2), rgba(192, 132, 252, 0.2));
            border: 1px solid var(--border-glass-glow);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
        }
        .master-title-text h1 {
            font-size: 24px;
            font-weight: 800;
            letter-spacing: -0.5px;
            color: #ffffff;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .version-badge {
            font-size: 11px;
            font-weight: 700;
            padding: 3px 8px;
            border-radius: var(--radius-pill);
            background: rgba(56, 189, 248, 0.15);
            color: var(--accent-cyan);
            border: 1px solid rgba(56, 189, 248, 0.3);
            text-transform: uppercase;
        }
        .master-desc { color: var(--text-muted); font-size: 13px; margin-top: 4px; }
        .time-tag {
            font-size: 12px;
            color: var(--text-dim);
            background: rgba(15, 23, 42, 0.9);
            border: 1px solid var(--border-glass);
            padding: 6px 12px;
            border-radius: var(--radius-sm);
            display: flex;
            align-items: center;
            gap: 6px;
        }

        /* Modality Big Segment Cards */
        .modality-segment-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        .modality-tab-card {
            background: var(--bg-glass-inner);
            border: 1px solid var(--border-glass);
            border-radius: var(--radius-lg);
            padding: 18px 20px;
            cursor: pointer;
            transition: border-color 0.2s, background 0.2s, transform 0.2s;
            display: flex;
            align-items: center;
            justify-content: space-between;
            position: relative;
            user-select: none;
            transform: translateZ(0);
        }
        .modality-tab-card:hover {
            border-color: rgba(255, 255, 255, 0.2);
            background: var(--bg-glass-hover);
            transform: translateY(-2px);
        }
        .modality-tab-card.active.text-tab {
            border-color: var(--accent-cyan);
            background: linear-gradient(135deg, rgba(56, 189, 248, 0.12) 0%, rgba(15, 23, 42, 0.98) 100%);
            box-shadow: var(--glow-cyan);
        }
        .modality-tab-card.active.image-tab {
            border-color: var(--accent-pink);
            background: linear-gradient(135deg, rgba(244, 114, 182, 0.12) 0%, rgba(15, 23, 42, 0.98) 100%);
            box-shadow: var(--glow-pink);
        }
        .tab-card-left { display: flex; align-items: center; gap: 16px; }
        .modality-icon-bubble {
            width: 48px;
            height: 48px;
            border-radius: var(--radius-md);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border-glass);
        }
        .text-tab.active .modality-icon-bubble { background: rgba(56, 189, 248, 0.2); border-color: var(--accent-cyan); }
        .image-tab.active .modality-icon-bubble { background: rgba(244, 114, 182, 0.2); border-color: var(--accent-pink); }
        .tab-card-info h3 { font-size: 17px; font-weight: 700; color: #ffffff; margin-bottom: 3px; }
        .tab-card-info p { font-size: 12px; color: var(--text-muted); max-width: 440px; }
        .tab-card-right { text-align: right; }
        .tab-total-num { font-size: 28px; font-weight: 800; font-family: var(--font-mono); letter-spacing: -1px; color: var(--text-primary); }
        .text-tab.active .tab-total-num { color: var(--accent-cyan); }
        .image-tab.active .tab-total-num { color: var(--accent-pink); }
        .tab-sub-breakdown { font-size: 11.5px; color: var(--text-dim); margin-top: 2px; display: flex; gap: 6px; justify-content: flex-end; }
        .break-dark { color: var(--accent-purple); }
        .break-pub { color: var(--accent-emerald); }

        /* Panels & KPI Cards */
        .modality-panel { display: none; }
        .modality-panel.active { display: block; }

        .stats-kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
        .kpi-card {
            background: var(--bg-glass-card);
            border: 1px solid var(--border-glass);
            border-radius: var(--radius-lg);
            padding: 16px 20px;
            display: flex;
            flex-direction: column;
            gap: 6px;
            transition: transform 0.2s, border-color 0.2s;
            transform: translateZ(0);
        }
        .kpi-card:hover { border-color: rgba(255, 255, 255, 0.15); transform: translateY(-2px); }
        .kpi-header { display: flex; justify-content: space-between; align-items: center; }
        .kpi-label { font-size: 12px; font-weight: 600; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; }
        .kpi-icon { font-size: 16px; }
        .kpi-value { font-size: 26px; font-weight: 800; font-family: var(--font-mono); letter-spacing: -0.5px; color: #ffffff; }
        .kpi-desc { font-size: 12px; color: var(--text-muted); }

        /* Quick Navigation Bar */
        .quick-nav-bar {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            padding: 6px 0;
            margin-bottom: 20px;
        }
        .quick-nav-title { font-size: 12px; color: var(--text-dim); font-weight: 600; white-space: nowrap; margin-right: 4px; }
        .quick-nav-pill {
            background: var(--bg-glass-card);
            border: 1px solid var(--border-glass);
            padding: 5px 12px;
            border-radius: var(--radius-pill);
            font-size: 12px;
            color: var(--text-secondary);
            display: inline-flex;
            align-items: center;
            gap: 6px;
            white-space: nowrap;
            transition: border-color 0.15s, color 0.15s, transform 0.15s;
        }
        .quick-nav-pill:hover { border-color: var(--accent-cyan); color: #ffffff; transform: translateY(-1px); }
        .img-nav-pill:hover { border-color: var(--accent-pink); }
        .pill-num { font-size: 11px; background: rgba(255, 255, 255, 0.08); padding: 1px 6px; border-radius: 10px; font-family: var(--font-mono); }

        /* Sticky Search & Filter Toolbar */
        .sticky-toolbar {
            position: sticky;
            top: 12px;
            z-index: 80;
            background: var(--bg-glass-header);
            border: 1px solid var(--border-glass);
            border-radius: var(--radius-lg);
            padding: 12px 18px;
            margin-bottom: 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 14px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
            transform: translateZ(0);
        }
        .toolbar-left { display: flex; align-items: center; gap: 12px; flex: 1; max-width: 520px; }
        .search-box-wrap { position: relative; width: 100%; }
        .search-icon-svg {
            position: absolute; left: 14px; top: 50%; transform: translateY(-50%);
            color: var(--text-dim); pointer-events: none;
        }
        .search-input-field {
            width: 100%;
            background: var(--bg-glass-inner);
            border: 1px solid var(--border-glass);
            border-radius: var(--radius-md);
            padding: 9px 38px 9px 40px;
            color: var(--text-primary);
            font-size: 13.5px;
            outline: none;
            transition: border-color 0.2s;
        }
        .search-input-field:focus {
            border-color: var(--accent-cyan);
            background: rgba(10, 16, 32, 0.98);
        }
        .image-panel .search-input-field:focus {
            border-color: var(--accent-pink);
        }
        .search-clear-btn {
            position: absolute; right: 12px; top: 50%; transform: translateY(-50%);
            background: none; border: none; color: var(--text-dim); cursor: pointer; font-size: 14px; display: none;
        }
        .search-clear-btn.show { display: block; }
        .toolbar-right { display: flex; align-items: center; gap: 10px; }
        .filter-tab-group {
            display: flex; background: rgba(7, 12, 24, 0.9); padding: 3px;
            border-radius: var(--radius-md); border: 1px solid var(--border-glass); gap: 3px;
        }
        .filter-btn {
            background: transparent; border: none; color: var(--text-muted);
            padding: 6px 12px; border-radius: var(--radius-sm); font-size: 12.5px; font-weight: 500;
            cursor: pointer; transition: color 0.15s, background 0.15s; display: flex; align-items: center; gap: 6px;
        }
        .filter-btn:hover { color: #ffffff; background: rgba(255, 255, 255, 0.05); }
        .filter-btn.active { background: rgba(56, 189, 248, 0.18); color: var(--accent-cyan); font-weight: 600; }
        .image-panel .filter-btn.active { background: rgba(244, 114, 182, 0.18); color: var(--accent-pink); }
        .tool-btn {
            background: var(--bg-glass-inner); border: 1px solid var(--border-glass);
            color: var(--text-secondary); padding: 7px 12px; border-radius: var(--radius-md);
            font-size: 12.5px; cursor: pointer; display: flex; align-items: center; gap: 6px; transition: all 0.15s;
        }
        .tool-btn:hover { background: var(--bg-glass-hover); color: #ffffff; border-color: rgba(255, 255, 255, 0.2); }

        /* Vendor Glass Cards & Month Group (支持虚拟化加速与硬件渲染) */
        .vendor-glass-card {
            background: var(--bg-glass-card);
            border: 1px solid var(--border-glass);
            border-radius: var(--radius-xl);
            margin-bottom: 24px;
            overflow: hidden;
            box-shadow: var(--glow-card);
            scroll-margin-top: 80px;
            contain: content;
            content-visibility: auto;
            contain-intrinsic-size: auto 600px;
            transform: translateZ(0);
        }
        .vendor-glass-card:hover { border-color: rgba(255, 255, 255, 0.12); }
        .vendor-glass-header {
            padding: 16px 20px; background: var(--bg-glass-header);
            border-bottom: 1px solid var(--border-glass); display: flex;
            justify-content: space-between; align-items: center; cursor: pointer; user-select: none;
        }
        .vendor-glass-header:hover { background: rgba(18, 26, 48, 0.95); }
        .vendor-header-left { display: flex; align-items: center; gap: 12px; }
        .vendor-avatar-icon {
            width: 36px; height: 36px; border-radius: var(--radius-md);
            display: flex; align-items: center; justify-content: center; font-size: 18px; border: 1px solid;
        }
        .vendor-title-group { display: flex; flex-direction: column; gap: 2px; }
        .vendor-title { font-size: 18px; font-weight: 700; color: #ffffff; letter-spacing: -0.3px; }
        .vendor-subtitle { font-size: 12px; color: var(--text-dim); }
        .vendor-header-right { display: flex; align-items: center; gap: 10px; }
        .stat-pill {
            font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: var(--radius-pill);
            border: 1px solid; font-family: var(--font-mono);
        }
        .pill-total { background: rgba(255, 255, 255, 0.05); color: var(--text-secondary); border-color: var(--border-glass); }
        .pill-dark { background: rgba(192, 132, 252, 0.12); color: #e9d5ff; border-color: rgba(192, 132, 252, 0.3); }
        .pill-public { background: rgba(52, 211, 153, 0.12); color: #a7f3d0; border-color: rgba(52, 211, 153, 0.3); }
        .vendor-toggle-btn {
            background: rgba(255, 255, 255, 0.05); border: 1px solid var(--border-glass);
            color: var(--text-muted); width: 32px; height: 32px; border-radius: var(--radius-sm);
            display: flex; align-items: center; justify-content: center; cursor: pointer; transition: all 0.2s;
        }
        .vendor-toggle-btn:hover { background: rgba(255, 255, 255, 0.1); color: #ffffff; }
        .chevron-icon { transition: transform 0.3s; }
        .vendor-glass-card.collapsed .chevron-icon { transform: rotate(-90deg); }
        .vendor-glass-card.collapsed .vendor-glass-body { display: none; }
        .vendor-glass-body { padding: 24px; }
        .month-group { margin-bottom: 24px; scroll-margin-top: 80px; }
        .month-group:last-child { margin-bottom: 0; }
        .month-badge-header {
            display: flex; justify-content: space-between; align-items: center;
            padding: 8px 0 12px; border-bottom: 1px dashed var(--border-glass); margin-bottom: 14px;
        }
        .month-title-left { display: flex; align-items: center; gap: 8px; font-size: 14.5px; font-weight: 600; color: var(--text-secondary); }
        .month-counter-tag { font-size: 11.5px; color: var(--text-dim); font-family: var(--font-mono); }

        /* Tables & Row Highlighting */
        .table-container {
            overflow-x: auto; border-radius: var(--radius-md);
            border: 1px solid var(--border-glass-subtle); background: rgba(7, 12, 24, 0.5);
        }
        .glass-table { width: 100%; border-collapse: collapse; text-align: left; font-size: 13px; }
        .glass-table th {
            background: rgba(10, 16, 32, 0.9); color: var(--text-muted); font-weight: 600;
            font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; padding: 12px 16px;
            border-bottom: 1px solid var(--border-glass); white-space: nowrap;
        }
        .glass-table td {
            padding: 12px 16px; border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            vertical-align: middle; transition: background 0.15s;
        }
        .model-row:hover td { background: rgba(30, 41, 69, 0.35); }
        .model-row.is-hidden, .month-group.is-hidden, .vendor-glass-card.is-hidden { display: none !important; }
        .col-time .time-badge {
            font-family: var(--font-mono); font-size: 12px; color: var(--text-secondary);
            background: rgba(255, 255, 255, 0.04); padding: 3px 8px; border-radius: 4px; white-space: nowrap;
        }
        .model-title-wrap { display: flex; align-items: center; gap: 8px; }
        .model-name { font-size: 13.5px; font-weight: 600; color: #ffffff; letter-spacing: -0.2px; }

        /* UUID Pill & Copy Action */
        .uuid-card {
            display: inline-flex; align-items: center; gap: 6px; background: rgba(6, 10, 20, 0.9);
            border: 1px solid var(--border-glass); padding: 3px 6px 3px 10px; border-radius: var(--radius-sm);
            transition: border-color 0.2s;
        }
        .uuid-card:hover { border-color: rgba(56, 189, 248, 0.4); }
        .uuid-code { font-family: var(--font-mono); font-size: 11.5px; color: var(--accent-cyan); user-select: all; }
        .image-panel .uuid-code { color: var(--accent-pink); }
        .action-copy-btn {
            background: rgba(255, 255, 255, 0.08); border: 1px solid rgba(255, 255, 255, 0.1);
            color: var(--text-secondary); padding: 3px 8px; border-radius: 4px; font-size: 11px;
            font-weight: 500; cursor: pointer; display: inline-flex; align-items: center; gap: 4px; transition: all 0.2s;
        }
        .action-copy-btn:hover { background: var(--accent-cyan); color: #060911; border-color: var(--accent-cyan); }
        .image-panel .action-copy-btn:hover { background: var(--accent-pink); color: #060911; border-color: var(--accent-pink); }
        .action-copy-btn.copied { background: var(--accent-emerald) !important; color: #060911 !important; border-color: var(--accent-emerald) !important; }

        .org-name { font-weight: 500; color: var(--text-secondary); }
        .provider-pill {
            font-size: 10.5px; color: var(--text-dim); background: rgba(255, 255, 255, 0.05);
            padding: 2px 6px; border-radius: 4px; margin-left: 6px; font-family: var(--font-mono);
        }

        /* Badges System */
        .badge {
            display: inline-flex; align-items: center; gap: 4px; font-size: 11px; font-weight: 500;
            padding: 3px 8px; border-radius: var(--radius-sm); margin-right: 4px; margin-bottom: 2px; white-space: nowrap;
        }
        .badge-dot { width: 6px; height: 6px; border-radius: 50%; display: inline-block; }
        .dot-purple { background: #c084fc; box-shadow: 0 0 6px #c084fc; }
        .dot-emerald { background: #34d399; box-shadow: 0 0 6px #34d399; }
        .badge-dark { background: rgba(168, 85, 247, 0.15); color: #e9d5ff; border: 1px solid rgba(168, 85, 247, 0.35); }
        .badge-public { background: rgba(16, 185, 129, 0.15); color: #a7f3d0; border: 1px solid rgba(16, 185, 129, 0.35); }
        .badge-image { background: rgba(244, 114, 182, 0.15); color: #fbcfe8; border: 1px solid rgba(244, 114, 182, 0.35); }
        .badge-vision { background: rgba(56, 189, 248, 0.12); color: #bae6fd; border: 1px solid rgba(56, 189, 248, 0.3); }
        .badge-file { background: rgba(251, 191, 36, 0.12); color: #fde68a; border: 1px solid rgba(251, 191, 36, 0.3); }
        .badge-web { background: rgba(129, 140, 248, 0.12); color: #c7d2fe; border: 1px solid rgba(129, 140, 248, 0.3); }
        .badge-reasoning { background: rgba(244, 63, 94, 0.15); color: #fecdd3; border: 1px solid rgba(244, 63, 94, 0.35); }
        .badge-ratio { background: rgba(232, 121, 249, 0.15); color: #f5d0fe; border: 1px solid rgba(232, 121, 249, 0.35); }
        .badge-internal { background: rgba(245, 158, 11, 0.12); color: #fde68a; border: 1px solid rgba(245, 158, 11, 0.3); }
        .badge-neutral { background: rgba(148, 163, 184, 0.1); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.2); }

        /* Empty State & Toast */
        .empty-state-card {
            background: var(--bg-glass-card); border: 1px dashed var(--border-glass);
            border-radius: var(--radius-lg); padding: 48px 24px; text-align: center; display: none; margin-top: 16px;
        }
        .empty-state-card.show { display: block; }
        .empty-icon { font-size: 42px; margin-bottom: 12px; }
        .empty-text { font-size: 15px; color: var(--text-secondary); font-weight: 600; }
        .empty-sub { font-size: 13px; color: var(--text-dim); margin-top: 4px; }
        .dashboard-footer {
            text-align: center; color: var(--text-dim); font-size: 13px;
            margin-top: 48px; padding-top: 24px; border-top: 1px solid var(--border-glass); line-height: 1.8;
        }
        .footer-tip-code { background: rgba(255, 255, 255, 0.05); padding: 2px 6px; border-radius: 4px; color: var(--accent-cyan); }
        .toast-notification {
            position: fixed; bottom: 24px; right: 24px; background: #0f172a;
            border: 1px solid var(--accent-cyan); box-shadow: var(--glow-cyan); color: #ffffff;
            padding: 12px 20px; border-radius: var(--radius-md); font-size: 13.5px; display: flex;
            align-items: center; gap: 10px; transform: translateY(100px); opacity: 0;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); z-index: 9999;
        }
        .toast-notification.show { transform: translateY(0); opacity: 1; }

        /* Responsive Layout */
        @media (max-width: 1024px) {
            .modality-segment-grid { grid-template-columns: 1fr; }
            .stats-kpi-grid { grid-template-columns: repeat(2, 1fr); }
        }
        @media (max-width: 640px) {
            body { padding: 16px 12px 48px; }
            .master-header { padding: 20px 16px; }
            .stats-kpi-grid { grid-template-columns: 1fr; }
            .sticky-toolbar { flex-direction: column; align-items: stretch; }
            .toolbar-left { max-width: 100%; }
            .toolbar-right { justify-content: space-between; }
        }
        """

    @staticmethod
    def render_js() -> str:
        return """
        // 1. 一键复制 UUID 并给予 Toast 与按钮动画反馈 (带并发防重入锁定)
        document.addEventListener('click', function(e) {
            const copyBtn = e.target.closest('.action-copy-btn');
            if (!copyBtn) return;
            if (copyBtn.classList.contains('copied')) return; // 防重入锁定

            const uuid = copyBtn.getAttribute('data-uuid');
            if (!uuid) return;

            copyToClipboard(uuid, () => {
                const originalContent = copyBtn.innerHTML;
                copyBtn.classList.add('copied');
                copyBtn.innerHTML = `
                    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5">
                        <polyline points="20 6 9 17 4 12"></polyline>
                    </svg>
                    <span>已复制!</span>
                `;
                showToast(`已复制 UUID: ${uuid}`);
                setTimeout(() => {
                    copyBtn.classList.remove('copied');
                    copyBtn.innerHTML = originalContent;
                }, 1800);
            });
        });

        function copyToClipboard(text, successCallback) {
            if (navigator.clipboard && window.isSecureContext) {
                navigator.clipboard.writeText(text).then(successCallback).catch(() => fallbackCopy(text, successCallback));
            } else {
                fallbackCopy(text, successCallback);
            }
        }

        function fallbackCopy(text, callback) {
            const textArea = document.createElement("textarea");
            textArea.value = text;
            textArea.style.position = "fixed";
            textArea.style.left = "-999999px";
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            try {
                document.execCommand('copy');
                if (callback) callback();
            } catch (err) {
                console.error('复制失败:', err);
            }
            document.body.removeChild(textArea);
        }

        function showToast(message) {
            const toast = document.getElementById('toastNotification');
            const msgEl = document.getElementById('toastMessage');
            if (!toast || !msgEl) return;
            msgEl.textContent = message;
            toast.classList.add('show');
            setTimeout(() => {
                toast.classList.remove('show');
            }, 2200);
        }

        // 2. 模态分栏切换
        function switchModality(mode) {
            document.querySelectorAll('.modality-tab-card').forEach(card => card.classList.remove('active'));
            document.querySelectorAll('.modality-panel').forEach(panel => panel.classList.remove('active'));

            if (mode === 'text') {
                document.getElementById('tabCardText').classList.add('active');
                document.getElementById('panelText').classList.add('active');
            } else {
                document.getElementById('tabCardImage').classList.add('active');
                document.getElementById('panelImage').classList.add('active');
            }
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }

        // 3. 实时搜索与池过滤
        const currentPoolFilters = { text: 'all', image: 'all' };

        function setPoolFilter(modality, pool, btn) {
            currentPoolFilters[modality] = pool;
            const panel = document.getElementById(modality === 'text' ? 'panelText' : 'panelImage');
            panel.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            if (btn) btn.classList.add('active');
            handleSearch(modality);
        }

        function handleSearch(modality) {
            const isText = (modality === 'text');
            const panel = document.getElementById(isText ? 'panelText' : 'panelImage');
            const searchInput = panel.querySelector('.search-input-field');
            const clearBtn = panel.querySelector('.search-clear-btn');
            const emptyState = document.getElementById(isText ? 'textEmptyState' : 'imageEmptyState');
            
            const q = (searchInput ? searchInput.value : '').trim().toLowerCase();
            if (clearBtn) {
                if (q) clearBtn.classList.add('show');
                else clearBtn.classList.remove('show');
            }

            const poolFilter = currentPoolFilters[modality];
            const rows = panel.querySelectorAll('.model-row');
            let totalVisible = 0;

            rows.forEach(row => {
                const meta = row.getAttribute('data-meta') || '';
                const pool = row.getAttribute('data-pool') || '';
                const matchesQuery = !q || meta.includes(q);
                let matchesPool = true;
                if (poolFilter === 'dark') matchesPool = (pool === 'dark');
                if (poolFilter === 'public') matchesPool = (pool === 'public');

                if (matchesQuery && matchesPool) {
                    row.classList.remove('is-hidden');
                    totalVisible++;
                } else {
                    row.classList.add('is-hidden');
                }
            });

            // 联动隐藏空月份和空厂商
            panel.querySelectorAll('.month-group').forEach(group => {
                const visibleRows = group.querySelectorAll('.model-row:not(.is-hidden)');
                if (visibleRows.length > 0) group.classList.remove('is-hidden');
                else group.classList.add('is-hidden');
            });

            panel.querySelectorAll('.vendor-glass-card').forEach(card => {
                const visibleGroups = card.querySelectorAll('.month-group:not(.is-hidden)');
                if (visibleGroups.length > 0) {
                    card.classList.remove('is-hidden');
                    if (q) {
                        card.classList.remove('collapsed');
                    }
                } else {
                    card.classList.add('is-hidden');
                }
            });

            if (totalVisible === 0) {
                if (emptyState) emptyState.classList.add('show');
            } else {
                if (emptyState) emptyState.classList.remove('show');
            }
        }

        function clearSearch(modality, shouldFocus = true) {
            const isText = (modality === 'text');
            const panel = document.getElementById(isText ? 'panelText' : 'panelImage');
            const searchInput = panel.querySelector('.search-input-field');
            if (searchInput) {
                searchInput.value = '';
                if (shouldFocus) {
                    searchInput.focus();
                } else {
                    searchInput.blur();
                }
            }
            handleSearch(modality);
        }

        // 4. 厂商卡片折叠
        function toggleVendorCard(header) {
            const card = header.closest('.vendor-glass-card');
            if (card) card.classList.toggle('collapsed');
        }

        const vendorExpandState = { text: true, image: true };

        function toggleAllVendors(modality) {
            const isText = (modality === 'text');
            const panel = document.getElementById(isText ? 'panelText' : 'panelImage');
            const btnText = document.getElementById(isText ? 'textToggleAllText' : 'imgToggleAllText');
            const currentState = vendorExpandState[modality];
            const cards = panel.querySelectorAll('.vendor-glass-card');

            cards.forEach(card => {
                if (currentState) {
                    card.classList.add('collapsed');
                } else {
                    card.classList.remove('collapsed');
                }
            });

            vendorExpandState[modality] = !currentState;
            if (btnText) {
                btnText.textContent = currentState ? '展开全部' : '折叠全部';
            }
        }

        // 5. 键盘快捷键 (/ 聚焦搜索, Esc 清除搜索并失焦)
        document.addEventListener('keydown', function(e) {
            if (e.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
                e.preventDefault();
                const activePanel = document.querySelector('.modality-panel.active');
                if (activePanel) {
                    const input = activePanel.querySelector('.search-input-field');
                    if (input) input.focus();
                }
            } else if (e.key === 'Escape') {
                const activePanel = document.querySelector('.modality-panel.active');
                if (activePanel) {
                    const modality = activePanel.id === 'panelText' ? 'text' : 'image';
                    clearSearch(modality, false);
                }
            }
        });
        """

    @classmethod
    def render_badges(cls, model: Dict[str, Any], modality_type: str) -> str:
        """根据模型元数据渲染能力徽章"""
        raw_name = (model.get("displayName") or model.get("name") or "").lower()
        caps = model.get("capabilities") or {}
        in_caps = caps.get("inputCapabilities") or {}
        out_caps = caps.get("outputCapabilities") or {}

        badges = []
        if modality_type == "image":
            badges.append('<span class="badge badge-image">🎨 图像生成</span>')
            if in_caps.get("image"):
                badges.append('<span class="badge badge-vision">📷 图生图</span>')
            if out_caps.get("image") and isinstance(out_caps.get("image"), dict):
                if out_caps.get("image", {}).get("aspectRatios"):
                    badges.append('<span class="badge badge-ratio">📐 多比例</span>')
        else:
            if in_caps.get("image"):
                badges.append('<span class="badge badge-vision">📷 传图 (Vision)</span>')
            if in_caps.get("file"):
                badges.append('<span class="badge badge-file">📁 文件解析</span>')
            if out_caps.get("web"):
                badges.append('<span class="badge badge-web">🌐 Web 输出</span>')
            if any(k in raw_name for k in ["thinking", "reasoning", "(max)"]):
                badges.append('<span class="badge badge-reasoning">🧠 深度思考</span>')

        if any(k in raw_name for k in ["internal", "test", "dlp"]):
            badges.append('<span class="badge badge-internal">🔒 内部测试</span>')

        return " ".join(badges) if badges else '<span class="badge badge-neutral">标准对话</span>'

    @classmethod
    def render_model_row(cls, m: Dict[str, Any], modality_type: str) -> str:
        """渲染单条模型数据表格行"""
        raw_mid = m.get("id") or ""
        raw_name = m.get("displayName") or m.get("name") or m.get("publicName") or "Unknown"
        raw_org = m.get("organization") or m.get("provider") or "Arena"
        raw_provider = m.get("provider") or ""
        time_str = m.get("_time_str") or "2026+"
        is_hidden = m.get("_is_hidden", False)

        safe_mid = html.escape(raw_mid)
        safe_name = html.escape(raw_name)
        safe_org = html.escape(raw_org)
        safe_provider = html.escape(raw_provider)
        safe_search_meta = html.escape(f"{raw_name} {raw_mid} {raw_org} {raw_provider}".lower())

        badge_pool = (
            '<span class="badge badge-dark"><span class="badge-dot dot-purple"></span>🔒 纯正暗池</span>'
            if is_hidden else
            '<span class="badge badge-public"><span class="badge-dot dot-emerald"></span>🌐 明池公开</span>'
        )
        badge_str = cls.render_badges(m, modality_type)
        org_display = f'<span class="org-name">{safe_org}</span>' + (
            f'<span class="provider-pill">{safe_provider}</span>' if safe_provider and safe_provider != safe_org else ''
        )

        return f"""
        <tr class="model-row" data-meta="{safe_search_meta}" data-pool="{'dark' if is_hidden else 'public'}">
            <td class="col-time"><span class="time-badge">{time_str}</span></td>
            <td class="col-name">
                <div class="model-title-wrap">
                    <span class="model-name">{safe_name}</span>
                </div>
            </td>
            <td class="col-pool">{badge_pool}</td>
            <td class="col-uuid">
                <div class="uuid-card">
                    <code class="uuid-code">{safe_mid}</code>
                    <button class="action-copy-btn" data-uuid="{safe_mid}" title="一键复制 UUID 直连">
                        <svg class="copy-svg" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2">
                            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                        </svg>
                        <span class="btn-text">复制</span>
                    </button>
                </div>
            </td>
            <td class="col-org">{org_display}</td>
            <td class="col-caps">{badge_str}</td>
        </tr>
        """

    @classmethod
    def render_vendor_sections(cls, tree: Dict[str, Any], modality_type: str = "text") -> str:
        """渲染厂商板块卡片列表"""
        vendor_sections = []
        for vid, vendor in tree.items():
            if vendor.get("count", 0) == 0:
                continue

            months_html = []
            for month_name, m_list in vendor.get("months", {}).items():
                if not m_list:
                    continue

                m_label = month_name
                if len(month_name) == 7 and "-" in month_name:
                    try:
                        y, mo = month_name.split("-")
                        m_label = f"{y}年{int(mo):02d}月"
                    except Exception:
                        pass

                clean_m_key = re.sub(r"[^a-zA-Z0-9_-]", "_", month_name)
                month_dom_id = f"{modality_type}-month-{clean_m_key}"
                rows_html = [cls.render_model_row(m, modality_type) for m in m_list]
                months_html.append(f"""
                <div class="month-group" id="{month_dom_id}">
                    <div class="month-badge-header">
                        <div class="month-title-left">
                            <span class="month-cal-icon">📅</span>
                            <span class="month-text">{html.escape(m_label)}</span>
                        </div>
                        <span class="month-counter-tag">{len(m_list)} 个模型入库</span>
                    </div>
                    <div class="table-container">
                        <table class="glass-table">
                            <thead>
                                <tr>
                                    <th style="width: 145px;">入库精确时间</th>
                                    <th style="width: 250px;">模型名称 / 代号</th>
                                    <th style="width: 125px;">池类型</th>
                                    <th style="width: 360px;">模型 UUID (直连 ID)</th>
                                    <th style="width: 165px;">所属组织 / Provider</th>
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

            vendor_sections.append(f"""
            <section class="vendor-glass-card" id="{modality_type}-vendor-{vid}">
                <div class="vendor-glass-header" onclick="toggleVendorCard(this)">
                    <div class="vendor-header-left">
                        <span class="vendor-avatar-icon" style="background: {vendor['color']}18; border-color: {vendor['color']}44; color: {vendor['color']};">{vendor['icon']}</span>
                        <div class="vendor-title-group">
                            <h2 class="vendor-title">{html.escape(vendor['name'])}</h2>
                            <span class="vendor-subtitle">时间倒序归档 · 100% 完整收录</span>
                        </div>
                    </div>
                    <div class="vendor-header-right">
                        <span class="stat-pill pill-total">{vendor['count']} 个模型</span>
                        <span class="stat-pill pill-dark">🔒 暗池 {vendor['dark_count']}</span>
                        <span class="stat-pill pill-public">🌐 明池 {vendor['public_count']}</span>
                        <button class="vendor-toggle-btn" title="折叠/展开当前板块">
                            <svg class="chevron-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5">
                                <polyline points="6 9 12 15 18 9"></polyline>
                            </svg>
                        </button>
                    </div>
                </div>
                <div class="vendor-glass-body">
                    {"".join(months_html)}
                </div>
            </section>
            """)

        return "\n".join(vendor_sections)

    @classmethod
    def render_dashboard(
        cls,
        text_tree: Dict[str, Any],
        text_stats: Dict[str, int],
        img_tree: Dict[str, Any],
        img_stats: Dict[str, int],
        is_demo: bool = False
    ) -> str:
        """组装生成完整仪表盘 HTML 页面 (生图统一不拆厂商，纯净无使用说明)"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        version_title = "极简 Demo 版" if is_demo else "旗舰版"
        desc_text = "极简体验 Demo · 纯文本与生图各自精选 1 个代表性模型 · 支持一键复制直连" if is_demo else "全模态顶栏分栏架构 · 文本与生图彻底物理隔离 · 100% 全量暗池覆盖 · 支持一键复制 UUID 直连"

        text_sections_html = cls.render_vendor_sections(text_tree, "text")
        img_sections_html = cls.render_vendor_sections(img_tree, "image")

        text_nav_pills = "".join([
            f'<a href="#text-vendor-{vid}" class="quick-nav-pill"><span class="pill-icon">{v["icon"]}</span>{html.escape(v["name"].split("（")[0])} <span class="pill-num">{v["count"]}</span></a>'
            for vid, v in text_tree.items() if v["count"] > 0
        ])
        
        # 生图快速定位：按月份锚点导航
        img_vendor = img_tree.get("all_images", {})
        img_nav_pills = "".join([
            f'<a href="#image-month-{re.sub(r"[^a-zA-Z0-9_-]", "_", m_name)}" class="quick-nav-pill img-nav-pill"><span class="pill-icon">📅</span>{html.escape(m_name)} <span class="pill-num">{len(m_list)}</span></a>'
            for m_name, m_list in img_vendor.get("months", {}).items()
        ])

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Arena 全模态模型全景仪表盘 ({version_title})</title>
    <style>
{cls.render_css()}
    </style>
</head>
<body>
    <div class="dashboard-container">
        <!-- 顶栏总览与模态大切换 -->
        <header class="master-header">
            <div class="master-top-bar">
                <div class="master-title-flex">
                    <div class="master-logo">🏛️</div>
                    <div class="master-title-text">
                        <h1>
                            Arena 全模态模型全景仪表盘
                            <span class="version-badge">{version_title}</span>
                        </h1>
                        <p class="master-desc">{desc_text}</p>
                    </div>
                </div>
                <div class="master-meta-box">
                    <div class="time-tag">
                        <span>🕒</span>
                        <span>生成时间：{now_str}</span>
                    </div>
                </div>
            </div>

            <!-- 模态分栏切换卡片 -->
            <div class="modality-segment-grid">
                <!-- 纯文本对话模型 -->
                <div class="modality-tab-card text-tab active" id="tabCardText" onclick="switchModality('text')">
                    <div class="tab-card-left">
                        <div class="modality-icon-bubble">💬</div>
                        <div class="tab-card-info">
                            <h3>纯文本对话模型</h3>
                            <p>12 大生态厂商 + 盲测暗池全景收录（严格排除生图/视频/音频，暗池 100% 完整覆盖）</p>
                        </div>
                    </div>
                    <div class="tab-card-right">
                        <div class="tab-total-num">{text_stats['total']}</div>
                        <div class="tab-sub-breakdown">
                            <span class="break-dark">🔒 暗池 {text_stats['dark']}</span>
                            <span>·</span>
                            <span class="break-pub">🌐 明池 {text_stats['public']}</span>
                        </div>
                    </div>
                </div>

                <!-- 图像生图生成模型 -->
                <div class="modality-tab-card image-tab" id="tabCardImage" onclick="switchModality('image')">
                    <div class="tab-card-left">
                        <div class="modality-icon-bubble">🎨</div>
                        <div class="tab-card-info">
                            <h3>图像生图生成模型</h3>
                            <p>全量生图直连暗池（统一汇总展示，不拆分厂商，涵盖文生图/图生图/多比例输出）</p>
                        </div>
                    </div>
                    <div class="tab-card-right">
                        <div class="tab-total-num">{img_stats['total']}</div>
                        <div class="tab-sub-breakdown">
                            <span class="break-dark">🔒 暗池 {img_stats['dark']}</span>
                            <span>·</span>
                            <span class="break-pub">🌐 明池 {img_stats['public']}</span>
                        </div>
                    </div>
                </div>
            </div>
        </header>

        <!-- 板块 1: 纯文本对话模型面板 -->
        <main id="panelText" class="modality-panel active text-panel">
            <div class="stats-kpi-grid">
                <div class="kpi-card">
                    <div class="kpi-header">
                        <span class="kpi-label">纯文本模型总数</span>
                        <span class="kpi-icon">📊</span>
                    </div>
                    <div class="kpi-value" style="color: var(--accent-cyan);">{text_stats['total']} <span style="font-size: 14px; font-weight: normal; color: var(--text-dim);">个</span></div>
                    <div class="kpi-desc">严格纯文本对话 (userSelectable)</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-header">
                        <span class="kpi-label">纯文本暗池模型</span>
                        <span class="kpi-icon">🔒</span>
                    </div>
                    <div class="kpi-value" style="color: var(--accent-purple);">{text_stats['dark']} <span style="font-size: 14px; font-weight: normal; color: var(--text-dim);">个</span></div>
                    <div class="kpi-desc">收录标准 >= 2026-06-01 (全收录)</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-header">
                        <span class="kpi-label">明池公开可用模型</span>
                        <span class="kpi-icon">🌐</span>
                    </div>
                    <div class="kpi-value" style="color: var(--accent-emerald);">{text_stats['public']} <span style="font-size: 14px; font-weight: normal; color: var(--text-dim);">个</span></div>
                    <div class="kpi-desc">主流大厂与实验室 >= 2026-04-01</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-header">
                        <span class="kpi-label">覆盖生态大类</span>
                        <span class="kpi-icon">🏢</span>
                    </div>
                    <div class="kpi-value" style="font-size: 18px; color: var(--accent-indigo);">12 大厂体系 + 盲测暗池</div>
                    <div class="kpi-desc">DeepSeek · Kimi · GLM · OpenAI · Qwen · Grok · Claude ...</div>
                </div>
            </div>

            <div class="quick-nav-bar">
                <span class="quick-nav-title">快速定位厂商：</span>
                {text_nav_pills}
            </div>

            <div class="sticky-toolbar">
                <div class="toolbar-left">
                    <div class="search-box-wrap">
                        <svg class="search-icon-svg" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
                            <circle cx="11" cy="11" r="8"></circle>
                            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                        </svg>
                        <input type="text" class="search-input-field" id="textSearchInput" placeholder="实时检索文本模型名称、UUID、代号或组织... (按 / 聚焦)" oninput="handleSearch('text')">
                        <button class="search-clear-btn" id="textSearchClear" onclick="clearSearch('text')">✕</button>
                    </div>
                </div>
                <div class="toolbar-right">
                    <div class="filter-tab-group">
                        <button class="filter-btn active" data-filter="all" onclick="setPoolFilter('text', 'all', this)">全部 ({text_stats['total']})</button>
                        <button class="filter-btn" data-filter="dark" onclick="setPoolFilter('text', 'dark', this)">🔒 仅暗池 ({text_stats['dark']})</button>
                        <button class="filter-btn" data-filter="public" onclick="setPoolFilter('text', 'public', this)">🌐 仅明池 ({text_stats['public']})</button>
                    </div>
                    <button class="tool-btn" onclick="toggleAllVendors('text')">
                        <span id="textToggleAllText">折叠全部</span>
                    </button>
                </div>
            </div>

            <div id="textVendorContainer">
                {text_sections_html}
            </div>

            <div id="textEmptyState" class="empty-state-card">
                <div class="empty-icon">🔍</div>
                <div class="empty-text">未找到符合条件的纯文本模型</div>
                <div class="empty-sub">请尝试调整搜索关键词或池类型过滤选项</div>
            </div>
        </main>

        <!-- 板块 2: 图像生图生成模型面板 (统一全景板块，不拆分厂商) -->
        <main id="panelImage" class="modality-panel image-panel">
            <div class="stats-kpi-grid">
                <div class="kpi-card">
                    <div class="kpi-header">
                        <span class="kpi-label">图像生图模型总数</span>
                        <span class="kpi-icon">🎨</span>
                    </div>
                    <div class="kpi-value" style="color: var(--accent-pink);">{img_stats['total']} <span style="font-size: 14px; font-weight: normal; color: var(--text-dim);">个</span></div>
                    <div class="kpi-desc">具备图像生成输出能力 (userSelectable)</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-header">
                        <span class="kpi-label">生图暗池直连模型</span>
                        <span class="kpi-icon">🔒</span>
                    </div>
                    <div class="kpi-value" style="color: var(--accent-purple);">{img_stats['dark']} <span style="font-size: 14px; font-weight: normal; color: var(--text-dim);">个</span></div>
                    <div class="kpi-desc">全量直连暗池 >= 2026-06-01</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-header">
                        <span class="kpi-label">明池公开生图</span>
                        <span class="kpi-icon">🌐</span>
                    </div>
                    <div class="kpi-value" style="color: var(--accent-emerald);">{img_stats['public']} <span style="font-size: 14px; font-weight: normal; color: var(--text-dim);">个</span></div>
                    <div class="kpi-desc">前端公开列表可见</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-header">
                        <span class="kpi-label">支持特性范围</span>
                        <span class="kpi-icon">✨</span>
                    </div>
                    <div class="kpi-value" style="font-size: 18px; color: var(--accent-pink);">文生图 · 图生图 · 多比例</div>
                    <div class="kpi-desc">不区分厂商 · 统一按月份倒序收录</div>
                </div>
            </div>

            <div class="quick-nav-bar">
                <span class="quick-nav-title">快速定位月份：</span>
                {img_nav_pills}
            </div>

            <div class="sticky-toolbar">
                <div class="toolbar-left">
                    <div class="search-box-wrap">
                        <svg class="search-icon-svg" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
                            <circle cx="11" cy="11" r="8"></circle>
                            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                        </svg>
                        <input type="text" class="search-input-field" id="imgSearchInput" placeholder="实时检索生图模型名称、UUID、代号或组织... (按 / 聚焦)" oninput="handleSearch('image')">
                        <button class="search-clear-btn" id="imgSearchClear" onclick="clearSearch('image')">✕</button>
                    </div>
                </div>
                <div class="toolbar-right">
                    <div class="filter-tab-group">
                        <button class="filter-btn active" data-filter="all" onclick="setPoolFilter('image', 'all', this)">全部 ({img_stats['total']})</button>
                        <button class="filter-btn" data-filter="dark" onclick="setPoolFilter('image', 'dark', this)">🔒 仅暗池 ({img_stats['dark']})</button>
                        <button class="filter-btn" data-filter="public" onclick="setPoolFilter('image', 'public', this)">🌐 仅明池 ({img_stats['public']})</button>
                    </div>
                    <button class="tool-btn" onclick="toggleAllVendors('image')">
                        <span id="imgToggleAllText">折叠全部</span>
                    </button>
                </div>
            </div>

            <div id="imageVendorContainer">
                {img_sections_html}
            </div>

            <div id="imageEmptyState" class="empty-state-card">
                <div class="empty-icon">🔍</div>
                <div class="empty-text">未找到符合条件的图像生图模型</div>
                <div class="empty-sub">请尝试调整搜索关键词或池类型过滤选项</div>
            </div>
        </main>
    </div>

    <div id="toastNotification" class="toast-notification">
        <span style="font-size: 16px;">📋</span>
        <span id="toastMessage">UUID 已成功复制到剪贴板！</span>
    </div>

    <script>
{cls.render_js()}
    </script>
</body>
</html>
"""

# ==============================================================================
# 8. 极简 Demo 模式数据构建 (基于真实线上暗池模型)
# ==============================================================================
def build_demo_data(
    available_text_models: Optional[List[Dict[str, Any]]] = None,
    available_img_models: Optional[List[Dict[str, Any]]] = None
) -> Tuple[Dict[str, Any], Dict[str, int], Dict[str, Any], Dict[str, int]]:
    """
    构建极简 Demo 数据：文本和生图各自仅保留 1 个真实的代表性暗池模型。
    优先从当前已筛选的真实模型库中选取最新模型，避免使用 mock 伪造 UUID 导致直连时触发 CF 盾错误。
    """
    selected_text_model = None
    selected_img_model = None

    if available_text_models:
        for m in available_text_models:
            if m.get("_is_hidden"):
                selected_text_model = m
                break
        if not selected_text_model and available_text_models:
            selected_text_model = available_text_models[0]

    if available_img_models:
        for m in available_img_models:
            if m.get("_is_hidden"):
                selected_img_model = m
                break
        if not selected_img_model and available_img_models:
            selected_img_model = available_img_models[0]

    # 兜底真实暗池模型 (真实可用的线上 UUID)
    if not selected_text_model:
        selected_text_model = {
            "id": "01a00197-e3fe-7965-9978-b4e35974daf4",
            "name": "deepseek-v4-flash-internal-test-v2",
            "displayName": "deepseek-v4-flash-internal-test-v2",
            "publicName": "deepseek-v4-flash-internal-test-v2",
            "organization": "deepseek",
            "provider": "DeepSeek",
            "userSelectable": True,
            "capabilities": {
                "inputCapabilities": {"text": True, "image": True},
                "outputCapabilities": {"text": True, "web": True}
            },
            "_timestamp": 1787680000000.0,
            "_time_str": "2026-08-15 02:45",
            "_month_str": "2026年08月",
            "_is_hidden": True,
            "_pool_type": "纯正暗池"
        }

    if not selected_img_model:
        selected_img_model = {
            "id": "019f42b5-8c52-7793-9be8-de35eecf7ea9",
            "name": "seedream-5.0-pro",
            "displayName": "seedream-5.0-pro",
            "publicName": "seedream-5.0-pro",
            "organization": "bytedance",
            "provider": "Seedream",
            "userSelectable": True,
            "capabilities": {
                "inputCapabilities": {"text": True, "image": True},
                "outputCapabilities": {"image": {"aspectRatios": ["1:1", "16:9", "9:16"]}}
            },
            "_timestamp": 1787766400000.0,
            "_time_str": "2026-07-09 01:10",
            "_month_str": "2026年07月",
            "_is_hidden": True,
            "_pool_type": "纯正暗池"
        }

    text_tree = build_modality_tree([selected_text_model], TEXT_VENDOR_DEFS, match_text_vendor)
    img_tree = build_modality_tree([selected_img_model], IMAGE_VENDOR_DEFS, match_image_vendor)

    text_stats = {
        "total": 1,
        "dark": 1 if selected_text_model.get("_is_hidden") else 0,
        "public": 0 if selected_text_model.get("_is_hidden") else 1
    }
    img_stats = {
        "total": 1,
        "dark": 1 if selected_img_model.get("_is_hidden") else 0,
        "public": 0 if selected_img_model.get("_is_hidden") else 1
    }

    return text_tree, text_stats, img_tree, img_stats

# ==============================================================================
# 9. 文件写入与安全目录辅助函数
# ==============================================================================
def ensure_parent_dir(path: Path) -> Path:
    """确保父目录存在"""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

# ==============================================================================
# 10. 主执行入口
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="Arena 全模态模型全景导出与现代化可视化仪表盘生成器")
    parser.add_argument("--demo", action="store_true", help="仅生成精简 Demo 版本 (各保留 1 个模型)")
    parser.add_argument("--offline", action="store_true", help="强制仅从本地缓存读取，不发起在线联网抓取")
    parser.add_argument("--html-out", type=str, default="", help="自定义 HTML 导出路径")
    args = parser.parse_args()

    print("=" * 70)
    print("🚀 启动 Arena 全模态模型全景导出与现代化可视化仪表盘生成器 (实时在线抓取版)")
    print("=" * 70)

    if args.demo:
        print("\n[Demo 模式] 正在构建单模型极简数据集 (纯文本 + 图像生图 各保留 1 个模型)...")
        text_tree, text_stats, img_tree, img_stats = build_demo_data()
        html_content = ArenaDashboardRenderer.render_dashboard(text_tree, text_stats, img_tree, img_stats, is_demo=True)
        
        target_path = Path(args.html_out) if args.html_out else OUTPUT_DEMO_HTML_PATH
        ensure_parent_dir(target_path)
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        print(f"\n✨ Demo 页面生成完毕: {target_path}")
        print("=" * 70)
        return

    # 全量生成模式
    print("\n[1/3] 正在获取 Arena 全量模型元数据...")
    models = fetch_arena_models(prefer_online=not args.offline)
    if not models:
        print("[Error] 无法获取模型列表，请确认网络连接或 arena_models_cache.json 是否存在！")
        sys.exit(1)

    print(f"      -> 成功加载原始模型记录：{len(models)} 个")

    print("\n[2/3] 正在根据模态与时间窗口进行多维过滤分类...")
    text_models = []
    img_models = []
    text_dark, text_pub = 0, 0
    img_dark, img_pub = 0, 0

    for m in models:
        if m.get("userSelectable") is not True:
            continue

        ts, time_str, month_str = get_model_time_info(m)
        if ts is None:
            continue

        m["_timestamp"] = ts
        m["_time_str"] = time_str
        m["_month_str"] = month_str

        is_hidden = is_hidden_from_frontend_picker(m)
        m["_is_hidden"] = is_hidden
        m["_pool_type"] = "纯正暗池" if is_hidden else "明池 (公开)"

        cutoff = FILTER_TIMESTAMP_DARK_START if is_hidden else FILTER_TIMESTAMP_PUBLIC_START
        if ts < cutoff:
            continue

        if is_strictly_text_modality(m):
            v_info = match_text_vendor(m)
            if v_info:
                text_models.append(m)
                if is_hidden:
                    text_dark += 1
                else:
                    text_pub += 1
            continue

        if is_image_modality(m):
            img_models.append(m)
            if is_hidden:
                img_dark += 1
            else:
                img_pub += 1

    text_tree = build_modality_tree(text_models, TEXT_VENDOR_DEFS, match_text_vendor)
    img_tree = build_modality_tree(img_models, IMAGE_VENDOR_DEFS, match_image_vendor)

    text_stats = {"total": len(text_models), "dark": text_dark, "public": text_pub}
    img_stats = {"total": len(img_models), "dark": img_dark, "public": img_pub}

    print("\n[3/3] 正在渲染并导出多模态 HTML 仪表盘...")
    
    # 导出完整版 HTML 仪表盘
    html_target = Path(args.html_out) if args.html_out else OUTPUT_HTML_PATH
    ensure_parent_dir(html_target)
    html_content = ArenaDashboardRenderer.render_dashboard(text_tree, text_stats, img_tree, img_stats, is_demo=False)
    with open(html_target, "w", encoding="utf-8") as f:
        f.write(html_content)

    # 同时生成一份独立的 Demo HTML 仪表盘
    ensure_parent_dir(OUTPUT_DEMO_HTML_PATH)
    demo_tree_t, demo_stats_t, demo_tree_i, demo_stats_i = build_demo_data(text_models, img_models)
    demo_html = ArenaDashboardRenderer.render_dashboard(demo_tree_t, demo_stats_t, demo_tree_i, demo_stats_i, is_demo=True)
    with open(OUTPUT_DEMO_HTML_PATH, "w", encoding="utf-8") as f:
        f.write(demo_html)

    print("\n✨ 导出工作全部圆满完成！")
    print(f"  ├── 💬 纯文本对话模型: {len(text_models)} 个 (🔒 暗池 {text_dark} / 🌐 明池 {text_pub})")
    print(f"  ├── 🎨 图像生图生成模型: {len(img_models)} 个 (🔒 暗池 {img_dark} / 🌐 明池 {img_pub})")
    print(f"  ├── 🌟 HTML 现代仪表盘 (完整版): {html_target}")
    print(f"  └── 🧪 HTML 现代仪表盘 (Demo版): {OUTPUT_DEMO_HTML_PATH}")
    print("=" * 70)

if __name__ == '__main__':
    main()
