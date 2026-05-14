// ========== 元素定义 Schema ==========

const DEFAULT_SELECTOR_DEFINITIONS = [
    {
        key: "input_box",
        description: "用户输入文本的输入框（textarea 或 contenteditable 元素）",
        enabled: true,
        required: true
    },
    {
        key: "send_btn",
        description: "发送消息的按钮（通常是 type=submit 或带有发送图标的按钮）",
        enabled: true,
        required: true
    },
    {
        key: "result_container",
        description: "AI 回复内容的容器（仅包含 AI 的输出文本，不含用户消息）",
        enabled: true,
        required: true
    },
    {
        key: "new_chat_btn",
        description: "新建对话的按钮（点击后开始新的对话）",
        enabled: true,
        required: false
    },
    {
        key: "message_wrapper",
        description: "消息完整容器（包裹单条消息的外层元素，用于多节点拼接）",
        enabled: false,
        required: false
    },
    {
        key: "generating_indicator",
        description: "生成中指示器（如停止按钮、加载动画，用于检测是否还在输出）",
        enabled: false,
        required: false
    },
    {
        key: "upload_btn",
        description: "打开文件选择器的上传按钮（点击后通常会弹出原生选文件）",
        enabled: false,
        required: false
    },
    {
        key: "file_input",
        description: "原生文件输入框（input[type=file]），用于直接注入文件",
        enabled: false,
        required: false
    },
    {
        key: "drop_zone",
        description: "支持拖拽上传的区域（某些站点不支持粘贴但支持拖拽）",
        enabled: false,
        required: false
    }
];

// ========== 配置 Schema 定义 ==========

