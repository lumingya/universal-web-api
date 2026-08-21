import os
import re
import sys
import time
import json
import base64
import shutil
import requests
from pathlib import Path

API_URL = "http://127.0.0.1:8199/group/arena-image/v1/chat/completions"
OUTPUT_DIR = Path(r"C:\Users\QIU\Desktop\新建文件夹 (2)")
PROMPT_FILE = Path(__file__).resolve().parent / "prompt.txt"

# 用户上传的图一（风格/构图参考）与图二（角色参考）
IMAGE1_PATH = Path(r"C:\Users\QIU\.gemini\antigravity\brain\13158e24-022d-4ee8-8b23-615a93ee4a28\.user_uploaded\media_1787220848527.jpg")
IMAGE2_PATH = Path(r"C:\Users\QIU\.gemini\antigravity\brain\13158e24-022d-4ee8-8b23-615a93ee4a28\.user_uploaded\media_1787220871854.jpg")
DESKTOP_111_PATH = OUTPUT_DIR / "111.jpg"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 如果桌面没有 111.jpg，将图二复制一份过去以确保路径可用
if IMAGE2_PATH.exists() and not DESKTOP_111_PATH.exists():
    shutil.copy2(IMAGE2_PATH, DESKTOP_111_PATH)
    print(f"[+] 已同步参考图二到: {DESKTOP_111_PATH}")

def load_prompt():
    if PROMPT_FILE.exists():
        with open(PROMPT_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return "生成一张二次元精美动漫壁纸插画"

def get_image_base64(image_path: Path) -> str:
    if image_path.exists():
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    return ""

def save_image_from_data_url(data_url: str, output_path: Path):
    match = re.match(r"data:image/(\w+);base64,(.+)", data_url)
    if match:
        ext, b64data = match.groups()
        with open(output_path, "wb") as f:
            f.write(base64.b64decode(b64data))
        print(f"[+] 成功保存 Base64 图片: {output_path}")
        return True
    return False

def save_image_from_url(url: str, output_path: Path):
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(r.content)
            print(f"[+] 成功下载远程图片: {output_path}")
            return True
    except Exception as e:
        print(f"[-] 下载图片失败 {url}: {e}")
    return False

def generate(attach_character=True, attach_style=False):
    prompt = load_prompt()
    print("=" * 60)
    print("【当前生图提示词】:")
    print(prompt)
    print("=" * 60)

    content_list = [{"type": "text", "text": prompt}]

    # 上传图二作为核心角色参考图
    if attach_character:
        ref_char_path = DESKTOP_111_PATH if DESKTOP_111_PATH.exists() else IMAGE2_PATH
        if ref_char_path.exists():
            print(f"[*] 必须上传：正在附加角色参考图（图二）：{ref_char_path}")
            b64_str2 = get_image_base64(ref_char_path)
            content_list.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64_str2}"}
            })
        else:
            print("[-] 警告：未找到角色参考图！")

    # 如果需要附加图一（风格构图参考）
    if attach_style and IMAGE1_PATH.exists():
        print(f"[*] 正在附加排版风格参考图（图一）：{IMAGE1_PATH}")
        b64_str1 = get_image_base64(IMAGE1_PATH)
        content_list.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64_str1}"}
        })

    messages = [{"role": "user", "content": content_list}]

    payload = {
        "model": "arena-image",
        "messages": messages,
        "stream": False
    }

    print(f"[*] 正在向 {API_URL} 发送请求 (包含 {len(content_list)-1} 张参考图)...")
    start_time = time.time()
    try:
        resp = requests.post(API_URL, json=payload, timeout=300)
        elapsed = time.time() - start_time
        print(f"[*] 请求响应状态码: {resp.status_code} (耗时: {elapsed:.2f}s)")
        
        try:
            result = resp.json()
        except Exception:
            print(f"[-] 响应非 JSON: {resp.text}")
            return None

        # 保存原始 JSON
        timestamp = int(time.time())
        json_path = OUTPUT_DIR / f"result_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[+] 已保存响应结果到: {json_path}")

        # 解析图片
        images_found = []

        # 检查 media 字段
        media_list = result.get("media", []) or []
        for choice in result.get("choices", []):
            msg = choice.get("message", {})
            media_list.extend(msg.get("media", []) or [])

        for idx, media_item in enumerate(media_list):
            if isinstance(media_item, str):
                url = media_item
            elif isinstance(media_item, dict):
                url = media_item.get("url") or media_item.get("src") or ""
            else:
                continue

            img_file = OUTPUT_DIR / f"generated_{timestamp}_{idx}.jpg"
            if url.startswith("data:image/"):
                if save_image_from_data_url(url, img_file):
                    images_found.append(str(img_file))
            elif url.startswith("http://") or url.startswith("https://"):
                if save_image_from_url(url, img_file):
                    images_found.append(str(img_file))

        # 检查 content 中的 markdown 或 base64
        for choice in result.get("choices", []):
            content = (choice.get("message") or {}).get("content") or ""
            if content:
                print(f"[文本回复]: {content}")
                md_urls = re.findall(r"!\[.*?\]\((.+?)\)", content)
                for idx, u in enumerate(md_urls):
                    img_file = OUTPUT_DIR / f"generated_{timestamp}_md_{idx}.jpg"
                    if u.startswith("data:image/"):
                        if save_image_from_data_url(u, img_file):
                            images_found.append(str(img_file))
                    elif u.startswith("http://") or u.startswith("https://"):
                        if save_image_from_url(u, img_file):
                            images_found.append(str(img_file))

        if not images_found:
            print("[-] 未在响应中解析到图片，请查看 JSON 响应。")
        else:
            print(f"[✓] 共保存 {len(images_found)} 张生成图片:")
            for p in images_found:
                print(f"    -> {p}")

        return images_found

    except Exception as e:
        print(f"[-] 请求发生异常: {e}")
        return None

if __name__ == "__main__":
    attach_both = "--both" in sys.argv
    generate(attach_character=True, attach_style=attach_both)
