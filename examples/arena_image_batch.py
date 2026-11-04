"""Arena 生图批量调用的最小示例。

这个脚本**不是服务的一部分**，只是演示「用本项目暴露的 OpenAI 兼容接口批量生图」。
所有路径与地址都从命令行参数或环境变量读取，不包含任何机器相关的硬编码路径。

前置条件
--------
1. 服务已启动（``uv run python main.py``），受控浏览器里 Arena 已登录；
2. 目标路由组/预设可用（默认示例用 ``/group/arena-image/``，可按需改成
   ``/url/arena.ai/v1/chat/completions`` 或其它路由）。

用法
----
    # 只带提示词
    python examples/arena_image_batch.py --output-dir ./arena-images

    # 带角色参考图（--both 再额外带风格参考图）
    python examples/arena_image_batch.py --output-dir ./arena-images \
        --prompt-file ./prompt.txt --character-image ./ref_character.jpg \
        --style-image ./ref_style.jpg --both

    # 换地址 / 模型
    python examples/arena_image_batch.py --base-url http://127.0.0.1:8199 \
        --path /url/arena.ai/v1/chat/completions --output-dir ./out --model arena-image

产物：``<output-dir>/result_<ts>.json`` 与 ``<output-dir>/generated_<ts>_<n>.jpg``。
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from pathlib import Path

import requests

DEFAULT_PROMPT = "生成一张二次元精美动漫壁纸插画"
DEFAULT_PATH = "/group/arena-image/v1/chat/completions"


def build_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + path


def load_prompt(prompt_file: Path) -> str:
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8").strip()
    return DEFAULT_PROMPT


def get_image_base64(image_path: Path) -> str:
    if image_path and image_path.exists():
        return base64.b64encode(image_path.read_bytes()).decode("utf-8")
    return ""


def save_image_from_data_url(data_url: str, output_path: Path) -> bool:
    match = re.match(r"data:image/(\w+);base64,(.+)", data_url)
    if not match:
        return False
    _, b64data = match.groups()
    output_path.write_bytes(base64.b64decode(b64data))
    print(f"[+] 成功保存 Base64 图片: {output_path}")
    return True


def save_image_from_url(url: str, output_path: Path, headers: dict | None = None) -> bool:
    try:
        response = requests.get(url, timeout=30, headers=headers)
        if response.status_code == 200:
            output_path.write_bytes(response.content)
            print(f"[+] 成功下载远程图片: {output_path}")
            return True
    except (requests.RequestException, OSError) as exc:
        print(f"[-] 下载图片失败 {url}: {exc}")
    return False


def _text_part(prompt: str) -> dict:
    return {"type": "text", "text": prompt}


def _image_part(image_path: Path) -> dict | None:
    b64 = get_image_base64(image_path)
    if not b64:
        return None
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def generate(
    api_url: str,
    model: str,
    output_dir: Path,
    prompt_file: Path,
    character_image: Path | None = None,
    style_image: Path | None = None,
    attach_character: bool = True,
    attach_style: bool = False,
    headers: dict | None = None,
) -> list[str] | None:
    output_dir.mkdir(parents=True, exist_ok=True)

    prompt = load_prompt(prompt_file)
    print("=" * 60)
    print("【当前生图提示词】:")
    print(prompt)
    print("=" * 60)

    content_list: list[dict] = [_text_part(prompt)]

    if attach_character and character_image and character_image.exists():
        print(f"[*] 正在附加角色参考图: {character_image}")
        part = _image_part(character_image)
        if part:
            content_list.append(part)
    elif attach_character:
        print("[-] 警告：未找到角色参考图，仅发送提示词")

    if attach_style and style_image and style_image.exists():
        print(f"[*] 正在附加风格参考图: {style_image}")
        part = _image_part(style_image)
        if part:
            content_list.append(part)

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content_list}],
        "stream": False,
    }

    print(f"[*] 正在向 {api_url} 发送请求 (包含 {len(content_list) - 1} 张参考图)...")
    started = time.time()
    try:
        response = requests.post(api_url, json=payload, timeout=300, headers=headers)
        print(f"[*] 请求响应状态码: {response.status_code} (耗时: {time.time() - started:.2f}s)")

        try:
            result = response.json()
        except ValueError:
            print(f"[-] 响应非 JSON: {response.text}")
            return None

        timestamp = int(time.time())
        json_path = output_dir / f"result_{timestamp}.json"
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[+] 已保存响应结果到: {json_path}")

        images_found: list[str] = []
        media_list: list = list(result.get("media") or [])
        for choice in result.get("choices", []):
            media_list.extend((choice.get("message") or {}).get("media") or [])

        for idx, media_item in enumerate(media_list):
            if isinstance(media_item, str):
                url = media_item
            elif isinstance(media_item, dict):
                url = media_item.get("url") or media_item.get("src") or ""
            else:
                continue

            img_file = output_dir / f"generated_{timestamp}_{idx}.jpg"
            if url.startswith("data:image/"):
                if save_image_from_data_url(url, img_file):
                    images_found.append(str(img_file))
            elif url.startswith(("http://", "https://")):
                if save_image_from_url(url, img_file, headers):
                    images_found.append(str(img_file))

        for choice in result.get("choices", []):
            content = (choice.get("message") or {}).get("content") or ""
            if not content:
                continue
            print(f"[文本回复]: {content}")
            for idx, url in enumerate(re.findall(r"!\[.*?\]\((.+?)\)", content)):
                img_file = output_dir / f"generated_{timestamp}_md_{idx}.jpg"
                if url.startswith("data:image/"):
                    if save_image_from_data_url(url, img_file):
                        images_found.append(str(img_file))
                elif url.startswith(("http://", "https://")):
                    if save_image_from_url(url, img_file, headers):
                        images_found.append(str(img_file))

        if not images_found:
            print("[-] 未在响应中解析到图片，请查看 JSON 响应。")
        else:
            print(f"[✓] 共保存 {len(images_found)} 张生成图片:")
            for path in images_found:
                print(f"    -> {path}")
        return images_found

    except (requests.RequestException, ValueError, OSError) as exc:
        print(f"[-] 请求发生异常: {exc}")
        return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Arena 生图批量调用的最小示例")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8199",
        help="服务地址（默认 http://127.0.0.1:8199）",
    )
    parser.add_argument("--path", default=DEFAULT_PATH, help="请求路径（默认路由组 /group/arena-image）")
    parser.add_argument("--model", default="arena-image", help="模型名或预设名")
    parser.add_argument("--output-dir", required=True, type=Path, help="图片与 JSON 的输出目录")
    parser.add_argument("--prompt-file", type=Path, default=Path("prompt.txt"), help="提示词文件")
    parser.add_argument("--character-image", type=Path, default=None, help="角色参考图")
    parser.add_argument("--style-image", type=Path, default=None, help="风格/构图参考图")
    parser.add_argument("--both", action="store_true", help="同时附加风格参考图")
    parser.add_argument("--api-key", default="", help="启用认证时的 Bearer 令牌")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else None

    result = generate(
        api_url=build_url(args.base_url, args.path),
        model=args.model,
        output_dir=args.output_dir,
        prompt_file=args.prompt_file,
        character_image=args.character_image,
        style_image=args.style_image,
        attach_character=True,
        attach_style=args.both,
        headers=headers,
    )
    return 0 if result is not None else 1


if __name__ == "__main__":
    sys.exit(main())