// 浏览器常量 Schema（纯中文显示）
const BROWSER_CONSTANTS_SCHEMA = {
    connection: {
        label: '连接配置',
        icon: '🔌',
        items: {
            CONNECTION_TIMEOUT: {
                label: '连接超时',
                unit: '秒',
                desc: '浏览器连接超时时间',
                type: 'number',
                min: 1,
                max: 60,
                default: 10
            }
        }
    },
    delay: {
        label: '操作延迟',
        icon: '⏱️',
        desc: '模拟人类操作的随机延迟范围',
        items: {
            STEALTH_DELAY_MIN: {
                label: '隐身延迟下限',
                unit: '秒',
                type: 'number',
                step: 0.05,
                min: 0,
                default: 0.03
            },
            STEALTH_DELAY_MAX: {
                label: '隐身延迟上限',
                unit: '秒',
                type: 'number',
                step: 0.05,
                min: 0,
                default: 0.1
            },
            ACTION_DELAY_MIN: {
                label: '动作延迟下限',
                unit: '秒',
                type: 'number',
                step: 0.05,
                min: 0,
                default: 0.06
            },
            ACTION_DELAY_MAX: {
                label: '动作延迟上限',
                unit: '秒',
                type: 'number',
                step: 0.05,
                min: 0,
                default: 0.14
            }
        }
    },
    element: {
        label: '元素查找',
        icon: '🔍',
        items: {
            DEFAULT_ELEMENT_TIMEOUT: {
                label: '默认等待时间',
                unit: '秒',
                desc: '查找元素的默认超时',
                type: 'number',
                min: 1,
                default: 3
            },
            FALLBACK_ELEMENT_TIMEOUT: {
                label: '备用等待时间',
                unit: '秒',
                desc: '首次失败后的重试超时',
                type: 'number',
                min: 0.5,
                default: 1
            },
            ELEMENT_CACHE_MAX_AGE: {
                label: '缓存有效期',
                unit: '秒',
                desc: '元素位置缓存时间',
                type: 'number',
                min: 1,
                default: 5.0
            }
        }
    },
    text_input: {
        label: '文本输入',
        icon: '⌨️',
        items: {
            TEXT_INPUT_CHUNK_SIZE: {
                label: '长文本分块大小',
                unit: '字符',
                desc: '普通模式下，长文本会按这个大小分块写入输入框。值越大越快，但更容易触发页面限制；值越小更稳，但输入更慢。不影响文件粘贴阈值。',
                type: 'number',
                min: 1000,
                step: 1000,
                default: 30000
            }
        }
    },
    stream: {
        label: '流式监控',
        icon: '📡',
        desc: '只影响 DOM 流式监听。这里控制轮询频率、开始等待时间和结束判定。',
        items: {
            STREAM_CHECK_INTERVAL_MIN: {
                label: '检查间隔下限',
                unit: '秒',
                desc: '有新内容出现时，轮询会尽量压到这个最小间隔。值越小，显示越及时，但更吃性能。',
                type: 'number',
                step: 0.05,
                min: 0.05,
                default: 0.1
            },
            STREAM_CHECK_INTERVAL_MAX: {
                label: '检查间隔上限',
                unit: '秒',
                desc: '内容暂时不变时，轮询间隔会逐步放大到这个上限。值越大，更省资源，但结束判断会更慢。',
                type: 'number',
                step: 0.1,
                min: 0.1,
                default: 1.0
            },
            STREAM_CHECK_INTERVAL_DEFAULT: {
                label: '默认检查间隔',
                unit: '秒',
                desc: '开始监听时先用这个间隔检查，后续会在上下限之间动态调整。',
                type: 'number',
                step: 0.05,
                min: 0.05,
                default: 0.3
            },
            STREAM_SILENCE_THRESHOLD: {
                label: '静默超时阈值',
                unit: '秒',
                desc: '内容已经开始变化后，连续这么久没有新内容，并且稳定次数也达标时，判定这轮回复完成。',
                type: 'number',
                min: 1,
                default: 8.0
            },
            STREAM_SILENCE_THRESHOLD_FALLBACK: {
                label: '静默超时备用',
                unit: '秒',
                desc: '兜底静默基准。主判定迟迟不满足时，会按更宽松的窗口收尾，防止长时间挂住。',
                type: 'number',
                min: 1,
                default: 12
            },
            STREAM_MAX_TIMEOUT: {
                label: '最大超时',
                unit: '秒',
                desc: '单轮监听的硬上限。无论页面状态如何，超过这个时间都会强制结束。',
                type: 'number',
                min: 60,
                default: 600
            },
            STREAM_INITIAL_WAIT: {
                label: '初始等待',
                unit: '秒',
                desc: '发送后等待 AI 明确开始回复的最长时间。一直没有新节点、新文字或生成态时，会按超时处理。',
                type: 'number',
                min: 10,
                default: 180
            },
            STREAM_STABLE_COUNT_THRESHOLD: {
                label: '稳定判定次数',
                desc: '内容连续多少次检查都没变化，才算稳定。它需要和静默超时一起满足，才会判定结束。',
                type: 'number',
                min: 1,
                default: 8
            }
        }
    },
    streamAdvanced: {
        label: '流式监控（高级）',
        icon: '⚙️',
        collapsed: true,
        desc: '这里只保留当前版本仍在生效的兼容参数，大多数站点不用改。',
        items: {
            STREAM_CONTENT_SHRINK_TOLERANCE: {
                label: '内容收缩容忍次数',
                desc: '允许回复在小范围内回退多少字符仍不当成异常。用于容忍编辑器重排、占位符回收这类轻微波动。',
                type: 'number',
                min: 0,
                default: 3
            }
        }
    },
    validation: {
        label: '输入验证',
        icon: '✅',
        items: {
            MAX_MESSAGE_LENGTH: {
                label: '单条消息本地上限',
                unit: '字符',
                desc: '这里只控制程序侧的输入校验。超过这个字符数会在发送前拦下，不代表目标站点或模型的真实长度限制。',
                type: 'number',
                min: 1000,
                default: 100000
            },
            MAX_MESSAGES_COUNT: {
                label: '消息条数本地上限',
                unit: '条',
                desc: '这里只控制程序一次接收的 messages 条数。超过后会在本地校验阶段拦下，不代表站点真实上下文上限。',
                type: 'number',
                min: 1,
                default: 100
            }
        }
    },

    // 🆕 图片发送相关
    image: {
        label: '图片发送',
        icon: '🖼️',
        items: {
            UPLOAD_HISTORY_IMAGES: {
                label: '上传历史对话中的图片',
                desc: '开启：会把历史消息里出现的图片也一起上传；关闭：只上传本次用户消息里的图片',
                type: 'switch',
                default: true
            }
        }
    },
    globalIntercept: {
        label: '全局网络拦截',
        icon: '🛡️',
        collapsed: true,
        items: {
            GLOBAL_NETWORK_INTERCEPTION_ENABLED: {
                label: '启用常驻监听',
                desc: '空闲标签页持续监听网络事件；任务执行时会自动让位给工作流监听',
                type: 'switch',
                default: false
            },
            GLOBAL_NETWORK_INTERCEPTION_LISTEN_PATTERN: {
                label: '监听模式',
                desc: 'DrissionPage listen.start() 的 pattern，通常用 http',
                type: 'text',
                default: 'http'
            },
            GLOBAL_NETWORK_INTERCEPTION_WAIT_TIMEOUT: {
                label: '轮询超时',
                unit: '秒',
                desc: 'wait() 单次等待超时，越小响应越快但开销更高',
                type: 'number',
                step: 0.1,
                min: 0.1,
                default: 0.5
            },
            GLOBAL_NETWORK_INTERCEPTION_RETRY_DELAY: {
                label: '异常重试间隔',
                unit: '秒',
                desc: '监听器异常后重启间隔',
                type: 'number',
                step: 0.1,
                min: 0.2,
                default: 1.0
            }
        }
    }
};

