// R2-8：由 dashboard-methods.js 拆分——环境配置、浏览器常量与更新白名单。方法原样迁出，由 dashboard-methods.js 统一组装。
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
        // ========== 环境配置 ==========

        async loadEnvConfig() {
            this.isLoadingEnv = true;
            try {
                const data = await this.apiRequest('/api/settings/env');
                const fileConfig = data.config || {};
                // 修复：DASHBOARD_AUTH_ENABLED 未写进 .env 时，后端运行时语义是「沿用 AUTH_ENABLED」。
                // 若这里直接用 schema 默认值 false 填充，用户保存任意一项都会把
                // DASHBOARD_AUTH_ENABLED=false 写进 .env，静默关掉控制面板认证。
                // 后端在 effective 里给出真实生效值，仅在 .env 确实缺该键时采纳。
                const effective = (data.effective && typeof data.effective === 'object') ? data.effective : {};
                const inherited = {};
                if (!Object.prototype.hasOwnProperty.call(fileConfig, 'DASHBOARD_AUTH_ENABLED')
                    && Object.prototype.hasOwnProperty.call(effective, 'DASHBOARD_AUTH_ENABLED')) {
                    inherited.DASHBOARD_AUTH_ENABLED = !!effective.DASHBOARD_AUTH_ENABLED;
                }
                this.envConfig = {
                    ...this.getEnvDefaults(),
                    ...inherited,
                    ...fileConfig
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
                const scheduledRestartKeys = launcherKeys.filter((key) => String(key || '').startsWith('SCHEDULED_RESTART_'));
                const browserRestartKeys = launcherKeys.filter((key) => !String(key || '').startsWith('SCHEDULED_RESTART_'));

                if (browserRestartKeys.length > 0) {
                    const launcherLabels = browserRestartKeys.map((key) => {
                        return this.getEnvFieldMeta(key)?.label || key;
                    }).join(', ');

                    this.notify(
                        '环境配置已保存。服务会自动重启，但以下启动型配置要完全生效，请关闭当前浏览器和脚本后重新运行 start.bat：' + launcherLabels,
                        'warning'
                    );
                } else if (scheduledRestartKeys.length > 0) {
                    this.notify('服务守护配置已保存，后端会自动重启；浏览器和现有标签页保持不变', 'success');
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
                // 修复：browserConstantsRaw 只在首屏/首次进设置页加载各一次，期间「标签页池」页签
                // 可能已通过 PUT /api/tab-pool/config 改过同一个 browser_config.json。用陈旧快照做
                // merge 基底会把 route_groups / excluded_urls / allocation_mode 等整块回退。
                // 这里只重新拉取 raw 基底，不调用 loadBrowserConstants()，以免覆盖用户当前未保存的编辑。
                let rawBase = this.browserConstantsRaw;
                try {
                    const fresh = await this.apiRequest('/api/settings/browser-constants');
                    if (fresh && fresh.config && typeof fresh.config === 'object') {
                        rawBase = fresh.config;
                    }
                } catch (error) {
                    console.warn('刷新浏览器常量基底失败，沿用上次快照:', error);
                }

                const payload = this.serializeBrowserConstants(this.browserConstants, rawBase);
                await this.apiRequest('/api/settings/browser-constants', {
                    method: 'POST',
                    body: JSON.stringify({ config: payload })
                });

                this.browserConstantsRaw = JSON.parse(JSON.stringify(payload));
                this.browserConstants = this.normalizeBrowserConstantsForEditor(payload);
                this.browserConstantsOriginal = JSON.parse(JSON.stringify(this.browserConstants));
                this.applyDashboardRuntimeSettings();
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
    });
})();
