// R2-8：由 dashboard-methods.js 拆分——配置导入与导出。方法原样迁出，由 dashboard-methods.js 统一组装。
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
        // ========== 导入功能（支持全量和单站点） ==========

        triggerImport() {
            this.forceSingleSiteImport = false;
            this.singleSiteImportTargetDomain = '';
            if (this.$refs.importFileInput) {
                this.$refs.importFileInput.click();
            }
        },

        triggerImportSingleSite() {
            this.forceSingleSiteImport = true;
            this.singleSiteImportTargetDomain = this.currentDomain || '';
            if (this.$refs.importFileInput) {
                this.$refs.importFileInput.click();
            }
        },

        handleImportFile(event) {
            const file = event.target.files[0];
            if (!file) return;

            const sizeError = importFileSizeError(file, SITE_CONFIG_IMPORT_MAX_BYTES, '配置文件');
            if (sizeError) {
                this.notify(sizeError, 'error');
                this.singleSiteImportTargetDomain = '';
                this.forceSingleSiteImport = false;
                event.target.value = '';
                return;
            }

            this.importFileName = file.name;

            const reader = new FileReader();
            reader.onerror = () => {
                this.notify('读取导入文件失败', 'error');
                this.singleSiteImportTargetDomain = '';
                this.forceSingleSiteImport = false;
            };
            reader.onload = (e) => {
                try {
                    const config = JSON.parse(e.target.result);

                    // 检测是单站点还是全量配置
                    const detectResult = this.detectConfigType(config, this.forceSingleSiteImport);

                    if (!detectResult.valid) {
                        this.notify('导入文件格式无效', 'error');
                        return;
                    }

                    this.importType = detectResult.type;
                    this.importedConfig = detectResult.normalizedConfig;
                    this.singleSiteImportDomain = detectResult.suggestedDomain || this.singleSiteImportTargetDomain || this.currentDomain || '';
                    this.singleSiteImportTargetDomain = '';
                    this.forceSingleSiteImport = false;
                    this.showImportDialog = true;
                } catch (error) {
                    this.notify('JSON 解析失败: ' + error.message, 'error');
                    this.singleSiteImportTargetDomain = '';
                    this.forceSingleSiteImport = false;
                }
            };
            reader.readAsText(file);

            event.target.value = '';
        },

        // 从配置内容内部提取站点域名标识（优先识别文件内部元数据与单站点包装）
        extractDomainFromContent(config, knownDomains = []) {
            if (!config || typeof config !== 'object' || Array.isArray(config)) {
                return '';
            }

            // 1. 严格限定域名语义字段，避免被通用的 name/id 字段误导
            const candidateKeys = ['domain', 'site_domain', 'target_domain', 'site', '_domain'];
            for (const key of candidateKeys) {
                const val = config[key];
                if (typeof val === 'string') {
                    let cleanVal = val.trim();
                    if (!cleanVal || cleanVal.startsWith('_')) continue;
                    // 剥离协议头、端口与路径（如 https://chat.deepseek.com/ -> chat.deepseek.com）
                    cleanVal = cleanVal.replace(/^https?:\/\//i, '').split('/')[0].split(':')[0].trim();
                    if (!cleanVal) continue;

                    const matched = knownDomains.find(d => d.toLowerCase() === cleanVal.toLowerCase());
                    if (matched) return matched;
                    // 基本域名格式特征：包含点且由合法字符构成
                    if (/^[a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z0-9]+$/.test(cleanVal)) {
                        return cleanVal;
                    }
                }
            }

            // 2. 检查单站点包装格式（如 { "chat.deepseek.com": { presets: ... } }）
            const validKeys = Object.keys(config).filter(k => k && !k.startsWith('_'));
            if (validKeys.length === 1 && this.validateSingleSiteConfig(config[validKeys[0]])) {
                const singleKey = validKeys[0].trim();
                const matched = knownDomains.find(d => d.toLowerCase() === singleKey.toLowerCase());
                return matched || singleKey;
            }

            return '';
        },

        // 从文件名中清洗并提取站点域名（支持去除 (4)、_1、- 副本、-config 等）
        extractDomainFromFilename(filename, knownDomains = []) {
            if (!filename || typeof filename !== 'string') {
                return '';
            }

            let name = filename.trim();
            // 剥离 .json 后缀
            name = name.replace(/\.json$/i, '').trim();

            // 循环剥离末尾的重复下载序号、括号序号、副本标记与配置后缀
            const dupSuffixRegex = /(?:\s*\(\s*\d+\s*\)|\s*\[\s*\d+\s*\]|[-_\s]+\d+|[-_\s]*(?:副本|复本|copy)(?:\s*\(\d+\))?)$/i;
            const configSuffixRegex = /[-_.]*(?:(?:sites?|site|presets?)[-_.]*)?config(?:uration)?$/i;

            let prev = null;
            let loopCount = 0;
            while (prev !== name && loopCount < 10) {
                prev = name;
                name = name.replace(dupSuffixRegex, '').trim();
                name = name.replace(configSuffixRegex, '').trim();
                loopCount++;
            }

            if (!name) return '';

            // 1. 优先完全匹配已知站点域名（不区分大小写）
            const exactMatch = knownDomains.find(d => d.toLowerCase() === name.toLowerCase());
            if (exactMatch) return exactMatch;

            // 2. 若清洗后的 name 本身符合标准域名结构，直接采纳，严禁退化去匹配更短的父域或任意子串
            if (/^[a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z0-9]+$/.test(name)) {
                return name;
            }

            // 3. 仅当末尾带有未剥离的连接符后缀时，按最长前缀优先匹配已有站点（如 chat.deepseek.com-v2）
            const sortedKnown = [...knownDomains].sort((a, b) => b.length - a.length);
            const lowN = name.toLowerCase();
            const prefixMatch = sortedKnown.find(d => {
                const lowD = d.toLowerCase();
                return lowN.startsWith(lowD + '-') || lowN.startsWith(lowD + '_') || lowN.startsWith(lowD + '.');
            });
            if (prefixMatch) return prefixMatch;

            return name;
        },

        // 检测配置类型：全量配置 or 单站点配置
        detectConfigType(config, forceSingle = false) {
            if (typeof config !== 'object' || config === null || Array.isArray(config)) {
                return { valid: false };
            }

            const knownDomains = Object.keys(this.sites || {}).filter(k => k && !k.startsWith('_'));
            // 优先从文件内容内部提取站点域名标识，次选从清洗后的文件名提取
            const contentDomain = this.extractDomainFromContent(config, knownDomains);
            const filenameDomain = this.extractDomainFromFilename(this.importFileName, knownDomains);
            const suggestedDomain = contentDomain || filenameDomain || this.singleSiteImportTargetDomain || '';

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

                return {
                    valid: true,
                    type: 'single',
                    normalizedConfig: config,
                    suggestedDomain: suggestedDomain || this.singleSiteImportTargetDomain || ''
                };
            }

            const keys = Object.keys(config).filter(k => k && !k.startsWith('_'));
            // 若显式指定单站点导入且文件只包含 1 个站点对象
            if (forceSingle && keys.length === 1 && this.validateSingleSiteConfig(config[keys[0]])) {
                return {
                    valid: true,
                    type: 'single',
                    normalizedConfig: config[keys[0]],
                    suggestedDomain: keys[0] || suggestedDomain || this.singleSiteImportTargetDomain || ''
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
            this.flushConfigTabDrafts()
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
            this.flushConfigTabDrafts()
            if (!domain || !this.sites[domain]) {
                this.notify('站点不存在', 'error');
                return;
            }

            // 导出整个站点（含所有预设与站点域名自描述）
            const siteConfig = this.sites[domain];
            const exportPayload = {
                ...siteConfig,
                domain: domain
            };
            const dataStr = JSON.stringify(exportPayload, null, 2);
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

            const sizeError = importFileSizeError(file, SETTINGS_BACKUP_IMPORT_MAX_BYTES, '备份文件');
            if (sizeError) {
                this.notify('完整备份导入失败: ' + sizeError, 'error');
                event.target.value = '';
                return;
            }

            const reader = new FileReader();
            reader.onerror = () => {
                this.notify('完整备份导入失败: 读取文件出错', 'error');
            };
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
            // S13：备份文件会被下载/转存/分享，浏览器本地保存的面板令牌不再写入备份。
            // 旧版备份里的 dashboard_token / api_token 仍可在导入时读取（见下方 apply）。
            return {
                dark_mode: !!this.darkMode
            };
        },

        applyDashboardPreferencesBackup(preferences) {
            if (!preferences || typeof preferences !== 'object') return;

            if (typeof preferences.dark_mode === 'boolean') {
                this.darkMode = preferences.dark_mode;
            }

            if (typeof preferences.dashboard_token === 'string' || typeof preferences.api_token === 'string') {
                const token = String(preferences.dashboard_token || preferences.api_token || '').trim();
                try {
                    setStoredDashboardToken(token);
                } catch (e) { }
                this.tempToken = token;
                this.syncTokenPresence();
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

                const redactedKeys = Array.isArray(payload && payload.redacted_env_keys)
                    ? payload.redacted_env_keys
                    : [];
                this.notify(
                    redactedKeys.length
                        ? `完整配置备份已导出（已剔除 ${redactedKeys.length} 项令牌/密钥，导入时保留目标机器现值）`
                        : '完整配置备份已导出',
                    'success'
                );
            } catch (error) {
                this.notify('完整备份导出失败: ' + error.message, 'error');
            }
        },

        // 修复：此处原有一份与下方「浏览器常量」区块完全相同的
        // normalizeBrowserConstantsForEditor / serializeBrowserConstants 重复定义（后定义者生效），
        // 已删除这份无效的前置副本，只保留下方那份。

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
    });
})();
