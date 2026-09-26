// R2-8：由 dashboard-methods.js 拆分——元素定义与预设辅助。方法原样迁出，由 dashboard-methods.js 统一组装。
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

        handleSidebarPrimaryAction(tab) {
            if (tab === 'commands') {
                this.changeTab('commands')
                this.$nextTick(() => {
                    if (this.$refs.commandsTab && typeof this.$refs.commandsTab.openNewCommand === 'function') {
                        this.$refs.commandsTab.openNewCommand()
                    }
                })
                return
            }
            this.addNewSite()
        },

        async ensureTabDataLoaded(tab) {
            if (tab === 'monitor') {
                const now = Date.now()
                const stale = now - Number(this.requestHistoryFetchedAt || 0) > 2000
                const loaders = [];
                if (this.isRequestMonitorEnabled()) {
                    loaders.push(this.fetchRequestHistory({ silent: true, ifChanged: !stale }));
                } else {
                    this.requestHistory = [];
                    this.requestHistoryRevision = '';
                    this.requestHistoryError = '';
                }
                if (this.isSystemStatsPollingEnabled()) {
                    loaders.push(this.fetchSystemStats({ timeoutMs: 2500 }));
                }
                await Promise.all(loaders);
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
            if (!this.isRequestMonitorEnabled()) {
                this.requestHistory = [];
                this.requestHistoryMaxRecords = 0;
                this.requestHistoryRevision = '';
                this.requestHistoryError = '';
                this.requestHistoryPendingRefresh = null;
                return this.requestHistory;
            }
            if (this.requestHistoryLoading) {
                this.requestHistoryPendingRefresh = {
                    silent: this.requestHistoryPendingRefresh
                        ? (this.requestHistoryPendingRefresh.silent && silent)
                        : silent,
                    ifChanged: this.requestHistoryPendingRefresh
                        ? (this.requestHistoryPendingRefresh.ifChanged && ifChanged)
                        : ifChanged,
                    force: Boolean(force || (this.requestHistoryPendingRefresh && this.requestHistoryPendingRefresh.force))
                };
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
            const requestSeq = Number(this.requestHistoryRequestSeq || 0) + 1;
            this.requestHistoryRequestSeq = requestSeq;
            try {
                const maxRecords = this.getBrowserConstantNumber('REQUEST_MONITOR_MAX_RECORDS', 200, { min: 0, max: 2000 });
                if (maxRecords <= 0) {
                    this.requestHistory = [];
                    this.requestHistoryMaxRecords = 0;
                    this.requestHistoryRevision = '';
                    this.requestHistoryError = '';
                    return this.requestHistory;
                }
                const params = new URLSearchParams({ limit: String(Math.max(1, Math.floor(maxRecords))) });
                if (ifChanged && !force && this.requestHistoryRevision) {
                    params.set('if_revision', String(this.requestHistoryRevision));
                }
                const data = await this.apiRequest('/api/system/request-history?' + params.toString(), {
                    timeoutMs: 5000
                });
                if (!this.isRequestMonitorEnabled()) {
                    this.requestHistory = [];
                    this.requestHistoryRevision = '';
                    this.requestHistoryFetchedAt = Date.now();
                    this.requestHistoryError = '';
                    return this.requestHistory;
                }
                if (requestSeq !== this.requestHistoryRequestSeq) {
                    return this.requestHistory;
                }
                if (data && data.enabled === false) {
                    this.requestHistory = [];
                    this.requestHistoryMaxRecords = 0;
                    this.requestHistoryRevision = '';
                    this.requestHistoryFetchedAt = Date.now();
                    this.requestHistoryError = '';
                    return this.requestHistory;
                }
                const revision = String(data.revision || '');
                this.requestHistoryMaxRecords = Math.max(
                    0,
                    Number(data && data.max_records || maxRecords || 0)
                );
                if (data.not_modified && revision && revision === this.requestHistoryRevision) {
                    this.requestHistoryFetchedAt = Date.now();
                    this.requestHistoryError = '';
                    return this.requestHistory;
                }
                if (!ifChanged || force || !this.requestHistoryRevision || revision !== this.requestHistoryRevision) {
                    const detailCache = new Map(
                        this.requestHistory
                            .filter(item => item && item.detail_loaded && item.id)
                            .map(item => [String(item.history_key || item.id), {
                                prompt: item.prompt,
                                response: item.response,
                                error_stack: item.error_stack,
                                // 修复：这里原本缓存 payload / response_payload，但后端从未产出这两个键，
                                // 每次轮询都会把两个 undefined 键塞进每条记录，已删除。
                                // 同时补上 error_message / summary：不缓存的话，下一次轮询会用列表里的
                                // 截断版本覆盖掉已加载的完整详情。
                                error_message: item.error_message,
                                summary: item.summary,
                                detail_loaded: true,
                                has_detail: true
                            }])
                    );
                    const records = Array.isArray(data.records) ? data.records : [];
                    this.requestHistory = records.map(item => {
                        const cached = detailCache.get(String(item && (item.history_key || item.id) || ''));
                        return cached ? { ...item, ...cached, detail_loaded: true, has_detail: true } : item;
                    });
                    this.requestHistoryRevision = revision;
                }
                this.requestHistoryFetchedAt = Date.now();
                this.requestHistoryError = '';
                return this.requestHistory;
            } catch (error) {
                if (!silent) {
                    this.requestHistoryError = error.message || '请求历史加载失败';
                }
                return this.requestHistory;
            } finally {
                if (requestSeq === this.requestHistoryRequestSeq) {
                    this.requestHistoryLoading = false;
                    const pending = this.requestHistoryPendingRefresh;
                    this.requestHistoryPendingRefresh = null;
                    if (pending) {
                        this.fetchRequestHistory(pending).catch(() => {});
                    }
                }
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
                    const current = this.requestHistory[index];
                    const detailPayload = detail && typeof detail === 'object' ? detail : {};
                    const updated = {
                        ...current,
                        prompt: detailPayload.prompt ?? current.prompt,
                        response: detailPayload.response ?? current.response,
                        error_stack: detailPayload.error_stack ?? current.error_stack,
                        // 修复：列表投影会把 error_message 截到 800 字、summary 截到 240 字，
                        // 详情接口返回的才是完整原文；此前白名单漏掉这两个键，详情抽屉永远显示截断版本。
                        error_message: detailPayload.error_message ?? current.error_message,
                        summary: detailPayload.summary ?? current.summary,
                        // 修复：这里原本还合并 payload / response_payload，但后端从未产出这两个键，只会
                        // 给记录塞进两个 undefined 字段，已删除。
                        token_estimate: detailPayload.token_estimate ?? current.token_estimate,
                        detail_text_lengths: detailPayload.detail_text_lengths ?? current.detail_text_lengths,
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
    });
})();
