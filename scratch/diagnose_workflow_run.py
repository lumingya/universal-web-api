# scratch/diagnose_workflow_run.py
import asyncio
import json
import time
import random
import os
import uuid
import sys
from pathlib import Path

# Add project root to sys.path
project_root = Path(r"c:\Users\QIU\Desktop\useful\projects\普遍反代\新测试版")
sys.path.insert(0, str(project_root))

from app.core.config import logger
from app.core.browser.main import BrowserCore
from app.services.config_engine import config_engine

def uuid7() -> str:
    """Generate a UUIDv7 string (RFC 9562)."""
    ms = int(time.time() * 1000)
    rand_bytes = os.urandom(10)
    b = bytearray(16)
    b[0:6] = ms.to_bytes(6, byteorder='big')
    b[6:16] = rand_bytes
    b[6] = (b[6] & 0x0F) | 0x70  # Version 7
    b[8] = (b[8] & 0x3F) | 0x80  # Variant 1
    return str(uuid.UUID(bytes=bytes(b)))

PROMPTS = [
    "写一首关于秋天金黄落叶的四句短诗",
    "请用两句话解释什么是量子纠缠",
    "给我推荐三种适合夏天的清爽水果并说明理由",
    "用轻松幽默的语气讲一个程序员的小故事",
    "请用简明扼要的一段话介绍光合作用的原理"
]

async def run_diagnostics(test_model="kimi-k2.5-thinking", prompt=None):
    if not prompt:
        prompt = f"{random.choice(PROMPTS)} (编号: {random.randint(1000, 9999)})"
        
    print(f"=== [DIAGNOSTIC START] Testing model: {test_model} ===")
    print(f"[+] Unique Prompt: {prompt}")
    
    browser_core = BrowserCore()
    # Acquire active tab or connect to browser
    async with browser_core.tab_pool.get_tab(task_id="diag_task_1") as tab_session:
        if not tab_session:
            print("[-] Failed to acquire tab session")
            return
            
        tab = tab_session.tab
        print(f"[+] Acquired Tab: {tab_session.id}, Current URL: {tab.url}")
        
        # 1. 挂载浏览器端探针，监听 window.__ARENA_INTERCEPTOR_CONFIG__ 和 fetch
        probe_script = """
        (function() {
            if (!window.__WORKFLOW_TELEMETRY__) {
                window.__WORKFLOW_TELEMETRY__ = {
                    logs: [],
                    requests: [],
                    stepTimestamps: {}
                };
            }
            
            if (!window.__FETCH_TELEMETRY_INSTALLED__) {
                window.__FETCH_TELEMETRY_INSTALLED__ = true;
                const originalFetch = window.fetch;
                window.fetch = async function(resource, init) {
                    const urlStr = String(typeof resource === 'string' ? resource : ((resource && resource.url) || resource));
                    const record = {
                        timestamp: Date.now(),
                        url: urlStr,
                        method: (init && init.method) || 'GET',
                        bodyBefore: init && init.body ? init.body : null,
                        interceptorConfig: window.__ARENA_INTERCEPTOR_CONFIG__ || null,
                        interceptorInstalled: Boolean(window.__ARENA_PAYLOAD_INTERCEPTOR_INSTALLED__)
                    };
                    window.__WORKFLOW_TELEMETRY__.requests.push(record);
                    return originalFetch.apply(this, arguments);
                };
            }
        })();
        """
        try:
            tab.run_js(probe_script)
            # Register for all subsequent navigations via CDP
            try:
                tab.run_cdp("Page.addScriptToEvaluateOnNewDocument", {"source": probe_script})
            except Exception:
                pass
            print("[+] Diagnostic telemetry probe attached (including Page.addScriptToEvaluateOnNewDocument)")
        except Exception as e:
            print(f"[-] Failed to attach initial probe (will proceed): {e}")

        print("[+] Executing browser_core._execute_workflow_stream_once...")
        start_t = time.time()
        
        chunk_count = 0
        first_chunk_t = None
        accumulated_chunks = []
        
        try:
            messages = [{"role": "user", "content": prompt}]
            for raw_chunk in browser_core._execute_workflow_stream_once(
                session=tab_session,
                messages=messages,
                preset_name="万能直连-通用文本",
                requested_model=test_model
            ):
                chunk_count += 1
                if first_chunk_t is None:
                    first_chunk_t = time.time() - start_t
                    print(f"[+] First stream chunk received in {first_chunk_t:.2f}s: {raw_chunk[:120]!r}")
                accumulated_chunks.append(raw_chunk)
                
            total_time = time.time() - start_t
            print(f"[+] Workflow completed in {total_time:.2f}s, total chunks: {chunk_count}")
            print(f"[+] Total raw output len: {len(''.join(accumulated_chunks))}")
            if accumulated_chunks:
                print(f"[+] Output Preview:\n{''.join(accumulated_chunks)[:600]}")
            
        except Exception as e:
            print(f"[-] Workflow execution raised exception: {e}")
            import traceback
            traceback.print_exc()
            
        # Safely read telemetry collected on browser
        telemetry = {}
        try:
            telemetry = tab.run_js("return window.__WORKFLOW_TELEMETRY__ || {};") or {}
        except Exception as e:
            print(f"[-] Note: Could not read live telemetry after workflow finish: {e}")
            
        print("\n=== [TELEMETRY REPORT] ===")
        print(f"Captured requests count: {len(telemetry.get('requests', []))}")
        for idx, req in enumerate(telemetry.get("requests", [])):
            if "/nextjs-api/stream/" in str(req.get('url', '')):
                print(f"\n--- Stream Request ---")
                print(f"URL: {req.get('url')}")
                print(f"Method: {req.get('method')}")
                print(f"Interceptor Installed: {req.get('interceptorInstalled')}")
                print(f"Interceptor Config: {req.get('interceptorConfig')}")
                body_sample = str(req.get('bodyBefore', ''))[:300]
                print(f"Body: {body_sample}")

if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "kimi-k2.5-thinking"
    custom_prompt = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(run_diagnostics(model, custom_prompt))
