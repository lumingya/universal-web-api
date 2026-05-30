// Dashboard methods extracted from dashboard.js
(() => {
    const DEFAULT_SELECTOR_DEFINITIONS = window.DEFAULT_SELECTOR_DEFINITIONS || []
    const BROWSER_CONSTANTS_SCHEMA = window.BROWSER_CONSTANTS_SCHEMA || {}
    const ENV_CONFIG_SCHEMA = window.ENV_CONFIG_SCHEMA || {}

    window.DashboardMethods = {
        async initializeDashboard() {
            await this.loadConfig(true)

            this.startLogPolling()
            await this.loadHealthStatus({ silent: true, timeoutMs: 2500 }).catch(() => false)
            this.loadUpdateCheck({ silent: true })
                .then((status) => this.scheduleUpdateCheckStatusRefresh(status))
                .catch(() => {})
            this.startRequestHistoryPolling()
            this.ensureTabDataLoaded(this.activeTab)

            // 每 2 秒刷新系统状态
            this.fetchSystemStats({ timeoutMs: 5000 }).catch(() => {})
            if (this.systemStatsTimer) {
                clearInterval(this.systemStatsTimer)
            }
            this.systemStatsTimer = setInterval(() => {
                this.fetchSystemStats({ timeoutMs: 5000 }).catch(() => {})
            }, 15000)
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

        startRequestHistoryPolling() {
            if (this.requestHistoryTimer) {
                clearInterval(this.requestHistoryTimer)
            }
            this.requestHistoryTimer = setInterval(() => {
                if (this.activeTab === 'monitor' && document.visibilityState !== 'hidden') {
                    this.fetchRequestHistory({ silent: true, ifChanged: true }).catch(() => {})
                }
            }, 3000)
        },

        stopRequestHistoryPolling() {
            if (!this.requestHistoryTimer) {
                return
            }
            clearInterval(this.requestHistoryTimer)
            this.requestHistoryTimer = null
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
            const timeoutMs = Number(options.timeoutMs || 0)
            const headers = {
                'Content-Type': 'application/json',
                ...options.headers
            }

            if (token) {
                headers['Authorization'] = 'Bearer ' + token
            }

            const fetchOptions = { ...options }
            delete fetchOptions.timeoutMs

            let timeoutId = null
            let controller = null
            if (timeoutMs > 0 && typeof AbortController !== 'undefined') {
                controller = new AbortController()
                fetchOptions.signal = controller.signal
                timeoutId = setTimeout(() => {
                    controller.abort()
                }, timeoutMs)
            }

            try {
                const response = await fetch(url, {
                    ...fetchOptions,
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
                if (error && error.name === 'AbortError') {
                    throw new Error('REQUEST_TIMEOUT')
                }
                if (error.message !== 'UNAUTHORIZED') {
                    console.error('API 请求错误:', error)
                }
                throw error
            } finally {
                if (timeoutId) {
                    clearTimeout(timeoutId)
                }
            }
        },

        async loadConfig(silent) {
            // 防御：@click="loadConfig" 会传入 Event 对象，需要过滤
            if (typeof silent !== 'boolean') {
                silent = false
            }

            this.isLoading = true
            try {
                const data = await this.apiRequest('/api/config', { timeoutMs: 5000 })
                this.sites = this.normalizeConfig(data)

                if (!this.currentDomain && Object.keys(this.sites).length > 0) {
                    this.currentDomain = Object.keys(this.sites)[0]
                }
                saveStoredSitesCache(this.sites, this.currentDomain)

                if (!silent) {
                    this.notify('配置已刷新 (' + Object.keys(this.sites).length + ' 个站点)', 'success')
                }
                return true
            } catch (error) {
                this.notify('加载配置失败: ' + error.message, 'error')
                if (Object.keys(this.sites || {}).length === 0) {
                    this.sites = {}
                }
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

        async checkAuth() {
            return this.loadHealthStatus({ silent: true })
        },

        async testSelector(key, selector) {
            this.currentTestingSelectorKey = key || ''
            this.testSelectorInput = selector || ''
            this.showTestDialog = true
            this.testResult = null

            if (!String(this.testSelectorInput || '').trim()) {
                this.notify('当前字段还没填，先在测试工作台里输入一个选择器再测。', 'info')
                return
            }

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

        async applyTestSelectorCandidate(payload) {
            const selector = String(payload && payload.selector || '').trim()
            if (!selector) {
                return
            }

            this.testSelectorInput = selector

            const key = String(this.currentTestingSelectorKey || '').trim()
            const preset = this.getActivePresetConfig()
            if (key && preset && preset.selectors) {
                preset.selectors[key] = selector
                this.notify('候选选择器已回填到当前字段', 'success')
            } else {
                this.notify('已代入当前测试输入框', 'info')
            }

            if (payload && payload.rerun) {
                await this.runTest()
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
            const previousImageConfig = pc
                ? JSON.parse(JSON.stringify(pc.image_extraction || {}))
                : null

            if (pc) pc.image_extraction = newConfig;

            try {
                const presetName = this.getActivePresetName()
                const payload = { ...newConfig, preset_name: presetName }
                await this.apiRequest(`/api/sites/${this.currentDomain}/image-config`, {
                    method: 'PUT',
                    body: JSON.stringify(payload)
                });
                this.notify('多模态提取配置已保存', 'success');
            } catch (error) {
                if (pc) {
                    pc.image_extraction = previousImageConfig || {}
                }
                console.error('保存图片配置失败:', error);
                this.notify('保存多模态提取配置失败: ' + error.message, 'error');
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

        async openMainCompareSummaryDialog() {
            this.showMainCompareSummaryDialog = true;
            await this.loadMainCompareSummary();
        },

        closeMainCompareSummaryDialog() {
            this.showMainCompareSummaryDialog = false;
        },

        async loadMainCompareSummary() {
            this.mainCompareSummaryLoading = true;
            this.mainCompareSummaryError = '';
            try {
                const data = await this.apiRequest('/api/config/compare-main-summary');
                this.mainCompareSummaryItems = Array.isArray(data.items) ? data.items : [];
                this.mainCompareSummaryCounts = {
                    same: 0,
                    different: 0,
                    local_only_preset: 0,
                    local_only_site: 0,
                    main_only_preset: 0,
                    main_only_site: 0,
                    ...(data.counts || {})
                };
                this.mainCompareSummaryPath = String(data.path || 'config/sites.json').trim() || 'config/sites.json';
                return true;
            } catch (error) {
                this.mainCompareSummaryItems = [];
                this.mainCompareSummaryError = error.message;
                return false;
            } finally {
                this.mainCompareSummaryLoading = false;
            }
        },

        getMainCompareStatusClass(status) {
            if (status === 'different') {
                return 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-300';
            }
            if (status === 'local_only_preset') {
                return 'border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-800 dark:bg-blue-900/30 dark:text-blue-300';
            }
            if (status === 'local_only_site') {
                return 'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-800 dark:bg-rose-900/30 dark:text-rose-300';
            }
            return 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300';
        },

        getMainCompareStatusText(status) {
            if (status === 'different') return '字段不同';
            if (status === 'local_only_preset') return '本地自定义预设';
            if (status === 'local_only_site') return '本地自定义站点';
            return '与官方一致';
        },

        async waitForConfigTabRef() {
            for (let attempt = 0; attempt < 30; attempt++) {
                await this.$nextTick();
                const ref = this.$refs.configTab;
                if (ref && typeof ref.openConfigCompareForPreset === 'function') {
                    return ref;
                }
                await new Promise(resolve => setTimeout(resolve, 60));
            }
            return null;
        },

        async openMainCompareDetail(item) {
            if (!item || !item.domain) {
                return;
            }

            this.showMainCompareSummaryDialog = false;
            this.activeTab = 'config';
            this.currentDomain = item.domain;

            const configTab = await this.waitForConfigTabRef();
            if (!configTab) {
                this.notify('配置面板尚未准备好', 'error');
                return;
            }

            const targetPreset = String(item.local_preset_name || '').trim();
            await configTab.openConfigCompareForPreset(targetPreset);
        },
        // ========== 日志相关 ==========

        async pollLogs() {
            if (this.pauseLogs || document.visibilityState === 'hidden') return;

            try {
                const result = await this.apiRequest('/api/logs?after_seq=' + this.lastLogSeq);

                if (result.logs && result.logs.length > 0) {
                    const nextLogs = result.logs.map(log => {
                        const messageText = log.message_text || log.display_message || log.message || '';
                        const kind = log.kind || log.level;
                        return {
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
                        }
                    });
                    this.logs = this.logs.concat(nextLogs).slice(-500);
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

        // ========== 版本管理方法 ==========

        applyUpdateCheck(data) {
            const current = this.updateCheck && typeof this.updateCheck === 'object'
                ? this.updateCheck
                : {};
            this.updateCheck = {
                checked: !!(data && data.checked),
                checking: !!(data && data.checking),
                available: !!(data && data.available),
                current_version: String((data && data.current_version) || current.current_version || ''),
                latest_version: String((data && data.latest_version) || current.latest_version || ''),
                latest_tag: String((data && data.latest_tag) || current.latest_tag || ''),
                published_at: String((data && data.published_at) || current.published_at || ''),
                repo: String((data && data.repo) || current.repo || ''),
                checked_at: data && data.checked_at ? data.checked_at : (current.checked_at || null),
                error: String((data && data.error) || '')
            };
            if (this.updateCheck.current_version && !this.releasesCurrentVersion) {
                this.releasesCurrentVersion = this.updateCheck.current_version;
            }
            return this.updateCheck;
        },

        async loadUpdateCheck({ silent = false } = {}) {
            try {
                const data = await this.apiRequest('/api/update/check', { timeoutMs: 2500 });
                return this.applyUpdateCheck(data);
            } catch (error) {
                if (!silent) {
                    this.notify('版本检查状态读取失败: ' + error.message, 'error');
                }
                throw error;
            }
        },

        scheduleUpdateCheckStatusRefresh(status, attempt = 0) {
            if (this.updateCheckTimer) {
                clearTimeout(this.updateCheckTimer);
                this.updateCheckTimer = null;
            }
            const stillPending = status && (status.checking || !status.checked);
            if (!stillPending || attempt >= 12) {
                return;
            }
            this.updateCheckTimer = setTimeout(() => {
                this.updateCheckTimer = null;
                this.loadUpdateCheck({ silent: true })
                    .then((nextStatus) => this.scheduleUpdateCheckStatusRefresh(nextStatus, attempt + 1))
                    .catch(() => {});
            }, 1500);
        },

        async loadReleases() {
            this.releasesLoading = true;
            this.releasesError = '';
            try {
                const data = await this.apiRequest('/api/update/releases');
                this.releases = Array.isArray(data.releases) ? data.releases : [];
                this.releasesCurrentVersion = data.current_version || '';
                if (data.update_check) {
                    this.applyUpdateCheck(data.update_check);
                }
            } catch (error) {
                this.releasesError = '加载失败: ' + error.message;
                this.releases = [];
            } finally {
                this.releasesLoading = false;
            }
        },

        async switchToVersion(tag) {
            if (this.switchingTag) {
                this.notify('已有版本切换任务正在运行，请稍候', 'warning');
                return;
            }
            if (!confirm('确定要切换到 ' + tag + ' 吗？\n切换完成后服务将自动重启，页面需要手动刷新。')) {
                return;
            }
            this.switchingTag = tag;
            try {
                await this.apiRequest('/api/update/switch', {
                    method: 'POST',
                    body: JSON.stringify({ tag: tag })
                });
                this.notify('版本切换任务已启动：' + tag + '，下载中...', 'info');
                this.startSwitchStatusPolling();
            } catch (error) {
                this.notify('启动版本切换失败: ' + error.message, 'error');
                this.switchingTag = null;
            }
        },

        startSwitchStatusPolling() {
            this.stopSwitchStatusPolling();
            this.switchStatusPolling = setInterval(async () => {
                try {
                    const status = await this.apiRequest('/api/update/status');
                    if (!status.running) {
                        this.stopSwitchStatusPolling();
                        if (status.success === true) {
                            this.notify('版本 ' + status.tag + ' 切换成功，服务正在重启，请稍后刷新页面', 'success');
                        } else if (status.success === false) {
                            var errMsg = status.error ? '：' + status.error : '';
                            this.notify('版本 ' + status.tag + ' 切换失败' + errMsg, 'error');
                            this.switchingTag = null;
                        }
                    }
                } catch (e) {
                    // 服务重启中，连接可能断开
                }
            }, 2000);
        },

        stopSwitchStatusPolling() {
            if (this.switchStatusPolling) {
                clearInterval(this.switchStatusPolling);
                this.switchStatusPolling = null;
            }
        },

        showChangelog(tag, body) {
            this.changelogTag = tag;
            this.changelogContent = body || '（无更新说明）';
            this.showChangelogModal = true;
        },

        formatReleaseDate(isoStr) {
            if (!isoStr) return '—';
            try {
                var d = new Date(isoStr);
                var pad = function(n) { return String(n).padStart(2, '0'); };
                return d.getFullYear() + '/' + pad(d.getMonth()+1) + '/' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
            } catch (e) {
                return isoStr;
            }
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
            this.markTabAsVisited(tab)
            this.activeTab = tab;
        },

        async ensureTabDataLoaded(tab) {
            if (tab === 'monitor') {
                const now = Date.now()
                const stale = now - Number(this.requestHistoryFetchedAt || 0) > 2000
                await Promise.all([
                    this.fetchRequestHistory({ silent: true, ifChanged: !stale }),
                    this.fetchSystemStats({ timeoutMs: 5000 })
                ]);
                return;
            }
            if (tab === 'settings' && !this.hasLoadedSettings) {
                this.hasLoadedSettings = true;
                await Promise.all([
                    this.loadEnvConfig(),
                    this.loadBrowserConstants(),
                    this.loadUpdatePreserveSettings(),
                    this.loadSelectorDefinitions(),
                    this.loadReleases()
                ]);
                return;
            }
        },

        async fetchRequestHistory({ silent = false, ifChanged = false, force = false } = {}) {
            if (this.requestHistoryLoading) {
                return this.requestHistory;
            }
            const now = Date.now();
            if (!force && ifChanged && now - Number(this.requestHistoryFetchedAt || 0) < 1200) {
                return this.requestHistory;
            }
            this.requestHistoryLoading = true;
            if (!silent) {
                this.requestHistoryError = '';
            }
            try {
                const data = await this.apiRequest('/api/system/request-history?limit=200', {
                    timeoutMs: 5000
                });
                const revision = String(data.revision || '');
                if (!ifChanged || force || !this.requestHistoryRevision || revision !== this.requestHistoryRevision) {
                    const detailCache = new Map(
                        this.requestHistory
                            .filter(item => item && item.detail_loaded && item.id)
                            .map(item => [String(item.history_key || item.id), item])
                    );
                    const records = Array.isArray(data.records) ? data.records : [];
                    this.requestHistory = records.map(item => {
                        const cached = detailCache.get(String(item && (item.history_key || item.id) || ''));
                        return cached ? { ...item, ...cached } : item;
                    });
                    this.requestHistoryRevision = revision;
                }
                this.requestHistoryFetchedAt = Date.now();
                this.requestHistoryError = '';
                return this.requestHistory;
            } catch (error) {
                this.requestHistoryError = error.message || '请求历史加载失败';
                return this.requestHistory;
            } finally {
                this.requestHistoryLoading = false;
            }
        },

        async fetchRequestHistoryDetail(requestId) {
            const id = String(requestId || '').trim();
            if (!id || this.requestHistoryDetailLoading[id]) {
                return null;
            }

            const matchesRequestHistoryId = (item) => {
                if (!item) return false;
                return String(item.history_key || '').trim() === id || String(item.id || '').trim() === id;
            };
            const existingIndex = this.requestHistory.findIndex(matchesRequestHistoryId);
            if (existingIndex >= 0 && this.requestHistory[existingIndex].detail_loaded) {
                return this.requestHistory[existingIndex];
            }

            this.requestHistoryDetailLoading = {
                ...this.requestHistoryDetailLoading,
                [id]: true
            };
            try {
                const detail = await this.apiRequest('/api/system/request-history/' + encodeURIComponent(id), {
                    timeoutMs: 5000
                });
                const detailKey = String(detail && detail.history_key || '').trim();
                const detailId = String(detail && detail.id || '').trim();
                const index = this.requestHistory.findIndex(item => {
                    if (!item) return false;
                    return String(item.history_key || '').trim() === (detailKey || id)
                        || String(item.history_key || '').trim() === id
                        || (
                            !detailKey
                            && detailId
                            && String(item.id || '').trim() === detailId
                        )
                        || String(item.id || '').trim() === id;
                });
                if (index >= 0) {
                    const updated = {
                        ...this.requestHistory[index],
                        ...(detail || {}),
                        detail_loaded: true,
                        has_detail: true
                    };
                    const nextHistory = this.requestHistory.slice();
                    nextHistory[index] = updated;
                    this.requestHistory = nextHistory;
                    return updated;
                }
                return detail || null;
            } catch (error) {
                this.notify('加载请求详情失败: ' + error.message, 'error');
                return null;
            } finally {
                const nextLoading = { ...this.requestHistoryDetailLoading };
                delete nextLoading[id];
                this.requestHistoryDetailLoading = nextLoading;
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
            const name = this.resolveExistingPresetName(this.currentConfig, this.getActivePresetName())
            const configuredDefault = this.currentConfig.default_preset
            return presets[name]
                || (configuredDefault ? presets[configuredDefault] : null)
                || presets['主预设']
                || Object.values(presets)[0]
                || null
        },

        resolveExistingPresetName(site, presetName) {
            const presets = site && site.presets
            const normalized = String(presetName || '').trim()
            if (!presets || typeof presets !== 'object' || !normalized) {
                return normalized
            }
            if (presets[normalized]) {
                return normalized
            }
            if (normalized.startsWith('预设_')) {
                const stripped = normalized.slice(3).trim()
                if (stripped && presets[stripped]) {
                    return stripped
                }
            } else {
                const prefixed = '预设_' + normalized
                if (presets[prefixed]) {
                    return prefixed
                }
            }
            return normalized
        },

        // ========== 数据操作 ==========

        normalizeConfig(raw) {
            const norm = {}
            // 预设内的字段列表（用于清理顶层残留）
            const PRESET_FIELDS = [
                'selectors', 'workflow', 'stealth', 'stream_config',
                'image_extraction', 'file_paste', 'prompt_padding',
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

                if (step.action === 'COORD_SCROLL') {
                    const startX = Number(step.value?.start_x)
                    const startY = Number(step.value?.start_y)
                    const endX = Number(step.value?.end_x)
                    const endY = Number(step.value?.end_y)
                    if (![startX, startY, endX, endY].every(Number.isFinite)) {
                        this.notify('步骤 ' + (i + 1) + ': 请输入完整的起点/终点坐标', 'error')
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
            } else if (step.action === 'PAGE_FETCH') {
                step.target = ''
                step.optional = true
                step.value = null
            } else if (step.action === 'READONLY_HINT') {
                step.target = ''
                const current = (step.value && typeof step.value === 'object' && !Array.isArray(step.value))
                    ? step.value
                    : {}
                step.value = {
                    title: String(current.title || '提示'),
                    text: String(current.text || '这是一条只读提示，不会在执行时触发页面操作。'),
                    tone: ['info', 'success', 'warning', 'danger'].includes(String(current.tone || '').trim().toLowerCase())
                        ? String(current.tone || '').trim().toLowerCase()
                        : 'info'
                }
            } else if (step.action === 'COORD_CLICK') {
                step.target = ''
                step.value = {
                    x: Number(step.value?.x ?? 0),
                    y: Number(step.value?.y ?? 0),
                    random_radius: Number(step.value?.random_radius ?? 10)
                }
            } else if (step.action === 'COORD_SCROLL') {
                step.target = ''
                step.value = {
                    start_x: Number(step.value?.start_x ?? 0),
                    start_y: Number(step.value?.start_y ?? 0),
                    end_x: Number(step.value?.end_x ?? 0),
                    end_y: Number(step.value?.end_y ?? 300)
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
                ],
                'experimental_hint': [
                    { action: 'READONLY_HINT', target: '', optional: false, value: { title: '提示', text: '这是一条只读提示，不会影响执行，只用于说明当前工作流中的特殊行为。', tone: 'info' } },
                    { action: 'PAGE_FETCH', target: '', optional: true, value: null },
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
            const activePresetName = this.getActivePresetName()
            const presetName = this.resolveExistingPresetName(site, activePresetName) || activePresetName
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

        restoreSitesCache() {
            const cached = loadStoredSitesCache()
            if (!cached || !cached.sites) {
                return
            }
            this.sites = this.normalizeConfig(cached.sites)
            const domains = Object.keys(this.sites)
            if (domains.length === 0) {
                return
            }
            if (cached.currentDomain && this.sites[cached.currentDomain]) {
                this.currentDomain = cached.currentDomain
                return
            }
            if (!this.currentDomain) {
                this.currentDomain = domains[0]
            }
        },

        async refreshStatus() {
            const [configOk, healthOk] = await Promise.all([
                this.loadConfig(true),
                this.loadHealthStatus({ timeoutMs: 2500 }),
                this.fetchSystemStats({ timeoutMs: 5000 })
            ])

            if (configOk || healthOk) {
                this.notify('状态已刷新', 'success')
            } else {
                this.notify('刷新失败', 'error')
            }
        },

        async fetchSystemStats({ timeoutMs = 0 } = {}) {
            if (this.isFetchingSystemStats) {
                return this.systemStats
            }
            this.isFetchingSystemStats = true
            try {
                this.systemStats = await this.apiRequest('/api/system/stats', {
                    timeoutMs: timeoutMs || 5000
                })
                return this.systemStats
            } catch (error) {
                return this.systemStats
            } finally {
                this.isFetchingSystemStats = false
            }
        },

        async loadHealthStatus({ silent = false, timeoutMs = 0 } = {}) {
            try {
                const health = await this.apiRequest('/health', {
                    timeoutMs: timeoutMs || 2500
                })
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
})();
