#!/usr/bin/env python
"""
End-to-end Gemini media generation test.

What it checks:
1. Send a generation prompt to the local OpenAI-compatible endpoint.
2. Wait for the final non-stream response.
3. Verify structured `media` exists in the response.
4. Verify returned media URLs are directly reachable by other frontends.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urljoin

import requests


DEFAULT_BASE_URL = "http://127.0.0.1:8199"
DEFAULT_ROUTE_DOMAIN = "gemini.com"
DEFAULT_MODEL = "gemini.google.com"
DEFAULT_TIMEOUT = 900
DEFAULT_VIDEO_PROMPT = (
    "Create a short cinematic video of a giant hero fighting a transforming robot "
    "in a city at sunset. Keep it dynamic and visually clear."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test Gemini media generation and structured media output."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base URL.")
    parser.add_argument(
        "--route-domain",
        default=DEFAULT_ROUTE_DOMAIN,
        help="Domain route used by the API, for example gemini.com.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model field in request JSON.")
    parser.add_argument(
        "--prompt",
        default=DEFAULT_VIDEO_PROMPT,
        help="Prompt sent to Gemini for media generation.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="Request timeout in seconds.",
    )
    parser.add_argument(
        "--token",
        default="",
        help="Optional bearer token. Leave empty if auth is disabled.",
    )
    parser.add_argument(
        "--expect-type",
        default="video",
        choices=["image", "audio", "video", "any"],
        help="Expected media type in structured response.",
    )
    parser.add_argument(
        "--output",
        default="output/tests/gemini_media_test_result.json",
        help="Where to save the raw JSON response.",
    )
    return parser.parse_args()


def build_headers(token: str) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
    }
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


def extract_media_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    media = payload.get("media")
    if isinstance(media, list) and media:
        return media

    try:
        message = payload["choices"][0]["message"]
    except Exception:
        return []

    message_media = message.get("media")
    if isinstance(message_media, list):
        return message_media
    return []


def fetch_media_url(base_url: str, media_item: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    raw_url = str(media_item.get("url") or "").strip()
    if not raw_url:
        return {
            "ok": False,
            "reason": "missing_url",
            "url": raw_url,
        }

    target_url = raw_url if raw_url.startswith(("http://", "https://")) else urljoin(base_url.rstrip("/") + "/", raw_url.lstrip("/"))

    response = requests.get(target_url, stream=True, timeout=timeout)
    try:
        chunk = next(response.iter_content(chunk_size=8192), b"")
        return {
            "ok": response.status_code == 200 and len(chunk) >= 0,
            "status_code": response.status_code,
            "content_type": response.headers.get("Content-Type", ""),
            "content_length": response.headers.get("Content-Length", ""),
            "sample_bytes": len(chunk),
            "url": target_url,
        }
    finally:
        response.close()


def save_json(path_str: str, payload: Dict[str, Any]) -> Path:
    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    args = parse_args()
    endpoint = (
        f"{args.base_url.rstrip('/')}/url/{args.route_domain}/v1/chat/completions"
    )

    payload = {
        "model": args.model,
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": args.prompt,
            }
        ],
    }

    print(f"[1/4] POST {endpoint}")
    print(f"[info] prompt: {args.prompt}")
    started = time.time()
    response = requests.post(
        endpoint,
        headers=build_headers(args.token),
        json=payload,
        timeout=args.timeout,
    )
    elapsed = time.time() - started
    print(f"[info] HTTP {response.status_code} in {elapsed:.1f}s")

    try:
        data = response.json()
    except Exception as exc:
        print(f"[error] Response is not JSON: {exc}", file=sys.stderr)
        print(response.text[:1000], file=sys.stderr)
        return 1

    output_path = save_json(args.output, data)
    print(f"[2/4] saved raw response to {output_path}")

    if response.status_code != 200:
        print("[error] API returned non-200 response.", file=sys.stderr)
        print(json.dumps(data, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1

    media_items = extract_media_items(data)
    if not media_items:
        print("[error] No structured media found in response.", file=sys.stderr)
        return 1

    print(f"[3/4] structured media items: {len(media_items)}")
    for index, item in enumerate(media_items, start=1):
        media_type = item.get("media_type", "unknown")
        mime = item.get("mime", "")
        url = item.get("url", "")
        print(f"  - #{index} type={media_type} mime={mime} url={url}")

    if args.expect_type != "any":
        matched = any(str(item.get("media_type") or "").lower() == args.expect_type for item in media_items)
        if not matched:
            print(
                f"[error] Expected media_type={args.expect_type!r} but got "
                f"{[item.get('media_type') for item in media_items]}",
                file=sys.stderr,
            )
            return 1

    print("[4/4] verifying media URLs are reachable")
    failures = []
    for item in media_items:
        if str(item.get("kind") or "") != "url":
            continue
        check = fetch_media_url(args.base_url, item, min(args.timeout, 120))
        if check["ok"]:
            print(
                f"  - ok {item.get('media_type')} {check['status_code']} "
                f"{check['content_type']} {check['url']}"
            )
        else:
            failures.append(check)
            print(f"  - fail {item.get('media_type')} {check}", file=sys.stderr)

    if failures:
        print("[error] Some media URLs are not directly reachable.", file=sys.stderr)
        return 1

    print("[ok] Gemini media generation test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
