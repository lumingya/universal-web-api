// R2-8：由 dashboard-methods.js 拆分——基础：站点显示、初始化、夜间模式、菜单。方法原样迁出，由 dashboard-methods.js 统一组装。
(() => {
    const {
        DEFAULT_SELECTOR_DEFINITIONS,
        BROWSER_CONSTANTS_SCHEMA,
        ENV_CONFIG_SCHEMA,
        DASHBOARD_TOKEN_STORAGE_KEY,
        LEGACY_API_TOKEN_STORAGE_KEY,
        SITE_CONFIG_IMPORT_MAX_BYTES,
        SETTINGS_BACKUP_IMPORT_MAX_BYTES,
        importFileSizeError,
        formatGitCompareErrorText,
        getStoredDashboardToken,
        setStoredDashboardToken,
        SITE_FRIENDLY_NAMES,
        GENERIC_DOMAIN_LABELS,
        siteDisplayName,
    } = window.DashboardShared;
    (window.DashboardMethodParts = window.DashboardMethodParts || []).push({
        siteDisplayName,
        siteStudioInitial(domain) {
            const name = this.siteDisplayName(domain)
            const initials = { 'aistudio.google.com': 'G', 'aistudio.xiaomimimo.com': 'M', 'chat.qwen.ai': 'Q', 'www.doubao.com': '豆', 'chatglm.cn': '智' }
            return initials[domain] || Array.from(name || '?')[0].toUpperCase()
        },
        siteLibrarySummary(domain) {
            const site = this.sites[domain] || {}
            const presets = site.presets || null
            const names = presets ? Object.keys(presets) : []
            const presetName = presets ? (names.includes(site.default_preset) ? site.default_preset : names.includes('主预设') ? '主预设' : names[0] || '无预设') : '主预设'
            const config = presets ? presets[presetName] || {} : site
            const selectors = config.selectors || {}
            const coreChecks = ['input_box', 'send_btn', 'result_container'].map(key => !!String(selectors[key] || '').trim())
            return { presetName, presets: presets ? names.length : 1, selectors: Object.keys(selectors).length, coreChecks, coreFilled: coreChecks.filter(Boolean).length }
        },
        showSiteLibrary() {
            this.flushConfigTabDrafts()
            this.siteConfigView = 'library'
            this.$nextTick(() => document.querySelector('.dashboard-scroll-region')?.scrollTo({ top: 0 }))
        },
        handleStudioShortcut(event) {
            if (this.activeTab !== 'config' || event.defaultPrevented) return
            const editing = event.target && (event.target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName))
            const visibleDialog = Array.from(document.querySelectorAll('[role="dialog"]')).some(el => el.getClientRects().length)
            const editingDialog = Object.entries(this.$data || {}).some(([key, value]) => /^show.*(Dialog|Modal|Preview)$/.test(key) && value === true)
            if (visibleDialog || editingDialog) return
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
                event.preventDefault()
                if (!this.isSaving && this.currentConfig && !this.showJsonPreview) this.saveConfig()
            } else if (event.key === '/' && !editing && !event.ctrlKey && !event.metaKey && !event.altKey) {
                const field = document.querySelector('.library-search input')
                if (field && field.getClientRects().length) {
                    event.preventDefault()
                    field.focus()
                }
            }
        },
        async initializeDashboard() {
            await this.loadConfig(true)
            await this.loadBrowserConstants().catch(() => {})

            this.startLogPolling()
            await this.loadHealthStatus({ silent: true, timeoutMs: 2500 }).catch(() => false)
            this.loadUpdateCheck({ silent: true })
                .then((status) => this.scheduleUpdateCheckStatusRefresh(status))
                .catch(() => {})
            this.startRequestHistoryPolling()
            this.ensureTabDataLoaded(this.activeTab)

            this.startSystemStatsPolling()
        },

        startLogPolling() {
            this.ensureLogPollingVisibilityHandler()
            const interval = this.getDashboardPollInterval('DASHBOARD_LOG_POLL_INTERVAL_MS', 1000)
            if (
                interval <= 0
                || !this.getBrowserConstantBool('LOG_WEB_COLLECTOR_ENABLED', true)
                || this.getBrowserConstantNumber('LOG_WEB_MAX_RECORDS', 5000) <= 0
            ) {
                this.stopLogPollingTimer()
                this.logs = []
                return
            }
            if (this.logPollingTimer || this.isDocumentHidden()) {
                return
            }

            this.pollLogs()
            this.logPollingTimer = setInterval(() => {
                this.pollLogs({ background: this.activeTab !== 'logs' })
            }, interval)
        },

        stopLogPolling() {
            this.stopLogPollingTimer()
            if (
                this.logVisibilityHandler
                && typeof document !== 'undefined'
                && typeof document.removeEventListener === 'function'
            ) {
                document.removeEventListener('visibilitychange', this.logVisibilityHandler)
            }
            this.logVisibilityHandler = null
        },

        stopLogPollingTimer() {
            if (!this.logPollingTimer) {
                return
            }

            clearInterval(this.logPollingTimer)
            this.logPollingTimer = null
        },

        ensureLogPollingVisibilityHandler() {
            if (
                this.logVisibilityHandler
                || typeof document === 'undefined'
                || typeof document.addEventListener !== 'function'
            ) {
                return
            }

            this.logVisibilityHandler = () => {
                if (this.isDocumentHidden()) {
                    this.stopLogPollingTimer()
                    return
                }
                this.startLogPolling()
            }
            document.addEventListener('visibilitychange', this.logVisibilityHandler)
        },

        markTabAsVisited(tab) {
            const key = String(tab || '').trim()
            if (!key || this.mountedTabs[key]) {
                return
            }
            this.mountedTabs = {
                ...this.mountedTabs,
                [key]: true
            }
        },

        shouldRenderTab(tab) {
            return this.activeTab === tab || !!this.mountedTabs[tab]
        },

        syncTokenPresence() {
            try {
                this.hasTokenPresent = !!getStoredDashboardToken()
            } catch (e) {
                this.hasTokenPresent = false
            }
        },

        ensureTokenStorageHandler() {
            if (
                this.tokenStorageHandler
                || typeof window === 'undefined'
                || typeof window.addEventListener !== 'function'
            ) {
                return
            }

            this.tokenStorageHandler = (event) => {
                if (
                    !event
                    || event.key === DASHBOARD_TOKEN_STORAGE_KEY
                    || event.key === LEGACY_API_TOKEN_STORAGE_KEY
                ) {
                    this.hasTokenPresent = !!getStoredDashboardToken()
                }
            }
            window.addEventListener('storage', this.tokenStorageHandler)
        },

        stopTokenStorageHandler() {
            if (
                this.tokenStorageHandler
                && typeof window !== 'undefined'
                && typeof window.removeEventListener === 'function'
            ) {
                window.removeEventListener('storage', this.tokenStorageHandler)
            }
            this.tokenStorageHandler = null
        },

        startRequestHistoryPolling() {
            this.ensureRequestHistoryVisibilityHandler()
            const interval = this.getDashboardPollInterval('DASHBOARD_REQUEST_HISTORY_POLL_INTERVAL_MS', 3000)
            if (!this.isRequestMonitorEnabled() || interval <= 0) {
                this.stopRequestHistoryPollingTimer()
                if (!this.isRequestMonitorEnabled()) {
                    this.requestHistory = []
                    this.requestHistoryRevision = ''
                    this.requestHistoryError = ''
                }
                return
            }
            if (this.requestHistoryTimer || this.isDocumentHidden()) {
                return
            }
            this.requestHistoryTimer = setInterval(() => {
                if (this.activeTab === 'monitor' && document.visibilityState !== 'hidden') {
                    this.fetchRequestHistory({ silent: true, ifChanged: true }).catch(() => {})
                }
            }, interval)
        },

        stopRequestHistoryPolling() {
            this.stopRequestHistoryPollingTimer()
            if (
                this.requestHistoryVisibilityHandler
                && typeof document !== 'undefined'
                && typeof document.removeEventListener === 'function'
            ) {
                document.removeEventListener('visibilitychange', this.requestHistoryVisibilityHandler)
            }
            this.requestHistoryVisibilityHandler = null
        },

        stopRequestHistoryPollingTimer() {
            if (!this.requestHistoryTimer) {
                return
            }
            clearInterval(this.requestHistoryTimer)
            this.requestHistoryTimer = null
        },

        ensureRequestHistoryVisibilityHandler() {
            if (
                this.requestHistoryVisibilityHandler
                || typeof document === 'undefined'
                || typeof document.addEventListener !== 'function'
            ) {
                return
            }

            this.requestHistoryVisibilityHandler = () => {
                if (this.isDocumentHidden()) {
                    this.stopRequestHistoryPollingTimer()
                    return
                }
                this.startRequestHistoryPolling()
                if (this.activeTab === 'monitor' && this.isRequestMonitorEnabled()) {
                    this.fetchRequestHistory({ silent: true, ifChanged: true }).catch(() => {})
                }
            }
            document.addEventListener('visibilitychange', this.requestHistoryVisibilityHandler)
        },

        isDocumentHidden() {
            return typeof document !== 'undefined' && document.visibilityState === 'hidden'
        },

        getBrowserConstantDefault(key, fallback = null) {
            const targetKey = String(key || '')
            for (const group of Object.values(BROWSER_CONSTANTS_SCHEMA || {})) {
                const items = group && group.items ? group.items : {}
                if (Object.prototype.hasOwnProperty.call(items, targetKey)) {
                    const field = items[targetKey] || {}
                    return Object.prototype.hasOwnProperty.call(field, 'default') ? field.default : fallback
                }
            }
            return fallback
        },

        getBrowserConstantValue(key, fallback = null) {
            const targetKey = String(key || '')
            const constants = this.browserConstants && typeof this.browserConstants === 'object' ? this.browserConstants : {}
            if (Object.prototype.hasOwnProperty.call(constants, targetKey)) {
                return constants[targetKey]
            }
            return this.getBrowserConstantDefault(targetKey, fallback)
        },

        getBrowserConstantNumber(key, fallback = 0, { min = null, max = null } = {}) {
            const value = Number(this.getBrowserConstantValue(key, fallback))
            if (!Number.isFinite(value)) {
                return fallback
            }
            let normalized = value
            if (Number.isFinite(min)) {
                normalized = Math.max(min, normalized)
            }
            if (Number.isFinite(max)) {
                normalized = Math.min(max, normalized)
            }
            return normalized
        },

        getBrowserConstantBool(key, fallback = true) {
            const value = this.getBrowserConstantValue(key, fallback)
            if (typeof value === 'boolean') {
                return value
            }
            if (typeof value === 'number') {
                return value !== 0
            }
            if (typeof value === 'string') {
                const normalized = value.trim().toLowerCase()
                if (['0', 'false', 'no', 'off'].includes(normalized)) {
                    return false
                }
                if (['1', 'true', 'yes', 'on'].includes(normalized)) {
                    return true
                }
            }
            return Boolean(fallback)
        },

        getDashboardPollInterval(key, fallback) {
            const value = this.getBrowserConstantNumber(key, fallback, { min: 0 })
            return Number.isFinite(value) ? Math.floor(value) : fallback
        },

        isRequestMonitorEnabled() {
            return this.getBrowserConstantBool('REQUEST_MONITOR_ENABLED', true)
                && this.getBrowserConstantNumber('REQUEST_MONITOR_MAX_RECORDS', 200, { min: 0 }) > 0
        },

        isSystemStatsPollingEnabled() {
            return this.getBrowserConstantBool('DASHBOARD_SYSTEM_STATS_ENABLED', true)
                && this.getDashboardPollInterval('DASHBOARD_SYSTEM_STATS_POLL_INTERVAL_MS', 3000) > 0
        },

        applyDashboardRuntimeSettings() {
            this.stopLogPollingTimer()
            this.stopRequestHistoryPollingTimer()
            this.stopSystemStatsPollingTimer()

            if (
                !this.getBrowserConstantBool('LOG_WEB_COLLECTOR_ENABLED', true)
                || this.getBrowserConstantNumber('LOG_WEB_MAX_RECORDS', 5000, { min: 0 }) <= 0
                || this.getDashboardPollInterval('DASHBOARD_LOG_POLL_INTERVAL_MS', 1000) <= 0
            ) {
                this.logs = []
                this.lastLogSeq = 0
                this.lastLogTimestamp = 0
                this.logPollPending = false
            }

            if (!this.isRequestMonitorEnabled()) {
                this.requestHistory = []
                this.requestHistoryRevision = ''
                this.requestHistoryError = ''
                this.requestHistoryPendingRefresh = null
            }

            this.startLogPolling()
            this.startRequestHistoryPolling()
            this.startSystemStatsPolling()

            if (this.activeTab === 'monitor' && this.isRequestMonitorEnabled()) {
                this.fetchRequestHistory({ silent: true, ifChanged: false, force: true }).catch(() => {})
            }
        },

        startSystemStatsPolling() {
            this.ensureSystemStatsVisibilityHandler()
            const interval = this.getDashboardPollInterval('DASHBOARD_SYSTEM_STATS_POLL_INTERVAL_MS', 3000)
            if (!this.getBrowserConstantBool('DASHBOARD_SYSTEM_STATS_ENABLED', true) || interval <= 0) {
                this.stopSystemStatsPollingTimer()
                return
            }
            if (this.systemStatsTimer || this.isDocumentHidden()) {
                return
            }

            this.fetchSystemStats({ timeoutMs: 2500 }).catch(() => {})

            this.systemStatsTimer = setInterval(() => {
                this.fetchSystemStats({ timeoutMs: 2500 }).catch(() => {})
            }, interval)
        },

        stopSystemStatsPollingTimer() {
            if (!this.systemStatsTimer) {
                return
            }

            clearInterval(this.systemStatsTimer)
            this.systemStatsTimer = null
        },

        ensureSystemStatsVisibilityHandler() {
            if (
                this.systemStatsVisibilityHandler
                || typeof document === 'undefined'
                || typeof document.addEventListener !== 'function'
            ) {
                return
            }
            this.systemStatsVisibilityHandler = () => {
                if (this.isDocumentHidden()) {
                    this.stopSystemStatsPollingTimer()
                    return
                }
                this.startSystemStatsPolling()
            }
            document.addEventListener('visibilitychange', this.systemStatsVisibilityHandler)
        },

        stopSystemStatsPolling() {
            this.stopSystemStatsPollingTimer()
            if (
                this.systemStatsVisibilityHandler
                && typeof document !== 'undefined'
                && typeof document.removeEventListener === 'function'
            ) {
                document.removeEventListener('visibilitychange', this.systemStatsVisibilityHandler)
            }
            this.systemStatsVisibilityHandler = null
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

        // ========== 菜单控制 ==========

        toggleSelectorMenu() {
            this.showSelectorMenu = !this.showSelectorMenu
        },

        toggleImportMenu(e) {
            if (e && typeof e.stopPropagation === 'function') e.stopPropagation();
            this.showImportMenu = !this.showImportMenu;
            this.showExportMenu = false;
        },

        toggleExportMenu(e) {
            if (e && typeof e.stopPropagation === 'function') e.stopPropagation();
            this.showExportMenu = !this.showExportMenu;
            this.showImportMenu = false;
        },

        closeAllMenus() {
            this.showSelectorMenu = false;
            this.showImportMenu = false;
            this.showExportMenu = false;
        },
    });
})();
