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
            DEFAULT_PORT: {
                label: '调试端口',
                desc: 'Chrome DevTools 远程调试端口',
                type: 'number',
                min: 1024,
                max: 65535,
                default: 9222
            },
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
                default: 0.1
            },
            STEALTH_DELAY_MAX: {
                label: '隐身延迟上限',
                unit: '秒',
                type: 'number',
                step: 0.05,
                min: 0,
                default: 0.3
            },
            ACTION_DELAY_MIN: {
                label: '动作延迟下限',
                unit: '秒',
                type: 'number',
                step: 0.05,
                min: 0,
                default: 0.15
            },
            ACTION_DELAY_MAX: {
                label: '动作延迟上限',
                unit: '秒',
                type: 'number',
                step: 0.05,
                min: 0,
                default: 0.3
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
    stream: {
        label: '流式监控',
        icon: '📡',
        desc: '控制 AI 响应的检测频率和超时判定',
        items: {
            STREAM_CHECK_INTERVAL_MIN: {
                label: '检查间隔下限',
                unit: '秒',
                type: 'number',
                step: 0.05,
                min: 0.05,
                default: 0.1
            },
            STREAM_CHECK_INTERVAL_MAX: {
                label: '检查间隔上限',
                unit: '秒',
                type: 'number',
                step: 0.1,
                min: 0.1,
                default: 1.0
            },
            STREAM_CHECK_INTERVAL_DEFAULT: {
                label: '默认检查间隔',
                unit: '秒',
                type: 'number',
                step: 0.05,
                min: 0.05,
                default: 0.3
            },
            STREAM_SILENCE_THRESHOLD: {
                label: '静默超时阈值',
                unit: '秒',
                desc: '无新内容多久后判定完成',
                type: 'number',
                min: 1,
                default: 8.0
            },
            STREAM_SILENCE_THRESHOLD_FALLBACK: {
                label: '静默超时备用',
                unit: '秒',
                desc: '慢速模型的备用阈值',
                type: 'number',
                min: 1,
                default: 12
            },
            STREAM_MAX_TIMEOUT: {
                label: '最大超时',
                unit: '秒',
                desc: '单次响应的绝对超时上限',
                type: 'number',
                min: 60,
                default: 600
            },
            STREAM_INITIAL_WAIT: {
                label: '初始等待',
                unit: '秒',
                desc: '等待首次响应的最长时间',
                type: 'number',
                min: 10,
                default: 180
            },
            STREAM_STABLE_COUNT_THRESHOLD: {
                label: '稳定判定次数',
                desc: '连续多少次检查不变才判定完成',
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
        items: {
            STREAM_RERENDER_WAIT: {
                label: '重渲染等待',
                unit: '秒',
                desc: '等待页面重新渲染',
                type: 'number',
                step: 0.1,
                default: 0.5
            },
            STREAM_CONTENT_SHRINK_TOLERANCE: {
                label: '内容收缩容忍次数',
                desc: '允许内容变短的次数',
                type: 'number',
                min: 0,
                default: 3
            },
            STREAM_MIN_VALID_LENGTH: {
                label: '最小有效长度',
                unit: '字符',
                desc: '响应被视为有效的最小长度',
                type: 'number',
                min: 1,
                default: 10
            },
            STREAM_INITIAL_ELEMENT_WAIT: {
                label: '初始元素等待',
                unit: '秒',
                type: 'number',
                min: 1,
                default: 10
            },
            STREAM_MAX_ABNORMAL_COUNT: {
                label: '最大异常次数',
                desc: '连续异常多少次后中止',
                type: 'number',
                min: 1,
                default: 5
            },
            STREAM_MAX_ELEMENT_MISSING: {
                label: '最大元素丢失次数',
                type: 'number',
                min: 1,
                default: 10
            },
            STREAM_CONTENT_SHRINK_THRESHOLD: {
                label: '内容收缩阈值',
                desc: '内容缩减超过此比例视为异常',
                type: 'number',
                step: 0.05,
                min: 0,
                max: 1,
                default: 0.3
            }
        }
    },
    validation: {
        label: '输入验证',
        icon: '✅',
        items: {
            MAX_MESSAGE_LENGTH: {
                label: '消息最大长度',
                unit: '字符',
                type: 'number',
                min: 1000,
                default: 100000
            },
            MAX_MESSAGES_COUNT: {
                label: '消息最大数量',
                unit: '条',
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
                default: 'dashboard.html'
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
