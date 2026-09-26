// R2-8：由 dashboard-methods.js 拆分——图片配置。方法原样迁出，由 dashboard-methods.js 统一组装。
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
        // ========== 图片配置 (新增) ==========

        // 🆕 更新图片配置
        async updateImageConfig(newConfig) {
            if (!this.currentDomain || !this.currentConfig) return;

            const domain = this.currentDomain
            const pc = this.getActivePresetConfig()
            const presetName = this.getActivePresetName()
            const nextConfig = JSON.parse(JSON.stringify(newConfig || {}))
            const saveSeq = Number(this.imageConfigSaveSeq || 0) + 1
            this.imageConfigSaveSeq = saveSeq

            if (pc) pc.image_extraction = nextConfig;

            const previousSave = this.imageConfigSaveQueue || Promise.resolve()
            const saveRequest = previousSave
                .catch(() => undefined)
                .then(() => {
                    const payload = { ...nextConfig, preset_name: presetName }
                    return this.apiRequest(`/api/sites/${encodeURIComponent(domain)}/image-config`, {
                        method: 'PUT',
                        body: JSON.stringify(payload)
                    })
                })
            this.imageConfigSaveQueue = saveRequest

            try {
                await saveRequest
                if (
                    saveSeq === this.imageConfigSaveSeq
                    && domain === this.currentDomain
                    && presetName === this.getActivePresetName()
                ) {
                    this.notify('多模态提取配置已保存', 'success')
                }
            } catch (error) {
                if (
                    saveSeq === this.imageConfigSaveSeq
                    && domain === this.currentDomain
                    && presetName === this.getActivePresetName()
                ) {
                    console.error('保存图片配置失败:', error)
                    this.notify('保存多模态提取配置失败: ' + error.message, 'error')
                    await this.reloadConfig()
                }
            } finally {
                if (this.imageConfigSaveQueue === saveRequest) {
                    this.imageConfigSaveQueue = null
                }
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

        formatOfficialConfigSourceTime(value) {
            const raw = String(value || '').trim();
            if (!raw) return '';
            const timestamp = new Date(raw);
            if (Number.isNaN(timestamp.getTime())) return raw;
            return timestamp.toLocaleString('zh-CN', { hour12: false });
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
                this.mainCompareSummaryPath = String(data.path || 'config/sites/index.json').trim() || 'config/sites/index.json';
                this.mainCompareSummarySource = {
                    repository: '',
                    branch: 'main',
                    status: '',
                    stale: false,
                    fetched_at: '',
                    checked_at: '',
                    warning: '',
                    ...(data.source || {})
                };
                return true;
            } catch (error) {
                this.mainCompareSummaryItems = [];
                this.mainCompareSummarySource = {
                    repository: '',
                    branch: 'main',
                    status: '',
                    stale: false,
                    fetched_at: '',
                    checked_at: '',
                    warning: ''
                };
                this.mainCompareSummaryError = formatGitCompareErrorText(error);
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
            this.siteConfigView = 'editor';

            const configTab = await this.waitForConfigTabRef();
            if (!configTab) {
                this.notify('配置面板尚未准备好', 'error');
                return;
            }

            const targetPreset = String(item.local_preset_name || '').trim();
            await configTab.openConfigCompareForPreset(targetPreset);
        },
    });
})();