// 环境变量 Schema
const ENV_CONFIG_SCHEMA = {
    service: {
        label: '服务配置',
        icon: '🖥️',
        items: {
            APP_HOST: {
                label: '监听地址',
                desc: '0.0.0.0 允许外部访问，127.0.0.1 仅本地',
                type: 'text',
                default: '127.0.0.1'
            },
            APP_PORT: {
                label: '监听端口',
                type: 'number',
                min: 1,
                max: 65535,
                default: 8199
            },
            APP_DEBUG: {
                label: '调试模式',
                desc: '开启 API 文档和详细错误',
                type: 'switch',
                default: true
            },
            LOG_LEVEL: {
                label: '日志级别',
                type: 'select',
                options: ['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                default: 'INFO'
            }
        }
    },
    auth: {
        label: '认证配置',
        icon: '🔐',
        items: {
            AUTH_ENABLED: {
                label: '启用认证',
                type: 'switch',
                default: false
            },
            AUTH_TOKEN: {
                label: 'Bearer Token',
                type: 'password',
                desc: 'AUTH_ENABLED=true 时必须设置',
                default: ''
            }
        }
    },
    cors: {
        label: 'CORS 配置',
        icon: '🌐',
        items: {
            CORS_ENABLED: {
                label: '启用 CORS',
                type: 'switch',
                default: true
            },
            CORS_ORIGINS: {
                label: '允许的跨域源',
                desc: '多个用逗号分隔，* 表示全部允许',
                type: 'text',
                default: '*'
            }
        }
    },
    browser: {
        label: '浏览器配置',
        icon: '🌍',
        items: {
            BROWSER_PORT: {
                label: 'Chrome 调试端口',
                type: 'number',
                min: 1024,
                max: 65535,
                default: 9222
            }
        }
    },
    proxy: {
        label: '代理配置',
        icon: '🔀',
        items: {
            PROXY_ENABLED: {
                label: '启用代理',
                desc: '开启后浏览器将通过代理服务器访问网络',
                type: 'switch',
                default: false
            },
            PROXY_ADDRESS: {
                label: '代理地址',
                desc: '支持 socks5:// 或 http:// 协议',
                type: 'text',
                default: 'socks5://127.0.0.1:1080'
            },
            PROXY_BYPASS: {
                label: '绕过代理',
                desc: '不走代理的地址，多个用逗号分隔',
                type: 'text',
                default: 'localhost,127.0.0.1'
            }
        }
    },
    dashboard: {
        label: 'Dashboard 配置',
        icon: '📊',
        items: {
            DASHBOARD_ENABLED: {
                label: '启用 Dashboard',
                type: 'switch',
                default: true
            },
            DASHBOARD_FILE: {
                label: 'Dashboard 文件路径',
                type: 'text',
                default: 'static/index.html'
            }
        }
    },
    ai: {
        label: 'AI 分析配置',
        icon: '🤖',
        desc: '辅助 AI 用于自动分析页面结构',
        items: {
            HELPER_API_KEY: {
                label: 'API Key',
                type: 'password',
                default: ''
            },
            HELPER_BASE_URL: {
                label: 'API 地址',
                type: 'text',
                default: 'http://127.0.0.1:5104/v1'
            },
            HELPER_MODEL: {
                label: '模型名称',
                type: 'text',
                default: 'gemini-3.0-pro'
            },
            MAX_HTML_CHARS: {
                label: 'HTML 最大字符数',
                desc: '超过会截断以节省 Token',
                type: 'number',
                min: 10000,
                default: 120000
            },
            CANVAS_IMAGE_MAX_SIZE: {
                label: 'Canvas 图片最大边长',
                unit: 'px',
                desc: '浏览器内下载 URL 图片时，Canvas 压缩后的最长边。默认 1024；调大可保留更多细节，但会增加返回体和内存占用。',
                type: 'number',
                min: 1,
                step: 256,
                default: 1024
            }
        }
    },
    toolCalling: {
        apply: 'service',
        label: '函数调用',
        icon: '🧰',
        desc: '控制函数调用的内部修复、结果清洗与媒体后处理策略。',
        items: {
            TOOL_CALLING_RETRY_STRATEGY: {
                label: '重试策略',
                desc: '聚焦修复只发送必要的修复信息；完整上下文会把原对话和修复反馈一起发给模型。',
                type: 'select',
                options: [
                    { label: '聚焦修复（推荐）', value: 'focused_repair' },
                    { label: '完整上下文', value: 'full_context' }
                ],
                default: 'focused_repair'
            },
            TOOL_CALLING_INTERNAL_RETRY_MAX: {
                label: '内部修复重试次数',
                desc: '函数调用结果校验失败时，自动修复后再次重试的次数。0 表示关闭自动修复；默认 2；最大 5。',
                type: 'number',
                min: 0,
                max: 5,
                default: 2
            },
            TOOL_CALLING_MAX_TOOL_RESULT_CHARS: {
                label: '单条 Tool Result 上限',
                unit: '字符',
                desc: '单条函数调用结果超过此字符数时，后端会直接返回明确错误，避免把超大结果继续塞给网页模型。默认 300000，可按需要调大。',
                type: 'number',
                min: 1,
                step: 10000,
                default: 300000
            },
            TOOL_CALLING_ALLOW_MEDIA_POSTPROCESS: {
                label: '允许媒体后处理',
                desc: '开启后，函数调用隐藏回合也会执行媒体二次提取、占位补偿和 Markdown 媒体注入。兼容旧行为，但更容易污染 tool payload；默认关闭。',
                type: 'switch',
                default: false
            },
            TOOL_CALLING_SANITIZE_ASSISTANT_CONTENT: {
                label: '解析前清洗回复',
                desc: '开启后，函数调用在解析 assistant 内容前会移除占位链接和尾部媒体 Markdown。推荐保持开启；只有需要完全回退旧行为时再关闭。',
                type: 'switch',
                default: true
            }
        }
    },
    files: {
        label: '配置文件',
        icon: '📁',
        items: {
            SITES_CONFIG_FILE: {
                label: '站点配置文件路径',
                type: 'text',
                default: 'sites.json'
            }
        }
    }
};


window.DEFAULT_SELECTOR_DEFINITIONS = DEFAULT_SELECTOR_DEFINITIONS;
window.BROWSER_CONSTANTS_SCHEMA = BROWSER_CONSTANTS_SCHEMA;
window.ENV_CONFIG_SCHEMA = ENV_CONFIG_SCHEMA;
