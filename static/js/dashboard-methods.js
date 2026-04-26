// Dashboard methods extracted from dashboard.js
(() => {
    const DEFAULT_SELECTOR_DEFINITIONS = window.DEFAULT_SELECTOR_DEFINITIONS || [];
    const BROWSER_CONSTANTS_SCHEMA = window.BROWSER_CONSTANTS_SCHEMA || {};
    const ENV_CONFIG_SCHEMA = window.ENV_CONFIG_SCHEMA || {};

    window.DashboardMethods = {

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
    };
})();
