// R2-8：由 dashboard-methods.js 拆分——工具功能。方法原样迁出，由 dashboard-methods.js 统一组装。
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
            // 修复：先把面板草稿回写到 presetConfig，否则预览显示的是未包含本次编辑的旧值
            this.flushConfigTabDrafts()
            const config = this.getActivePresetConfig() || {}
            return JSON.parse(JSON.stringify(config))
        },

        async saveJsonPreview(rawText) {
            // 修复：保存前同样要 flush 面板草稿，否则这条保存路径会静默丢弃用户未回写的编辑
            this.flushConfigTabDrafts()
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
            const token = this.tempToken.trim()
            if (token) {
                setStoredDashboardToken(token)
                this.notify('控制面板访问密钥已保存', 'success')
            } else {
                setStoredDashboardToken('')
                this.notify('控制面板访问密钥已清除', 'info')
            }
            this.syncTokenPresence()

            this.showTokenDialog = false
            this.tempToken = ''

            this.loadConfig(true)
        },

        restoreSitesCache() {
            const cached = window.loadStoredSitesCache()
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
                this.fetchSystemStats({ timeoutMs: 2500 })
            ])

            if (configOk || healthOk) {
                this.notify('状态已刷新', 'success')
            } else {
                this.notify('刷新失败', 'error')
            }
        },

        async fetchSystemStats({ timeoutMs = 0 } = {}) {
            if (!this.getBrowserConstantBool('DASHBOARD_SYSTEM_STATS_ENABLED', true)) {
                return this.systemStats
            }
            if (this.isFetchingSystemStats) {
                return this.systemStatsRequestPromise || this.systemStats
            }
            this.isFetchingSystemStats = true
            const requestPromise = this.apiRequest('/api/system/stats', {
                timeoutMs: timeoutMs || 2500
            })
                .then((stats) => {
                    this.systemStats = stats
                    return this.systemStats
                })
                .catch(() => this.systemStats)
                .finally(() => {
                    if (this.systemStatsRequestPromise === requestPromise) {
                        this.systemStatsRequestPromise = null
                        this.isFetchingSystemStats = false
                    }
                })
            this.systemStatsRequestPromise = requestPromise
            return requestPromise
        },

        async loadHealthStatus({ silent = false, timeoutMs = 0 } = {}) {
            try {
                const health = await this.apiRequest('/health', {
                    timeoutMs: timeoutMs || 2500
                })
                this.browserStatus = health.browser || {}
                this.authEnabled = health.config?.dashboard_auth_enabled ?? health.config?.auth_enabled ?? false
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
    });
})();
