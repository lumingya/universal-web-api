(function () {
    const MAIN_PRESET_NAME = '主预设';
    const REVIEW_TOKEN_STORAGE_KEY = 'marketplace_github_review_token';
    const MARKETPLACE_CLIENT_VERSION_STORAGE_KEY = 'marketplace_client_version';
    const MARKETPLACE_SEEN_SNAPSHOT_STORAGE_KEY = 'marketplace_seen_snapshot_v1';
    const MARKETPLACE_LATEST_SNAPSHOT_STORAGE_KEY = 'marketplace_latest_snapshot_v1';

    function deepClone(value) {
        return JSON.parse(JSON.stringify(value));
    }

    function safeString(value) {
        return String(value || '').trim();
    }

    function loadStoredMarketplaceClientVersion() {
        try {
            return String(localStorage.getItem(MARKETPLACE_CLIENT_VERSION_STORAGE_KEY) || '').trim();
        } catch (error) {
            return '';
        }
    }

    function saveStoredMarketplaceClientVersion(version) {
        try {
            const value = String(version || '').trim();
            if (value) {
                localStorage.setItem(MARKETPLACE_CLIENT_VERSION_STORAGE_KEY, value);
            } else {
                localStorage.removeItem(MARKETPLACE_CLIENT_VERSION_STORAGE_KEY);
            }
        } catch (error) {
            // ignore storage failures and keep runtime data available
        }
    }

    function saveStoredMarketplaceSnapshot(storageKey, snapshot) {
        try {
            if (!snapshot) {
                localStorage.removeItem(storageKey);
                return;
            }
            localStorage.setItem(storageKey, JSON.stringify(snapshot));
        } catch (error) {
            // ignore storage failures and keep runtime data available
        }
    }

    function buildMarketplaceSnapshot(catalog) {
        const items = Array.isArray(catalog && catalog.items) ? catalog.items : [];
        const ids = items
            .map((item) => safeString(item && item.id))
            .filter(Boolean)
            .sort((left, right) => left.localeCompare(right, 'zh-CN'));

        return {
            ids,
            count: ids.length,
            savedAt: Date.now()
        };
    }

    function createEmptyReviewSession() {
        return {
            connected: false,
            can_review: false,
            repo: '',
            repo_url: '',
            login: '',
            role_name: '',
            permission_label: '',
            permissions: {}
        };
    }

    Vue.createApp({
        data() {
            return {
                darkMode: true,
                loading: false,
                error: '',
                busyId: '',
                importSaving: false,
                submitSaving: false,
                commandLoading: false,
                searchQuery: '',
                selectedType: 'all',
                selectedSite: 'all',
                sortBy: 'downloads_desc',
                catalog: {
                    source_name: '本地插件市场',
                    source_url: '',
                    repo_url: '',
                    upload_url: '',
                    warning: '',
                    submit_mode: 'local',
                    submit_label: '投稿上传',
                    submit_help: '',
                    submit_target: '',
                    count: 0,
                    approved_count: 0,
                    pending_count: 0,
                    total_downloads: 0,
                    items: []
                },
                previewDetail: null,
                pendingImport: null,
                showPreviewDialog: false,
                showImportDialog: false,
                showSubmitDialog: false,
                showReviewDialog: false,
                importStrategy: 'overwrite',
                importPresetName: '',
                reviewToken: '',
                reviewChecking: false,
                reviewBusyId: '',
                reviewSession: createEmptyReviewSession(),
                appVersion: loadStoredMarketplaceClientVersion(),
                siteConfigs: {},
                commandOptions: [],
                submitForm: {
                    item_type: 'site_config',
                    title: '',
                    summary: '',
                    author: '本地投稿',
                    site_domain: '',
                    preset_name: '',
                    category: '',
                    version: '1.0.0',
                    compatibility: '',
                    tagsText: '',
                    selected_command_ids: [],
                    parser_id: '',
                    parser_class_name: '',
                    parser_module_name: '',
                    parser_source: '',
                    parser_patterns_text: '',
                    parser_filename: ''
                },
                toasts: []
            };
        },

        computed: {
            typeOptions() {
                return [
                    { value: 'all', label: '全部' },
                    { value: 'site_config', label: '站点配置' },
                    { value: 'command_bundle', label: '命令系统' },
                    { value: 'response_parser', label: '响应解析器' }
                ];
            },

            siteOptions() {
                const values = new Set();
                for (const item of this.catalog.items || []) {
                    const domain = safeString(item.site_domain || item.domain);
                    if (domain) {
                        values.add(domain);
                    }
                }
                return Array.from(values).sort((a, b) => a.localeCompare(b, 'zh-CN'));
            },

            filteredItems() {
                const search = this.searchQuery.toLowerCase();
                const items = (this.catalog.items || []).filter((item) => {
                    if (this.selectedType !== 'all' && item.item_type !== this.selectedType) {
                        return false;
                    }
                    if (this.selectedType !== 'command_bundle'
                        && this.selectedType !== 'response_parser'
                        && this.selectedSite !== 'all'
                        && safeString(item.site_domain) !== this.selectedSite) {
                        return false;
                    }
                    if (!search) {
                        return true;
                    }

                    const haystack = [
                        item.name,
                        item.summary,
                        item.author,
                        item.submitted_by,
                        item.site_domain,
                        item.category,
                        ...(Array.isArray(item.tags) ? item.tags : [])
                    ]
                        .filter(Boolean)
                        .join(' ')
                        .toLowerCase();
                    return haystack.includes(search);
                });

                items.sort((left, right) => this.compareItems(left, right));
                return items;
            },

            availableSites() {
                return Object.keys(this.siteConfigs || {}).sort((a, b) => a.localeCompare(b, 'zh-CN'));
            },

            availablePresets() {
                const domain = safeString(this.submitForm.site_domain);
                const site = domain ? this.siteConfigs[domain] : null;
                const presets = site && site.presets ? Object.keys(site.presets) : [];
                return presets.sort((a, b) => a.localeCompare(b, 'zh-CN'));
            },

            previewJsonText() {
                return JSON.stringify(this.previewDetail || {}, null, 2);
            },

            submissionPreviewText() {
                try {
                    return JSON.stringify(this.buildSubmissionPayload(), null, 2);
                } catch (error) {
                    return '// 预览暂不可用: ' + error.message;
                }
            },

            submitIsExternal() {
                return this.catalog.submit_mode !== 'local' && !!safeString(this.catalog.upload_url);
            },

            submitEntryLabel() {
                return safeString(this.catalog.submit_label) || (this.submitIsExternal ? '投稿到公共市场' : '投稿上传');
            },

            reviewEntryLabel() {
                if (this.reviewSession.can_review) {
                    return this.reviewSession.login
                        ? ('审核权限 · ' + this.reviewSession.login)
                        : '审核权限已连接';
                }
                if (this.reviewSession.connected && this.reviewSession.login) {
                    return 'GitHub 身份 · ' + this.reviewSession.login;
                }
                return 'GitHub 审核';
            },

            submitHelpText() {
                return safeString(this.catalog.submit_help)
                    || (this.submitIsExternal
                        ? '投稿会打开 GitHub 公共页面，完整预览 JSON 会先复制到剪贴板。'
                        : '投稿会直接写入当前实例的本地市场。');
            },

            sourceBadgeLabel() {
                if (this.catalog.source_mode === 'hybrid') {
                    return '公共索引 + 本地回退';
                }
                if (this.catalog.source_mode === 'remote') {
                    return 'GitHub 公共索引';
                }
                return '本地市场';
            },

            emptyStateDescription() {
                return this.submitIsExternal
                    ? '可以先提交一个站点配置或命令系统到公共市场，审核收录后会显示在这里。'
                    : '可以先上传一个站点配置或命令系统，列表会自动出现在这里。';
            }
        },

        mounted() {
            this.loadTheme();
            this.reviewToken = localStorage.getItem(REVIEW_TOKEN_STORAGE_KEY) || '';
            this.loadAppVersion({ silent: true }).finally(() => {
                this.loadCatalog();
                if (this.reviewToken) {
                    this.loadReviewStatus({ silent: true });
                }
            });
            window.addEventListener('keydown', this.handleKeydown);
        },

        beforeUnmount() {
            window.removeEventListener('keydown', this.handleKeydown);
        },

        methods: {
            syncMarketplaceSeenState(catalog) {
                const snapshot = buildMarketplaceSnapshot(catalog);
                saveStoredMarketplaceSnapshot(MARKETPLACE_LATEST_SNAPSHOT_STORAGE_KEY, snapshot);
                saveStoredMarketplaceSnapshot(MARKETPLACE_SEEN_SNAPSHOT_STORAGE_KEY, snapshot);
            },

            async apiRequest(url, options = {}) {
                const token = localStorage.getItem('api_token');
                const headers = {
                    'Content-Type': 'application/json',
                    ...(options.headers || {})
                };

                if (token) {
                    headers.Authorization = 'Bearer ' + token;
                }

                const response = await fetch(url, {
                    ...options,
                    headers
                });

                const rawText = await response.text();
                let payload = null;

                if (rawText) {
                    try {
                        payload = JSON.parse(rawText);
                    } catch (error) {
                        payload = rawText;
                    }
                }

                if (!response.ok) {
                    let message = '请求失败';
                    if (payload && typeof payload === 'object') {
                        if (typeof payload.detail === 'string') {
                            message = payload.detail;
                        } else if (payload.error && typeof payload.error.message === 'string') {
                            message = payload.error.message;
                        }
                    } else if (typeof payload === 'string' && payload) {
                        message = payload;
                    }

                    const error = new Error(message);
                    error.status = response.status;
                    throw error;
                }

                return payload;
            },

            loadTheme() {
                const stored = localStorage.getItem('darkMode');
                this.darkMode = stored !== 'false';
                this.applyTheme();
            },

            applyTheme() {
                // Tailwind dark mode toggle
                if (this.darkMode) {
                    document.documentElement.classList.add('dark');
                    document.body.classList.add('dark');
                    document.body.classList.remove('mp-light');
                } else {
                    document.documentElement.classList.remove('dark');
                    document.body.classList.remove('dark');
                    document.body.classList.add('mp-light');
                }
            },

            toggleDarkMode() {
                this.darkMode = !this.darkMode;
                localStorage.setItem('darkMode', String(this.darkMode));
                this.applyTheme();
            },

            goDashboard() {
                window.location.href = '/';
            },

            getMarketplaceClientVersion() {
                return safeString(this.appVersion) || loadStoredMarketplaceClientVersion();
            },

            appendMarketplaceClientVersion(url) {
                const target = safeString(url);
                const version = this.getMarketplaceClientVersion();
                if (!target || !version) {
                    return target;
                }
                const separator = target.includes('?') ? '&' : '?';
                return target + separator + 'app_version=' + encodeURIComponent(version);
            },

            async loadAppVersion({ silent = false } = {}) {
                try {
                    const health = await this.apiRequest('/health');
                    this.appVersion = safeString(health && health.version);
                    saveStoredMarketplaceClientVersion(this.appVersion);
                    return true;
                } catch (error) {
                    if (!silent) {
                        this.notify('读取版本信息失败: ' + error.message, 'warning');
                    }
                    return false;
                }
            },

            openLink(url) {
                const target = safeString(url);
                if (!target) {
                    return;
                }
                window.open(target, '_blank', 'noopener,noreferrer');
            },

            getReviewHeaders() {
                const token = safeString(this.reviewToken || localStorage.getItem(REVIEW_TOKEN_STORAGE_KEY));
                return token ? { 'X-GitHub-Token': token } : {};
            },

            openReviewDialog() {
                this.reviewToken = safeString(this.reviewToken || localStorage.getItem(REVIEW_TOKEN_STORAGE_KEY));
                this.showReviewDialog = true;
            },

            closeReviewDialog() {
                this.showReviewDialog = false;
            },

            async saveReviewToken() {
                const token = safeString(this.reviewToken);
                if (!token) {
                    this.notify('请先粘贴 GitHub Token', 'warning');
                    return;
                }

                localStorage.setItem(REVIEW_TOKEN_STORAGE_KEY, token);
                await this.loadReviewStatus();
            },

            async loadReviewStatus({ silent = false } = {}) {
                const token = safeString(this.reviewToken || localStorage.getItem(REVIEW_TOKEN_STORAGE_KEY));
                if (!token) {
                    this.reviewSession = createEmptyReviewSession();
                    return;
                }

                this.reviewChecking = true;
                try {
                    const data = await this.apiRequest('/api/marketplace/review/status', {
                        headers: this.getReviewHeaders()
                    });
                    this.reviewSession = {
                        ...createEmptyReviewSession(),
                        ...(data || {}),
                        connected: true
                    };
                    this.reviewToken = token;
                    if (!silent) {
                        if (this.reviewSession.can_review) {
                            this.notify('GitHub 审核权限已连接', 'success');
                        } else {
                            this.notify('GitHub 身份已连接，可管理你自己的投稿', 'success');
                        }
                    }
                } catch (error) {
                    this.reviewSession = createEmptyReviewSession();
                    if (!silent) {
                        this.notify('GitHub 审核连接失败: ' + error.message, 'error');
                    }
                } finally {
                    this.reviewChecking = false;
                }
            },

            clearReviewToken() {
                localStorage.removeItem(REVIEW_TOKEN_STORAGE_KEY);
                this.reviewToken = '';
                this.reviewSession = createEmptyReviewSession();
                this.notify('已清除本地保存的 GitHub Token', 'success');
            },

            formatNumber(value) {
                const number = Number(value || 0);
                return Number.isFinite(number) ? number.toLocaleString('en-US') : '0';
            },

            typeLabel(type) {
                if (type === 'command_bundle') {
                    return '命令系统';
                }
                if (type === 'response_parser') {
                    return '响应解析器';
                }
                return '站点配置';
            },

            reviewLabel(item) {
                if (safeString(item && item.review_label)) {
                    return safeString(item.review_label);
                }
                return safeString(item && item.review_status) === 'pending' ? '待审核' : '';
            },

            isPending(item) {
                return safeString(item && item.review_status) === 'pending';
            },

            canImport(item) {
                return !item || !item.import_disabled;
            },

            canReviewItem(item) {
                return this.isPending(item)
                    && !!this.reviewSession.can_review
                    && Number(item && item.issue_number) > 0;
            },

            displayAuthor(item) {
                const author = safeString(item && item.author);
                const submittedBy = safeString(item && item.submitted_by);
                return author || submittedBy || '社区贡献';
            },

            showSubmitter(item) {
                const author = this.displayAuthor(item).toLowerCase();
                const submittedBy = safeString(item && item.submitted_by);
                return !!submittedBy && submittedBy.toLowerCase() !== author;
            },

            isItemOwner(item) {
                const login = safeString(this.reviewSession.login).toLowerCase();
                const submittedBy = safeString(item && item.submitted_by).toLowerCase();
                return !!login && !!submittedBy && login === submittedBy;
            },

            canRemoveItem(item) {
                if (!item || !item.id || !this.reviewSession.connected) {
                    return false;
                }
                if (this.isPending(item) && this.canReviewItem(item) && !this.isItemOwner(item)) {
                    return false;
                }
                return !!this.reviewSession.can_review || this.isItemOwner(item);
            },

            removeItemLabel(item) {
                return this.isPending(item) ? '撤回投稿' : '下架';
            },

            compareItems(left, right) {
                const leftName = safeString(left.name);
                const rightName = safeString(right.name);

                if (this.sortBy === 'updated_desc') {
                    return safeString(right.updated_at).localeCompare(safeString(left.updated_at)) || leftName.localeCompare(rightName, 'zh-CN');
                }
                if (this.sortBy === 'stars_desc') {
                    return Number(right.stars || 0) - Number(left.stars || 0) || leftName.localeCompare(rightName, 'zh-CN');
                }
                if (this.sortBy === 'name_asc') {
                    return leftName.localeCompare(rightName, 'zh-CN');
                }
                return Number(right.downloads || 0) - Number(left.downloads || 0) || leftName.localeCompare(rightName, 'zh-CN');
            },

            async loadCatalog({ force = false } = {}) {
                this.loading = true;
                this.error = '';

                try {
                    const suffix = force ? '?refresh=true' : '';
                    const url = this.appendMarketplaceClientVersion('/api/marketplace' + suffix);
                    const data = await this.apiRequest(url);
                    this.catalog = {
                        source_name: '本地插件市场',
                        source_url: '',
                        repo_url: '',
                        upload_url: '',
                        warning: '',
                        submit_mode: 'local',
                        submit_label: '投稿上传',
                        submit_help: '',
                        submit_target: '',
                        count: 0,
                        approved_count: 0,
                        pending_count: 0,
                        total_downloads: 0,
                        items: [],
                        ...(data || {})
                    };
                    this.syncMarketplaceSeenState(this.catalog);
                } catch (error) {
                    this.error = error.status === 401
                        ? '请先在控制台里配置 API Token，再打开插件市场。'
                        : error.message;
                } finally {
                    this.loading = false;
                }
            },

            async reviewItem(item, action) {
                if (!this.canReviewItem(item)) {
                    this.notify('当前没有可用的审核权限', 'warning');
                    return;
                }
                if (action === 'approve' && !this.canImport(item)) {
                    this.notify('这个投稿缺少 JSON，暂时不能直接通过', 'warning');
                    return;
                }

                const actionLabel = action === 'approve' ? '通过' : '拒绝';
                const confirmed = window.confirm(`确认要${actionLabel}这个投稿吗？`);
                if (!confirmed) {
                    return;
                }

                this.reviewBusyId = action + ':' + item.id;
                try {
                    const result = await this.apiRequest(
                        '/api/marketplace/review/issues/' + encodeURIComponent(item.issue_number) + '/' + action,
                        {
                            method: 'POST',
                            headers: this.getReviewHeaders(),
                            body: JSON.stringify({ note: '' })
                        }
                    );
                    this.notify((result && result.message) || ('已' + actionLabel + '投稿'), 'success');
                    await this.loadCatalog({ force: true });
                } catch (error) {
                    this.notify(actionLabel + '投稿失败: ' + error.message, 'error');
                } finally {
                    this.reviewBusyId = '';
                }
            },

            async removeItem(item) {
                if (!this.canRemoveItem(item)) {
                    this.notify('当前没有可用的下架权限', 'warning');
                    return;
                }

                const actionLabel = this.removeItemLabel(item);
                const confirmed = window.confirm(`确认要${actionLabel}这个项目吗？`);
                if (!confirmed) {
                    return;
                }

                this.reviewBusyId = 'remove:' + item.id;
                try {
                    const result = await this.apiRequest(
                        '/api/marketplace/review/items/' + encodeURIComponent(item.id) + '/remove',
                        {
                            method: 'POST',
                            headers: this.getReviewHeaders(),
                            body: JSON.stringify({ note: '' })
                        }
                    );
                    this.notify((result && result.message) || ('已' + actionLabel), 'success');
                    await this.loadCatalog({ force: true });
                } catch (error) {
                    this.notify(actionLabel + '失败: ' + error.message, 'error');
                } finally {
                    this.reviewBusyId = '';
                }
            },

            async previewItem(item) {
                if (!item || !item.id) {
                    return;
                }

                try {
                    const url = this.appendMarketplaceClientVersion('/api/marketplace/items/' + encodeURIComponent(item.id));
                    this.previewDetail = await this.apiRequest(url);
                    this.showPreviewDialog = true;
                } catch (error) {
                    this.notify('加载预览失败: ' + error.message, 'error');
                }
            },

            closePreviewDialog() {
                this.showPreviewDialog = false;
            },

            async copyPreviewJson() {
                await this.copyText(this.previewJsonText, '预览 JSON 已复制');
            },

            async startImportFromPreview() {
                const detail = this.previewDetail;
                if (!detail || !detail.id) {
                    return;
                }
                this.closePreviewDialog();
                await this.startImport(detail);
            },

            async startImport(item) {
                if (!item || !item.id) {
                    return;
                }

                this.busyId = item.id;
                try {
                    const url = this.appendMarketplaceClientVersion('/api/marketplace/items/' + encodeURIComponent(item.id));
                    const detail = await this.apiRequest(url);
                    if (detail.item_type === 'command_bundle') {
                        await this.importCommandBundle(detail);
                        return;
                    }
                    if (detail.item_type === 'response_parser') {
                        await this.importResponseParser(detail);
                        return;
                    }

                    this.pendingImport = detail;
                    this.importStrategy = 'overwrite';
                    this.importPresetName = safeString(detail.name || detail.preset_name || '市场预设');
                    this.showImportDialog = true;
                } catch (error) {
                    this.notify('加载导入内容失败: ' + error.message, 'error');
                } finally {
                    this.busyId = '';
                }
            },

            closeImportDialog() {
                this.showImportDialog = false;
                this.pendingImport = null;
                this.importStrategy = 'overwrite';
                this.importPresetName = '';
            },

            async confirmImport() {
                if (!this.pendingImport) {
                    return;
                }

                this.importSaving = true;
                try {
                    await this.loadSiteConfigs();
                    if (this.importStrategy === 'save_as_preset') {
                        await this.applySiteSaveAsPreset();
                    } else {
                        await this.applySiteOverwrite();
                    }
                    this.closeImportDialog();
                } catch (error) {
                    this.notify('导入失败: ' + error.message, 'error');
                } finally {
                    this.importSaving = false;
                }
            },

            async loadSiteConfigs() {
                const data = await this.apiRequest('/api/config');
                this.siteConfigs = this.normalizeConfig(data);
                return this.siteConfigs;
            },

            async saveSiteConfigs(config) {
                await this.apiRequest('/api/config', {
                    method: 'POST',
                    body: JSON.stringify({ config })
                });
                this.siteConfigs = this.normalizeConfig(config);
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
                    }
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

            normalizeConfig(raw) {
                const normalized = {};
                const presetFields = [
                    'selectors',
                    'workflow',
                    'stealth',
                    'stream_config',
                    'image_extraction',
                    'file_paste',
                    'extractor_id',
                    'extractor_verified'
                ];

                for (const [domain, value] of Object.entries(raw || {})) {
                    if (!value || typeof value !== 'object' || Array.isArray(value)) {
                        continue;
                    }

                    if (value.presets && typeof value.presets === 'object' && !Array.isArray(value.presets)) {
                        const presets = {};
                        for (const [presetName, presetData] of Object.entries(value.presets)) {
                            presets[presetName] = {
                                ...deepClone(presetData),
                                selectors: presetData && typeof presetData.selectors === 'object' && !Array.isArray(presetData.selectors)
                                    ? deepClone(presetData.selectors)
                                    : {},
                                workflow: Array.isArray(presetData && presetData.workflow)
                                    ? deepClone(presetData.workflow)
                                    : [],
                                stealth: !!(presetData && presetData.stealth)
                            };
                        }

                        const presetKeys = Object.keys(presets);
                        const configuredDefault = typeof value.default_preset === 'string' ? value.default_preset : '';
                        const defaultPreset = configuredDefault && presets[configuredDefault]
                            ? configuredDefault
                            : (presets[MAIN_PRESET_NAME] ? MAIN_PRESET_NAME : (presetKeys[0] || MAIN_PRESET_NAME));

                        const siteConfig = {
                            presets,
                            default_preset: defaultPreset
                        };

                        for (const [field, fieldValue] of Object.entries(value)) {
                            if (field !== 'presets' && field !== 'default_preset' && !presetFields.includes(field)) {
                                siteConfig[field] = deepClone(fieldValue);
                            }
                        }

                        normalized[domain] = siteConfig;
                        continue;
                    }

                    normalized[domain] = {
                        default_preset: MAIN_PRESET_NAME,
                        presets: {
                            [MAIN_PRESET_NAME]: {
                                ...deepClone(value),
                                selectors: value && typeof value.selectors === 'object' && !Array.isArray(value.selectors)
                                    ? deepClone(value.selectors)
                                    : {},
                                workflow: Array.isArray(value && value.workflow)
                                    ? deepClone(value.workflow)
                                    : [],
                                stealth: !!value.stealth
                            }
                        }
                    };
                }

                return normalized;
            },

            getSingleImportedSite(detail) {
                const siteConfig = detail && detail.site_config;
                if (!this.validateImportedConfig(siteConfig)) {
                    throw new Error('市场配置格式无效');
                }

                const domains = Object.keys(siteConfig || {});
                if (domains.length !== 1) {
                    throw new Error('当前只支持单站点配置导入');
                }

                const domain = domains[0];
                const normalized = this.normalizeConfig(siteConfig);
                const site = normalized[domain];
                if (!site) {
                    throw new Error('站点配置解析失败');
                }

                return { domain, site };
            },

            async applySiteOverwrite() {
                const imported = this.getSingleImportedSite(this.pendingImport);
                const nextConfig = deepClone(this.siteConfigs);
                nextConfig[imported.domain] = imported.site;
                await this.saveSiteConfigs(nextConfig);
                this.notify('站点配置已覆盖导入: ' + imported.domain, 'success');
            },

            async applySiteSaveAsPreset() {
                const imported = this.getSingleImportedSite(this.pendingImport);
                const newPresetName = safeString(this.importPresetName);
                if (!newPresetName) {
                    throw new Error('请填写另存为的预设名称');
                }

                const nextConfig = deepClone(this.siteConfigs);
                const targetSite = nextConfig[imported.domain]
                    ? deepClone(nextConfig[imported.domain])
                    : { default_preset: newPresetName, presets: {} };

                if (targetSite.presets && targetSite.presets[newPresetName]) {
                    throw new Error('该预设名称已存在，请换一个名称');
                }

                const importedPresets = imported.site.presets || {};
                const sourcePresetName = imported.site.default_preset && importedPresets[imported.site.default_preset]
                    ? imported.site.default_preset
                    : Object.keys(importedPresets)[0];

                if (!sourcePresetName) {
                    throw new Error('导入内容缺少预设');
                }

                targetSite.presets = targetSite.presets || {};
                targetSite.presets[newPresetName] = deepClone(importedPresets[sourcePresetName]);
                if (!targetSite.default_preset || !targetSite.presets[targetSite.default_preset]) {
                    targetSite.default_preset = newPresetName;
                }

                nextConfig[imported.domain] = targetSite;
                await this.saveSiteConfigs(nextConfig);
                this.notify('站点配置已另存为预设: ' + newPresetName, 'success');
            },

            async loadCommands() {
                this.commandLoading = true;
                try {
                    const data = await this.apiRequest('/api/commands');
                    const commands = Array.isArray(data && data.commands) ? data.commands : [];
                    commands.sort((a, b) => safeString(a.name).localeCompare(safeString(b.name), 'zh-CN'));
                    this.commandOptions = commands;
                    return commands;
                } catch (error) {
                    this.notify('加载命令列表失败: ' + error.message, 'error');
                    return [];
                } finally {
                    this.commandLoading = false;
                }
            },

            prepareCommandImportPayload(command) {
                const payload = deepClone(command || {});
                delete payload.id;
                delete payload.last_triggered;
                delete payload.trigger_count;
                return payload;
            },

            async importCommandBundle(detail) {
                const bundle = detail && detail.command_bundle;
                const commands = Array.isArray(bundle && bundle.commands) ? bundle.commands : [];
                if (!commands.length) {
                    throw new Error('命令包内容为空');
                }

                const idMap = {};
                const importedCommands = [];

                for (const command of commands) {
                    const originalId = command.id;
                    const payload = this.prepareCommandImportPayload(command);
                    const response = await this.apiRequest('/api/commands', {
                        method: 'POST',
                        body: JSON.stringify(payload)
                    });
                    const created = response.command;
                    importedCommands.push(created);
                    if (originalId && created && created.id) {
                        idMap[originalId] = created.id;
                    }
                }

                for (let index = 0; index < commands.length; index += 1) {
                    const original = commands[index];
                    const created = importedCommands[index];
                    if (!original || !created || !original.trigger) {
                        continue;
                    }

                    const trigger = deepClone(original.trigger);
                    if (trigger.command_id && idMap[trigger.command_id]) {
                        trigger.command_id = idMap[trigger.command_id];
                    }
                    if (Array.isArray(trigger.command_ids)) {
                        trigger.command_ids = trigger.command_ids.map((commandId) => idMap[commandId] || commandId);
                    }

                    await this.apiRequest('/api/commands/' + encodeURIComponent(created.id), {
                        method: 'PUT',
                        body: JSON.stringify({ trigger })
                    });
                }

                this.notify('命令系统已导入，共 ' + importedCommands.length + ' 条命令', 'success');
            },

            async importResponseParser(detail) {
                const parserPackage = detail && detail.parser_package;
                const parserId = safeString((parserPackage && parserPackage.parser_id) || detail.parser_id);
                if (!parserPackage || !parserId) {
                    throw new Error('解析器包内容无效');
                }

                const confirmed = window.confirm(
                    '导入解析器会写入 app/core/parsers 并注册到当前应用，这会执行 Python 代码。确认继续吗？'
                );
                if (!confirmed) {
                    return;
                }

                try {
                    const result = await this.apiRequest('/api/parsers/install', {
                        method: 'POST',
                        body: JSON.stringify({
                            parser_package: parserPackage,
                            overwrite: false
                        })
                    });
                    this.notify((result && result.message) || ('解析器已导入: ' + parserId), 'success');
                } catch (error) {
                    if (error.status !== 409) {
                        throw error;
                    }

                    const replace = window.confirm('同名解析器已存在。是否覆盖已安装的版本？');
                    if (!replace) {
                        return;
                    }

                    const result = await this.apiRequest('/api/parsers/install', {
                        method: 'POST',
                        body: JSON.stringify({
                            parser_package: parserPackage,
                            overwrite: true
                        })
                    });
                    this.notify((result && result.message) || ('解析器已覆盖安装: ' + parserId), 'success');
                }
            },

            resetSubmitForm() {
                this.submitForm = {
                    item_type: 'site_config',
                    title: '',
                    summary: '',
                    author: '本地投稿',
                    site_domain: '',
                    preset_name: '',
                    category: '',
                    version: '1.0.0',
                    compatibility: '',
                    tagsText: '',
                    selected_command_ids: [],
                    parser_id: '',
                    parser_class_name: '',
                    parser_module_name: '',
                    parser_source: '',
                    parser_patterns_text: '',
                    parser_filename: ''
                };
            },

            async openSubmitDialog() {
                this.resetSubmitForm();
                this.showSubmitDialog = true;
                try {
                    await this.loadSiteConfigs();
                    if (this.availableSites.length > 0) {
                        this.submitForm.site_domain = this.availableSites[0];
                        this.syncPresetSelection();
                    }
                } catch (error) {
                    this.notify('加载站点配置失败: ' + error.message, 'error');
                }
            },

            closeSubmitDialog() {
                this.showSubmitDialog = false;
            },

            async setSubmitType(type) {
                this.submitForm.item_type = type;
                if (type === 'command_bundle') {
                    this.submitForm.category = '命令系统';
                    if (!this.commandOptions.length) {
                        await this.loadCommands();
                    }
                    return;
                }
                if (type === 'response_parser') {
                    if (!this.submitForm.category
                        || this.submitForm.category === '命令系统'
                        || this.submitForm.category === this.submitForm.site_domain) {
                        this.submitForm.category = '响应解析器';
                    }
                    return;
                }

                if (this.availableSites.length > 0 && !this.submitForm.site_domain) {
                    this.submitForm.site_domain = this.availableSites[0];
                }
                this.syncPresetSelection();
            },

            syncPresetSelection() {
                if (this.submitForm.item_type !== 'site_config') {
                    return;
                }

                if (!this.submitForm.category
                    || this.submitForm.category === '命令系统'
                    || this.availableSites.includes(this.submitForm.category)) {
                    this.submitForm.category = this.submitForm.site_domain || '';
                }

                const presets = this.availablePresets;
                if (!presets.includes(this.submitForm.preset_name)) {
                    this.submitForm.preset_name = presets[0] || '';
                }
            },

            parseTags(text) {
                return String(text || '')
                    .split(/[,\n，]/)
                    .map((item) => item.trim())
                    .filter(Boolean);
            },

            normalizeParserModuleName(value) {
                const normalized = String(value || '')
                    .replace(/\.py$/i, '')
                    .trim()
                    .replace(/[^A-Za-z0-9_]+/g, '_')
                    .replace(/^_+|_+$/g, '');
                return normalized || '';
            },

            normalizeParserId(value) {
                const normalized = String(value || '')
                    .trim()
                    .replace(/[^A-Za-z0-9._-]+/g, '_')
                    .replace(/^_+|_+$/g, '');
                return normalized || '';
            },

            extractParserClassName(sourceText) {
                const text = String(sourceText || '');
                const parserMatch = text.match(/class\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*ResponseParser\b/);
                if (parserMatch) {
                    return parserMatch[1];
                }
                const anyClassMatch = text.match(/class\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(/);
                return anyClassMatch ? anyClassMatch[1] : '';
            },

            async handleParserFileChange(event) {
                const file = event && event.target && event.target.files && event.target.files[0];
                if (!file) {
                    return;
                }

                try {
                    const source = await file.text();
                    const moduleName = this.normalizeParserModuleName(file.name);
                    const parserId = this.normalizeParserId(moduleName.replace(/_parser$/i, ''));
                    const className = this.extractParserClassName(source);

                    this.submitForm.parser_filename = file.name;
                    this.submitForm.parser_source = source;
                    if (!this.submitForm.parser_module_name) {
                        this.submitForm.parser_module_name = moduleName;
                    }
                    if (!this.submitForm.parser_id) {
                        this.submitForm.parser_id = parserId;
                    }
                    if (!this.submitForm.parser_class_name) {
                        this.submitForm.parser_class_name = className;
                    }
                    if (!this.submitForm.title) {
                        this.submitForm.title = className || parserId || moduleName || file.name.replace(/\.py$/i, '');
                    }
                } catch (error) {
                    this.notify('读取解析器文件失败: ' + error.message, 'error');
                } finally {
                    if (event && event.target) {
                        event.target.value = '';
                    }
                }
            },

            sanitizeCommandForBundle(command) {
                const payload = deepClone(command || {});
                delete payload.last_triggered;
                delete payload.trigger_count;
                return payload;
            },

            getSelectedCommands() {
                const selectedIds = new Set(this.submitForm.selected_command_ids || []);
                return (this.commandOptions || [])
                    .filter((command) => selectedIds.has(command.id))
                    .map((command) => this.sanitizeCommandForBundle(command));
            },

            buildSubmissionPayload() {
                const title = safeString(this.submitForm.title);
                const summary = safeString(this.submitForm.summary);
                const author = safeString(this.submitForm.author || '本地投稿') || '本地投稿';
                const category = safeString(this.submitForm.category);
                const compatibility = safeString(this.submitForm.compatibility);
                const version = safeString(this.submitForm.version || '1.0.0') || '1.0.0';
                const tags = this.parseTags(this.submitForm.tagsText);

                if (!title) {
                    throw new Error('请填写标题');
                }
                if (!summary) {
                    throw new Error('请填写简介');
                }

                if (this.submitForm.item_type === 'command_bundle') {
                    const commands = this.getSelectedCommands();
                    if (!commands.length) {
                        throw new Error('请至少选择一个命令');
                    }

                    return {
                        item_type: 'command_bundle',
                        title,
                        summary,
                        author,
                        category: category || '命令系统',
                        compatibility,
                        version,
                        tags,
                        command_bundle: {
                            group_name: '',
                            commands
                        }
                    };
                }

                if (this.submitForm.item_type === 'response_parser') {
                    const parserId = this.normalizeParserId(this.submitForm.parser_id);
                    const className = String(this.submitForm.parser_class_name || '').trim();
                    const moduleName = this.normalizeParserModuleName(this.submitForm.parser_module_name);
                    const sourceCode = String(this.submitForm.parser_source || '');
                    const supportedPatterns = this.parseTags(this.submitForm.parser_patterns_text);

                    if (!parserId) {
                        throw new Error('请填写解析器 ID');
                    }
                    if (!className) {
                        throw new Error('请填写解析器类名');
                    }
                    if (!moduleName) {
                        throw new Error('请填写解析器模块名');
                    }
                    if (!sourceCode.trim()) {
                        throw new Error('请上传或粘贴解析器源码');
                    }

                    return {
                        item_type: 'response_parser',
                        title,
                        summary,
                        author,
                        category: category || '响应解析器',
                        compatibility,
                        version,
                        tags,
                        parser_package: {
                            parser_id: parserId,
                            class_name: className,
                            module_name: moduleName,
                            filename: moduleName + '.py',
                            name: title,
                            description: summary,
                            source_code: sourceCode,
                            supported_patterns: supportedPatterns
                        }
                    };
                }

                const siteDomain = safeString(this.submitForm.site_domain);
                if (!siteDomain) {
                    throw new Error('请选择站点');
                }

                const siteConfig = this.siteConfigs[siteDomain];
                if (!siteConfig || !siteConfig.presets) {
                    throw new Error('当前站点没有可投稿的配置');
                }

                const presetName = safeString(this.submitForm.preset_name || siteConfig.default_preset || MAIN_PRESET_NAME);
                const presetConfig = siteConfig.presets[presetName];
                if (!presetConfig) {
                    throw new Error('请选择可用预设');
                }

                return {
                    item_type: 'site_config',
                    title,
                    summary,
                    author,
                    category: category || siteDomain,
                    site_domain: siteDomain,
                    preset_name: presetName,
                    compatibility,
                    version,
                    tags,
                    site_config: {
                        [siteDomain]: {
                            default_preset: presetName,
                            presets: {
                                [presetName]: deepClone(presetConfig)
                            }
                        }
                    }
                };
            },

            async copySubmissionPreview() {
                await this.copyText(this.submissionPreviewText, '投稿预览已复制');
            },

            buildSubmissionJsonBlock(payload) {
                return [
                    '```json',
                    JSON.stringify(payload, null, 2),
                    '```'
                ].join('\n');
            },

            async submitItem() {
                let payload = null;
                try {
                    payload = this.buildSubmissionPayload();
                } catch (error) {
                    this.notify(error.message, 'warning');
                    return;
                }

                this.submitSaving = true;
                try {
                    const result = await this.apiRequest('/api/marketplace/items', {
                        method: 'POST',
                        body: JSON.stringify(payload)
                    });

                    if (result && result.mode === 'external' && safeString(result.submission_url)) {
                        const copied = await this.tryCopyText(this.buildSubmissionJsonBlock(payload));
                        this.openLink(result.submission_url);
                        this.notify(
                            copied
                                ? (result.message || '已打开 GitHub 公共投稿页，请把已复制的 JSON 代码块粘贴到“预览 JSON”下面')
                                : '已打开 GitHub 公共投稿页，但剪贴板复制失败，请手动复制 JSON 预览',
                            copied ? 'success' : 'warning'
                        );
                        this.closeSubmitDialog();
                        return;
                    }

                    this.notify((result && result.message) || '投稿已加入本地市场', 'success');
                    this.closeSubmitDialog();
                    await this.loadCatalog({ force: true });
                } catch (error) {
                    this.notify('投稿失败: ' + error.message, 'error');
                } finally {
                    this.submitSaving = false;
                }
            },

            async tryCopyText(text) {
                try {
                    await navigator.clipboard.writeText(text);
                    return true;
                } catch (error) {
                    return false;
                }
            },

            async copyText(text, successMessage) {
                const copied = await this.tryCopyText(text);
                if (copied) {
                    this.notify(successMessage, 'success');
                } else {
                    this.notify('复制失败，请检查浏览器权限', 'error');
                }
            },

            handleKeydown(event) {
                if (event.key !== 'Escape') {
                    return;
                }
                if (this.showReviewDialog) {
                    this.closeReviewDialog();
                    return;
                }
                if (this.showSubmitDialog) {
                    this.closeSubmitDialog();
                    return;
                }
                if (this.showImportDialog) {
                    this.closeImportDialog();
                    return;
                }
                if (this.showPreviewDialog) {
                    this.closePreviewDialog();
                }
            },

            notify(message, type = 'info') {
                const id = Date.now() + Math.random();
                this.toasts.push({ id, message, type });
                window.setTimeout(() => {
                    this.toasts = this.toasts.filter((toast) => toast.id !== id);
                }, 3200);
            }
        }
    }).mount('#marketplace-app');
})();
