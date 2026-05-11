"""
app/core/request_transport.py - 预设级请求发送方式与页面直发 profile

职责：
- 定义 request_transport 配置默认值与 profile 元数据
- 规范化 request_transport 配置
- 执行页面内 page_fetch profile
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional

from app.core.config import logger


REQUEST_TRANSPORT_MODE_WORKFLOW = "workflow"
REQUEST_TRANSPORT_MODE_PAGE_FETCH = "page_fetch"


def get_default_request_transport_config() -> Dict[str, Any]:
    return {
        "mode": REQUEST_TRANSPORT_MODE_WORKFLOW,
        "profile": "",
        "options": {},
    }


def get_request_transport_profiles() -> List[Dict[str, Any]]:
    return [
        {
            "id": "deepseek_completion",
            "name": "DeepSeek 直发",
            "description": "在当前 DeepSeek 页面中直接新建会话并发起 completion 请求。",
            "transport_mode": REQUEST_TRANSPORT_MODE_PAGE_FETCH,
            "supported_domains": ["chat.deepseek.com"],
            "supported_parsers": ["deepseek"],
            "supports_images": False,
            "supports_file_inputs": False,
            "options": [
                {
                    "key": "model_type",
                    "label": "模型类型",
                    "type": "enum",
                    "default": "auto",
                    "description": "auto 跟随 profile 配置或页面状态，也可固定为 default / expert / vision。",
                    "choices": [
                        {"value": "auto", "label": "自动"},
                        {"value": "default", "label": "快速模式"},
                        {"value": "expert", "label": "专家模式"},
                        {"value": "vision", "label": "识图模式"},
                    ],
                },
                {
                    "key": "context_mode",
                    "label": "上下文模式",
                    "type": "enum",
                    "default": "full_prompt",
                    "description": "v1 固定使用主链已构造好的完整 prompt 字符串。",
                    "choices": [
                        {"value": "full_prompt", "label": "完整上下文 prompt"},
                    ],
                },
                {
                    "key": "search_enabled",
                    "label": "联网搜索",
                    "type": "enum",
                    "default": "auto",
                    "description": "auto 跟随页面当前开关，也可强制开/关。",
                    "choices": [
                        {"value": "auto", "label": "自动"},
                        {"value": "on", "label": "开启"},
                        {"value": "off", "label": "关闭"},
                    ],
                },
                {
                    "key": "thinking_enabled",
                    "label": "深度思考",
                    "type": "enum",
                    "default": "auto",
                    "description": "auto 跟随页面当前开关，也可强制开/关。",
                    "choices": [
                        {"value": "auto", "label": "自动"},
                        {"value": "on", "label": "开启"},
                        {"value": "off", "label": "关闭"},
                    ],
                },
                {
                    "key": "fallback_mode",
                    "label": "失败回退",
                    "type": "enum",
                    "default": "workflow",
                    "description": "workflow 表示直发失败时回退原工作流。",
                    "choices": [
                        {"value": "workflow", "label": "回退工作流"},
                        {"value": "error", "label": "直接报错"},
                    ],
                },
                {
                    "key": "client_version",
                    "label": "客户端版本",
                    "type": "string",
                    "default": "2.0.0",
                    "description": "附加到 x-client-version 请求头。",
                },
                {
                    "key": "app_version",
                    "label": "应用版本",
                    "type": "string",
                    "default": "2.0.0",
                    "description": "附加到 x-app-version 请求头。",
                },
            ],
        }
    ]


def _get_request_transport_profile_map() -> Dict[str, Dict[str, Any]]:
    return {
        str(profile.get("id") or "").strip(): profile
        for profile in get_request_transport_profiles()
        if str(profile.get("id") or "").strip()
    }


def get_request_transport_profile(profile_id: Any) -> Optional[Dict[str, Any]]:
    pid = str(profile_id or "").strip()
    profile = _get_request_transport_profile_map().get(pid)
    return copy.deepcopy(profile) if profile else None


def _coerce_transport_mode(value: Any) -> str:
    mode = str(value or REQUEST_TRANSPORT_MODE_WORKFLOW).strip().lower()
    if mode in {REQUEST_TRANSPORT_MODE_WORKFLOW, REQUEST_TRANSPORT_MODE_PAGE_FETCH}:
        return mode
    return REQUEST_TRANSPORT_MODE_WORKFLOW


def normalize_request_transport_config(raw_config: Any) -> Dict[str, Any]:
    result = get_default_request_transport_config()
    if not isinstance(raw_config, dict):
        return result

    result["mode"] = _coerce_transport_mode(raw_config.get("mode"))
    result["profile"] = str(raw_config.get("profile") or "").strip()

    profile = get_request_transport_profile(result["profile"])
    raw_options = raw_config.get("options")
    normalized_options: Dict[str, Any] = {}
    if isinstance(raw_options, dict):
        normalized_options = copy.deepcopy(raw_options)

    if profile:
        option_defs = profile.get("options") or []
        next_options: Dict[str, Any] = {}
        for option_def in option_defs:
            key = str(option_def.get("key") or "").strip()
            if not key:
                continue
            default_value = copy.deepcopy(option_def.get("default"))
            option_type = str(option_def.get("type") or "").strip().lower()
            value = normalized_options.get(key, default_value)
            if option_type == "enum":
                allowed_values = {
                    str(choice.get("value") or "").strip()
                    for choice in (option_def.get("choices") or [])
                    if str(choice.get("value") or "").strip()
                }
                value_text = str(value or "").strip()
                if value_text not in allowed_values:
                    value = default_value
                else:
                    value = value_text
            elif option_type == "string":
                value = str(value or "").strip() or str(default_value or "").strip()
            next_options[key] = value
        result["options"] = next_options
    else:
        result["options"] = normalized_options

    return result


def get_request_transport_defaults_payload() -> Dict[str, Any]:
    return {
        "defaults": get_default_request_transport_config(),
        "mode_options": [
            REQUEST_TRANSPORT_MODE_WORKFLOW,
            REQUEST_TRANSPORT_MODE_PAGE_FETCH,
        ],
        "profiles": get_request_transport_profiles(),
    }


def _build_deepseek_completion_script(
    prompt: str,
    options: Dict[str, Any],
    *,
    consume_response: bool,
) -> str:
    explicit_model_type = str(options.get("model_type") or "").strip().lower()
    if explicit_model_type not in {"", "auto", "default", "expert", "vision"}:
        explicit_model_type = ""

    context_mode = str(options.get("context_mode") or "full_prompt").strip().lower() or "full_prompt"
    if context_mode != "full_prompt":
        context_mode = "full_prompt"

    search_enabled = str(options.get("search_enabled") or "auto").strip().lower() or "auto"
    thinking_enabled = str(options.get("thinking_enabled") or "auto").strip().lower() or "auto"
    client_version = str(options.get("client_version") or "2.0.0").strip() or "2.0.0"
    app_version = str(options.get("app_version") or client_version).strip() or client_version

    script = f"""
    return (async () => {{
        const prompt = {json.dumps(prompt, ensure_ascii=False)};
        const preferredModelType = {json.dumps(explicit_model_type)};
        const contextMode = {json.dumps(context_mode)};
        const searchEnabledMode = {json.dumps(search_enabled)};
        const thinkingEnabledMode = {json.dumps(thinking_enabled)};
        const clientVersion = {json.dumps(client_version)};
        const appVersion = {json.dumps(app_version)};
        const consumeResponse = {str(bool(consume_response)).lower()};

        const chunk = window.webpackChunk_deepseek_chat;
        if (!chunk || typeof chunk.push !== 'function') {{
            return {{ ok: false, error: 'deepseek_webpack_missing' }};
        }}

        let __req = null;
        chunk.push([[Symbol('request_transport_deepseek_completion')], {{}}, (req) => {{ __req = req; }}]);
        if (!__req) {{
            return {{ ok: false, error: 'deepseek_require_missing' }};
        }}

        const app = __req('86389').y;
        const createPowManager = __req('87109').Qc;
        const buildPowHeader = __req('42587').Bx;
        const base64Encode = (text) => btoa(unescape(encodeURIComponent(text)));
        const tracker = {{
            info() {{}},
            error() {{}},
            withError: (error, payload) => ({{
                ...(payload || {{}}),
                error: String(error && error.message ? error.message : error),
            }}),
        }};

        if (contextMode !== 'full_prompt') {{
            return {{ ok: false, error: 'deepseek_context_mode_unsupported' }};
        }}

        const readStoredBool = (key, fallbackValue) => {{
            try {{
                const raw = localStorage.getItem(key);
                if (!raw) return fallbackValue;
                const parsed = JSON.parse(raw);
                if (typeof parsed === 'boolean') return parsed;
                if (parsed && typeof parsed === 'object' && 'value' in parsed) {{
                    return !!parsed.value;
                }}
                return !!parsed;
            }} catch (error) {{
                return fallbackValue;
            }}
        }};

        const resolveToggleMode = (mode, storageKey, fallbackValue) => {{
            const text = String(mode || '').trim().toLowerCase();
            if (text === 'on' || text === 'true' || text === '1') return true;
            if (text === 'off' || text === 'false' || text === '0') return false;
            return readStoredBool(storageKey, fallbackValue);
        }};

        const getToken = () => {{
            try {{
                if (app && typeof app.getStorageUserToken === 'function') {{
                    const tokenValue = app.getStorageUserToken();
                    if (typeof tokenValue === 'string' && tokenValue) return tokenValue;
                    if (tokenValue && typeof tokenValue === 'object' && tokenValue.value) {{
                        return String(tokenValue.value);
                    }}
                }}
            }} catch (error) {{}}

            try {{
                const raw = localStorage.getItem('userToken');
                if (!raw) return '';
                const parsed = JSON.parse(raw);
                if (typeof parsed === 'string' && parsed) return parsed;
                if (parsed && typeof parsed === 'object' && parsed.value) {{
                    return String(parsed.value);
                }}
            }} catch (error) {{}}

            return '';
        }};

        const getLocale = () => {{
            try {{
                if (app && typeof app.getLocale === 'function') {{
                    const locale = app.getLocale();
                    if (locale) return String(locale).replace('-', '_');
                }}
            }} catch (error) {{}}
            return String(document.documentElement.lang || navigator.language || 'zh-CN').replace('-', '_');
        }};

        const inferModelType = (rawValue) => {{
            const text = String(rawValue || '').trim().toLowerCase();
            if (!text) return '';
            if (text.includes('expert') || text.includes('专家')) return 'expert';
            if (text.includes('vision') || text.includes('识图')) return 'vision';
            if (text.includes('default') || text.includes('快速')) return 'default';
            return '';
        }};

        const getSelectedModelType = () => {{
            const radios = Array.from(document.querySelectorAll('[role="radio"][aria-checked="true"], input[type="radio"]:checked'));
            for (const radio of radios) {{
                const samples = [
                    radio.innerText,
                    radio.textContent,
                    radio.getAttribute && radio.getAttribute('aria-label'),
                    radio.value,
                    radio.parentElement && radio.parentElement.innerText,
                ];
                for (const sample of samples) {{
                    const parsed = inferModelType(sample);
                    if (parsed) return parsed;
                }}
            }}
            return inferModelType(preferredModelType) || 'default';
        }};

        const token = getToken();
        if (!token) {{
            return {{ ok: false, error: 'deepseek_user_token_missing' }};
        }}

        const baseHeaders = {{
            authorization: `Bearer ${{token}}`,
            'x-client-locale': getLocale(),
            'x-client-timezone-offset': String(-new Date().getTimezoneOffset() * 60),
            'x-client-platform': 'web',
            'x-client-version': clientVersion,
            'x-app-version': appVersion,
            'content-type': 'application/json',
        }};

        let latestChallenge = null;
        const powManager = createPowManager({{
            getTracker: () => tracker,
            getChallengeByScene: async (scene) => {{
                const targetPath = scene === 'upload_file'
                    ? '/api/v0/file/upload_file'
                    : '/api/v0/chat/completion';
                const challengeResp = await fetch('/api/v0/chat/create_pow_challenge', {{
                    method: 'POST',
                    headers: baseHeaders,
                    credentials: 'include',
                    body: JSON.stringify({{ target_path: targetPath }}),
                }});
                const challengeJson = await challengeResp.json();
                const challengeData = challengeJson && challengeJson.data && challengeJson.data.biz_data
                    ? challengeJson.data.biz_data
                    : null;
                const challenge = challengeData ? challengeData.challenge : null;
                if (!challenge) {{
                    return Promise.reject(new Error('deepseek_pow_challenge_missing'));
                }}
                latestChallenge = {{
                    ...challenge,
                    expireAt: challenge.expire_at,
                    expireAfter: challenge.expire_after,
                    targetPath,
                }};
                return latestChallenge;
            }},
        }});

        const createSessionResp = await fetch('/api/v0/chat_session/create', {{
            method: 'POST',
            headers: baseHeaders,
            credentials: 'include',
            body: '{{}}',
        }});
        const createSessionText = await createSessionResp.text();
        let createSessionJson = null;
        try {{
            createSessionJson = JSON.parse(createSessionText);
        }} catch (error) {{}}
        const bizData = createSessionJson && createSessionJson.data ? createSessionJson.data.biz_data || {{}} : {{}};
        const sessionId = (bizData.chat_session && bizData.chat_session.id) || bizData.id || '';
        if (!sessionId) {{
            return {{
                ok: false,
                error: 'deepseek_create_session_failed',
                status: createSessionResp.status,
                responsePreview: createSessionText.slice(0, 1200),
            }};
        }}

        const powAnswer = await powManager.retrieveAnswer('completion_like');
        if (!powAnswer || !powAnswer.res || !latestChallenge) {{
            return {{ ok: false, error: 'deepseek_pow_answer_missing', sessionId }};
        }}

        const [powHeaderName, powHeaderValue] = buildPowHeader(
            {{ ...latestChallenge, ...powAnswer.res }},
            '/api/v0/chat/completion',
            base64Encode,
        );

        const resolvedModelType = preferredModelType && preferredModelType !== 'auto'
            ? preferredModelType
            : getSelectedModelType();
        const searchEnabled = resolveToggleMode(searchEnabledMode, 'searchEnabled', true);
        const thinkingEnabled = resolveToggleMode(thinkingEnabledMode, 'thinkingEnabled', false);

        const completionResp = await fetch('/api/v0/chat/completion', {{
            method: 'POST',
            headers: {{
                ...baseHeaders,
                [powHeaderName]: powHeaderValue,
            }},
            credentials: 'include',
            body: JSON.stringify({{
                chat_session_id: sessionId,
                parent_message_id: null,
                model_type: resolvedModelType,
                prompt,
                ref_file_ids: [],
                thinking_enabled: thinkingEnabled,
                search_enabled: searchEnabled,
                preempt: false,
            }}),
        }});

        const contentType = completionResp.headers.get('content-type') || '';
        if (!completionResp.ok) {{
            let errorText = '';
            try {{
                errorText = await completionResp.text();
            }} catch (error) {{}}
            return {{
                ok: false,
                error: 'deepseek_completion_http_error',
                status: completionResp.status,
                content_type: contentType,
                responsePreview: errorText.slice(0, 1200),
                session_id: sessionId,
                model_type: resolvedModelType,
            }};
        }}

        if (!consumeResponse) {{
            return {{
                ok: true,
                status: completionResp.status,
                url: completionResp.url || '/api/v0/chat/completion',
                content_type: contentType,
                session_id: sessionId,
                model_type: resolvedModelType,
                raw_text: '',
            }};
        }}

        const rawText = await completionResp.text();
        return {{
            ok: true,
            status: completionResp.status,
            url: completionResp.url || '/api/v0/chat/completion',
            content_type: contentType,
            session_id: sessionId,
            model_type: resolvedModelType,
            raw_text: rawText,
        }};
    }})()
    """
    return script


def execute_request_transport(
    tab: Any,
    transport_config: Dict[str, Any],
    *,
    prompt: str,
    consume_response: bool,
) -> Dict[str, Any]:
    normalized = normalize_request_transport_config(transport_config)
    mode = normalized.get("mode")
    profile_id = str(normalized.get("profile") or "").strip()
    options = normalized.get("options") or {}

    if mode != REQUEST_TRANSPORT_MODE_PAGE_FETCH or not profile_id:
        return {"ok": False, "error": "request_transport_not_enabled"}

    if profile_id != "deepseek_completion":
        return {"ok": False, "error": f"unsupported_request_transport_profile:{profile_id}"}

    script = _build_deepseek_completion_script(
        prompt=str(prompt or ""),
        options=options if isinstance(options, dict) else {},
        consume_response=consume_response,
    )

    try:
        result = tab.run_js(script) or {}
    except Exception as e:
        logger.warning(f"[REQUEST_TRANSPORT] 页面直发执行异常: {e}")
        return {"ok": False, "error": str(e)}

    if not isinstance(result, dict):
        return {"ok": False, "error": "invalid_request_transport_result"}

    return result


__all__ = [
    "REQUEST_TRANSPORT_MODE_PAGE_FETCH",
    "REQUEST_TRANSPORT_MODE_WORKFLOW",
    "execute_request_transport",
    "get_default_request_transport_config",
    "get_request_transport_defaults_payload",
    "get_request_transport_profile",
    "get_request_transport_profiles",
    "normalize_request_transport_config",
]
