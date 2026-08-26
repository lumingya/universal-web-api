/**
 * @description 通用 Arena 请求拦截与 Payload 模型动态重写脚本
 * @version 2.0.0
 *
 * 作用：
 * 劫持页面发起的前端 API 请求（如 /nextjs-api/stream/create-evaluation），
 * 动态替换请求体中的模型标识符（如 modelAId、model 等）为指定的目标模型 UUID，
 * 并智能识别目标模型模态（图片/视频/文本），自动校准 Payload 中的 modality 字段。
 */

(function() {
    'use strict';

    const ctx = typeof __CONTEXT__ !== 'undefined' ? __CONTEXT__ : {};
    const args = typeof __ARGS__ !== 'undefined' ? __ARGS__ : {};

    // 1. 打印初始化调试日志
    console.log('[ArenaInterceptor:INIT]', {
        timestamp: new Date().toISOString(),
        context: ctx,
        args: args
    });

    // 权威已知模型与暗池 UUID 静态映射备用表
    const VERIFIED_STATIC_MAP = {
        // --- 字节跳动 Seed 系列 (生图 / 视频 / 文本) ---
        'dreamina-seedance-2.0-720p': '019d4231-5b3d-72d1-aa8c-04841b8eab5f',
        'k2': '019d4231-5b3d-72d1-aa8c-04841b8eab5f',
        'seedance-v1-pro': 'e705b65f-82cd-40cb-9630-d9e6ca92d06f',
        'seedance-v1-pro-text-to-video': 'e705b65f-82cd-40cb-9630-d9e6ca92d06f',
        'seedance-v1-pro-image-to-video': '4ddc4e52-2867-49b6-a603-5aab24a566ca',
        'seedance-v1-lite': '13ce11ba-def2-4c80-a70b-b0b2c14d293e',
        'seedance-v1-lite-text-to-video': '13ce11ba-def2-4c80-a70b-b0b2c14d293e',
        'seedance-v1-lite-image-to-video': '4c8dde6e-1b2c-45b9-91c3-413b2ceafffb',
        'seedream-5.0-pro': '019f42b5-8c52-7793-9be8-de35eecf7ea9',
        'seedream-5.0-lite': '019c9078-386f-7c92-99f3-97d5d6b0f239',
        'seedream-4.5': '019b3943-7503-776f-9632-c3c5da0c39b7',
        'autumn-byteplus': '019b3943-7503-776f-9632-c3c5da0c39b7',
        'seedream-3': 'd8771262-8248-4372-90d5-eb41910db034',
        'seededit-3.0': 'e2969ebb-6450-4bc4-87c9-bbdcf95840da',
        'dola-seed-2.0-pro-text': '019d2ac4-285d-7765-8296-d9a3aa2f2662',
        'dola-seed-2.0-pro-vision': '019d2ac4-713c-7ea3-baac-26b313dda937',
        'dola-seed-2.0-preview-text': '019c6453-8727-7186-9523-e130170d2fb9',
        'seed-2.1-pro-preview': '019ec9f6-5932-7156-aa54-8c913e29ac90',

        // --- 顶级生图与暗池模型 ---
        'mona-lisa-1': '019fe39a-b4c5-7442-850f-26c39b95b3ca',
        'luna-lisa-alpha': '01a01b46-ca55-712a-b52b-d08701456e80',
        'lina-f-alpha': '01a03537-4cb0-7842-9e06-b5af8474bf60',
        'lina-alpha': '01a02110-7a8a-71a9-9458-709effe0b3a3',
        'silver_halide': '01a0022c-e5a8-7caf-a1c7-1fa103b63823',
        'silver_halide [high]': '01a02284-7442-70bd-a78d-e62d455698b1',
        'silver_halide [low]': '01a02284-1d67-7b58-8976-556af6a340ef',
        'gpt-image-1': '6e855f13-55d7-4127-8656-9168a9f4dcc0',
        'gpt-image-1-high-fidelity': '69f90b32-01dc-43e1-8c48-bf494f8f4f38',
        'flux-2-pro': '019b7541-5e4b-7ff7-a34b-b0255b6ca9aa',
        'flux-2-dev': '019b478d-74ae-7d19-9a8f-6cfde89ab4ca',
        'flux-1-kontext-max': '0633b1ef-289f-49d4-a834-3d475a25e46b',
        'flux-1-kontext-pro': '28a8f330-3554-448c-9f32-2c0a08ec6477',
        'flux-1-kontext-dev': 'eb90ae46-a73a-4f27-be8b-40f090592c9a',
        'z-image': '019d9b62-1815-7ce9-a681-30eb7c95e1e0',
        'gemini-2.5-flash-image-preview': '019ae4b4-d7ca-7be8-b1ad-38a4d46ad8da',
        'gemini-3.1-flash-lite-image': '019f97d3-92f7-7b82-8412-28df5295aa16',
        'grok-imagine-image-quality': '019f3957-c3fe-789a-ab86-455b7f1ba8e5',
        'grok-imagine-image': '019c8052-a77a-7be8-b99b-014c0a52084c',

        // --- 文本 / 推理旗舰暗池 ---
        'claude-sonnet-5-high': '019f19f2-41f1-7c6d-9891-48d02fd9952c',
        'claude-sonnet-5-search': '019f19f2-41f1-7c6d-9891-48d02fd9952c',
        'claude-sonnet-4-6': '019c6d29-a30c-7e20-9bd0-6650af926623',
        'claude-sonnet-4-thinking-32k': '4653dded-a46b-442a-a8fe-9bb9730e2453',
        'gpt-5.4-high': '019cc0bb-f834-7162-a5c1-05c623b89c20',
        'gpt-5.4': '019cc0bb-5d0f-71bc-9aa3-d8fc64b8968d',
        'gpt-5.2-high': '019cc0ba-d215-79b9-b55a-f4b06876238b',
        'gpt-5.2': '019cc543-573d-7a3f-b155-ad9cc5733aa6',
        'gpt-5.1-codex-max': '019aeb38-cc3b-7421-a472-0bfaaeace035',
        'gpt-5.1-high': '019a8548-a2b1-70ce-b1be-eba096d41f58',
        'o4-mini-2025-04-16': 'f1102bbf-34ca-468f-a9fc-14bcf63f315b',
        'o3-2025-04-16': 'cb0f1e24-e8e9-4745-aabc-b926ffde7475',
        'kimi-k3': '019faec3-b3a8-7871-9059-8eea66f9f279',
        'kimi-k2.7-code': '019ebd5b-a6ed-7e66-89e9-1143d106e0e6',
        'kimi-k2.6': '019e4126-2c14-74d0-b898-1a08462025e1',
        'kimi-k2.5-thinking': '019d308f-e3c0-72e5-b819-f51d807653df',
        'glm-5.2 (max)': '019ebf6a-94d4-7649-b704-1dbbd5eb0942',
        'glm-5.2': '019faec4-4a81-765c-832f-9b2d909acee3',
        'glm-5.1': '019d5e8d-d53e-75f3-bcf5-815ae0cf202a',
        'glm-5': '019c45d7-96f0-7d39-8143-9d57941b5523',
        'deepseek-v4-pro-max': '019ec802-53b6-79c2-9861-bf96fe4dcfb6',
        'deepseek-v4-flash-high': '019ec801-b54c-7c0b-a193-41fa1a2ad9e0',
        'gemini-3.7-pro': '019ebf6a-94d4-7649-b704-1dbbd5eb0942',
        'grok-4.6-high-public': '019e34c9-b7a4-79fa-bb18-a6211eb906cf'
    };

    const MAX_MODEL_ID = '019b24bb-5caf-71c3-b854-37d0c7086f21';

    // 动态提取页面 Next.js 内存中的全量 initialModels 映射表
    function getDynamicPageModelMap() {
        const map = {};
        const meta = {};
        const modelsList = [];
        let payloadTexts = [];
        const readArray = (text, marker) => {
            const markerIndex = text.indexOf(marker);
            if (markerIndex < 0) return null;
            const start = text.indexOf('[', markerIndex + marker.length);
            if (start < 0) return null;
            let depth = 0, quoted = false, escaped = false;
            for (let i = start; i < text.length; i++) {
                const c = text[i];
                if (quoted) {
                    if (escaped) escaped = false;
                    else if (c === '\\') escaped = true;
                    else if (c === '"') quoted = false;
                    continue;
                }
                if (c === '"') quoted = true;
                else if (c === '[') depth++;
                else if (c === ']') {
                    depth--;
                    if (depth === 0) return text.slice(start, i + 1);
                }
            }
            return null;
        };

        try {
            for (const script of document.scripts) {
                const source = String(script.textContent || '').trim();
                const prefix = 'self.__next_f.push(';
                if (!source.startsWith(prefix) || !source.endsWith(')')) continue;
                try {
                    const payload = JSON.parse(source.slice(prefix.length, -1));
                    if (Array.isArray(payload) && typeof payload[1] === 'string') {
                        payloadTexts.push(payload[1]);
                    }
                } catch (_) {}
            }
            for (const text of payloadTexts) {
                const rawModels = readArray(text, '"initialModels":');
                if (!rawModels) continue;
                try {
                    const models = JSON.parse(rawModels);
                    for (const m of models) {
                        if (!m || !m.id) continue;
                        const id = String(m.id).trim();
                        const isVideo = Boolean(m.capabilities?.outputCapabilities?.video);
                        const isImage = Boolean(m.capabilities?.outputCapabilities?.image);
                        const modality = isVideo ? 'video' : (isImage ? 'image' : 'chat');

                        meta[id] = { id, modality, isVideo, isImage };
                        map[id.toLowerCase()] = id;
                        if (m.name) map[String(m.name).trim().toLowerCase()] = id;
                        if (m.displayName) map[String(m.displayName).trim().toLowerCase()] = id;
                        if (m.publicName) map[String(m.publicName).trim().toLowerCase()] = id;

                        modelsList.push({
                            id,
                            name: m.name,
                            displayName: m.displayName,
                            publicName: m.publicName,
                            modality
                        });
                    }
                } catch (_) {}
            }
        } catch (_) {}

        return { map, meta, modelsList };
    }

    let rawModel = String(args.override_model || ctx.model || window.__ARENA_TARGET_MODEL_ID || '').trim();
    if (rawModel.startsWith('arena.ai/direct/')) {
        rawModel = rawModel.replace('arena.ai/direct/', '');
    }

    const { map: dynamicMap, meta: modelMeta, modelsList } = getDynamicPageModelMap();
    const cleanKey = rawModel.toLowerCase().trim();

    // 打印 DOM 抓取到的模型概览
    console.log('[ArenaInterceptor:DOM_MODELS]', {
        count: modelsList.length,
        samples: modelsList.slice(0, 5)
    });

    // 透传模型清单：这些模型不覆盖请求体中的模型 ID，保留页面原生路由与暗池分发状态
    const PASSTHROUGH_MODELS = [
        'gpt-image-2',
        'gpt-image-2 (medium)',
        'gpt-image-2-medium',
        'openai/gpt-image-2',
        'auto',
        'max'
    ];
    const isPassthrough = PASSTHROUGH_MODELS.some(p => cleanKey === p) ||
        cleanKey.includes('gpt-image-2') ||
        cleanKey.includes('gpt_image_2');

    // 智能解析目标 UUID（优先级：动态页面提取 > 权威静态字典 > 原值）
    let targetModelId = isPassthrough ? null : (dynamicMap[cleanKey] || VERIFIED_STATIC_MAP[cleanKey] || rawModel);
    
    // 如果仍然不是 UUID 格式且非透传模型，尝试部分模糊匹配
    if (!isPassthrough && targetModelId) {
        const isUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(targetModelId);
        if (!isUuid) {
            for (const [k, v] of Object.entries(VERIFIED_STATIC_MAP)) {
                if (cleanKey.includes(k) || k.includes(cleanKey)) {
                    targetModelId = v;
                    break;
                }
            }
        }
    }

    // 智能识别模型模态能力（用于适配 request payload 中的 modality 字段）
    // 注意：Arena 后端 Zod Schema 明确规定文本聊天模态为 'chat' 或 'auto'，禁止使用 'text'
    const meta = (targetModelId && modelMeta[targetModelId]) ? modelMeta[targetModelId] : {};
    let targetModality = meta.modality || null;
    if (targetModality === 'text') {
        targetModality = 'chat';
    }
    if (!targetModality) {
        if (cleanKey.includes('video') || cleanKey.includes('seedance')) {
            targetModality = 'video';
        } else if (
            cleanKey.includes('image') ||
            cleanKey.includes('imagine') ||
            cleanKey.includes('mona') ||
            cleanKey.includes('luna') ||
            cleanKey.includes('lina') ||
            cleanKey.includes('seedream') ||
            cleanKey.includes('seededit') ||
            cleanKey.includes('halide') ||
            cleanKey.includes('flux') ||
            cleanKey.includes('z-image') ||
            cleanKey.includes('imagen')
        ) {
            targetModality = 'image';
        } else {
            targetModality = 'chat';
        }
    } else if (targetModality !== 'image' && targetModality !== 'video') {
        targetModality = 'chat';
    }

    const targetEndpoint = args.target_endpoint || '/nextjs-api/stream/create-evaluation';

    function isEvaluationEndpoint(url, config) {
        if (!url || typeof url !== 'string') return false;
        if (
            url.includes('/nextjs-api/stream/create-evaluation') ||
            url.includes('/nextjs-api/stream/post-to-evaluation')
        ) {
            return true;
        }
        if (config && config.targetEndpoint && url.includes(config.targetEndpoint)) {
            return true;
        }
        return false;
    }

    function rewritePayloadObject(parsed, config) {
        if (!parsed || typeof parsed !== 'object') return parsed;
        if (config && config.isPassthrough) {
            console.log('[ArenaInterceptor:PASSTHROUGH] Model is in passthrough list, preserving native payload');
            return parsed;
        }
        // 绝不修改 mode、id、userMessageId 等其他顶层字段，仅精准替换模型与模态
        if ('modelAId' in parsed) parsed.modelAId = config.targetModelId;
        if ('modelBId' in parsed) parsed.modelBId = config.targetModelId;
        if ('model' in parsed) parsed.model = config.targetModelId;
        if ('modelId' in parsed) parsed.modelId = config.targetModelId;
        if ('model_name' in parsed) parsed.model_name = config.targetModelId;
        if (config.targetModality && 'modality' in parsed && parsed.modality !== config.targetModality) {
            parsed.modality = config.targetModality;
        }
        return parsed;
    }

    console.log('[ArenaInterceptor:CONFIG]', {
        rawModel,
        cleanKey,
        targetModelId,
        targetModality,
        isPassthrough,
        targetEndpoint
    });

    const interceptorConfig = {
        targetModelId,
        targetModality,
        targetEndpoint,
        isPassthrough,
        prompt: ctx.prompt || ''
    };

    window.__ARENA_INTERCEPTOR_CONFIG__ = {
        targetEndpoint,
        targetModelId,
        targetModality,
        isPassthrough,
        maxModelId: MAX_MODEL_ID
    };

    if (window.__ARENA_PAYLOAD_INTERCEPTOR_INSTALLED__) {
        console.log('[ArenaInterceptor] 拦截器已存在，已热更新目标模型配置:', window.__ARENA_INTERCEPTOR_CONFIG__);
        return true;
    }

    window.__ARENA_PAYLOAD_INTERCEPTOR_INSTALLED__ = true;

    // 1. 劫持 window.fetch
    const originalFetch = window.fetch;
    window.fetch = async function(resource, init) {
        try {
            let url = typeof resource === 'string' ? resource : ((resource && resource.url) || (resource && resource.href) || String(resource || ''));
            const config = window.__ARENA_INTERCEPTOR_CONFIG__ || {};
            const method = (init && init.method) || (resource instanceof Request && resource.method) || 'GET';
            const isHit = isEvaluationEndpoint(url, config);

            console.log('[ArenaInterceptor:REQ] [fetch]', {
                url: String(url),
                method: String(method).toUpperCase(),
                isEvaluationEndpoint: isHit,
                targetModelId: config.targetModelId
            });

            if (isHit) {
                if (config.isPassthrough) {
                    console.log('[ArenaInterceptor:PASSTHROUGH] [fetch] 透传模式：保留页面原生请求 Payload');
                } else if (!config.targetModelId) {
                    console.warn('[ArenaInterceptor:SKIP] [fetch] 命中评测接口但 targetModelId 为空且非透传模型，跳过重写');
                } else if (init && init.body && typeof init.body === 'string') {
                    let bodyText = init.body;
                    let parsedBefore = null;
                    try {
                        parsedBefore = JSON.parse(bodyText);
                    } catch (err) {
                        parsedBefore = `<Non-JSON: ${err.message}>`;
                    }
                    console.log('[ArenaInterceptor:PAYLOAD_BEFORE] [fetch]', {
                        preview: bodyText.slice(0, 300),
                        length: bodyText.length,
                        parsed: parsedBefore
                    });

                    try {
                        let parsed = typeof parsedBefore === 'object' && parsedBefore !== null ? parsedBefore : JSON.parse(bodyText);
                        parsed = rewritePayloadObject(parsed, config);
                        bodyText = JSON.stringify(parsed);
                        init.body = bodyText;
                        console.log('[ArenaInterceptor:PAYLOAD_AFTER] [fetch]', {
                            preview: bodyText.slice(0, 300),
                            length: bodyText.length,
                            parsed: parsed
                        });
                        console.log(`[ArenaInterceptor] ✅ 已成功重写请求 Payload 模型为: ${config.targetModelId}, 模态: ${config.targetModality || 'auto'}`);
                    } catch (parseErr) {
                        console.warn('[ArenaInterceptor:ERROR] [fetch] 重写 JSON 失败:', parseErr);
                        init.body = bodyText;
                    }
                } else if (resource instanceof Request) {
                    try {
                        const clonedReq = resource.clone();
                        let bodyText = await clonedReq.text();
                        if (bodyText) {
                            let parsedBefore = null;
                            try {
                                parsedBefore = JSON.parse(bodyText);
                            } catch (err) {
                                parsedBefore = `<Non-JSON: ${err.message}>`;
                            }
                            console.log('[ArenaInterceptor:PAYLOAD_BEFORE] [fetch Request]', {
                                preview: bodyText.slice(0, 300),
                                length: bodyText.length,
                                parsed: parsedBefore
                            });

                            try {
                                let parsed = typeof parsedBefore === 'object' && parsedBefore !== null ? parsedBefore : JSON.parse(bodyText);
                                parsed = rewritePayloadObject(parsed, config);
                                bodyText = JSON.stringify(parsed);
                                if (init) {
                                    init.body = bodyText;
                                } else {
                                    resource = new Request(resource, { body: bodyText });
                                }
                                console.log('[ArenaInterceptor:PAYLOAD_AFTER] [fetch Request]', {
                                    preview: bodyText.slice(0, 300),
                                    length: bodyText.length,
                                    parsed: parsed
                                });
                                console.log(`[ArenaInterceptor] ✅ 已成功重写 Request 实例 Payload 模型为: ${config.targetModelId}`);
                            } catch (parseErr) {
                                console.warn('[ArenaInterceptor:ERROR] [fetch Request] 重写 Request 实例 JSON 失败:', parseErr);
                            }
                        } else {
                            console.warn('[ArenaInterceptor:SKIP] [fetch Request] Request 实例 bodyText 为空');
                        }
                    } catch (err) {
                        console.warn('[ArenaInterceptor:ERROR] [fetch Request] 读取 Request 流失败:', err);
                    }
                } else {
                    console.warn('[ArenaInterceptor:SKIP] [fetch] 未检测到可拦截的 body (init.body 为空且非 Request 实例)');
                }
            }
        } catch (e) {
            console.warn('[ArenaInterceptor:ERROR] [fetch] 拦截异常（放行）:', e);
        }
        return init !== undefined ? originalFetch.call(this, resource, init) : originalFetch.call(this, resource);
    };

    // 2. 劫持 XMLHttpRequest
    const originalXHROpen = XMLHttpRequest.prototype.open;
    const originalXHRSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function(method, url) {
        this.__intercept_method = method;
        this.__intercept_url = typeof url === 'string' ? url : ((url && url.href) || String(url || ''));
        return originalXHROpen.apply(this, arguments);
    };

    XMLHttpRequest.prototype.send = function(body) {
        try {
            const config = window.__ARENA_INTERCEPTOR_CONFIG__ || {};
            const url = this.__intercept_url || '';
            const method = (this.__intercept_method || 'POST').toUpperCase();
            const isHit = isEvaluationEndpoint(url, config);

            console.log('[ArenaInterceptor:REQ] [xhr]', {
                url: String(url),
                method: String(method),
                isEvaluationEndpoint: isHit,
                targetModelId: config.targetModelId
            });

            if (isHit) {
                if (config.isPassthrough) {
                    console.log('[ArenaInterceptor:PASSTHROUGH] [xhr] 透传模式：保留页面原生 XHR Payload');
                } else if (!config.targetModelId) {
                    console.warn('[ArenaInterceptor:SKIP] [xhr] XHR 命中评测接口但 targetModelId 为空且非透传模型，跳过重写');
                } else if (typeof body === 'string') {
                    let parsedBefore = null;
                    try {
                        parsedBefore = JSON.parse(body);
                    } catch (err) {
                        parsedBefore = `<Non-JSON: ${err.message}>`;
                    }
                    console.log('[ArenaInterceptor:PAYLOAD_BEFORE] [xhr]', {
                        preview: body.slice(0, 300),
                        length: body.length,
                        parsed: parsedBefore
                    });

                    try {
                        let parsed = typeof parsedBefore === 'object' && parsedBefore !== null ? parsedBefore : JSON.parse(body);
                        parsed = rewritePayloadObject(parsed, config);
                        body = JSON.stringify(parsed);
                        console.log('[ArenaInterceptor:PAYLOAD_AFTER] [xhr]', {
                            preview: body.slice(0, 300),
                            length: body.length,
                            parsed: parsed
                        });
                        console.log(`[ArenaInterceptor] ✅ 已成功重写 XHR Payload 模型为: ${config.targetModelId}`);
                    } catch (parseErr) {
                        console.warn('[ArenaInterceptor:ERROR] [xhr] XHR 重写 JSON 失败:', parseErr);
                    }
                } else {
                    console.warn('[ArenaInterceptor:SKIP] [xhr] XHR 命中评测接口但 body 并非字符串或为空:', typeof body);
                }
            }
        } catch (e) {
            console.warn('[ArenaInterceptor:ERROR] [xhr] XHR 拦截异常:', e);
        }
        return originalXHRSend.call(this, body);
    };

    console.log('[ArenaInterceptor] 🚀 Arena 智能多模态网络拦截器已就绪！目标:', targetModelId);
    return true;
})();
