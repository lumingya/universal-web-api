const { createApp } = Vue
const MARKETPLACE_CLIENT_VERSION_STORAGE_KEY = 'marketplace_client_version'

function loadStoredMarketplaceClientVersion() {
    try {
        return String(localStorage.getItem(MARKETPLACE_CLIENT_VERSION_STORAGE_KEY) || '').trim()
    } catch (error) {
        return ''
    }
}

function saveStoredMarketplaceClientVersion(version) {
    try {
        const value = String(version || '').trim()
        if (value) {
            localStorage.setItem(MARKETPLACE_CLIENT_VERSION_STORAGE_KEY, value)
        } else {
            localStorage.removeItem(MARKETPLACE_CLIENT_VERSION_STORAGE_KEY)
        }
    } catch (error) {
        // ignore storage failures and keep runtime data available
    }
}

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
    logging: {
        label: '日志',
        icon: '🪄',
        items: {
            LOG_INFO_CUTE_MODE: {
                label: 'INFO 日志可爱化',
                desc: '开启后，日志列表会优先显示润色后的 INFO 文案；原始日志不会丢失，鼠标悬停日志正文仍可查看原文。',
                type: 'switch',
                default: false
            },
            LOG_DEBUG_CUTE_MODE: {
                label: 'DEBUG 日志可爱化',
                desc: '开启后，日志列表会优先显示润色后的主要 DEBUG 文案；原始日志不会丢失，鼠标悬停日志正文仍可查看原文。',
                type: 'switch',
                default: false
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
    },
    commandPeriodic: {
        label: '命令调度',
        icon: '⚡',
        collapsed: true,
        items: {
            COMMAND_PERIODIC_CHECK_ENABLED: {
                label: '启用全局周期检测',
                desc: '控制命令系统的空闲标签页周期扫描开关',
                type: 'switch',
                default: true
            },
            COMMAND_PERIODIC_CHECK_INTERVAL_SEC: {
                label: '全局检测间隔',
                unit: '秒',
                desc: '命令系统的默认周期检测间隔',
                type: 'number',
                step: 0.5,
                min: 1,
                default: 8.0
            },
            COMMAND_PERIODIC_CHECK_JITTER_SEC: {
                label: '全局检测抖动',
                unit: '秒',
                desc: '为周期检测增加少量随机抖动，避免固定节奏碰撞',
                type: 'number',
                step: 0.2,
                min: 0,
                default: 2.0
            }
        }
    },
    tabPool: {
        label: '标签页池',
        icon: '🗂️',
        collapsed: true,
        items: {
            TAB_POOL_MAX_TABS: {
                label: '最大标签页数',
                desc: '超过后不再自动纳入新的标签页',
                type: 'number',
                min: 1,
                default: 5
            },
            TAB_POOL_MIN_TABS: {
                label: '最小保留标签页数',
                desc: '标签页池尽量维持的最小可用数量',
                type: 'number',
                min: 1,
                default: 1
            },
            TAB_POOL_IDLE_TIMEOUT: {
                label: '空闲超时',
                unit: '秒',
                desc: '标签页空闲多久后允许被回收或重置',
                type: 'number',
                min: 10,
                default: 300
            },
            TAB_POOL_ACQUIRE_TIMEOUT: {
                label: '占用等待超时',
                unit: '秒',
                desc: '获取标签页会话的最大等待时间',
                type: 'number',
                min: 1,
                default: 60
            },
            TAB_POOL_STUCK_TIMEOUT: {
                label: '卡死强制释放超时',
                unit: '秒',
                desc: '标签页忙碌超过该时长后，系统会尝试取消任务并强制释放',
                type: 'number',
                min: 10,
                default: 180
            }
        }
    }
};

// 环境变量 Schema
const ENV_CONFIG_SCHEMA = {
    service: {
        apply: 'service',
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
            PUBLIC_BASE_URL: {
                label: '公开访问地址',
                desc: '用于生成返回给客户端的可访问链接，例如图片下载地址',
                type: 'text',
                default: 'http://127.0.0.1:8199'
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
        apply: 'service',
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
        apply: 'service',
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
        apply: 'launcher',
        label: '浏览器配置',
        icon: '🌍',
        items: {
            BROWSER_PORT: {
                label: 'Chrome 调试端口',
                type: 'number',
                min: 1024,
                max: 65535,
                default: 9222
            },
            BROWSER_PATH: {
                label: '自定义浏览器路径',
                desc: '可选，留空时自动检测 Chrome、Edge、Brave 等浏览器',
                type: 'text',
                default: ''
            },
            BROWSER_PROFILE_DIR: {
                label: '浏览器配置目录',
                desc: '留空时使用项目内的 chrome_profile 目录',
                type: 'text',
                default: ''
            },
            BROWSER_PROFILE_NAME: {
                label: '浏览器配置名称',
                desc: '例如 Default、Profile 1',
                type: 'text',
                default: ''
            }
        }
    },
    proxy: {
        apply: 'launcher',
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
        apply: 'service',
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
    update: {
        apply: 'launcher',
        label: '更新配置',
        icon: '🔄',
        items: {
            AUTO_UPDATE_ENABLED: {
                label: '启用自动更新',
                desc: '启动脚本会在启动前检查并应用更新',
                type: 'switch',
                default: true
            },
            GITHUB_REPO: {
                label: 'GitHub 仓库',
                desc: '自动更新检查使用的仓库，格式为 owner/repo',
                type: 'text',
                default: 'lumingya/universal-web-api'
            }
        }
    },
    ai: {
        apply: 'service',
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
            HELPER_API_PROVIDER: {
                label: 'API 提供商',
                desc: '支持 auto、openai、gemini、claude',
                type: 'select',
                options: ['auto', 'openai', 'gemini', 'claude'],
                default: 'auto'
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
    toolCalling: {
        apply: 'service',
        label: '函数调用',
        icon: '🧰',
        desc: '控制函数调用的内部修复提示词与重试策略。',
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
            }
        }
    },
    files: {
        apply: 'service',
        label: '配置文件',
        icon: '📁',
        items: {
            SITES_CONFIG_FILE: {
                label: '站点配置文件路径',
                type: 'text',
                default: 'config/sites.json'
            }
        }
    }
};

// ========== Vue 应用 ==========

const app = createApp({
    data() {
        return {
            // 数据
            sites: {},
            currentDomain: null,
            searchQuery: '',

            // UI 状态
            toasts: [],
            toastCounter: 0,
            hasLoadedSettings: false,
            hasLoadedMarketplace: false,
            hasLoadedExtractors: false,
            isSaving: false,
            isLoading: false,
            showJsonPreview: false,
            showTokenDialog: false,
            showStepTemplates: false,
            showTestDialog: false,
            showSelectorMenu: false,
            darkMode: false,

            // Tab 切换（新增 settings）
            activeTab: 'config',  // 'config' | 'logs' | 'settings'

            // 折叠面板状态
            selectorCollapsed: true,
            workflowCollapsed: true,

            // 浏览器状态
            browserStatus: {
                connected: false,
                tab_url: null,
                tab_title: null
            },

            // 认证
            authEnabled: false,
            tempToken: '',

            // 选择器测试
            testSelectorInput: '',
            testTimeout: 2,
            testResult: null,
            isTesting: false,
            testHighlight: false,

            // 日志相关
            logs: [],
            logLevelFilter: 'ALL',
            pauseLogs: false,
            lastLogTimestamp: 0,
            lastLogSeq: 0,
            logPollingTimer: null,

            // ========== 导入功能 ==========
            showImportDialog: false,
            importMode: 'merge',  // 'merge' | 'replace'
            importType: 'full',   // 'full' | 'single' (新增：导入类型)
            importedConfig: null,
            importFileName: '',
            singleSiteImportDomain: '',  // 新增：单站点导入时的域名

            // ========== 系统设置 ==========
            // 环境配置
            envConfig: {},
            envConfigOriginal: {},
            envCollapsed: {},
            isSavingEnv: false,
            isLoadingEnv: false,

            // 浏览器常量
            browserConstants: {},
            browserConstantsOriginal: {},
            browserConstantsRaw: {},
            browserConstantsCollapsed: {},
            isSavingConstants: false,
            isLoadingConstants: false,

            // 更新白名单
            updatePreserveOptions: [],
            updatePreserveSelected: [],
            updatePreserveSelectedOriginal: [],
            isSavingUpdatePreserve: false,
            isLoadingUpdatePreserve: false,

            // Schema 引用
            envSchema: ENV_CONFIG_SCHEMA,
            browserConstantsSchema: BROWSER_CONSTANTS_SCHEMA,

            // ========== 元素定义管理 ==========
            selectorDefinitions: [],
            selectorDefinitionsOriginal: [],
            isLoadingDefinitions: false,
            isSavingDefinitions: false,
            showAddDefinitionDialog: false,
            newDefinition: {
                key: '',
                description: '',
                enabled: true,
                required: false
            },
            editingDefinitionIndex: null,

            // ========== 插件市场 ==========
            marketplaceCatalog: {
                items: [],
                count: 0,
                total_downloads: 0,
                default_sort: 'downloads',
                source_mode: 'local',
                source_name: '配置市场',
                source_url: '',
                upload_url: '',
                warning: ''
            },
            marketplaceLoading: false,
            marketplaceError: '',
            marketplaceImportingId: null,
            showMarketplacePreview: false,
            marketplacePreviewData: null,
            marketplacePreviewTitle: '',
            showMarketplaceImportDialog: false,
            marketplacePendingImport: null,
            marketplaceImportStrategy: 'overwrite',
            marketplaceImportPresetName: '',
            showMarketplaceSubmitDialog: false,
            marketplaceSubmitSaving: false,
            marketplaceCommandOptions: [],
            marketplaceCommandLoading: false,
            appVersion: loadStoredMarketplaceClientVersion(),
            marketplaceSubmitForm: {
                item_type: 'site_config',
                title: '',
                summary: '',
                author: '本地投稿',
                site_domain: '',
                category: '',
                preset_name: '',
                compatibility: '',
                version: '1.0.0',
                tagsText: '',
                selected_command_ids: [],
                parser_id: '',
                parser_class_name: '',
                parser_module_name: '',
                parser_source: '',
                parser_patterns_text: '',
                parser_filename: ''
            },

        }
    },

    computed: {
        filteredSites() {
            const keys = Object.keys(this.sites).sort()
            return this.searchQuery
                ? keys.filter(d => d.toLowerCase().includes(this.searchQuery.toLowerCase()))
                : keys
        },

        currentConfig() {
            return this.currentDomain ? this.sites[this.currentDomain] : null
        },

        hasToken() {
            return !!localStorage.getItem('api_token')
        },

        // 过滤后的日志
        filteredLogs() {
            if (this.logLevelFilter === 'ALL') {
                return this.logs;
            }
            return this.logs.filter(log => log.level === this.logLevelFilter);
        },

        // 检测环境配置是否有变更
        envConfigChanged() {
            return JSON.stringify(this.envConfig) !== JSON.stringify(this.envConfigOriginal);
        },

        // 检测浏览器常量是否有变更
        browserConstantsChanged() {
            return JSON.stringify(this.browserConstants) !== JSON.stringify(this.browserConstantsOriginal);
        },

        // 检测元素定义是否有变更
        selectorDefinitionsChanged() {
            return JSON.stringify(this.selectorDefinitions) !== JSON.stringify(this.selectorDefinitionsOriginal);
        },

        // 检测更新白名单是否有变更
        updatePreserveChanged() {
            return JSON.stringify(this.updatePreserveSelected) !== JSON.stringify(this.updatePreserveSelectedOriginal);
        }
    },

    watch: {
        activeTab(tab) {
            this.ensureTabDataLoaded(tab)
        },
        darkMode() {
            this.applyDarkMode()
        }
    },

    mounted() {
        // 读取夜间模式设置
        let savedDarkMode = null
        try {
            savedDarkMode = localStorage.getItem('darkMode')
        } catch (e) {
            savedDarkMode = null
        }
        if (savedDarkMode !== null) {
            this.darkMode = savedDarkMode === 'true'
        } else {
            this.darkMode = window.matchMedia('(prefers-color-scheme: dark)').matches
        }
        this.applyDarkMode()

        // 初始化折叠状态
        this.initCollapsedStates()

        this.initializeDashboard()

        // 启动日志轮询（每 1 秒）

        // 加载系统设置

        // 加载元素定义

        // 加载提取器列表
    },

    beforeUnmount() {
        this.stopLogPolling()
    },

    methods: {
        async initializeDashboard() {
            await Promise.all([
                this.loadConfig(true),
                this.loadHealthStatus({ silent: true })
            ])

            this.startLogPolling()
            this.ensureTabDataLoaded(this.activeTab)
        },

        startLogPolling() {
            if (this.logPollingTimer) {
                return
            }

            this.pollLogs()
            this.logPollingTimer = setInterval(() => {
                this.pollLogs()
            }, 1000)
        },

        stopLogPolling() {
            if (!this.logPollingTimer) {
                return
            }

            clearInterval(this.logPollingTimer)
            this.logPollingTimer = null
        },
        // ========== 初始化 ==========

        initCollapsedStates() {
            // 环境配置分组默认折叠
            for (const key of Object.keys(ENV_CONFIG_SCHEMA)) {
                this.envCollapsed[key] = true;
            }
            // 浏览器常量分组默认折叠
            for (const [key] of Object.entries(BROWSER_CONSTANTS_SCHEMA)) {
                this.browserConstantsCollapsed[key] = true;
            }
        },

        // ========== 夜间模式 ==========

        applyDarkMode() {
            const isDark = !!this.darkMode
            const targets = [
                document.documentElement,
                document.body,
                document.getElementById('app')
            ].filter(Boolean)
            for (const el of targets) {
                el.classList.remove('dark', 'light')
                el.classList.add(isDark ? 'dark' : 'light')
                el.setAttribute('data-theme', isDark ? 'dark' : 'light')
            }
            document.documentElement.style.colorScheme = isDark ? 'dark' : 'light'
        },

        toggleDarkMode() {
            this.darkMode = !this.darkMode
            this.applyDarkMode()
            try {
                localStorage.setItem('darkMode', this.darkMode.toString())
            } catch (e) {
                // ignore storage failures and keep runtime theme switch available
            }
            this.notify('已切换到' + (this.darkMode ? '夜间' : '日间') + '模式', 'success')
        },

        // ========== 选择器菜单 ==========

        toggleSelectorMenu() {
            this.showSelectorMenu = !this.showSelectorMenu
        },

        closeAllMenus() {
            this.showSelectorMenu = false
        },

        // ========== API 调用 ==========

        async apiRequest(url, options = {}) {
            const token = localStorage.getItem('api_token')
            const headers = {
                'Content-Type': 'application/json',
                ...options.headers
            }

            if (token) {
                headers['Authorization'] = 'Bearer ' + token
            }

            try {
                const response = await fetch(url, {
                    ...options,
                    headers
                })

                if (!response.ok) {
                    if (response.status === 401) {
                        this.notify('认证失败，请检查 Token', 'error')
                        this.showTokenDialog = true
                        throw new Error('UNAUTHORIZED')
                    }

                    const errorData = await response.json().catch(() => ({}))
                    throw new Error(errorData.detail || '请求失败 (' + response.status + ')')
                }

                return await response.json()
            } catch (error) {
                if (error.message !== 'UNAUTHORIZED') {
                    console.error('API 请求错误:', error)
                }
                throw error
            }
        },

        async loadConfig(silent) {
            // 防御：@click="loadConfig" 会传入 Event 对象，需要过滤
            if (typeof silent !== 'boolean') {
                silent = false
            }

            this.isLoading = true
            try {
                const data = await this.apiRequest('/api/config')
                this.sites = this.normalizeConfig(data)

                if (!this.currentDomain && Object.keys(this.sites).length > 0) {
                    this.currentDomain = Object.keys(this.sites)[0]
                }

                if (!silent) {
                    this.notify('配置已刷新 (' + Object.keys(this.sites).length + ' 个站点)', 'success')
                }
                return true
            } catch (error) {
                this.notify('加载配置失败: ' + error.message, 'error')
                this.sites = {}
                return false
            } finally {
                this.isLoading = false
            }
        },

        async saveConfig() {
            if (!this.validateConfig()) {
                return
            }

            this.isSaving = true
            try {
                await this.apiRequest('/api/config', {
                    method: 'POST',
                    body: JSON.stringify({ config: this.sites })
                })
                this.notify('配置已保存', 'success')
            } catch (error) {
                this.notify('保存失败: ' + error.message, 'error')
            } finally {
                this.isSaving = false
            }
        },

        async refreshStatus() {
            const [configOk, healthOk] = await Promise.all([
                this.loadConfig(true),
                this.loadHealthStatus()
            ])

            if (configOk || healthOk) {
                this.notify('状态已刷新', 'success')
            } else {
                this.notify('刷新失败', 'error')
            }
        },

        async loadHealthStatus({ silent = false } = {}) {
            try {
                const health = await this.apiRequest('/health')
                this.appVersion = String(health.version || '').trim()
                saveStoredMarketplaceClientVersion(this.appVersion)
                this.browserStatus = health.browser || {}
                this.authEnabled = health.config?.auth_enabled || false
                return true
            } catch (error) {
                if (error.message === 'UNAUTHORIZED') {
                    this.authEnabled = true
                    return true
                }

                console.error('状态检查失败:', error)
                if (!silent) {
                    this.notify('状态检查失败: ' + error.message, 'error')
                }
                return false
            }
        },

        getMarketplaceClientVersion() {
            return String(this.appVersion || '').trim() || loadStoredMarketplaceClientVersion()
        },

        appendMarketplaceClientVersion(url) {
            const target = String(url || '')
            const version = this.getMarketplaceClientVersion()
            if (!target || !version) {
                return target
            }
            const separator = target.includes('?') ? '&' : '?'
            return target + separator + 'app_version=' + encodeURIComponent(version)
        },

        async checkAuth() {
            return this.loadHealthStatus({ silent: true })
        },

        async testSelector(key, selector) {
            if (!selector) {
                this.notify('选择器为空', 'warning')
                return
            }

            this.testSelectorInput = selector
            this.showTestDialog = true
            this.testResult = null

            await this.runTest()
        },

        async runTest() {
            if (!this.testSelectorInput) return

            this.isTesting = true
            this.testResult = null

            try {
                const result = await this.apiRequest('/api/debug/test-selector', {
                    method: 'POST',
                    body: JSON.stringify({
                        selector: this.testSelectorInput,
                        timeout: this.testTimeout,
                        highlight: this.testHighlight
                    })
                })

                this.testResult = result

                if (result.success) {
                    if (result.count > 1) {
                        this.notify('✅ 找到 ' + result.count + ' 个元素' + (this.testHighlight ? '，已全部高亮' : ''), 'success')
                    } else {
                        this.notify('✅ 选择器有效' + (this.testHighlight ? '，已高亮显示' : ''), 'success')
                    }
                } else {
                    this.notify('❌ 选择器无效', 'error')
                }
            } catch (error) {
                this.testResult = {
                    success: false,
                    message: error.message
                }
                this.notify('测试失败: ' + error.message, 'error')
            } finally {
                this.isTesting = false
            }
        },

        async testCurrentSite() {
            if (!this.currentConfig || Object.keys(this.currentConfig.selectors).length === 0) {
                this.notify('当前站点没有选择器', 'warning')
                return
            }

            this.notify('开始批量测试...', 'info')

            let successCount = 0
            let failCount = 0

            for (const [key, selector] of Object.entries(this.currentConfig.selectors)) {
                if (!selector) continue

                try {
                    const result = await this.apiRequest('/api/debug/test-selector', {
                        method: 'POST',
                        body: JSON.stringify({
                            selector: selector,
                            timeout: 2
                        })
                    })

                    if (result.success) {
                        successCount++
                        console.log('✅ ' + key + ': ' + selector)
                    } else {
                        failCount++
                        console.warn('❌ ' + key + ': ' + selector)
                    }
                } catch (error) {
                    failCount++
                    console.error('❌ ' + key + ': ' + error.message)
                }
            }

            this.notify('测试完成: ' + successCount + ' 成功, ' + failCount + ' 失败',
                failCount > 0 ? 'warning' : 'success')
        },

        async reanalyzeCurrentSite() {
            if (!this.currentDomain) return

            if (!confirm('确定要删除 ' + this.currentDomain + ' 的配置并重新分析吗？\n\n重新分析需要浏览器当前正在访问该站点。')) {
                return
            }

            try {
                await this.apiRequest('/api/config/' + this.currentDomain, {
                    method: 'DELETE'
                })

                this.notify('配置已删除，请刷新页面让 AI 重新分析', 'info')

                delete this.sites[this.currentDomain]
                this.currentDomain = null
            } catch (error) {
                this.notify('删除失败: ' + error.message, 'error')
            }
        },
        // ========== 图片配置 (新增) ==========

        // 🆕 更新图片配置
        async updateImageConfig(newConfig) {
            if (!this.currentDomain || !this.currentConfig) return;

            const pc = this.getActivePresetConfig()
            if (pc) pc.image_extraction = newConfig;

            try {
                const presetName = this.getActivePresetName()
                const payload = { ...newConfig, preset_name: presetName }
                await this.apiRequest(`/api/sites/${this.currentDomain}/image-config`, {
                    method: 'PUT',
                    body: JSON.stringify(payload)
                });
                this.notify('图片配置已保存', 'success');
            } catch (error) {
                console.error('保存图片配置失败:', error);
                this.notify('保存图片配置失败: ' + error.message, 'error');
            }
        },

        // 🆕 重新加载当前站点配置（应用预设后调用）
        async reloadConfig() {
            if (!this.currentDomain) return;

            try {
                const data = await this.apiRequest('/api/config/' + encodeURIComponent(this.currentDomain));
                // 返回的数据已经是预设格式 { presets: { ... } }
                // 对其进行规范化确保结构完整
                const normalized = this.normalizeConfig({ [this.currentDomain]: data })
                if (normalized[this.currentDomain]) {
                    this.sites[this.currentDomain] = normalized[this.currentDomain]
                }
                this.notify('配置已重新加载', 'success');
            } catch (error) {
                console.error('重新加载配置失败:', error);
                this.notify('加载失败: ' + error.message, 'error');
            }
        },
        // ========== 日志相关 ==========

        async pollLogs() {
            if (this.pauseLogs) return;

            try {
                const result = await this.apiRequest('/api/logs?after_seq=' + this.lastLogSeq);

                if (result.logs && result.logs.length > 0) {
                    result.logs.forEach(log => {
                        const messageText = log.message_text || log.display_message || log.message || '';
                        const kind = log.kind || log.level;
                        this.logs.push({
                            id: log.seq || (Date.now() + Math.random()),
                            seq: log.seq || 0,
                            timestamp: new Date(log.timestamp * 1000).toLocaleTimeString() + '.' +
                                String(Math.floor((log.timestamp % 1) * 1000)).padStart(3, '0'),
                            level: this.normalizeLogLevel(kind, messageText),
                            rawLevel: String(log.level || '').toUpperCase(),
                            kind: String(kind || '').toUpperCase(),
                            logger: log.logger || '',
                            requestId: log.request_id || 'SYSTEM',
                            message: log.display_message || log.message || messageText,
                            messageText,
                            originalMessageText: log.original_message_text || messageText,
                            messageAlias: log.message_alias || ''
                        });
                    });

                    if (this.logs.length > 500) {
                        this.logs = this.logs.slice(-500);
                    }

                    this.$nextTick(() => {
                        if (this.$refs.logContainer) {
                            this.$refs.logContainer.scrollTop = this.$refs.logContainer.scrollHeight;
                        }
                    });
                }
                this.lastLogSeq = Number(result.next_seq || this.lastLogSeq || 0);
                this.lastLogTimestamp = Number(result.timestamp || this.lastLogTimestamp || 0);
            } catch (error) {
                console.debug('日志轮询失败:', error.message);
            }
        },

        normalizeLogLevel(level, message) {
            const normalized = String(level || '').toUpperCase();
            if (normalized === 'WARNING') return 'WARN';
            if (normalized === 'CRITICAL') return 'ERROR';
            if (normalized === 'SUCCESS') return 'OK';
            if (normalized === 'DEBUG' || normalized === 'WARN' || normalized === 'ERROR') {
                return normalized;
            }

            if (normalized === 'INFO') {
                if (message.includes('[AI]')) return 'AI';
                if (message.includes('[OK]') || message.includes('[SUCCESS]') || message.includes('✅')) return 'OK';
                return 'INFO';
            }

            if (message.includes('[AI]')) return 'AI';
            if (message.includes('[ERROR]')) return 'ERROR';
            if (message.includes('[WARN]') || message.includes('[WARNING]')) return 'WARN';
            if (message.includes('[OK]') || message.includes('[SUCCESS]') || message.includes('✅')) return 'OK';
            return 'INFO';
        },

        getLogColorClass(level) {
            const colors = {
                'INFO': 'bg-green-50 dark:bg-green-900/20',
                'AI': 'bg-purple-50 dark:bg-purple-900/20',
                'OK': 'bg-green-50 dark:bg-green-900/20',
                'WARN': 'bg-yellow-50 dark:bg-yellow-900/20',
                'ERROR': 'bg-red-50 dark:bg-red-900/20',
                'KEY': 'bg-sky-50 dark:bg-sky-900/20'
            };
            return colors[level] || colors['INFO'];
        },

        getLogLevelClass(level) {
            const colors = {
                'INFO': 'text-green-600 dark:text-green-400',
                'AI': 'text-purple-600 dark:text-purple-400',
                'OK': 'text-green-600 dark:text-green-400',
                'WARN': 'text-yellow-600 dark:text-yellow-400',
                'ERROR': 'text-red-600 dark:text-red-400',
                'KEY': 'text-sky-500 dark:text-sky-300'
            };
            return colors[level] || colors['INFO'];
        },

        clearLogs() {
            if (confirm('确定清除所有日志吗？')) {
                this.logs = [];

                this.apiRequest('/api/logs', { method: 'DELETE' })
                    .catch(() => { });

                this.notify('日志已清除', 'success');
            }
        },

        // ========== 导入功能（支持全量和单站点） ==========

        triggerImport() {
            this.$refs.importFileInput.click();
        },

        handleImportFile(event) {
            const file = event.target.files[0];
            if (!file) return;

            this.importFileName = file.name;

            const reader = new FileReader();
            reader.onload = (e) => {
                try {
                    const config = JSON.parse(e.target.result);

                    // 检测是单站点还是全量配置
                    const detectResult = this.detectConfigType(config);

                    if (!detectResult.valid) {
                        this.notify('导入文件格式无效', 'error');
                        return;
                    }

                    this.importType = detectResult.type;
                    this.importedConfig = detectResult.normalizedConfig;
                    this.singleSiteImportDomain = detectResult.suggestedDomain || '';
                    this.showImportDialog = true;
                } catch (error) {
                    this.notify('JSON 解析失败: ' + error.message, 'error');
                }
            };
            reader.readAsText(file);

            event.target.value = '';
        },

        // 检测配置类型：全量配置 or 单站点配置
        detectConfigType(config) {
            if (typeof config !== 'object' || config === null || Array.isArray(config)) {
                return { valid: false };
            }

            // 检查是否是单站点格式（旧格式 selectors/workflow，或新格式 presets/default_preset）
            if (
                config.selectors !== undefined
                || config.workflow !== undefined
                || (config.presets && typeof config.presets === 'object' && !Array.isArray(config.presets))
            ) {
                // 单站点格式
                if (!this.validateSingleSiteConfig(config)) {
                    return { valid: false };
                }

                // 尝试从文件名提取域名
                let suggestedDomain = '';
                const match = this.importFileName.match(/^(.+?)(?:-config)?(?:-\d+)?\.json$/i);
                if (match) {
                    suggestedDomain = match[1];
                }

                return {
                    valid: true,
                    type: 'single',
                    normalizedConfig: config,
                    suggestedDomain: suggestedDomain
                };
            }

            // 检查是否是全量格式（域名 -> 配置）
            if (!this.validateImportedConfig(config)) {
                return { valid: false };
            }

            return {
                valid: true,
                type: 'full',
                normalizedConfig: config
            };
        },

        validateSingleSiteConfig(config) {
            if (typeof config !== 'object' || config === null || Array.isArray(config)) {
                return false;
            }

            if (config.presets !== undefined) {
                if (typeof config.presets !== 'object' || config.presets === null || Array.isArray(config.presets)) {
                    return false;
                }

                for (const presetData of Object.values(config.presets)) {
                    if (typeof presetData !== 'object' || presetData === null || Array.isArray(presetData)) {
                        return false;
                    }

                    if (presetData.selectors !== undefined && (typeof presetData.selectors !== 'object' || Array.isArray(presetData.selectors))) {
                        return false;
                    }

                    if (presetData.workflow !== undefined && !Array.isArray(presetData.workflow)) {
                        return false;
                    }
                }

                return true;
            }

            // selectors 必须是对象（如果存在）
            if (config.selectors !== undefined && (typeof config.selectors !== 'object' || Array.isArray(config.selectors))) {
                return false;
            }

            // workflow 必须是数组（如果存在）
            if (config.workflow !== undefined && !Array.isArray(config.workflow)) {
                return false;
            }

            return true;
        },

        validateImportedConfig(config) {
            if (typeof config !== 'object' || config === null || Array.isArray(config)) {
                return false;
            }

            for (const [domain, siteConfig] of Object.entries(config)) {
                if (!domain || typeof domain !== 'string') {
                    return false;
                }

                if (!this.validateSingleSiteConfig(siteConfig)) {
                    return false;
                }
            }

            return true;
        },

        mergeSiteConfigs(existingSite, importedSite) {
            const normalizedImported = this.normalizeConfig({ imported: importedSite || {} }).imported
            if (!normalizedImported) {
                return existingSite || null
            }

            if (!existingSite) {
                return normalizedImported
            }

            const normalizedExisting = this.normalizeConfig({ existing: existingSite }).existing || {
                default_preset: '主预设',
                presets: {}
            }

            const mergedPresets = {
                ...(normalizedExisting.presets || {}),
                ...(normalizedImported.presets || {})
            }

            let mergedDefault = normalizedImported.default_preset
            if (!mergedDefault || !mergedPresets[mergedDefault]) {
                mergedDefault = normalizedExisting.default_preset
            }
            if (!mergedDefault || !mergedPresets[mergedDefault]) {
                mergedDefault = mergedPresets['主预设'] ? '主预设' : (Object.keys(mergedPresets)[0] || '主预设')
            }

            return {
                ...normalizedExisting,
                ...normalizedImported,
                presets: mergedPresets,
                default_preset: mergedDefault
            }
        },

        async executeImport() {
            if (!this.importedConfig) return;

            if (this.importType === 'single') {
                // 单站点导入
                const domain = this.singleSiteImportDomain.trim();
                if (!domain) {
                    this.notify('请输入站点域名', 'warning');
                    return;
                }

                const normalizedMap = this.normalizeConfig({ [domain]: this.importedConfig });
                const normalizedSite = normalizedMap[domain];
                if (!normalizedSite) {
                    this.notify('导入文件格式无效', 'error');
                    return;
                }

                const exists = !!this.sites[domain];
                if (exists) {
                    const message = this.importMode === 'replace'
                        ? '站点 "' + domain + '" 已存在，将完整替换该站点的当前配置，是否继续？'
                        : '站点 "' + domain + '" 已存在，将按预设合并导入，同名预设会被覆盖，是否继续？';
                    if (!confirm(message)) {
                        return;
                    }
                }

                this.sites[domain] = this.importMode === 'replace'
                    ? normalizedSite
                    : this.mergeSiteConfigs(this.sites[domain], normalizedSite);
                this.currentDomain = domain;

                try {
                    await this.apiRequest('/api/config', {
                        method: 'POST',
                        body: JSON.stringify({ config: this.sites })
                    });

                    this.notify('成功导入站点: ' + domain, 'success');
                } catch (error) {
                    this.notify('保存失败: ' + error.message, 'error');
                }
            } else {
                // 全量导入
                const importCount = Object.keys(this.importedConfig).length;

                if (this.importMode === 'replace') {
                    this.sites = this.normalizeConfig(this.importedConfig);
                } else {
                    const normalized = this.normalizeConfig(this.importedConfig);
                    this.sites = { ...this.sites, ...normalized };
                }

                try {
                    await this.apiRequest('/api/config', {
                        method: 'POST',
                        body: JSON.stringify({ config: this.sites })
                    });

                    this.notify('成功导入 ' + importCount + ' 个站点配置', 'success');
                } catch (error) {
                    this.notify('保存失败: ' + error.message, 'error');
                }

                if (!this.currentDomain && Object.keys(this.sites).length > 0) {
                    this.currentDomain = Object.keys(this.sites)[0];
                }
            }

            // 清理
            this.showImportDialog = false;
            this.importedConfig = null;
            this.importFileName = '';
            this.singleSiteImportDomain = '';
        },

        cancelImport() {
            this.showImportDialog = false;
            this.importedConfig = null;
            this.importFileName = '';
            this.singleSiteImportDomain = '';
        },

        // ========== 导出功能（支持全量和单站点） ==========

        exportConfig() {
            const dataStr = JSON.stringify(this.sites, null, 2)
            const blob = new Blob([dataStr], { type: 'application/json' })
            const url = URL.createObjectURL(blob)
            const a = document.createElement('a')
            a.href = url
            a.download = 'sites-config-' + Date.now() + '.json'
            a.click()
            URL.revokeObjectURL(url)

            this.notify('全量配置已导出', 'success')
        },

        // 导出单个站点
        exportSingleSite(domain) {
            if (!domain || !this.sites[domain]) {
                this.notify('站点不存在', 'error');
                return;
            }

            // 导出整个站点（含所有预设）
            const siteConfig = this.sites[domain];
            const dataStr = JSON.stringify(siteConfig, null, 2);
            const blob = new Blob([dataStr], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = domain + '-config.json';
            a.click();
            URL.revokeObjectURL(url);

            this.notify('站点配置已导出: ' + domain, 'success');
        },

        // 导出当前站点
        exportCurrentSite() {
            if (!this.currentDomain) {
                this.notify('请先选择站点', 'warning');
                return;
            }
            this.exportSingleSite(this.currentDomain);
        },

        triggerSettingsBackupImport() {
            if (this.$refs.backupImportInput) {
                this.$refs.backupImportInput.click();
            }
        },

        handleSettingsBackupImportFile(event) {
            const file = event.target.files[0];
            if (!file) return;

            const reader = new FileReader();
            reader.onload = async (e) => {
                try {
                    const payload = JSON.parse(e.target.result);
                    await this.importSettingsBackup(payload);
                } catch (error) {
                    this.notify('完整备份导入失败: ' + error.message, 'error');
                }
            };
            reader.readAsText(file, 'utf-8');

            event.target.value = '';
        },

        getDashboardPreferencesBackup() {
            let apiToken = '';
            try {
                apiToken = localStorage.getItem('api_token') || '';
            } catch (e) {
                apiToken = '';
            }

            return {
                dark_mode: !!this.darkMode,
                api_token: apiToken
            };
        },

        applyDashboardPreferencesBackup(preferences) {
            if (!preferences || typeof preferences !== 'object') return;

            if (typeof preferences.dark_mode === 'boolean') {
                this.darkMode = preferences.dark_mode;
            }

            if (typeof preferences.api_token === 'string') {
                const token = preferences.api_token.trim();
                try {
                    if (token) {
                        localStorage.setItem('api_token', token);
                    } else {
                        localStorage.removeItem('api_token');
                    }
                } catch (e) { }
                this.tempToken = token;
            }
        },

        async exportSettingsBackup() {
            try {
                const payload = await this.apiRequest('/api/settings/backup');
                const exportPayload = {
                    ...payload,
                    dashboard_preferences: this.getDashboardPreferencesBackup()
                };
                const dataStr = JSON.stringify(exportPayload, null, 2);
                const blob = new Blob([dataStr], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'settings-backup-' + Date.now() + '.json';
                a.click();
                URL.revokeObjectURL(url);

                this.notify('完整配置备份已导出', 'success');
            } catch (error) {
                this.notify('完整备份导出失败: ' + error.message, 'error');
            }
        },

        async importSettingsBackup(payload) {
            if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
                throw new Error('备份文件格式无效');
            }

            const result = await this.apiRequest('/api/settings/backup', {
                method: 'POST',
                body: JSON.stringify(payload)
            });

            this.applyDashboardPreferencesBackup(payload.dashboard_preferences);

            if (!result.will_restart) {
                await Promise.all([
                    this.loadConfig(true),
                    this.loadEnvConfig(),
                    this.loadBrowserConstants(),
                    this.loadUpdatePreserveSettings(),
                    this.loadSelectorDefinitions()
                ]);
            }

            const sections = Array.isArray(result.imported_sections)
                ? result.imported_sections.join('、')
                : '';
            this.notify(
                result.will_restart
                    ? '完整备份已导入，服务将自动重启' + (sections ? '：' + sections : '')
                    : '完整备份已导入' + (sections ? '：' + sections : ''),
                result.will_restart ? 'warning' : 'success'
            );
        },

        // ========== 环境配置 ==========

        async loadEnvConfig() {
            this.isLoadingEnv = true;
            try {
                const data = await this.apiRequest('/api/settings/env');
                this.envConfig = {
                    ...this.getEnvDefaults(),
                    ...(data.config || {})
                };
                this.envConfigOriginal = JSON.parse(JSON.stringify(this.envConfig));
            } catch (error) {
                console.error('加载环境配置失败:', error);
                this.envConfig = this.getEnvDefaults();
                this.envConfigOriginal = JSON.parse(JSON.stringify(this.envConfig));
            } finally {
                this.isLoadingEnv = false;
            }
        },

        getEnvDefaults() {
            const defaults = {};
            for (const group of Object.values(ENV_CONFIG_SCHEMA)) {
                for (const [key, field] of Object.entries(group.items)) {
                    defaults[key] = field.default;
                }
            }
            return defaults;
        },

        normalizeEnvCompareValue(value) {
            if (value === undefined || value === null) return '';
            if (typeof value === 'boolean') return value ? 'true' : 'false';
            return String(value);
        },

        getEnvFieldMeta(fieldKey) {
            for (const group of Object.values(ENV_CONFIG_SCHEMA)) {
                if (!group || !group.items || !Object.prototype.hasOwnProperty.call(group.items, fieldKey)) {
                    continue;
                }

                const field = group.items[fieldKey] || {};
                return {
                    ...field,
                    apply: field.apply || group.apply || 'service'
                };
            }

            return null;
        },

        getEnvChangedKeys() {
            const current = this.envConfig || {};
            const original = this.envConfigOriginal || {};
            const keys = new Set([
                ...Object.keys(current),
                ...Object.keys(original)
            ]);

            return Array.from(keys).filter((key) => {
                return this.normalizeEnvCompareValue(current[key]) !== this.normalizeEnvCompareValue(original[key]);
            });
        },

        async saveEnvConfig() {
            this.isSavingEnv = true;
            try {
                const changedKeys = this.getEnvChangedKeys();
                await this.apiRequest('/api/settings/env', {
                    method: 'POST',
                    body: JSON.stringify({ config: this.envConfig })
                });

                this.envConfigOriginal = JSON.parse(JSON.stringify(this.envConfig));
                const launcherKeys = changedKeys.filter((key) => {
                    return (this.getEnvFieldMeta(key)?.apply || 'service') === 'launcher';
                });

                if (launcherKeys.length > 0) {
                    const launcherLabels = launcherKeys.map((key) => {
                        return this.getEnvFieldMeta(key)?.label || key;
                    }).join(', ');

                    this.notify(
                        '环境配置已保存。服务会自动重启，但以下启动型配置要完全生效，请关闭当前浏览器和脚本后重新运行 start.bat：' + launcherLabels,
                        'warning'
                    );
                } else {
                    this.notify('环境配置已保存，服务将自动重启后生效', 'success');
                }
            } catch (error) {
                this.notify('保存失败: ' + error.message, 'error');
            } finally {
                this.isSavingEnv = false;
            }
        },

        resetEnvConfig() {
            if (!confirm('确定要重置环境配置为默认值吗？')) return;

            this.envConfig = this.getEnvDefaults();
            this.notify('已重置为默认值，请点击保存以应用', 'info');
        },

        // ========== 浏览器常量 ==========

        normalizeBrowserConstantsForEditor(rawConfig = {}) {
            const raw = rawConfig && typeof rawConfig === 'object' ? rawConfig : {};
            const normalized = {};

            for (const group of Object.values(BROWSER_CONSTANTS_SCHEMA)) {
                for (const [key, field] of Object.entries(group.items || {})) {
                    normalized[key] = field.default;
                }
            }

            for (const key of Object.keys(normalized)) {
                if (key.startsWith('TAB_POOL_')) {
                    continue;
                }
                if (Object.prototype.hasOwnProperty.call(raw, key)) {
                    normalized[key] = raw[key];
                }
            }

            const tabPool = raw.tab_pool && typeof raw.tab_pool === 'object' ? raw.tab_pool : {};
            normalized.TAB_POOL_MAX_TABS = raw.TAB_POOL_MAX_TABS ?? tabPool.max_tabs ?? normalized.TAB_POOL_MAX_TABS;
            normalized.TAB_POOL_MIN_TABS = raw.TAB_POOL_MIN_TABS ?? tabPool.min_tabs ?? normalized.TAB_POOL_MIN_TABS;
            normalized.TAB_POOL_IDLE_TIMEOUT = raw.TAB_POOL_IDLE_TIMEOUT ?? tabPool.idle_timeout ?? normalized.TAB_POOL_IDLE_TIMEOUT;
            normalized.TAB_POOL_ACQUIRE_TIMEOUT = raw.TAB_POOL_ACQUIRE_TIMEOUT ?? tabPool.acquire_timeout ?? normalized.TAB_POOL_ACQUIRE_TIMEOUT;
            normalized.TAB_POOL_STUCK_TIMEOUT = raw.TAB_POOL_STUCK_TIMEOUT ?? tabPool.stuck_timeout ?? normalized.TAB_POOL_STUCK_TIMEOUT;

            return normalized;
        },

        serializeBrowserConstants(editorConfig = {}, rawBase = {}) {
            const base = rawBase && typeof rawBase === 'object'
                ? JSON.parse(JSON.stringify(rawBase))
                : {};
            const merged = this.normalizeBrowserConstantsForEditor(editorConfig);
            const obsoleteKeys = [
                'DEFAULT_PORT',
                'STREAM_RERENDER_WAIT',
                'STREAM_MIN_VALID_LENGTH',
                'STREAM_INITIAL_ELEMENT_WAIT',
                'STREAM_MAX_ABNORMAL_COUNT',
                'STREAM_MAX_ELEMENT_MISSING',
                'STREAM_CONTENT_SHRINK_THRESHOLD'
            ];

            for (const key of obsoleteKeys) {
                delete base[key];
            }

            for (const key of Object.keys(merged)) {
                if (key.startsWith('TAB_POOL_')) {
                    continue;
                }
                base[key] = merged[key];
            }

            const existingTabPool = base.tab_pool && typeof base.tab_pool === 'object' ? base.tab_pool : {};
            base.tab_pool = {
                ...existingTabPool,
                max_tabs: merged.TAB_POOL_MAX_TABS,
                min_tabs: merged.TAB_POOL_MIN_TABS,
                idle_timeout: merged.TAB_POOL_IDLE_TIMEOUT,
                acquire_timeout: merged.TAB_POOL_ACQUIRE_TIMEOUT,
                stuck_timeout: merged.TAB_POOL_STUCK_TIMEOUT
            };

            return base;
        },

        async loadBrowserConstants() {
            this.isLoadingConstants = true;
            try {
                const data = await this.apiRequest('/api/settings/browser-constants');
                this.browserConstantsRaw = JSON.parse(JSON.stringify(data.config || {}));
                this.browserConstants = this.normalizeBrowserConstantsForEditor(this.browserConstantsRaw);
                this.browserConstantsOriginal = JSON.parse(JSON.stringify(this.browserConstants));
            } catch (error) {
                console.error('加载浏览器常量失败:', error);
                this.browserConstants = this.getBrowserConstantsDefaults();
                this.browserConstantsRaw = this.serializeBrowserConstants(this.browserConstants, {});
                this.browserConstantsOriginal = JSON.parse(JSON.stringify(this.browserConstants));
            } finally {
                this.isLoadingConstants = false;
            }
        },

        getBrowserConstantsDefaults() {
            return this.normalizeBrowserConstantsForEditor({});
        },

        async saveBrowserConstants() {
            this.isSavingConstants = true;
            try {
                const payload = this.serializeBrowserConstants(this.browserConstants, this.browserConstantsRaw);
                await this.apiRequest('/api/settings/browser-constants', {
                    method: 'POST',
                    body: JSON.stringify({ config: payload })
                });

                this.browserConstantsRaw = JSON.parse(JSON.stringify(payload));
                this.browserConstants = this.normalizeBrowserConstantsForEditor(payload);
                this.browserConstantsOriginal = JSON.parse(JSON.stringify(this.browserConstants));
                this.notify('浏览器常量已保存', 'success');
            } catch (error) {
                this.notify('保存失败: ' + error.message, 'error');
            } finally {
                this.isSavingConstants = false;
            }
        },

        resetBrowserConstants() {
            if (!confirm('确定要重置浏览器常量为默认值吗？')) return;

            this.browserConstants = this.getBrowserConstantsDefaults();
            this.notify('已重置为默认值，请点击保存以应用', 'info');
        },

        // ========== 更新白名单 ==========

        async loadUpdatePreserveSettings() {
            this.isLoadingUpdatePreserve = true;
            try {
                const data = await this.apiRequest('/api/settings/update-preserve');
                this.updatePreserveOptions = Array.isArray(data.options) ? data.options : [];
                this.updatePreserveSelected = Array.isArray(data.selected_patterns) ? data.selected_patterns.slice() : [];
                this.updatePreserveSelectedOriginal = JSON.parse(JSON.stringify(this.updatePreserveSelected));
            } catch (error) {
                console.error('加载更新白名单失败:', error);
                this.updatePreserveOptions = [];
                this.updatePreserveSelected = [];
                this.updatePreserveSelectedOriginal = [];
            } finally {
                this.isLoadingUpdatePreserve = false;
            }
        },

        toggleUpdatePreserve(pattern) {
            const value = String(pattern || '').trim();
            if (!value) return;
            const next = new Set(this.updatePreserveSelected || []);
            if (next.has(value)) {
                next.delete(value);
            } else {
                next.add(value);
            }
            this.updatePreserveSelected = Array.from(next);
        },

        async saveUpdatePreserveSettings() {
            this.isSavingUpdatePreserve = true;
            try {
                const data = await this.apiRequest('/api/settings/update-preserve', {
                    method: 'POST',
                    body: JSON.stringify({
                        selected_patterns: this.updatePreserveSelected
                    })
                });
                this.updatePreserveSelected = Array.isArray(data.selected_patterns)
                    ? data.selected_patterns.slice()
                    : this.updatePreserveSelected;
                this.updatePreserveSelectedOriginal = JSON.parse(JSON.stringify(this.updatePreserveSelected));
                this.notify('更新白名单已保存，下次自动更新生效', 'success');
            } catch (error) {
                this.notify('保存失败: ' + error.message, 'error');
            } finally {
                this.isSavingUpdatePreserve = false;
            }
        },

        resetUpdatePreserveSettings() {
            this.updatePreserveSelected = JSON.parse(JSON.stringify(this.updatePreserveSelectedOriginal));
            this.notify('已恢复到上次保存的更新白名单', 'info');
        },

        // ========== 元素定义管理方法 ==========

        async loadSelectorDefinitions() {
            this.isLoadingDefinitions = true;
            try {
                const data = await this.apiRequest('/api/settings/selector-definitions');
                this.selectorDefinitions = data.definitions || DEFAULT_SELECTOR_DEFINITIONS;
                this.selectorDefinitionsOriginal = JSON.parse(JSON.stringify(this.selectorDefinitions));
            } catch (error) {
                console.error('加载元素定义失败:', error);
                this.selectorDefinitions = JSON.parse(JSON.stringify(DEFAULT_SELECTOR_DEFINITIONS));
                this.selectorDefinitionsOriginal = JSON.parse(JSON.stringify(this.selectorDefinitions));
            } finally {
                this.isLoadingDefinitions = false;
            }
        },

        async saveSelectorDefinitions() {
            this.isSavingDefinitions = true;
            try {
                await this.apiRequest('/api/settings/selector-definitions', {
                    method: 'POST',
                    body: JSON.stringify({ definitions: this.selectorDefinitions })
                });

                this.selectorDefinitionsOriginal = JSON.parse(JSON.stringify(this.selectorDefinitions));
                this.notify('元素定义已保存', 'success');
            } catch (error) {
                this.notify('保存失败: ' + error.message, 'error');
            } finally {
                this.isSavingDefinitions = false;
            }
        },

        async resetSelectorDefinitions() {
            if (!confirm('确定要重置元素定义为默认值吗？')) return;

            try {
                const data = await this.apiRequest('/api/settings/selector-definitions/reset', {
                    method: 'POST'
                });

                this.selectorDefinitions = data.definitions;
                this.selectorDefinitionsOriginal = JSON.parse(JSON.stringify(this.selectorDefinitions));
                this.notify('已重置为默认值', 'success');
            } catch (error) {
                this.notify('重置失败: ' + error.message, 'error');
            }
        },

        toggleDefinitionEnabled(index) {
            const def = this.selectorDefinitions[index];

            if (def.required) {
                this.notify('必需字段不能禁用', 'warning');
                return;
            }

            def.enabled = !def.enabled;
        },

        openAddDefinitionDialog() {
            this.newDefinition = {
                key: '',
                description: '',
                enabled: true,
                required: false
            };
            this.editingDefinitionIndex = null;
            this.showAddDefinitionDialog = true;
        },

        openEditDefinitionDialog(index) {
            const def = this.selectorDefinitions[index];
            this.newDefinition = { ...def };
            this.editingDefinitionIndex = index;
            this.showAddDefinitionDialog = true;
        },

        saveDefinition() {
            if (!this.newDefinition.key.trim()) {
                this.notify('请输入关键词', 'warning');
                return;
            }

            if (!this.newDefinition.description.trim()) {
                this.notify('请输入描述', 'warning');
                return;
            }

            const key = this.newDefinition.key.trim();
            const existingIndex = this.selectorDefinitions.findIndex(d => d.key === key);

            if (this.editingDefinitionIndex === null) {
                // 新增模式
                if (existingIndex !== -1) {
                    this.notify('关键词已存在', 'error');
                    return;
                }

                this.selectorDefinitions.push({
                    key: key,
                    description: this.newDefinition.description.trim(),
                    enabled: this.newDefinition.enabled,
                    required: false
                });
            } else {
                // 编辑模式
                if (existingIndex !== -1 && existingIndex !== this.editingDefinitionIndex) {
                    this.notify('关键词已存在', 'error');
                    return;
                }

                this.selectorDefinitions[this.editingDefinitionIndex] = {
                    ...this.selectorDefinitions[this.editingDefinitionIndex],
                    key: key,
                    description: this.newDefinition.description.trim(),
                    enabled: this.newDefinition.enabled
                };
            }

            this.showAddDefinitionDialog = false;
            this.notify('已添加，请点击保存以应用', 'info');
        },

        removeDefinition(index) {
            const def = this.selectorDefinitions[index];

            if (def.required) {
                this.notify('必需字段不能删除', 'warning');
                return;
            }

            if (!confirm('确定要删除 "' + def.key + '" 吗？')) return;

            this.selectorDefinitions.splice(index, 1);
            this.notify('已删除，请点击保存以应用', 'info');
        },

        moveDefinition(index, direction) {
            const newIndex = index + direction;
            if (newIndex < 0 || newIndex >= this.selectorDefinitions.length) return;

            const temp = this.selectorDefinitions[index];
            this.selectorDefinitions[index] = this.selectorDefinitions[newIndex];
            this.selectorDefinitions[newIndex] = temp;
        },

        changeTab(tab) {
            if (tab === 'extractors') {
                tab = 'config';
            }
            this.activeTab = tab;
        },

        async ensureTabDataLoaded(tab) {
            if (tab === 'marketplace' && !this.hasLoadedMarketplace) {
                this.hasLoadedMarketplace = true;
                await this.loadMarketplaceCatalog({ silent: true });
                return;
            }
            if (tab === 'settings' && !this.hasLoadedSettings) {
                this.hasLoadedSettings = true;
                await Promise.all([
                    this.loadEnvConfig(),
                    this.loadBrowserConstants(),
                    this.loadUpdatePreserveSettings(),
                    this.loadSelectorDefinitions()
                ]);
                return;
            }
        },

        async loadMarketplaceCatalog({ silent = false, force = false } = {}) {
            this.marketplaceLoading = true;
            this.marketplaceError = '';
            try {
                const suffix = force ? '?refresh=true' : '';
                const url = this.appendMarketplaceClientVersion('/api/marketplace' + suffix);
                const data = await this.apiRequest(url);
                this.marketplaceCatalog = {
                    items: [],
                    count: 0,
                    total_downloads: 0,
                    default_sort: 'downloads',
                    source_mode: 'local',
                    source_name: '配置市场',
                    source_url: '',
                    upload_url: '',
                    warning: '',
                    ...(data || {})
                };
                if (!silent) {
                    this.notify('插件市场已刷新', 'success');
                }
                return true;
            } catch (error) {
                this.marketplaceError = error.message;
                if (!silent) {
                    this.notify('加载插件市场失败: ' + error.message, 'error');
                }
                return false;
            } finally {
                this.marketplaceLoading = false;
            }
        },

        openMarketplace() {
            window.location.href = '/static/marketplace.html';
        },

        openExternalLink(url) {
            const target = String(url || '').trim();
            if (!target) {
                this.notify('当前项目没有可打开的外部链接', 'warning');
                return;
            }
            window.open(target, '_blank', 'noopener,noreferrer');
        },

        async previewMarketplaceItem(item) {
            if (!item || !item.id) {
                return;
            }
            try {
                const url = this.appendMarketplaceClientVersion('/api/marketplace/items/' + encodeURIComponent(item.id));
                const detail = await this.apiRequest(url);
                this.marketplacePreviewTitle = '市场预览 · ' + (detail.name || item.name || item.id);
                this.marketplacePreviewData = detail;
                this.showMarketplacePreview = true;
            } catch (error) {
                this.notify('加载预览失败: ' + error.message, 'error');
            }
        },

        copyMarketplacePreview() {
            const payload = JSON.stringify(this.marketplacePreviewData || {}, null, 2);
            navigator.clipboard.writeText(payload)
                .then(() => this.notify('预览内容已复制', 'success'))
                .catch(() => this.notify('复制失败', 'error'));
        },

        saveMarketplacePreview() {
            const payload = JSON.stringify(this.marketplacePreviewData || {}, null, 2);
            this.downloadDataAsJson('marketplace-preview-' + Date.now() + '.json', payload);
            this.notify('预览文件已导出', 'success');
        },

        openMarketplaceSubmitDialog() {
            this.resetMarketplaceSubmitForm();
            this.showMarketplaceSubmitDialog = true;
        },

        resetMarketplaceSubmitForm() {
            this.marketplaceSubmitForm = {
                item_type: 'site_config',
                title: this.currentDomain ? (this.currentDomain + ' 配置投稿') : '',
                summary: '',
                author: '本地投稿',
                site_domain: this.currentDomain || '',
                category: this.currentDomain || '',
                preset_name: this.getActivePresetName(),
                compatibility: '',
                version: '1.0.0',
                tagsText: '',
                selected_command_ids: [],
                parser_id: '',
                parser_class_name: '',
                parser_module_name: '',
                parser_source: '',
                parser_patterns_text: '',
                parser_filename: ''
            };
        },

        async setMarketplaceSubmitType(itemType) {
            this.marketplaceSubmitForm.item_type = itemType;
            if (itemType === 'command_bundle') {
                if (!this.marketplaceCommandOptions.length) {
                    await this.loadMarketplaceCommandOptions();
                }
                this.marketplaceSubmitForm.title = this.marketplaceSubmitForm.title || '命令包投稿';
                this.marketplaceSubmitForm.category = '命令系统';
            } else if (itemType === 'response_parser') {
                if (!this.marketplaceSubmitForm.category
                    || this.marketplaceSubmitForm.category === '命令系统'
                    || this.marketplaceSubmitForm.category === this.marketplaceSubmitForm.site_domain
                    || this.marketplaceSubmitForm.category === this.currentDomain) {
                    this.marketplaceSubmitForm.category = '响应解析器';
                }
            } else {
                this.marketplaceSubmitForm.site_domain = this.marketplaceSubmitForm.site_domain || this.currentDomain || '';
                this.marketplaceSubmitForm.category = this.marketplaceSubmitForm.category || this.marketplaceSubmitForm.site_domain;
                this.marketplaceSubmitForm.preset_name = this.marketplaceSubmitForm.preset_name || this.getActivePresetName();
            }
        },

        async loadMarketplaceCommandOptions() {
            this.marketplaceCommandLoading = true;
            try {
                const data = await this.apiRequest('/api/commands');
                const commands = Array.isArray(data.commands) ? data.commands : [];
                commands.sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'zh-CN'));
                this.marketplaceCommandOptions = commands;
                return commands;
            } catch (error) {
                this.notify('加载命令列表失败: ' + error.message, 'error');
                return [];
            } finally {
                this.marketplaceCommandLoading = false;
            }
        },

        parseMarketplaceTags(text) {
            return String(text || '')
                .split(/[,\n，]/)
                .map(item => item.trim())
                .filter(Boolean);
        },

        normalizeMarketplaceParserModuleName(value) {
            const normalized = String(value || '')
                .replace(/\.py$/i, '')
                .trim()
                .replace(/[^A-Za-z0-9_]+/g, '_')
                .replace(/^_+|_+$/g, '');
            return normalized || '';
        },

        normalizeMarketplaceParserId(value) {
            const normalized = String(value || '')
                .trim()
                .replace(/[^A-Za-z0-9._-]+/g, '_')
                .replace(/^_+|_+$/g, '');
            return normalized || '';
        },

        extractMarketplaceParserClassName(sourceText) {
            const text = String(sourceText || '');
            const parserMatch = text.match(/class\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*ResponseParser\b/);
            if (parserMatch) {
                return parserMatch[1];
            }
            const anyClassMatch = text.match(/class\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(/);
            return anyClassMatch ? anyClassMatch[1] : '';
        },

        async handleMarketplaceParserFileChange(event) {
            const file = event && event.target && event.target.files && event.target.files[0];
            if (!file) {
                return;
            }

            try {
                const source = await file.text();
                const moduleName = this.normalizeMarketplaceParserModuleName(file.name);
                const parserId = this.normalizeMarketplaceParserId(moduleName.replace(/_parser$/i, ''));
                const className = this.extractMarketplaceParserClassName(source);
                const defaultTitle = this.currentDomain ? (this.currentDomain + ' 配置投稿') : '';

                this.marketplaceSubmitForm.parser_filename = file.name;
                this.marketplaceSubmitForm.parser_source = source;
                if (!this.marketplaceSubmitForm.parser_module_name) {
                    this.marketplaceSubmitForm.parser_module_name = moduleName;
                }
                if (!this.marketplaceSubmitForm.parser_id) {
                    this.marketplaceSubmitForm.parser_id = parserId;
                }
                if (!this.marketplaceSubmitForm.parser_class_name) {
                    this.marketplaceSubmitForm.parser_class_name = className;
                }
                if (!this.marketplaceSubmitForm.title || this.marketplaceSubmitForm.title === defaultTitle) {
                    this.marketplaceSubmitForm.title = className || parserId || moduleName || file.name.replace(/\.py$/i, '');
                }
            } catch (error) {
                this.notify('读取解析器文件失败: ' + error.message, 'error');
            } finally {
                if (event && event.target) {
                    event.target.value = '';
                }
            }
        },

        sanitizeCommandForBundle(command) {
            const cloned = JSON.parse(JSON.stringify(command || {}));
            delete cloned.last_triggered;
            delete cloned.trigger_count;
            return cloned;
        },

        getMarketplaceSelectedCommands() {
            const selectedIds = new Set(this.marketplaceSubmitForm.selected_command_ids || []);
            return (this.marketplaceCommandOptions || [])
                .filter(command => selectedIds.has(command.id))
                .map(command => this.sanitizeCommandForBundle(command));
        },

        buildMarketplaceSubmissionPayload() {
            const form = this.marketplaceSubmitForm || {};
            const title = String(form.title || '').trim();
            const summary = String(form.summary || '').trim();
            const author = String(form.author || '本地投稿').trim() || '本地投稿';
            const category = String(form.category || '').trim();
            const presetName = String(form.preset_name || this.getActivePresetName() || '主预设').trim() || '主预设';
            const compatibility = String(form.compatibility || '').trim();
            const version = String(form.version || '1.0.0').trim() || '1.0.0';
            const tags = this.parseMarketplaceTags(form.tagsText);

            if (!title) {
                throw new Error('请填写标题');
            }
            if (!summary) {
                throw new Error('请填写简介');
            }

            if (form.item_type === 'command_bundle') {
                const commands = this.getMarketplaceSelectedCommands();
                if (!commands.length) {
                    throw new Error('请至少选择一个命令');
                }
                return {
                    item_type: 'command_bundle',
                    title,
                    summary,
                    author,
                    category: category || '命令系统',
                    compatibility,
                    version,
                    tags,
                    command_bundle: {
                        group_name: '',
                        commands
                    }
                };
            }

            if (form.item_type === 'response_parser') {
                const parserId = this.normalizeMarketplaceParserId(form.parser_id);
                const className = String(form.parser_class_name || '').trim();
                const moduleName = this.normalizeMarketplaceParserModuleName(form.parser_module_name);
                const sourceCode = String(form.parser_source || '');
                const supportedPatterns = this.parseMarketplaceTags(form.parser_patterns_text);

                if (!parserId) {
                    throw new Error('请填写解析器 ID');
                }
                if (!className) {
                    throw new Error('请填写解析器类名');
                }
                if (!moduleName) {
                    throw new Error('请填写解析器模块名');
                }
                if (!sourceCode.trim()) {
                    throw new Error('请上传或粘贴解析器源码');
                }

                return {
                    item_type: 'response_parser',
                    title,
                    summary,
                    author,
                    category: category || '响应解析器',
                    compatibility,
                    version,
                    tags,
                    parser_package: {
                        parser_id: parserId,
                        class_name: className,
                        module_name: moduleName,
                        filename: moduleName + '.py',
                        name: title,
                        description: summary,
                        source_code: sourceCode,
                        supported_patterns: supportedPatterns
                    }
                };
            }

            const siteDomain = String(form.site_domain || this.currentDomain || '').trim();
            if (!siteDomain) {
                throw new Error('请填写站点域名');
            }
            const presetConfig = this.getActivePresetConfig();
            if (!presetConfig) {
                throw new Error('当前没有可上传的站点配置');
            }

            return {
                item_type: 'site_config',
                title,
                summary,
                author,
                category: category || siteDomain,
                site_domain: siteDomain,
                preset_name: presetName,
                compatibility,
                version,
                tags,
                site_config: {
                    [siteDomain]: {
                        default_preset: presetName,
                        presets: {
                            [presetName]: JSON.parse(JSON.stringify(presetConfig))
                        }
                    }
                }
            };
        },

        getMarketplaceSubmissionPreviewText() {
            try {
                return JSON.stringify(this.buildMarketplaceSubmissionPayload(), null, 2);
            } catch (error) {
                return '// 预览暂不可用: ' + error.message;
            }
        },

        async submitMarketplaceItem() {
            let payload = null;
            try {
                payload = this.buildMarketplaceSubmissionPayload();
            } catch (error) {
                this.notify(error.message, 'warning');
                return;
            }

            this.marketplaceSubmitSaving = true;
            try {
                await this.apiRequest('/api/marketplace/items', {
                    method: 'POST',
                    body: JSON.stringify(payload)
                });
                this.showMarketplaceSubmitDialog = false;
                this.activeTab = 'marketplace';
                this.hasLoadedMarketplace = true;
                await this.loadMarketplaceCatalog({ silent: true, force: true });
                this.notify('投稿已加入本地市场', 'success');
            } catch (error) {
                this.notify('投稿失败: ' + error.message, 'error');
            } finally {
                this.marketplaceSubmitSaving = false;
            }
        },

        async startMarketplaceImport(item) {
            if (!item || !item.id) {
                return;
            }

            this.marketplaceImportingId = item.id;
            try {
                const url = this.appendMarketplaceClientVersion('/api/marketplace/items/' + encodeURIComponent(item.id));
                const detail = await this.apiRequest(url);
                if (detail.item_type === 'command_bundle') {
                    await this.importMarketplaceCommandBundle(detail);
                    return;
                }
                if (detail.item_type === 'response_parser') {
                    await this.importMarketplaceParserPackage(detail);
                    return;
                }

                this.marketplacePendingImport = detail;
                this.marketplaceImportStrategy = 'overwrite';
                this.marketplaceImportPresetName = (detail.name || detail.preset_name || '市场预设').trim();
                this.showMarketplaceImportDialog = true;
            } catch (error) {
                this.notify('加载导入内容失败: ' + error.message, 'error');
            } finally {
                this.marketplaceImportingId = null;
            }
        },

        async confirmMarketplaceImport() {
            if (!this.marketplacePendingImport) {
                return;
            }

            try {
                if (this.marketplaceImportStrategy === 'save_as_preset') {
                    await this.applyMarketplaceSiteSaveAsPreset();
                } else {
                    await this.applyMarketplaceSiteOverwrite();
                }
                this.showMarketplaceImportDialog = false;
                this.marketplacePendingImport = null;
                this.activeTab = 'config';
            } catch (error) {
                this.notify('导入失败: ' + error.message, 'error');
            }
        },

        getSingleImportedSite(detail) {
            const siteConfig = detail && detail.site_config;
            if (!this.validateImportedConfig(siteConfig)) {
                throw new Error('市场配置格式无效');
            }

            const domains = Object.keys(siteConfig || {});
            if (domains.length !== 1) {
                throw new Error('当前仅支持单站点配置导入');
            }

            const domain = domains[0];
            const normalizedMap = this.normalizeConfig(siteConfig);
            const normalizedSite = normalizedMap[domain];
            if (!normalizedSite) {
                throw new Error('站点配置解析失败');
            }

            return { domain, site: normalizedSite };
        },

        async applyMarketplaceSiteOverwrite() {
            const imported = this.getSingleImportedSite(this.marketplacePendingImport);
            this.sites[imported.domain] = imported.site;
            this.currentDomain = imported.domain;
            await this.apiRequest('/api/config', {
                method: 'POST',
                body: JSON.stringify({ config: this.sites })
            });
            this.notify('站点配置已覆盖导入: ' + imported.domain, 'success');
        },

        async applyMarketplaceSiteSaveAsPreset() {
            const imported = this.getSingleImportedSite(this.marketplacePendingImport);
            const newPresetName = String(this.marketplaceImportPresetName || '').trim();
            if (!newPresetName) {
                throw new Error('请填写另存为预设名称');
            }

            const targetSite = this.sites[imported.domain]
                ? JSON.parse(JSON.stringify(this.sites[imported.domain]))
                : { default_preset: newPresetName, presets: {} };

            if (targetSite.presets && targetSite.presets[newPresetName]) {
                throw new Error('该预设名已存在，请换一个名字');
            }

            const importedPresets = imported.site.presets || {};
            const sourcePresetName = imported.site.default_preset && importedPresets[imported.site.default_preset]
                ? imported.site.default_preset
                : Object.keys(importedPresets)[0];
            if (!sourcePresetName) {
                throw new Error('导入内容缺少预设');
            }

            targetSite.presets = targetSite.presets || {};
            targetSite.presets[newPresetName] = JSON.parse(JSON.stringify(importedPresets[sourcePresetName]));
            targetSite.default_preset = targetSite.default_preset || newPresetName;
            this.sites[imported.domain] = targetSite;
            this.currentDomain = imported.domain;

            await this.apiRequest('/api/config', {
                method: 'POST',
                body: JSON.stringify({ config: this.sites })
            });
            this.notify('站点配置已另存为预设: ' + newPresetName, 'success');
        },

        prepareCommandImportPayload(command) {
            const cloned = JSON.parse(JSON.stringify(command || {}));
            delete cloned.id;
            delete cloned.last_triggered;
            delete cloned.trigger_count;
            return cloned;
        },

        async importMarketplaceCommandBundle(detail) {
            const bundle = detail && detail.command_bundle;
            const commands = Array.isArray(bundle && bundle.commands) ? bundle.commands : [];
            if (!commands.length) {
                throw new Error('命令包为空');
            }

            const idMap = {};
            const importedCommands = [];

            for (const command of commands) {
                const oldId = command.id;
                const payload = this.prepareCommandImportPayload(command);
                const response = await this.apiRequest('/api/commands', {
                    method: 'POST',
                    body: JSON.stringify(payload)
                });
                const created = response.command;
                importedCommands.push(created);
                if (oldId && created && created.id) {
                    idMap[oldId] = created.id;
                }
            }

            for (let index = 0; index < commands.length; index++) {
                const original = commands[index];
                const created = importedCommands[index];
                if (!original || !created || !original.trigger) {
                    continue;
                }

                const trigger = JSON.parse(JSON.stringify(original.trigger));
                if (trigger.command_id && idMap[trigger.command_id]) {
                    trigger.command_id = idMap[trigger.command_id];
                }
                if (Array.isArray(trigger.command_ids)) {
                    trigger.command_ids = trigger.command_ids.map(commandId => idMap[commandId] || commandId);
                }

                await this.apiRequest('/api/commands/' + encodeURIComponent(created.id), {
                    method: 'PUT',
                    body: JSON.stringify({ trigger })
                });
            }

            this.notify('命令包已导入，共 ' + importedCommands.length + ' 条命令', 'success');
        },

        async importMarketplaceParserPackage(detail) {
            const parserPackage = detail && detail.parser_package;
            const parserId = String((parserPackage && parserPackage.parser_id) || detail.parser_id || '').trim();
            if (!parserPackage || !parserId) {
                throw new Error('解析器包内容无效');
            }

            const confirmed = window.confirm(
                '导入解析器会写入 app/core/parsers 并注册到当前应用，这会执行 Python 代码。确认继续吗？'
            );
            if (!confirmed) {
                return;
            }

            try {
                const result = await this.apiRequest('/api/parsers/install', {
                    method: 'POST',
                    body: JSON.stringify({
                        parser_package: parserPackage,
                        overwrite: false
                    })
                });
                this.notify((result && result.message) || ('解析器已导入: ' + parserId), 'success');
            } catch (error) {
                if (error.status !== 409) {
                    throw error;
                }

                const replace = window.confirm('同名解析器已存在。是否覆盖已安装的版本？');
                if (!replace) {
                    return;
                }

                const result = await this.apiRequest('/api/parsers/install', {
                    method: 'POST',
                    body: JSON.stringify({
                        parser_package: parserPackage,
                        overwrite: true
                    })
                });
                this.notify((result && result.message) || ('解析器已覆盖安装: ' + parserId), 'success');
            }
        },

        downloadDataAsJson(filename, payloadText) {
            const blob = new Blob([payloadText], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = filename;
            link.click();
            URL.revokeObjectURL(url);
        },

        // ========== 预设辅助方法 ==========

        getActivePresetName() {
            try {
                if (this.$refs.configTab && this.$refs.configTab.selectedPreset) {
                    return this.$refs.configTab.selectedPreset
                }
            } catch (e) { }
            const presets = this.currentConfig && this.currentConfig.presets
            if (presets && typeof presets === 'object') {
                const configuredDefault = this.currentConfig.default_preset
                if (configuredDefault && presets[configuredDefault]) {
                    return configuredDefault
                }
                if (presets['主预设']) {
                    return '主预设'
                }
                const keys = Object.keys(presets)
                if (keys.length > 0) {
                    return keys[0]
                }
            }
            return '主预设'
        },

        getActivePresetConfig() {
            if (!this.currentConfig) return null
            const presets = this.currentConfig.presets
            if (!presets) return this.currentConfig
            const name = this.getActivePresetName()
            const configuredDefault = this.currentConfig.default_preset
            return presets[name]
                || (configuredDefault ? presets[configuredDefault] : null)
                || presets['主预设']
                || Object.values(presets)[0]
                || null
        },

        // ========== 数据操作 ==========

        normalizeConfig(raw) {
            const norm = {}
            // 预设内的字段列表（用于清理顶层残留）
            const PRESET_FIELDS = [
                'selectors', 'workflow', 'stealth', 'stream_config',
                'image_extraction', 'file_paste',
                'extractor_id', 'extractor_verified'
            ]
            for (const [k, v] of Object.entries(raw || {})) {
                if (v.presets) {
                    // 新格式：保留 presets 结构，确保每个预设有基本字段
                    const normalizedPresets = {}
                    for (const [presetName, presetData] of Object.entries(v.presets)) {
                        normalizedPresets[presetName] = {
                            ...presetData,
                            selectors: presetData.selectors || {},
                            workflow: presetData.workflow || [],
                            stealth: !!presetData.stealth
                        }
                    }
                    const presetKeys = Object.keys(normalizedPresets)
                    const configuredDefault = typeof v.default_preset === 'string'
                        ? v.default_preset
                        : null
                    const resolvedDefault = (configuredDefault && normalizedPresets[configuredDefault])
                        ? configuredDefault
                        : (normalizedPresets['主预设'] ? '主预设' : (presetKeys[0] || '主预设'))
                    // 构建站点对象，只保留 presets，清理预设外的残留字段
                    const siteObj = {
                        presets: normalizedPresets,
                        default_preset: resolvedDefault
                    }
                    // 保留非预设字段（如未来可能的站点级元数据）
                    for (const [field, value] of Object.entries(v)) {
                        if (field !== 'presets' && field !== 'default_preset' && !PRESET_FIELDS.includes(field)) {
                            siteObj[field] = value
                        }
                    }
                    norm[k] = siteObj
                } else {
                    // 旧格式兼容：包装为预设（后端迁移后不应再出现，但做兜底）
                    norm[k] = {
                        default_preset: '主预设',
                        presets: {
                            '主预设': {
                                ...v,
                                selectors: v.selectors || {},
                                workflow: v.workflow || [],
                                stealth: !!v.stealth
                            }
                        }
                    }
                }
            }
            return norm
        },

        validateConfig() {
            if (!this.currentDomain || !this.currentConfig) {
                this.notify('请选择站点', 'warning')
                return false
            }

            // 获取当前活跃预设的配置
            const presetConfig = this.getActivePresetConfig()
            if (!presetConfig) {
                this.notify('无法获取预设配置', 'error')
                return false
            }

            const selectors = presetConfig.selectors || {}
            const workflow = presetConfig.workflow || []
            const hasSelectorActions = workflow.some(step => ['FILL_INPUT', 'CLICK', 'STREAM_WAIT'].includes(step.action))
            if (hasSelectorActions && Object.keys(selectors).length === 0) {
                this.notify('至少需要一个选择器', 'warning')
                return false
            }

            for (let i = 0; i < workflow.length; i++) {
                const step = workflow[i]

                if (!step.action) {
                    this.notify('步骤 ' + (i + 1) + ': 缺少动作类型', 'error')
                    return false
                }

                if (['FILL_INPUT', 'CLICK', 'STREAM_WAIT'].includes(step.action)) {
                    if (!step.target) {
                        this.notify('步骤 ' + (i + 1) + ': 请选择目标选择器', 'error')
                        return false
                    }
                }

                if (step.action === 'COORD_CLICK') {
                    const x = Number(step.value?.x)
                    const y = Number(step.value?.y)
                    if (!Number.isFinite(x) || !Number.isFinite(y)) {
                        this.notify('步骤 ' + (i + 1) + ': 请输入有效的 X/Y 坐标', 'error')
                        return false
                    }
                }

                if (step.action === 'KEY_PRESS' && !step.target) {
                    this.notify('步骤 ' + (i + 1) + ': 请输入按键名称', 'error')
                    return false
                }

                if (step.action === 'WAIT' && (!step.value || step.value <= 0)) {
                    this.notify('步骤 ' + (i + 1) + ': 等待时间必须大于 0', 'error')
                    return false
                }
            }

            for (let i = 0; i < workflow.length; i++) {
                const step = workflow[i]
                if (step.action === 'JS_EXEC' && !String(step.value || '').trim()) {
                    this.notify('步骤 ' + (i + 1) + ': 请输入 JavaScript 代码', 'error')
                    return false
                }
            }

            return true
        },

        selectSite(domain) {
            this.currentDomain = domain
        },

        addNewSite() {
            const domain = prompt('请输入域名（例如: chat.example.com）:')
            if (!domain) return

            if (this.sites[domain]) {
                this.notify('该站点已存在', 'warning')
                this.currentDomain = domain
                return
            }

            this.sites[domain] = {
                default_preset: '主预设',
                presets: {
                    '主预设': {
                        selectors: {},
                        workflow: [],
                        stealth: false
                    }
                }
            }
            this.currentDomain = domain
            this.notify('已创建站点: ' + domain, 'success')
        },

        confirmDelete(domain) {
            if (!confirm('确定要删除 ' + domain + ' 的配置吗？')) {
                return
            }

            delete this.sites[domain]

            if (this.currentDomain === domain) {
                this.currentDomain = Object.keys(this.sites)[0] || null
            }

            this.notify('已删除: ' + domain, 'info')
        },

        // ========== 选择器操作 ==========

        addSelector(preset) {
            this.showSelectorMenu = false
            const pc = this.getActivePresetConfig()
            if (!pc) return

            let key
            if (preset === 'custom') {
                key = prompt('请输入选择器名称（例如: input_box）')
                if (!key) return
            } else {
                key = preset
            }

            if (pc.selectors[key]) {
                this.notify('选择器 "' + key + '" 已存在', 'warning')
                return
            }

            pc.selectors[key] = ''
            this.notify('已添加选择器: ' + key, 'success')
        },

        removeSelector(key) {
            if (!confirm('确定删除选择器 ' + key + ' 吗？')) {
                return
            }

            const pc = this.getActivePresetConfig()
            if (!pc) return

            delete pc.selectors[key]

                ; (pc.workflow || []).forEach(function (step) {
                    if (step.target === key) {
                        step.target = ''
                    }
                })
        },

        updateSelectorKey(oldKey, newKey) {
            if (!newKey || oldKey === newKey) return

            newKey = newKey.trim()

            const pc = this.getActivePresetConfig()
            if (!pc) return

            if (pc.selectors[newKey]) {
                this.notify('该键名已存在', 'error')
                return
            }

            pc.selectors[newKey] = pc.selectors[oldKey]
            delete pc.selectors[oldKey]

                ; (pc.workflow || []).forEach(function (step) {
                    if (step.target === oldKey) {
                        step.target = newKey
                    }
                })
        },

        // ========== 工作流操作 ==========

        addStep() {
            const pc = this.getActivePresetConfig()
            if (!pc) return

            const defaultStep = {
                action: 'CLICK',
                target: '',
                optional: false,
                value: null
            }

            if (!pc.workflow) pc.workflow = []
            pc.workflow.push(defaultStep)
        },

        removeStep(index) {
            const pc = this.getActivePresetConfig()
            if (!pc || !pc.workflow) return

            pc.workflow.splice(index, 1)
        },

        moveStep(index, direction) {
            const pc = this.getActivePresetConfig()
            if (!pc || !pc.workflow) return

            const arr = pc.workflow
            const newIndex = index + direction

            if (newIndex < 0 || newIndex >= arr.length) return

            const temp = arr[index]
            arr[index] = arr[newIndex]
            arr[newIndex] = temp
        },

        onActionChange(step) {
            if (['FILL_INPUT', 'CLICK', 'STREAM_WAIT'].includes(step.action)) {
                step.value = null
                if (!step.target) step.target = ''
            } else if (step.action === 'COORD_CLICK') {
                step.target = ''
                step.value = {
                    x: Number(step.value?.x ?? 0),
                    y: Number(step.value?.y ?? 0),
                    random_radius: Number(step.value?.random_radius ?? 10)
                }
            } else if (step.action === 'KEY_PRESS') {
                step.value = null
                if (!step.target) step.target = 'Enter'
            } else if (step.action === 'JS_EXEC') {
                step.target = ''
                if (!String(step.value || '').trim()) step.value = 'return document.title;'
            } else if (step.action === 'WAIT') {
                step.target = ''
                if (!step.value) step.value = '1.0'
            }
        },

        showTemplates() {
            this.showStepTemplates = true
        },

        applyTemplate(type) {
            const templates = {
                'default': [
                    { action: 'CLICK', target: 'new_chat_btn', optional: true, value: null },
                    { action: 'WAIT', target: '', optional: false, value: '0.5' },
                    { action: 'FILL_INPUT', target: 'input_box', optional: false, value: null },
                    { action: 'CLICK', target: 'send_btn', optional: true, value: null },
                    { action: 'KEY_PRESS', target: 'Enter', optional: true, value: null },
                    { action: 'STREAM_WAIT', target: 'result_container', optional: false, value: null }
                ],
                'simple': [
                    { action: 'FILL_INPUT', target: 'input_box', optional: false, value: null },
                    { action: 'KEY_PRESS', target: 'Enter', optional: false, value: null },
                    { action: 'STREAM_WAIT', target: 'result_container', optional: false, value: null }
                ]
            }

            if (!confirm('这将覆盖当前的工作流配置，确定继续吗？')) {
                return
            }

            const pc = this.getActivePresetConfig()
            if (!pc) return
            pc.workflow = JSON.parse(JSON.stringify(templates[type]))
            this.showStepTemplates = false
            this.notify('模板已应用', 'success')
        },

        // ========== 工具功能 ==========

        copyJson(textOverride) {
            const text = typeof textOverride === 'string'
                ? textOverride
                : JSON.stringify(this.getJsonPreviewData(), null, 2)
            navigator.clipboard.writeText(text).then(() => {
                this.notify('已复制到剪贴板', 'success')
            }).catch(() => {
                this.notify('复制失败', 'error')
            })
        },

        getJsonPreviewData() {
            const config = this.getActivePresetConfig() || {}
            return JSON.parse(JSON.stringify(config))
        },

        async saveJsonPreview(rawText) {
            if (!this.currentDomain) {
                this.notify('请先选择站点', 'warning')
                return
            }

            let parsed
            try {
                parsed = JSON.parse(rawText)
            } catch (error) {
                this.notify('JSON 解析失败: ' + error.message, 'error')
                return
            }

            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
                this.notify('JSON 顶层必须是对象', 'error')
                return
            }

            if (parsed.selectors !== undefined && (typeof parsed.selectors !== 'object' || Array.isArray(parsed.selectors))) {
                this.notify('selectors 必须是对象', 'error')
                return
            }

            if (parsed.workflow !== undefined && !Array.isArray(parsed.workflow)) {
                this.notify('workflow 必须是数组', 'error')
                return
            }

            if (parsed.presets && typeof parsed.presets === 'object' && !Array.isArray(parsed.presets)) {
                const normalized = this.normalizeConfig({ [this.currentDomain]: parsed })
                if (normalized[this.currentDomain]) {
                    this.sites[this.currentDomain] = normalized[this.currentDomain]
                }

                try {
                    await this.apiRequest('/api/config', {
                        method: 'POST',
                        body: JSON.stringify({ config: this.sites })
                    })
                    this.showJsonPreview = false
                    this.notify('站点 JSON 已保存', 'success')
                } catch (error) {
                    this.notify('保存失败: ' + error.message, 'error')
                }
                return
            }

            const site = JSON.parse(JSON.stringify(this.sites[this.currentDomain] || {}))
            const presets = site.presets || { '主预设': {} }
            const presetName = this.getActivePresetName()
            const currentPreset = presets[presetName] || presets['主预设'] || {}
            const { domain, preset_name, timestamp, ...presetPatch } = parsed

            presets[presetName] = {
                ...currentPreset,
                ...presetPatch,
                selectors: presetPatch.selectors !== undefined ? presetPatch.selectors : (currentPreset.selectors || {}),
                workflow: presetPatch.workflow !== undefined ? presetPatch.workflow : (currentPreset.workflow || []),
                stealth: presetPatch.stealth !== undefined ? !!presetPatch.stealth : !!currentPreset.stealth
            }

            site.presets = presets
            if (!site.default_preset || !site.presets[site.default_preset]) {
                site.default_preset = site.presets['主预设'] ? '主预设' : (Object.keys(site.presets)[0] || '主预设')
            }
            this.sites[this.currentDomain] = site

            try {
                await this.apiRequest('/api/config', {
                    method: 'POST',
                    body: JSON.stringify({ config: this.sites })
                })
                this.showJsonPreview = false
                this.notify('JSON 修改已保存', 'success')
            } catch (error) {
                this.notify('保存失败: ' + error.message, 'error')
            }
        },

        saveToken() {
            if (this.tempToken.trim()) {
                localStorage.setItem('api_token', this.tempToken.trim())
                this.notify('Token 已保存', 'success')
            } else {
                localStorage.removeItem('api_token')
                this.notify('Token 已清除', 'info')
            }

            this.showTokenDialog = false
            this.tempToken = ''

            this.loadConfig(true)
        },

        // ========== Toast 通知 ==========

        notify(message, type) {
            if (!type) type = 'info'
            const id = this.toastCounter++
            this.toasts.push({ id: id, message: message, type: type })

            const self = this
            setTimeout(function () {
                self.removeToast(id)
            }, 3000)
        },

        removeToast(id) {
            this.toasts = this.toasts.filter(function (t) {
                return t.id !== id
            })
        }
    }
});

// ========== 组件注册 ==========
app.component('sidebar-component', window.SidebarComponent);
app.component('config-tab', window.ConfigTab);
app.component('marketplace-tab', window.MarketplaceTab);
app.component('tabpool-tab', window.TabPoolTabComponent);
app.component('commands-tab', window.CommandsTabComponent);  // 🆕 命令系统
app.component('logs-tab', window.LogsTab);
app.component('settings-tab', window.SettingsTab);
app.component('json-preview-dialog', window.JsonPreviewDialog);
app.component('token-dialog', window.TokenDialog);
app.component('step-templates-dialog', window.StepTemplatesDialog);
app.component('test-dialog', window.TestDialog);
app.component('import-dialog', window.ImportDialog);
app.component('definition-dialog', window.DefinitionDialog);

// ========== 全局 Mixin (修复图标访问问题) ==========
app.mixin({
    computed: {
        $icons() {
            return window.$icons || window.icons || {};
        }
    }
});

// ========== 启动应用 ==========
app.mount('#app');
document.body.classList.add('app-mounted');
const appShell = document.getElementById('app-shell');
if (appShell) {
    appShell.style.display = 'none';
}
