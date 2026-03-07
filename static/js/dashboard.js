const { createApp } = Vue

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
            browserConstantsCollapsed: {},
            isSavingConstants: false,
            isLoadingConstants: false,

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

            // ========== 提取器管理 ==========
            extractors: [],
            defaultExtractorId: 'deep_mode_v1',
            isLoadingExtractors: false,
            showVerifyDialog: false,
            verifyDialogDomain: '',
            verifyDialogExtractorName: ''
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

        // 🆕 测试图片提取
        async testImageExtraction() {
            if (!this.currentDomain) {
                this.notify('请先选择站点', 'warning'); // 适配当前的 notify 方法
                return;
            }

            this.notify('图片提取测试功能开发中...', 'info');
            // TODO: 实现测试逻辑
            // 可以发送一个测试请求，然后显示返回的图片
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
                const result = await this.apiRequest('/api/logs?since=' + this.lastLogTimestamp);

                if (result.logs && result.logs.length > 0) {
                    result.logs.forEach(log => {
                        this.logs.push({
                            id: Date.now() + Math.random(),
                            timestamp: new Date(log.timestamp * 1000).toLocaleTimeString() + '.' +
                                String(Math.floor((log.timestamp % 1) * 1000)).padStart(3, '0'),
                            level: this.parseLogLevel(log.message),
                            message: log.message
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

                    this.lastLogTimestamp = result.timestamp;
                }
            } catch (error) {
                console.debug('日志轮询失败:', error.message);
            }
        },

        parseLogLevel(message) {
            if (message.includes('[AI]') || message.includes('AI')) return 'AI';
            if (message.includes('[ERROR]') || message.includes('ERROR')) return 'ERROR';
            if (message.includes('[WARN]') || message.includes('WARNING')) return 'WARN';
            if (message.includes('[OK]') || message.includes('[SUCCESS]') || message.includes('✅')) return 'OK';
            return 'INFO';
        },

        getLogColorClass(level) {
            const colors = {
                'INFO': 'bg-gray-50 dark:bg-gray-900',
                'AI': 'bg-purple-50 dark:bg-purple-900/20',
                'OK': 'bg-green-50 dark:bg-green-900/20',
                'WARN': 'bg-yellow-50 dark:bg-yellow-900/20',
                'ERROR': 'bg-red-50 dark:bg-red-900/20'
            };
            return colors[level] || colors['INFO'];
        },

        getLogLevelClass(level) {
            const colors = {
                'INFO': 'text-gray-600 dark:text-gray-400',
                'AI': 'text-purple-600 dark:text-purple-400',
                'OK': 'text-green-600 dark:text-green-400',
                'WARN': 'text-yellow-600 dark:text-yellow-400',
                'ERROR': 'text-red-600 dark:text-red-400'
            };
            return colors[level] || colors['INFO'];
        },

        clearLogs() {
            if (confirm('确定清除所有日志吗？')) {
                this.logs = [];
                this.lastLogTimestamp = Date.now() / 1000;

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

            // 检查是否是单站点格式（直接包含 selectors/workflow）
            if (config.selectors !== undefined || config.workflow !== undefined) {
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
            // selectors 必须是对象（如果存在）
            if (config.selectors !== undefined && typeof config.selectors !== 'object') {
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
                if (typeof siteConfig !== 'object') return false;

                if (siteConfig.selectors && typeof siteConfig.selectors !== 'object') {
                    return false;
                }

                if (siteConfig.workflow && !Array.isArray(siteConfig.workflow)) {
                    return false;
                }
            }

            return true;
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

                // 规范化单站点配置
                const normalizedSite = {
                    selectors: this.importedConfig.selectors || {},
                    workflow: this.importedConfig.workflow || [],
                    stealth: !!this.importedConfig.stealth
                };

                // 检查是否会覆盖
                if (this.sites[domain] && this.importMode !== 'replace') {
                    if (!confirm('站点 "' + domain + '" 已存在，是否覆盖？')) {
                        return;
                    }
                }

                this.sites[domain] = normalizedSite;
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

        async saveEnvConfig() {
            this.isSavingEnv = true;
            try {
                await this.apiRequest('/api/settings/env', {
                    method: 'POST',
                    body: JSON.stringify({ config: this.envConfig })
                });

                this.envConfigOriginal = JSON.parse(JSON.stringify(this.envConfig));
                this.notify('环境配置已保存（部分配置需重启生效）', 'success');
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

        async loadBrowserConstants() {
            this.isLoadingConstants = true;
            try {
                const data = await this.apiRequest('/api/settings/browser-constants');
                this.browserConstants = {
                    ...this.getBrowserConstantsDefaults(),
                    ...(data.config || {})
                };
                this.browserConstantsOriginal = JSON.parse(JSON.stringify(this.browserConstants));
            } catch (error) {
                console.error('加载浏览器常量失败:', error);
                this.browserConstants = this.getBrowserConstantsDefaults();
                this.browserConstantsOriginal = JSON.parse(JSON.stringify(this.browserConstants));
            } finally {
                this.isLoadingConstants = false;
            }
        },

        getBrowserConstantsDefaults() {
            const defaults = {};
            for (const group of Object.values(BROWSER_CONSTANTS_SCHEMA)) {
                for (const [key, field] of Object.entries(group.items)) {
                    defaults[key] = field.default;
                }
            }
            return defaults;
        },

        async saveBrowserConstants() {
            this.isSavingConstants = true;
            try {
                await this.apiRequest('/api/settings/browser-constants', {
                    method: 'POST',
                    body: JSON.stringify({ config: this.browserConstants })
                });

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

        // ========== 提取器管理方法 ==========

        async loadExtractors() {
            this.isLoadingExtractors = true;
            try {
                const data = await this.apiRequest('/api/extractors');
                this.extractors = data.extractors || [];
                this.defaultExtractorId = data.default || 'deep_mode_v1';
            } catch (error) {
                console.error('加载提取器列表失败:', error);
                this.extractors = [];
            } finally {
                this.isLoadingExtractors = false;
            }
        },

        async setDefaultExtractor(extractorId) {
            try {
                await this.apiRequest('/api/extractors/default', {
                    method: 'PUT',
                    body: JSON.stringify({ extractor_id: extractorId })
                });
                this.defaultExtractorId = extractorId;
                this.notify('默认提取器已设置为: ' + extractorId, 'success');
            } catch (error) {
                this.notify('设置失败: ' + error.message, 'error');
            }
        },

        async exportExtractorConfig() {
            try {
                const response = await fetch('/api/extractors/export');
                const config = await response.json();
                
                const dataStr = JSON.stringify(config, null, 2);
                const blob = new Blob([dataStr], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'extractors-config-' + Date.now() + '.json';
                a.click();
                URL.revokeObjectURL(url);
                
                this.notify('提取器配置已导出', 'success');
            } catch (error) {
                this.notify('导出失败: ' + error.message, 'error');
            }
        },

        async importExtractorConfig(config) {
            try {
                await this.apiRequest('/api/extractors/import', {
                    method: 'POST',
                    body: JSON.stringify(config)
                });
                await this.loadExtractors();
                this.notify('提取器配置导入成功', 'success');
            } catch (error) {
                this.notify('导入失败: ' + error.message, 'error');
            }
        },

        async setSiteExtractor(domain, extractorId) {
            try {
                const presetName = this.getActivePresetName()
                await this.apiRequest('/api/sites/' + encodeURIComponent(domain) + '/extractor', {
                    method: 'PUT',
                    body: JSON.stringify({ extractor_id: extractorId, preset_name: presetName })
                });

                // 更新当前预设的本地状态
                const pc = this.getActivePresetConfig()
                if (pc) {
                    pc.extractor_id = extractorId;
                    pc.extractor_verified = false;
                }

                this.notify('站点 ' + domain + ' 已绑定提取器: ' + extractorId, 'success');
            } catch (error) {
                this.notify('设置失败: ' + error.message, 'error');
            }
        },

        openVerifyDialog(domain) {
            const pc = this.getActivePresetConfig()
            const extractorId = pc?.extractor_id || this.defaultExtractorId;
            const extractor = this.extractors.find(e => e.id === extractorId);
            
            this.verifyDialogDomain = domain;
            this.verifyDialogExtractorName = extractor?.name || extractorId;
            this.showVerifyDialog = true;
        },

        async handleVerifyResult({ domain, passed }) {
            if (passed) {
                try {
                    await this.apiRequest('/api/sites/' + encodeURIComponent(domain) + '/extractor/verify', {
                        method: 'POST',
                        body: JSON.stringify({ verified: true })
                    });
                    
                    const pc = this.getActivePresetConfig()
                    if (pc) {
                        pc.extractor_verified = true;
                    }
                    
                    this.notify('验证状态已更新', 'success');
                } catch (error) {
                    console.error('更新验证状态失败:', error);
                }
            }
        },

        changeTab(tab) {
            this.activeTab = tab;
        },

        async ensureTabDataLoaded(tab) {
            if (tab === 'settings' && !this.hasLoadedSettings) {
                this.hasLoadedSettings = true;
                await Promise.all([
                    this.loadEnvConfig(),
                    this.loadBrowserConstants(),
                    this.loadSelectorDefinitions()
                ]);
                return;
            }

            if (tab === 'extractors' && !this.hasLoadedExtractors) {
                this.hasLoadedExtractors = true;
                await this.loadExtractors();
            }
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

        handleExtractorImportFile(event) {
            const file = event.target.files[0];
            if (!file) return;

            const reader = new FileReader();
            reader.onload = (e) => {
                try {
                    const config = JSON.parse(e.target.result);
                    this.importExtractorConfig(config);
                } catch (error) {
                    this.notify('JSON 解析失败: ' + error.message, 'error');
                }
            };
            reader.readAsText(file);
            event.target.value = '';
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
app.component('extractor-tab', window.ExtractorTab);
app.component('extractor-verify-dialog', window.ExtractorVerifyDialog);

// ========== 全局 Mixin (修复图标访问问题) ==========
app.mixin({
    computed: {
        $icons() {
            return window.icons || {}; 
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
