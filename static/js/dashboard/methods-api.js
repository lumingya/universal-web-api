// R2-8：由 dashboard-methods.js 拆分——API 调用与配置读写。方法原样迁出，由 dashboard-methods.js 统一组装。
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
        // ========== API 调用 ==========

        openTokenDialog() {
            if (this.showTokenDialog) {
                return
            }
            this.tempToken = getStoredDashboardToken()
            this.showTokenDialog = true
        },

        async apiRequest(url, options = {}) {
            const token = getStoredDashboardToken()
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
                        this.notify('控制面板认证失败，请检查访问密钥', 'error')
                        this.openTokenDialog()
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

            const requestSeq = Number(this.configLoadSeq || 0) + 1
            this.configLoadSeq = requestSeq
            this.isLoading = true
            try {
                const data = await this.apiRequest('/api/config', { timeoutMs: 5000 })
                if (requestSeq !== this.configLoadSeq) {
                    return false
                }

                this.sites = this.normalizeConfig(data)
                if (!this.currentDomain && Object.keys(this.sites).length > 0) {
                    this.currentDomain = Object.keys(this.sites)[0]
                }
                window.saveStoredSitesCache(this.sites, this.currentDomain)

                if (!silent) {
                    this.notify('配置已刷新 (' + Object.keys(this.sites).length + ' 个站点)', 'success')
                }
                return true
            } catch (error) {
                if (requestSeq !== this.configLoadSeq) {
                    return false
                }
                this.notify('加载配置失败: ' + error.message, 'error')
                if (Object.keys(this.sites || {}).length === 0) {
                    this.sites = {}
                }
                return false
            } finally {
                if (requestSeq === this.configLoadSeq) {
                    this.isLoading = false
                }
            }
        },

        // 修复：FilePastePanel / PromptPaddingPanel 编辑的是独立草稿，必须 flush 才会回写到
        // presetConfig。原先只有 saveConfig 调用，导致 JSON 预览既显示旧值也保存旧值。
        // 抽成方法后在读取/保存配置的各处统一调用。
        flushConfigTabDrafts() {
            if (this.$refs && this.$refs.configTab && typeof this.$refs.configTab.flushMutableSectionDrafts === 'function') {
                this.$refs.configTab.flushMutableSectionDrafts()
            }
        },

        async saveConfig() {
            if (!this.validateConfig()) {
                return
            }

            this.isSaving = true
            try {
                this.flushConfigTabDrafts()
                const tab = this.$refs?.configTab, configRef = tab?.currentConfig, transferRevision = tab?.pendingPresetTransfer
                await this.apiRequest('/api/config', {
                    method: 'POST',
                    body: JSON.stringify({ config: this.sites })
                })
                if (tab && tab.currentConfig === configRef && tab.pendingPresetTransfer === transferRevision) tab.pendingPresetTransfer = 0
                this.studioLastSavedAt = new Date().toLocaleTimeString('zh-CN', { hour12: false })
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
                        highlight: this.testHighlight,
                        route_domain: this.currentDomain || ''
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
                            timeout: 2,
                            route_domain: this.currentDomain || ''
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
    });
})();
