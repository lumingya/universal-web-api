(() => {
    const DEFAULT_SELECTOR_DEFINITIONS = window.DEFAULT_SELECTOR_DEFINITIONS || []
    const BROWSER_CONSTANTS_SCHEMA = window.BROWSER_CONSTANTS_SCHEMA || {}
    const ENV_CONFIG_SCHEMA = window.ENV_CONFIG_SCHEMA || {}

const DASHBOARD_SITES_CACHE_STORAGE_KEY = 'dashboard_sites_cache_v1'

function loadStoredSitesCache() {
    try {
        const raw = localStorage.getItem(DASHBOARD_SITES_CACHE_STORAGE_KEY)
        if (!raw) {
            return null
        }
        const parsed = JSON.parse(raw)
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
            return null
        }
        if (!parsed.sites || typeof parsed.sites !== 'object' || Array.isArray(parsed.sites)) {
            return null
        }
        return {
            sites: parsed.sites,
            currentDomain: typeof parsed.currentDomain === 'string' ? parsed.currentDomain : null
        }
    } catch (error) {
        return null
    }
}

function saveStoredSitesCache(sites, currentDomain) {
    try {
        localStorage.setItem(DASHBOARD_SITES_CACHE_STORAGE_KEY, JSON.stringify({
            sites: sites && typeof sites === 'object' ? sites : {},
            currentDomain: typeof currentDomain === 'string' ? currentDomain : null,
            savedAt: Date.now()
        }))
    } catch (error) {
        // ignore storage failures and keep runtime data available
    }
}

// ========== 元素定义 Schema ==========

    const DashboardState = {
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
            isSaving: false,
            isLoading: false,
            showJsonPreview: false,
            showTokenDialog: false,
            showStepTemplates: false,
            showTestDialog: false,
            showSelectorMenu: false,
            darkMode: false,
            showMainCompareSummaryDialog: false,
            mainCompareSummaryLoading: false,
            mainCompareSummaryError: '',
            mainCompareSummaryItems: [],
            mainCompareSummaryCounts: {
                same: 0,
                different: 0,
                local_only_preset: 0,
                local_only_site: 0,
                main_only_preset: 0,
                main_only_site: 0
            },
            mainCompareSummaryPath: 'config/sites.json',
            mainCompareShowSame: false,

            // Tab 切换（新增 settings）
            activeTab: 'config',  // 'config' | 'logs' | 'settings'
            mountedTabs: {
                config: true
            },

            // 折叠面板状态
            selectorCollapsed: true,
            workflowCollapsed: true,

            // 浏览器状态
            browserStatus: {
                connected: false,
                tab_url: null,
                tab_title: null
            },

            // 系统占用统计
            systemStats: {
                memory_mb: 0,
                disk_status: '加载中...',
                total_requests: 0
            },

            // 认证
            authEnabled: false,
            tempToken: '',

            // 选择器测试
            currentTestingSelectorKey: '',
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
            systemStatsTimer: null,
            isFetchingSystemStats: false,

            // 请求监控
            requestHistory: [],
            requestHistoryLoading: false,
            requestHistoryError: '',
            requestHistoryTimer: null,
            requestHistoryRevision: '',
            requestHistoryFetchedAt: 0,
            requestHistoryDetailLoading: {},

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

            // ========== 版本管理 ==========
            releases: [],
            releasesLoading: false,
            releasesError: '',
            releasesCurrentVersion: '',
            updateCheck: {
                checked: false,
                checking: false,
                available: false,
                current_version: '',
                latest_version: '',
                latest_tag: '',
                error: ''
            },
            updateCheckTimer: null,
            switchingTag: null,           // 正在切换的 tag
            switchStatusPolling: null,    // 轮询定时器
            showChangelogModal: false,
            changelogContent: '',
            changelogTag: '',

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
        },

        updateAvailable() {
            return !!(this.updateCheck && this.updateCheck.available);
        },

        mainCompareVisibleItems() {
            const items = Array.isArray(this.mainCompareSummaryItems) ? this.mainCompareSummaryItems : [];
            if (this.mainCompareShowSame) {
                return items;
            }
            return items.filter(item => String(item && item.status || '') !== 'same');
        },

        mainCompareDifferentTotal() {
            const counts = this.mainCompareSummaryCounts || {};
            return Number(counts.different || 0)
                + Number(counts.local_only_preset || 0)
                + Number(counts.local_only_site || 0);
        }
    },

    watch: {
        activeTab(tab) {
            this.markTabAsVisited(tab)
            this.ensureTabDataLoaded(tab)
            if (tab === 'logs') {
                this.pollLogs()
            }
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
        this.markTabAsVisited(this.activeTab)
        this.restoreSitesCache()

        this.initializeDashboard()

        // 启动日志轮询（每 1 秒）

        // 加载系统设置

        // 加载元素定义

        // 加载提取器列表
    },

    beforeUnmount() {
        this.stopLogPolling()
        this.stopRequestHistoryPolling()
        if (this.systemStatsTimer) {
            clearInterval(this.systemStatsTimer)
            this.systemStatsTimer = null
        }
        if (this.updateCheckTimer) {
            clearTimeout(this.updateCheckTimer)
            this.updateCheckTimer = null
        }
    },
    }

    window.DashboardState = DashboardState
    window.loadStoredSitesCache = loadStoredSitesCache
    window.saveStoredSitesCache = saveStoredSitesCache
})();
