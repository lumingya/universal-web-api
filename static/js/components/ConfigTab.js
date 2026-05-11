// ==================== 配置 Tab 组件 (拆分版) ====================

window.ConfigTab = {
    name: 'ConfigTab',
    props: {
        currentDomain: { type: String, default: null },
        currentConfig: { type: Object, default: null }
    },
    emits: [
        'add-selector', 'remove-selector', 'update-selector-key', 'test-selector',
        'add-step', 'remove-step', 'move-step', 'action-change', 'show-templates',
        'update-image-config', 'reload-config'
    ],
        // 注册子组件（确保模板可解析）
    components: {
        'selector-panel': window.SelectorPanel,
        'image-config-panel': window.ImageConfigPanel,
        'stream-config-panel': window.StreamConfigPanel,
        'workflow-panel': window.WorkflowPanel,
        'file-paste-panel': window.FilePastePanel
    },
    data() {
        return {
            // 🆕 预设管理
            selectedPreset: '主预设',
            defaultPreset: '主预设',
            availablePresets: [],
            presetLoading: false,
            newPresetName: '',
            showNewPresetInput: false,
            renamePresetName: '',
            showRenamePresetInput: false,

            // 折叠状态
            selectorCollapsed: true,
            workflowCollapsed: true,
            imageConfigCollapsed: true,
            streamConfigCollapsed: true,
            filePasteCollapsed: true,
            advancedConfigCollapsed: true,

            advancedConfigSaving: false,
            isolatedTabCreating: false,
            sharedTabCreating: false,
            showConfigCompareDialog: false,
            compareMainLoading: false,
            compareMainError: '',
            compareMainPresetName: '',
            compareMainMatchMode: '',
            compareMainPath: '',
            compareLocalOriginalText: '',
            compareLocalDraft: '',
            compareMainOriginalText: '',
            compareMainDraft: '',
            compareSavingLocal: false,

            // 默认配置
            defaultImageConfig: {
                enabled: false,
                modalities: {
                    image: false,
                    audio: false,
                    video: false
                },
                selector: 'img',
                audio_selector: 'audio, audio source',
                video_selector: 'video, video source',
                container_selector: null,
                debounce_seconds: 2.0,
                wait_for_load: true,
                load_timeout_seconds: 5.0,
                download_blobs: true,
                src_allow_patterns: [],
                max_size_mb: 10,
                mode: 'all'
            },
            defaultStreamConfig: {
                mode: 'dom',
                request_transport: {
                    mode: 'workflow',
                    profile: '',
                    options: {}
                },
                hard_timeout: 300,
                send_confirmation: {
                    attachment_sensitivity: 'medium'
                },
                attachment_monitor: {
                    root_selectors: [],
                    attachment_selectors: [],
                    pending_selectors: [],
                    busy_text_markers: [],
                    send_button_disabled_markers: [],
                    require_attachment_present: false,
                    continue_once_on_unconfirmed_send: true,
                    idle_timeout: 8.0,
                    hard_max_wait: 90.0
                },
                network: null
            }
        };
    },
    computed: {
        // 🆕 当前预设的配置数据
        presetConfig() {
            if (!this.currentConfig) return null;
            const presets = this.currentConfig.presets;
            if (!presets) return this.currentConfig; // 兼容旧格式
            return presets[this.selectedPreset]
                || presets[this.defaultPreset]
                || presets['主预设']
                || Object.values(presets)[0]
                || null;
        },
        imageConfig() {
            if (!this.presetConfig) return this.defaultImageConfig;
            const current = this.presetConfig.image_extraction || {};
            return {
                ...this.defaultImageConfig,
                ...current,
                modalities: {
                    ...(this.defaultImageConfig.modalities || {}),
                    ...((current && current.modalities) || {})
                }
            };
        },
        streamConfig() {
            if (!this.presetConfig) return this.defaultStreamConfig;
            const streamConfig = this.presetConfig.stream_config || {};
            return {
                ...this.defaultStreamConfig,
                mode: streamConfig.mode || this.defaultStreamConfig.mode,
                request_transport: {
                    ...(this.defaultStreamConfig.request_transport || {}),
                    ...((streamConfig && streamConfig.request_transport) || {}),
                    options: {
                        ...((this.defaultStreamConfig.request_transport && this.defaultStreamConfig.request_transport.options) || {}),
                        ...((streamConfig && streamConfig.request_transport && streamConfig.request_transport.options) || {})
                    }
                },
                hard_timeout: streamConfig.hard_timeout || this.defaultStreamConfig.hard_timeout,
                network: streamConfig.network || this.defaultStreamConfig.network,
                send_confirmation: {
                    ...(this.defaultStreamConfig.send_confirmation || {}),
                    ...(streamConfig.send_confirmation || {})
                },
                attachment_monitor: {
                    ...(this.defaultStreamConfig.attachment_monitor || {}),
                    ...(streamConfig.attachment_monitor || {})
                }
            };
        },
        siteAdvancedConfig() {
            if (!this.currentConfig) {
                return {
                    independent_cookies: false,
                    independent_cookies_auto_takeover: false
                };
            }
            return {
                independent_cookies: false,
                independent_cookies_auto_takeover: false,
                ...(this.currentConfig.advanced || {})
            };
        },
        compareLocalParsed() {
            return this.parseConfigCompareDraft(this.compareLocalDraft);
        },
        compareMainParsed() {
            return this.parseConfigCompareDraft(this.compareMainDraft);
        },
        compareLocalDirty() {
            return this.compareLocalDraft !== this.compareLocalOriginalText;
        },
        compareMainDirty() {
            return this.compareMainDraft !== this.compareMainOriginalText;
        },
        compareLocalSummaryItems() {
            if (!this.compareLocalParsed.valid) return [];
            return this.buildConfigCompareSummaryItems(this.compareLocalParsed.value);
        },
        compareMainSummaryItems() {
            if (!this.compareMainParsed.valid) return [];
            return this.buildConfigCompareSummaryItems(this.compareMainParsed.value);
        },
        compareFieldDiffs() {
            if (!this.compareLocalParsed.valid || !this.compareMainParsed.valid) return [];
            return this.buildConfigCompareFieldDiffs(
                this.compareLocalParsed.value,
                this.compareMainParsed.value
            );
        },
        compareDifferentCount() {
            return this.compareFieldDiffs.filter(item => item.status !== 'same').length;
        },
        compareSameCount() {
            return this.compareFieldDiffs.filter(item => item.status === 'same').length;
        }
    },
    methods: {
        buildAuthHeaders(extraHeaders = {}) {
            const token = String(localStorage.getItem('api_token') || '').trim();
            const headers = { ...extraHeaders };
            if (token) {
                headers['Authorization'] = 'Bearer ' + token;
            }
            return headers;
        },

        notifyCompare(message, type = 'info') {
            if (this.$parent && typeof this.$parent.notify === 'function') {
                this.$parent.notify(message, type);
                return;
            }
            if (type === 'error') {
                alert(message);
            } else {
                console.info(message);
            }
        },

        sortConfigCompareValue(value) {
            if (Array.isArray(value)) {
                return value.map(item => this.sortConfigCompareValue(item));
            }
            if (value && typeof value === 'object') {
                const sorted = {};
                Object.keys(value)
                    .sort((left, right) => String(left).localeCompare(String(right), 'zh-CN'))
                    .forEach(key => {
                        sorted[key] = this.sortConfigCompareValue(value[key]);
                    });
                return sorted;
            }
            return value;
        },

        getConfigCompareStableText(value) {
            return JSON.stringify(this.sortConfigCompareValue(value));
        },

        formatConfigCompareJson(value) {
            const normalized = (value && typeof value === 'object' && !Array.isArray(value))
                ? value
                : {};
            return JSON.stringify(this.sortConfigCompareValue(normalized), null, 2);
        },

        validateConfigCompareObject(parsed) {
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
                throw new Error('JSON 顶层必须是对象');
            }
            if (
                parsed.presets !== undefined
                || parsed.default_preset !== undefined
                || parsed.advanced !== undefined
            ) {
                throw new Error('这里只接受单个预设配置对象，不要包含 presets/default_preset/advanced');
            }
            if (
                parsed.selectors !== undefined
                && (
                    parsed.selectors === null
                    || typeof parsed.selectors !== 'object'
                    || Array.isArray(parsed.selectors)
                )
            ) {
                throw new Error('selectors 必须是对象');
            }
            if (parsed.workflow !== undefined && !Array.isArray(parsed.workflow)) {
                throw new Error('workflow 必须是数组');
            }
        },

        parseConfigCompareDraft(rawText) {
            const text = String(rawText || '').trim();
            if (!text) {
                return {
                    valid: false,
                    error: 'JSON 不能为空',
                    value: null
                };
            }

            try {
                const parsed = JSON.parse(text);
                this.validateConfigCompareObject(parsed);
                return {
                    valid: true,
                    error: '',
                    value: parsed
                };
            } catch (error) {
                return {
                    valid: false,
                    error: error && error.message ? error.message : 'JSON 解析失败',
                    value: null
                };
            }
        },

        buildConfigCompareSummaryItems(config) {
            const selectors = (config && config.selectors && typeof config.selectors === 'object' && !Array.isArray(config.selectors))
                ? Object.keys(config.selectors).length
                : 0;
            const workflowSteps = Array.isArray(config && config.workflow)
                ? config.workflow.length
                : 0;
            const rawStreamMode = String((config && config.stream_config && config.stream_config.mode) || 'dom').trim().toLowerCase();
            const streamMode = rawStreamMode === 'network'
                ? '网络模式'
                : (rawStreamMode === 'dom' ? 'DOM 模式' : (rawStreamMode || 'DOM 模式'));
            const imageEnabled = !!(config && config.image_extraction && config.image_extraction.enabled);
            const filePasteEnabled = !!(config && config.file_paste && config.file_paste.enabled);

            return [
                { label: '选择器', value: String(selectors) },
                { label: '工作流', value: workflowSteps + ' 步' },
                { label: '流式', value: streamMode, compact: true },
                { label: '图片提取', value: imageEnabled ? '开启' : '关闭' },
                { label: '文件粘贴', value: filePasteEnabled ? '开启' : '关闭' }
            ];
        },

        buildConfigCompareFieldDiffs(localConfig, mainConfig) {
            const labelMap = {
                selectors: '选择器',
                workflow: '工作流',
                stream_config: '流式配置',
                image_extraction: '图片提取',
                file_paste: '文件粘贴',
                stealth: '隐身模式',
                extractor_id: '提取器',
                extractor_verified: '提取器验证'
            };
            const priorityKeys = [
                'selectors',
                'workflow',
                'stream_config',
                'image_extraction',
                'file_paste',
                'stealth',
                'extractor_id',
                'extractor_verified'
            ];

            const local = localConfig || {};
            const main = mainConfig || {};
            const restKeys = new Set([
                ...Object.keys(local),
                ...Object.keys(main)
            ]);
            const orderedKeys = [];

            priorityKeys.forEach(key => {
                if (restKeys.has(key)) {
                    orderedKeys.push(key);
                    restKeys.delete(key);
                }
            });

            Array.from(restKeys)
                .sort((left, right) => String(left).localeCompare(String(right), 'zh-CN'))
                .forEach(key => orderedKeys.push(key));

            return orderedKeys.map(key => {
                const hasLocal = Object.prototype.hasOwnProperty.call(local, key);
                const hasMain = Object.prototype.hasOwnProperty.call(main, key);
                let status = 'same';

                if (hasLocal && hasMain) {
                    status = this.getConfigCompareStableText(local[key]) === this.getConfigCompareStableText(main[key])
                        ? 'same'
                        : 'different';
                } else if (hasLocal) {
                    status = 'local_only';
                } else if (hasMain) {
                    status = 'main_only';
                }

                return {
                    key,
                    label: labelMap[key] || key,
                    status
                };
            });
        },

        getConfigCompareDiffClass(status) {
            if (status === 'same') {
                return 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300';
            }
            if (status === 'different') {
                return 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-300';
            }
            if (status === 'main_only') {
                return 'border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-800 dark:bg-blue-900/30 dark:text-blue-300';
            }
            return 'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-800 dark:bg-rose-900/30 dark:text-rose-300';
        },

        getConfigCompareDiffText(status) {
            if (status === 'same') return '一致';
            if (status === 'different') return '内容不同';
            if (status === 'main_only') return '仅 main 有';
            return '仅本地有';
        },

        getConfigCompareMatchLabel(matchMode) {
            if (matchMode === 'exact') return '已命中同名预设';
            if (matchMode === 'default') return 'main 默认预设回退';
            if (matchMode === 'main_preset') return 'main 主预设回退';
            if (matchMode === 'first') return 'main 首个预设回退';
            if (matchMode === 'legacy_flat') return 'main 旧版扁平配置';
            return '';
        },

        syncConfigCompareLocalDraft(force = false) {
            const nextText = this.formatConfigCompareJson(this.presetConfig || {});
            this.compareLocalOriginalText = nextText;
            if (force || !this.compareLocalDirty) {
                this.compareLocalDraft = nextText;
            }
        },

        resetConfigCompareState() {
            this.showConfigCompareDialog = false;
            this.compareMainLoading = false;
            this.compareMainError = '';
            this.compareMainPresetName = '';
            this.compareMainMatchMode = '';
            this.compareMainPath = '';
            this.compareLocalOriginalText = '';
            this.compareLocalDraft = '';
            this.compareMainOriginalText = '';
            this.compareMainDraft = '';
            this.compareSavingLocal = false;
        },

        closeConfigCompareDialog() {
            if (
                (this.compareLocalDirty || this.compareMainDirty)
                && !window.confirm('关闭后未保存的比对草稿会丢失，确定关闭吗？')
            ) {
                return;
            }
            this.resetConfigCompareState();
        },

        async openConfigCompare() {
            if (!this.currentDomain || !this.currentConfig) return;

            if (!this.presetConfig && this.availablePresets.length) {
                this.selectedPreset = this.selectedPreset || this.defaultPreset || this.availablePresets[0] || '主预设';
            }

            this.showConfigCompareDialog = true;
            this.compareMainError = '';
            this.compareMainPresetName = '';
            this.compareMainMatchMode = '';
            this.compareMainPath = '';
            this.compareMainOriginalText = '';
            this.compareMainDraft = '';
            this.syncConfigCompareLocalDraft(true);
            this.$nextTick(() => {
                const root = this.$el && this.$el.querySelector ? this.$el.querySelector('[data-config-compare-root]') : null;
                if (root) {
                    root.style.display = 'flex';
                }
            });
            await this.loadMainBranchCompareConfig();
        },

        async openConfigCompareForPreset(presetName) {
            const targetPreset = String(presetName || '').trim();
            if (targetPreset) {
                if (!this.availablePresets.length) {
                    await this.loadPresets();
                }
                if (this.availablePresets.includes(targetPreset)) {
                    this.selectedPreset = targetPreset;
                }
            }
            await this.openConfigCompare();
        },

        async loadMainBranchCompareConfig() {
            if (!this.currentDomain) return;

            this.compareMainLoading = true;
            this.compareMainError = '';
            try {
                const queryPreset = encodeURIComponent(this.selectedPreset || '');
                const response = await fetch(
                    '/api/sites/' + encodeURIComponent(this.currentDomain) + '/main-branch-config?preset_name=' + queryPreset,
                    {
                        headers: this.buildAuthHeaders()
                    }
                );

                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || ('HTTP ' + response.status));
                }

                const data = await response.json();
                const formatted = this.formatConfigCompareJson(data.config || {});
                this.compareMainPresetName = String(data.preset_name || '').trim() || '主预设';
                this.compareMainMatchMode = String(data.match_mode || '').trim();
                this.compareMainPath = String(data.path || 'config/sites.json').trim() || 'config/sites.json';
                this.compareMainOriginalText = formatted;
                this.compareMainDraft = formatted;
            } catch (error) {
                console.error('加载 main 分支配置失败:', error);
                this.compareMainError = error && error.message ? error.message : '加载失败';
                this.compareMainPresetName = '';
                this.compareMainMatchMode = '';
                this.compareMainPath = '';
                this.compareMainOriginalText = '';
                this.compareMainDraft = '';
            } finally {
                this.compareMainLoading = false;
            }
        },

        resetConfigCompareLocalDraft() {
            const changed = this.compareLocalDirty;
            this.compareLocalDraft = this.compareLocalOriginalText;
            this.notifyCompare(changed ? '已撤销左侧草稿更改' : '左侧没有可撤销的改动', changed ? 'success' : 'info');
        },

        resetConfigCompareMainDraft() {
            const changed = this.compareMainDirty;
            this.compareMainDraft = this.compareMainOriginalText;
            this.notifyCompare(changed ? '已恢复右侧参考草稿' : '右侧没有可恢复的改动', changed ? 'success' : 'info');
        },

        async saveConfigCompareLocalDraft() {
            if (!this.currentDomain || !this.selectedPreset) return;
            if (!this.compareLocalParsed.valid) {
                this.notifyCompare('左侧本地草稿 JSON 无效，不能保存', 'warning');
                return;
            }

            this.compareSavingLocal = true;
            try {
                const response = await fetch(
                    '/api/sites/' + encodeURIComponent(this.currentDomain) + '/preset-config',
                    {
                        method: 'PUT',
                        headers: this.buildAuthHeaders({ 'Content-Type': 'application/json' }),
                        body: JSON.stringify({
                            preset_name: this.selectedPreset,
                            config: this.compareLocalParsed.value
                        })
                    }
                );

                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || ('HTTP ' + response.status));
                }

                const data = await response.json();
                const resolvedPreset = String(data.preset_name || this.selectedPreset).trim() || this.selectedPreset;
                const savedConfig = data.config || this.compareLocalParsed.value;
                const cloned = JSON.parse(JSON.stringify(savedConfig));

                if (this.currentConfig) {
                    if (this.currentConfig.presets && typeof this.currentConfig.presets === 'object') {
                        this.currentConfig.presets[resolvedPreset] = cloned;
                    } else {
                        Object.keys(this.currentConfig).forEach(key => {
                            delete this.currentConfig[key];
                        });
                        Object.assign(this.currentConfig, cloned);
                    }
                }

                this.selectedPreset = resolvedPreset;
                this.compareLocalOriginalText = this.formatConfigCompareJson(savedConfig);
                this.compareLocalDraft = this.compareLocalOriginalText;
                this.notifyCompare('当前预设已保存', 'success');
            } catch (error) {
                console.error('保存当前预设失败:', error);
                this.notifyCompare('保存失败: ' + error.message, 'error');
            } finally {
                this.compareSavingLocal = false;
            }
        },

        async applyMainDraftToCurrentPreset() {
            if (!this.compareMainParsed.valid) {
                this.notifyCompare('右侧 main 配置 JSON 无效，不能覆盖当前预设', 'warning');
                return;
            }
            this.compareLocalDraft = this.formatConfigCompareJson(this.compareMainParsed.value);
            await this.saveConfigCompareLocalDraft();
        },

        copyMainToLocalDraft() {
            this.compareLocalDraft = this.compareMainDraft;
        },

        copyLocalToMainDraft() {
            this.compareMainDraft = this.compareLocalDraft;
        },

        // 选择器值更新
        updateSelectorValue(key, value) {
            const pc = this.presetConfig;
            if (pc && pc.selectors) {
                pc.selectors[key] = value;
            }
        },

        // 流式配置保存
        async saveStreamConfig(config) {
            if (!this.currentDomain) return;
            try {
                const payload = { ...config, preset_name: this.selectedPreset };
                const response = await fetch('/api/sites/' + encodeURIComponent(this.currentDomain) + '/stream-config', {
                    method: 'PUT',
                    headers: this.buildAuthHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify(payload)
                });

                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || ('HTTP ' + response.status));
                }

                const pc = this.presetConfig;
                if (pc) pc.stream_config = config;
            } catch (e) {
                console.error('保存流式配置失败:', e);
                alert('保存失败: ' + e.message);
            }
        },

        async updateIndependentCookies(enabled, event) {
            if (!this.currentDomain || !this.currentConfig) return;
            const nextEnabled = !!enabled;
            const currentEnabled = !!this.siteAdvancedConfig.independent_cookies;
            if (nextEnabled && !currentEnabled) {
                const confirmed = window.confirm(
                    [
                        '开启“独立 Cookie 标签页”后，可能带来这些影响：',
                        '',
                        '1. 新开的该站点独立会话通常不会继承当前受控浏览器里的登录态、Cookie 和 localStorage，可能表现为未登录。',
                        '2. 独立会话通常会以单独窗口出现，并且内存占用会明显高于普通标签页。',
                        '',
                        '确认仍要开启吗？'
                    ].join('\n')
                );
                if (!confirmed) {
                    // 用户取消：把 checkbox 视觉状态还原
                    if (event && event.target) event.target.checked = currentEnabled;
                    return;
                }
            }

            this.advancedConfigSaving = true;
            const previousAdvanced = { ...(this.currentConfig.advanced || {}) };
            this.currentConfig.advanced = {
                ...previousAdvanced,
                independent_cookies: nextEnabled,
                independent_cookies_auto_takeover: !!previousAdvanced.independent_cookies_auto_takeover
            };

            try {
                const token = localStorage.getItem('api_token');
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = 'Bearer ' + token;

                const response = await fetch('/api/sites/' + encodeURIComponent(this.currentDomain) + '/advanced-config', {
                    method: 'PUT',
                    headers,
                    body: JSON.stringify({
                        independent_cookies: nextEnabled,
                        independent_cookies_auto_takeover: !!this.currentConfig.advanced.independent_cookies_auto_takeover
                    })
                });

                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || ('HTTP ' + response.status));
                }

                this.$emit('reload-config');
            } catch (e) {
                this.currentConfig.advanced = previousAdvanced;
                console.error('保存站点高级配置失败:', e);
                alert('保存失败: ' + e.message);
            } finally {
                this.advancedConfigSaving = false;
            }
        },

        async updateIndependentCookiesAutoTakeover(enabled) {
            if (!this.currentDomain || !this.currentConfig) return;

            this.advancedConfigSaving = true;
            const previousAdvanced = { ...(this.currentConfig.advanced || {}) };
            this.currentConfig.advanced = {
                ...previousAdvanced,
                independent_cookies_auto_takeover: !!enabled
            };

            try {
                const token = localStorage.getItem('api_token');
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = 'Bearer ' + token;

                const response = await fetch('/api/sites/' + encodeURIComponent(this.currentDomain) + '/advanced-config', {
                    method: 'PUT',
                    headers,
                    body: JSON.stringify({
                        independent_cookies: !!this.siteAdvancedConfig.independent_cookies,
                        independent_cookies_auto_takeover: !!enabled
                    })
                });

                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || ('HTTP ' + response.status));
                }

                this.$emit('reload-config');
            } catch (e) {
                this.currentConfig.advanced = previousAdvanced;
                console.error('保存站点高级配置失败:', e);
                alert('保存失败: ' + e.message);
            } finally {
                this.advancedConfigSaving = false;
            }
        },

        async createIsolatedCookieTab() {
            if (!this.currentDomain) return;

            this.isolatedTabCreating = true;
            try {
                const token = localStorage.getItem('api_token');
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = 'Bearer ' + token;

                const response = await fetch('/api/sites/' + encodeURIComponent(this.currentDomain) + '/isolated-tab', {
                    method: 'POST',
                    headers
                });

                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || ('HTTP ' + response.status));
                }

                const result = await response.json();
                alert(result.message || ('已为 ' + this.currentDomain + ' 新建独立 Cookie 标签页'));
            } catch (e) {
                console.error('新建独立 Cookie 标签页失败:', e);
                alert('新建失败: ' + e.message);
            } finally {
                this.isolatedTabCreating = false;
            }
        },

        async createSharedCookieTab() {
            if (!this.currentDomain) return;

            this.sharedTabCreating = true;
            try {
                const token = localStorage.getItem('api_token');
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = 'Bearer ' + token;

                const response = await fetch('/api/sites/' + encodeURIComponent(this.currentDomain) + '/shared-tab', {
                    method: 'POST',
                    headers
                });

                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || ('HTTP ' + response.status));
                }

                const result = await response.json();
                alert(result.message || ('已为 ' + this.currentDomain + ' 打开共享 Cookie 受控窗口'));
            } catch (e) {
                console.error('打开共享 Cookie 受控窗口失败:', e);
                alert('打开失败: ' + e.message);
            } finally {
                this.sharedTabCreating = false;
            }
        },

        // ===== 🆕 预设管理方法 =====

        async loadPresets() {
            if (!this.currentDomain) return;
            this.presetLoading = true;
            try {
                const response = await fetch('/api/presets/' + encodeURIComponent(this.currentDomain), {
                    headers: this.buildAuthHeaders()
                });
                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || ('HTTP ' + response.status));
                }

                const data = await response.json();
                this.availablePresets = data.presets || ['主预设'];
                const apiDefault = data.default_preset;
                if (apiDefault && this.availablePresets.includes(apiDefault)) {
                    this.defaultPreset = apiDefault;
                } else if (this.availablePresets.includes('主预设')) {
                    this.defaultPreset = '主预设';
                } else {
                    this.defaultPreset = this.availablePresets[0] || '主预设';
                }

                // 确保选中的预设仍然有效
                if (!this.availablePresets.includes(this.selectedPreset)) {
                    this.selectedPreset = this.defaultPreset || this.availablePresets[0] || '主预设';
                }
            } catch (e) {
                console.error('加载预设列表失败:', e);
                if (!this.availablePresets.length) {
                    this.availablePresets = ['主预设'];
                    this.defaultPreset = '主预设';
                    this.selectedPreset = '主预设';
                }
            } finally {
                this.presetLoading = false;
            }
        },

        switchPreset(presetName) {
            this.selectedPreset = presetName;
            // 触发父组件重新加载该预设的配置
            this.$emit('reload-config');
        },

        async setDefaultPreset() {
            if (!this.currentDomain || !this.selectedPreset) return;
            try {
                const response = await fetch('/api/presets/' + encodeURIComponent(this.currentDomain) + '/default', {
                    method: 'PUT',
                    headers: this.buildAuthHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({
                        preset_name: this.selectedPreset
                    })
                });

                if (response.ok) {
                    this.defaultPreset = this.selectedPreset;
                    this.$emit('reload-config');
                    alert('✅ 默认预设已设置为 "' + this.selectedPreset + '"（仅本地覆盖）');
                } else {
                    const err = await response.json();
                    alert('❌ 设置默认预设失败: ' + (err.detail || '未知错误'));
                }
            } catch (e) {
                alert('❌ 网络错误: ' + e.message);
            }
        },

        async createPreset() {
            const name = this.newPresetName.trim();
            if (!name) return;
            if (!this.currentDomain) return;
            const sourcePreset = this.selectedPreset;

            try {
                const response = await fetch('/api/presets/' + encodeURIComponent(this.currentDomain), {
                    method: 'POST',
                    headers: this.buildAuthHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({
                        new_name: name,
                        source_name: sourcePreset
                    })
                });

                if (response.ok) {
                    this.newPresetName = '';
                    this.showNewPresetInput = false;
                    await this.loadPresets();
                    this.selectedPreset = name;
                    this.$emit('reload-config');
                    alert('✅ 预设 "' + name + '" 已创建（克隆自 "' + sourcePreset + '"）');
                } else {
                    const err = await response.json();
                    alert('❌ 创建失败: ' + (err.detail || '未知错误'));
                }
            } catch (e) {
                alert('❌ 网络错误: ' + e.message);
            }
        },

        async renamePreset() {
            const newName = this.renamePresetName.trim();
            if (!newName) return;
            if (!this.currentDomain) return;
            if (!this.selectedPreset) return;
            if (newName === this.selectedPreset) {
                this.showRenamePresetInput = false;
                this.renamePresetName = '';
                return;
            }

            try {
                const response = await fetch('/api/presets/' + encodeURIComponent(this.currentDomain) + '/rename', {
                    method: 'PUT',
                    headers: this.buildAuthHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({
                        old_name: this.selectedPreset,
                        new_name: newName
                    })
                });

                if (response.ok) {
                    this.showRenamePresetInput = false;
                    this.renamePresetName = '';
                    await this.loadPresets();
                    this.selectedPreset = newName;
                    this.$emit('reload-config');
                    alert('✅ 预设已重命名为 "' + newName + '"');
                } else {
                    const err = await response.json();
                    alert('❌ 重命名失败: ' + (err.detail || '未知错误'));
                }
            } catch (e) {
                alert('❌ 网络错误: ' + e.message);
            }
        },

        async deletePreset() {
            if (this.availablePresets.length <= 1) {
                alert('不能删除最后一个预设');
                return;
            }
            if (!confirm('确定要删除预设 "' + this.selectedPreset + '" 吗？此操作不可撤销。')) {
                return;
            }

            try {
                const response = await fetch(
                    '/api/presets/' + encodeURIComponent(this.currentDomain) + '/' + encodeURIComponent(this.selectedPreset),
                    {
                        method: 'DELETE',
                        headers: this.buildAuthHeaders()
                    }
                );

                if (response.ok) {
                    await this.loadPresets();
                    this.selectedPreset = this.defaultPreset || this.availablePresets[0] || '主预设';
                    this.$emit('reload-config');
                    alert('✅ 预设已删除');
                } else {
                    const err = await response.json();
                    alert('❌ 删除失败: ' + (err.detail || '未知错误'));
                }
            } catch (e) {
                alert('❌ 网络错误: ' + e.message);
            }
        }
    },
    watch: {
        currentDomain: {
            handler(newDomain) {
                this.resetConfigCompareState();
                if (newDomain) {
                    // 切换站点时强制按站点默认预设初始化
                    this.selectedPreset = '';
                    this.defaultPreset = '主预设';
                    this.showNewPresetInput = false;
                    this.showRenamePresetInput = false;
                    this.newPresetName = '';
                    this.renamePresetName = '';
                    this.loadPresets();
                } else {
                    this.availablePresets = [];
                    this.selectedPreset = '主预设';
                    this.defaultPreset = '主预设';
                    this.showNewPresetInput = false;
                    this.showRenamePresetInput = false;
                    this.newPresetName = '';
                    this.renamePresetName = '';
                }
            },
            immediate: true
        },
        selectedPreset(newValue, oldValue) {
            if (newValue === oldValue || !this.showConfigCompareDialog) return;
            this.syncConfigCompareLocalDraft(true);
            this.loadMainBranchCompareConfig();
        },
        currentConfig: {
            handler() {
                if (!this.showConfigCompareDialog) return;
                this.syncConfigCompareLocalDraft(false);
            },
            deep: true
        }
    },
    template: `
        <div class="h-full overflow-auto p-4">
            <!-- 空状态 -->
            <div v-if="!currentDomain || !currentConfig" class="h-full flex items-center justify-center">
                <div class="text-center text-gray-400 dark:text-gray-500">
                    <div class="text-4xl mb-4">📝</div>
                    <div class="text-lg">请选择或新增站点配置</div>
                </div>
            </div>

            <!-- 配置内容 -->
            <div v-else class="space-y-4">

                <!-- 🆕 预设选择器 -->
                <div class="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-sm px-4 py-3">
                    <div class="flex items-center justify-between flex-wrap gap-3">
                        <div class="flex items-center gap-3">
                            <span class="text-sm font-semibold text-gray-700 dark:text-gray-300">🎛️ 预设:</span>
                            <select v-model="selectedPreset"
                                    @change="switchPreset(selectedPreset)"
                                    :disabled="presetLoading"
                                    class="border dark:border-gray-600 px-3 py-1.5 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent min-w-[140px]">
                                <option v-for="p in availablePresets" :key="p" :value="p">{{ p }}</option>
                            </select>
                            <span class="text-xs text-gray-400 dark:text-gray-500">
                                ({{ availablePresets.length }} 个预设)
                            </span>
                            <span class="text-xs px-2 py-0.5 rounded bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800">
                                默认: {{ defaultPreset || '主预设' }}
                            </span>
                        </div>

                        <div class="flex items-center gap-2">
                            <!-- 设为默认 -->
                            <button @click="setDefaultPreset"
                                    :disabled="!selectedPreset || selectedPreset === defaultPreset"
                                    class="px-3 py-1 text-xs font-medium text-emerald-700 dark:text-emerald-300 border border-emerald-300 dark:border-emerald-700 rounded hover:bg-emerald-50 dark:hover:bg-emerald-900/30 disabled:opacity-30">
                                ⭐ 设为默认
                            </button>

                            <!-- 新建预设 -->
                            <div v-if="showNewPresetInput" class="flex items-center gap-2">
                                <input v-model="newPresetName"
                                       @keyup.enter="createPreset"
                                       @keyup.escape="showNewPresetInput = false; newPresetName = ''"
                                       placeholder="输入预设名称"
                                       class="border dark:border-gray-600 px-2 py-1 rounded text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white w-32 focus:ring-2 focus:ring-blue-400"
                                       autofocus>
                                <button @click="createPreset"
                                        :disabled="!newPresetName.trim()"
                                        class="px-2 py-1 text-xs bg-green-500 text-white rounded hover:bg-green-600 disabled:opacity-50">
                                    创建
                                </button>
                                <button @click="showNewPresetInput = false"
                                        class="px-2 py-1 text-xs bg-gray-300 dark:bg-gray-600 text-gray-700 dark:text-gray-300 rounded hover:bg-gray-400 dark:hover:bg-gray-500">
                                    取消
                                </button>
                            </div>
                            <button v-else @click="showNewPresetInput = true; showRenamePresetInput = false; renamePresetName = ''"
                                    class="px-3 py-1 text-xs font-medium bg-blue-500 text-white rounded hover:bg-blue-600 flex items-center gap-1">
                                ＋ 新建预设
                            </button>

                            <!-- 重命名预设 -->
                            <div v-if="showRenamePresetInput" class="flex items-center gap-2">
                                <input v-model="renamePresetName"
                                       @keyup.enter="renamePreset"
                                       @keyup.escape="showRenamePresetInput = false; renamePresetName = ''"
                                       :placeholder="'重命名 ' + selectedPreset"
                                       class="border dark:border-gray-600 px-2 py-1 rounded text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white w-36 focus:ring-2 focus:ring-amber-400">
                                <button @click="renamePreset"
                                        :disabled="!renamePresetName.trim()"
                                        class="px-2 py-1 text-xs bg-amber-500 text-white rounded hover:bg-amber-600 disabled:opacity-50">
                                    重命名
                                </button>
                                <button @click="showRenamePresetInput = false; renamePresetName = ''"
                                        class="px-2 py-1 text-xs bg-gray-300 dark:bg-gray-600 text-gray-700 dark:text-gray-300 rounded hover:bg-gray-400 dark:hover:bg-gray-500">
                                    取消
                                </button>
                            </div>
                            <button v-else
                                    @click="showRenamePresetInput = true; renamePresetName = selectedPreset; showNewPresetInput = false; newPresetName = ''"
                                    :disabled="!selectedPreset"
                                    class="px-3 py-1 text-xs font-medium text-amber-700 dark:text-amber-300 border border-amber-300 dark:border-amber-700 rounded hover:bg-amber-50 dark:hover:bg-amber-900/30 disabled:opacity-30">
                                ✎ 重命名
                            </button>

                            <button @click="openConfigCompare"
                                    class="px-3 py-1 text-xs font-medium text-slate-700 dark:text-slate-200 border border-slate-300 dark:border-slate-600 rounded hover:bg-slate-50 dark:hover:bg-slate-700/60">
                                ⇄ 对比 main
                            </button>

                            <!-- 删除预设 -->
                            <button @click="deletePreset"
                                    :disabled="availablePresets.length <= 1"
                                    class="px-3 py-1 text-xs font-medium text-red-600 dark:text-red-400 border border-red-300 dark:border-red-600 rounded hover:bg-red-50 dark:hover:bg-red-900/30 disabled:opacity-30 disabled:cursor-not-allowed"
                                    :title="availablePresets.length <= 1 ? '不能删除最后一个预设' : '删除当前预设'">
                                🗑️ 删除
                            </button>
                        </div>
                    </div>
                    <p class="text-xs text-gray-400 dark:text-gray-500 mt-2">
                        新建预设会克隆当前选中的预设配置。在标签页池中可为不同标签页选择不同预设。未手动指定时会自动使用“默认预设”。“对比 main”会读取 Git main 分支里已提交的 config/sites.json，不会把你当前工作区未提交的改动算进去。
                    </p>
                </div>



                <!-- 选择器面板 -->
                <selector-panel v-if="presetConfig"
                    :selectors="presetConfig.selectors || {}"
                    :collapsed="selectorCollapsed"
                    @update:collapsed="selectorCollapsed = $event"
                    @add-selector="$emit('add-selector', $event)"
                    @remove-selector="$emit('remove-selector', $event)"
                    @update-selector-key="(oldKey, newKey) => $emit('update-selector-key', oldKey, newKey)"
                    @update-selector-value="updateSelectorValue"
                    @test-selector="(key, val) => $emit('test-selector', key, val)"
                />

                <!-- 图片配置面板 -->
                <image-config-panel v-if="presetConfig"
                    :image-config="imageConfig"
                    :current-domain="currentDomain"
                    :collapsed="imageConfigCollapsed"
                    @update:collapsed="imageConfigCollapsed = $event"
                    @update-image-config="$emit('update-image-config', $event)"
                    @reload-config="$emit('reload-config')"
                />

                <!-- 流式配置面板 -->
                <stream-config-panel v-if="presetConfig"
                    :stream-config="streamConfig"
                    :current-domain="currentDomain"
                    :collapsed="streamConfigCollapsed"
                    @update:collapsed="streamConfigCollapsed = $event"
                    @save-stream-config="saveStreamConfig"
                />
                <!-- 文件粘贴配置面板 -->
                <file-paste-panel v-if="presetConfig"
                    :sites="$parent.sites"
                    :current-domain="currentDomain"
                    :selected-preset="selectedPreset"
                    :collapsed="filePasteCollapsed"
                    @update:collapsed="filePasteCollapsed = $event"
                />
                <!-- 高级功能折叠面板 -->
                <div class="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-sm">
                    <div class="px-4 py-3 border-b dark:border-gray-700 flex items-center gap-2 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors select-none"
                         @click="advancedConfigCollapsed = !advancedConfigCollapsed">
                        <span class="w-4 inline-flex justify-center text-gray-500 dark:text-gray-400" v-html="advancedConfigCollapsed ? $icons.chevronDown : $icons.chevronUp"></span>
                        <h3 class="font-semibold text-gray-900 dark:text-white">🔒 高级功能</h3>
                        <span class="text-sm text-gray-500 dark:text-gray-400">
                            (独立 Cookie:
                            <span :class="siteAdvancedConfig.independent_cookies ? 'text-green-500' : 'text-gray-400'">
                                {{ siteAdvancedConfig.independent_cookies ? '已启用' : '未启用' }}
                            </span>)
                        </span>
                    </div>
                    <div v-show="!advancedConfigCollapsed" class="p-4 space-y-4">
                        <p class="text-xs text-gray-400 dark:text-gray-500">
                            适合像 arena.ai 这类需要多匿名会话的站点。
                        </p>
                        <details class="group">
                            <summary class="text-xs text-blue-500 dark:text-blue-400 cursor-pointer select-none">
                                查看说明
                            </summary>
                            <div class="mt-2 space-y-2 pl-2">
                                <p class="text-xs text-gray-400 dark:text-gray-500">开启后，可以为这个站点创建独立 Cookie 会话。</p>
                                <p class="text-xs text-gray-400 dark:text-gray-500">说明：Chromium 的独立上下文通常会显示为单独窗口。这不是新起一个完全独立的浏览器进程，而是同一浏览器里的隔离会话。</p>
                                <p class="text-xs text-amber-600 dark:text-amber-400">注意：开启后，新开的该站点标签页不会继承当前浏览器里已有的登录态、Cookie 或 localStorage。原本已登录的共享标签页如果重新进入并被转换为独立标签页，通常会表现为未登录。</p>
                                <p class="text-xs text-gray-400 dark:text-gray-500">单标签页清 Cookie 不会影响同站点的其它独立标签页。</p>
                                <p class="text-xs text-gray-400 dark:text-gray-500">默认不会自动接管你手动新开的普通标签页，避免原标签页被关闭；只有点下面的按钮才会新建独立会话。</p>
                            </div>
                        </details>

                        <div class="flex items-center justify-between">
                            <label class="flex items-center gap-3 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
                                <input
                                    type="checkbox"
                                    class="rounded"
                                    :checked="siteAdvancedConfig.independent_cookies"
                                    :disabled="advancedConfigSaving"
                                    @change="updateIndependentCookies($event.target.checked, $event)"
                                >
                                <span>独立 Cookie 标签页</span>
                            </label>
                        </div>

                        <label class="flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
                            <input
                                type="checkbox"
                                class="rounded"
                                :checked="siteAdvancedConfig.independent_cookies_auto_takeover"
                                :disabled="advancedConfigSaving || !siteAdvancedConfig.independent_cookies"
                                @change="updateIndependentCookiesAutoTakeover($event.target.checked)"
                            >
                            <span>自动接管手动新标签页（会关闭原页并改为独立窗口）</span>
                        </label>

                        <div class="flex items-center gap-3 flex-wrap">
                            <button
                                @click="createSharedCookieTab"
                                :disabled="sharedTabCreating"
                                class="px-3 py-1.5 text-xs font-medium bg-slate-600 text-white rounded hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                                {{ sharedTabCreating ? '打开中...' : '打开共享 Cookie 受控窗口' }}
                            </button>
                            <button
                                @click="createIsolatedCookieTab"
                                :disabled="!siteAdvancedConfig.independent_cookies || isolatedTabCreating"
                                class="px-3 py-1.5 text-xs font-medium bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                                {{ isolatedTabCreating ? '创建中...' : '新建独立 Cookie 会话（单独窗口）' }}
                            </button>
                        </div>
                    </div>
                </div>
                <!-- 工作流面板 -->
                <workflow-panel v-if="presetConfig"
                    :workflow="presetConfig.workflow || []"
                    :selectors="presetConfig.selectors || {}"
                    :current-domain="currentDomain"
                    :selected-preset="selectedPreset"
                    :collapsed="workflowCollapsed"
                    @update:collapsed="workflowCollapsed = $event"
                    @add-step="$emit('add-step')"
                    @remove-step="$emit('remove-step', $event)"
                    @move-step="(index, dir) => $emit('move-step', index, dir)"
                    @action-change="$emit('action-change', $event)"
                    @show-templates="$emit('show-templates')"
                />

                <div v-if="showConfigCompareDialog"
                     data-config-compare-root
                     class="fixed top-0 left-0 right-0 bottom-0 z-50 flex items-center justify-center bg-slate-950/75 p-4 sm:p-6 backdrop-blur-md"
                     style="position:fixed; left:0; top:0; width:100vw; height:100vh; z-index:9999; display:flex; align-items:center; justify-content:center;"
                     @click.self="closeConfigCompareDialog">
                    <div class="flex h-[94vh] w-full max-w-[1860px] flex-col overflow-hidden rounded-[24px] border border-slate-700/70 bg-slate-900 shadow-2xl ring-1 ring-white/10"
                         style="width:min(96vw, 1860px); height:94vh; max-height:94vh;">
                        <div class="relative overflow-hidden border-b border-slate-800 bg-slate-900/85 px-4 py-2.5 sm:px-5">
                            <div class="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(59,130,246,.18),transparent_35%),radial-gradient(circle_at_top_right,rgba(16,185,129,.12),transparent_28%)]"></div>
                            <div class="relative z-10 flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
                                <div>
                                    <div class="flex flex-wrap items-center gap-2">
                                        <span class="rounded-full border border-blue-500/30 bg-blue-500/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-blue-300">Main Compare</span>
                                        <span class="rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1 text-xs font-medium text-slate-300">{{ currentDomain }}</span>
                                    </div>
                                    <h3 class="mt-1.5 text-base font-bold tracking-tight text-white">配置对比与合并</h3>
                                </div>
                                <div class="flex flex-wrap items-center gap-2">
                                    <button @click="loadMainBranchCompareConfig"
                                            :disabled="compareMainLoading"
                                            class="rounded-xl border border-slate-700 bg-slate-800/80 px-3 py-1.5 text-xs font-medium text-slate-200 transition hover:bg-slate-700 hover:text-white disabled:cursor-not-allowed disabled:opacity-50">
                                        {{ compareMainLoading ? '读取中...' : '刷新 main' }}
                                    </button>
                                    <button @click="closeConfigCompareDialog"
                                            class="rounded-xl border border-rose-500/20 bg-rose-500/10 px-3 py-1.5 text-xs font-medium text-rose-300 transition hover:bg-rose-500/20 hover:text-rose-200">
                                        关闭
                                    </button>
                                </div>
                            </div>
                        </div>

                        <div class="flex flex-1 flex-col overflow-hidden bg-slate-950/40 p-3">
                            <div class="mb-2 rounded-2xl border border-slate-800 bg-slate-900/70 px-3 py-2 shadow-sm">
                                <div class="flex flex-wrap items-center justify-between gap-2 text-[11px]">
                                    <div class="flex flex-wrap items-center gap-2">
                                    <span class="rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1 text-slate-300">
                                        当前预设: {{ selectedPreset || defaultPreset || '主预设' }}
                                    </span>
                                    <span class="rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1 text-slate-300">
                                        main 来源: {{ compareMainPath || 'config/sites.json' }}
                                    </span>
                                    <span v-if="compareMainPresetName"
                                          class="rounded-full border border-blue-500/30 bg-blue-500/10 px-2.5 py-1 text-blue-300">
                                        main 预设: {{ compareMainPresetName }}
                                    </span>
                                    <span v-if="compareMainMatchMode && compareMainMatchMode !== 'exact'"
                                          class="rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-amber-300">
                                        {{ getConfigCompareMatchLabel(compareMainMatchMode) }}
                                    </span>
                                    </div>

                                    <div v-if="compareLocalParsed.valid && compareMainParsed.valid" class="flex flex-wrap items-center gap-2">
                                    <span class="rounded-full border border-rose-500/30 bg-rose-500/10 px-2 py-0.5 font-semibold text-rose-300">
                                        {{ compareDifferentCount }} 项不同
                                    </span>
                                    <span class="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 font-semibold text-emerald-300">
                                        {{ compareSameCount }} 项一致
                                    </span>
                                </div>
                                </div>

                                <div v-if="compareLocalParsed.valid && compareMainParsed.valid" class="mt-2 flex flex-wrap gap-1">
                                    <span v-for="item in compareFieldDiffs"
                                          :key="'compare-diff-' + item.key"
                                          :class="['rounded-full border px-2 py-0.5 text-[11px]', getConfigCompareDiffClass(item.status)]">
                                        {{ item.label }} · {{ getConfigCompareDiffText(item.status) }}
                                    </span>
                                </div>
                            </div>

                            <div v-if="compareMainError"
                                 class="mb-2 rounded-2xl border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
                                {{ compareMainError }}
                            </div>

                            <div class="flex min-h-0 flex-1 flex-col gap-2 xl:flex-row">
                                <div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/85 shadow-lg ring-1 ring-white/5">
                                    <div class="flex flex-wrap items-start justify-between gap-2 border-b border-slate-800 bg-slate-800/50 px-4 py-2.5">
                                        <div>
                                            <div class="text-sm font-semibold text-slate-100">本地预设草稿</div>
                                            <div class="mt-0.5 text-[11px] text-slate-500">保存后会直接写入当前工作区。</div>
                                        </div>
                                        <div class="flex flex-wrap gap-2">
                                            <button @click="resetConfigCompareLocalDraft"
                                                    :disabled="!compareLocalDirty"
                                                    class="rounded-lg px-2.5 py-1 text-[11px] font-medium text-slate-400 transition hover:bg-slate-700 hover:text-white disabled:opacity-30">
                                                撤销更改
                                            </button>
                                            <button @click="saveConfigCompareLocalDraft"
                                                    :disabled="compareSavingLocal || !compareLocalParsed.valid"
                                                    class="rounded-lg bg-blue-600 px-3 py-1 text-[11px] font-semibold text-white shadow-md transition hover:bg-blue-500 disabled:opacity-50">
                                                {{ compareSavingLocal ? '保存中...' : '保存到本地' }}
                                            </button>
                                        </div>
                                    </div>

                                    <div class="border-b border-slate-800 bg-slate-900/80 p-2">
                                        <div class="flex flex-wrap gap-1">
                                            <div v-for="item in compareLocalSummaryItems"
                                                 :key="'local-summary-' + item.label"
                                                 class="inline-flex items-center gap-1 rounded-lg border border-slate-700/60 bg-slate-800/60 px-2 py-1 text-[11px] text-slate-300">
                                                <span class="text-slate-500">{{ item.label }}</span>
                                                <span class="font-semibold text-slate-100">{{ item.value }}</span>
                                            </div>
                                        </div>
                                        <div v-if="compareLocalDraft && !compareLocalParsed.valid"
                                             class="mt-2 rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
                                            JSON 有误: {{ compareLocalParsed.error }}
                                        </div>
                                    </div>

                                    <textarea v-model="compareLocalDraft"
                                              spellcheck="false"
                                              class="flex-1 resize-none bg-[#0d1117] px-4 py-3 font-mono text-sm leading-7 text-slate-300 outline-none"></textarea>
                                </div>

                                <div class="flex flex-col items-center justify-center gap-2 xl:w-20">
                                    <div class="hidden h-full w-px bg-gradient-to-b from-transparent via-slate-700 to-transparent xl:block"></div>
                                    <div class="flex flex-row gap-2 xl:flex-col">
                                        <button @click="copyMainToLocalDraft"
                                                class="group flex h-9 min-w-[3.25rem] items-center justify-center rounded-xl border border-slate-700 bg-slate-800 px-2 text-[11px] font-medium text-slate-300 shadow-lg transition hover:scale-105 hover:border-blue-500 hover:bg-blue-500/10 hover:text-blue-300"
                                                title="把右侧草稿复制到左侧草稿">
                                            到左
                                        </button>
                                        <button @click="applyMainDraftToCurrentPreset"
                                                :disabled="compareSavingLocal || !compareMainParsed.valid"
                                                class="flex h-10 min-w-[3.5rem] items-center justify-center rounded-xl bg-gradient-to-br from-emerald-500 to-emerald-600 px-2 text-[11px] font-bold text-white shadow-lg shadow-emerald-500/30 transition hover:scale-105 disabled:pointer-events-none disabled:opacity-50"
                                                title="直接用右侧内容覆盖当前预设并保存">
                                            覆盖
                                        </button>
                                        <button @click="copyLocalToMainDraft"
                                                class="group flex h-9 min-w-[3.25rem] items-center justify-center rounded-xl border border-slate-700 bg-slate-800 px-2 text-[11px] font-medium text-slate-300 shadow-lg transition hover:scale-105 hover:border-emerald-500 hover:bg-emerald-500/10 hover:text-emerald-300"
                                                title="把左侧草稿复制到右侧草稿">
                                            到右
                                        </button>
                                    </div>
                                    <div class="hidden h-full w-px bg-gradient-to-b from-slate-700 via-slate-700 to-transparent xl:block"></div>
                                </div>

                                <div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/85 shadow-lg ring-1 ring-white/5">
                                    <div class="flex flex-wrap items-start justify-between gap-2 border-b border-slate-800 bg-slate-800/50 px-4 py-2.5">
                                        <div>
                                            <div class="text-sm font-semibold text-slate-100">main 分支参考</div>
                                            <div class="mt-0.5 text-[11px] text-slate-500">右侧是参考草稿，不会回写到远端仓库。</div>
                                        </div>
                                        <div class="flex flex-wrap gap-2">
                                            <button @click="resetConfigCompareMainDraft"
                                                    :disabled="!compareMainDirty"
                                                    class="rounded-lg px-2.5 py-1 text-[11px] font-medium text-slate-400 transition hover:bg-slate-700 hover:text-white disabled:opacity-30">
                                                恢复右侧
                                            </button>
                                            <button @click="loadMainBranchCompareConfig"
                                                    :disabled="compareMainLoading"
                                                    class="rounded-lg border border-slate-700 bg-slate-800 px-2.5 py-1 text-[11px] font-medium text-slate-300 transition hover:bg-slate-700 hover:text-white disabled:opacity-40">
                                                {{ compareMainLoading ? '读取中...' : '重读 main' }}
                                            </button>
                                        </div>
                                    </div>

                                    <div class="border-b border-slate-800 bg-slate-900/80 p-2">
                                        <div class="flex flex-wrap gap-1">
                                            <div v-for="item in compareMainSummaryItems"
                                                 :key="'main-summary-' + item.label"
                                                 class="inline-flex items-center gap-1 rounded-lg border border-slate-700/60 bg-slate-800/60 px-2 py-1 text-[11px] text-slate-300">
                                                <span class="text-slate-500">{{ item.label }}</span>
                                                <span class="font-semibold text-slate-100">{{ item.value }}</span>
                                            </div>
                                        </div>
                                        <div v-if="compareMainDraft && !compareMainParsed.valid"
                                             class="mt-2 rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
                                            JSON 有误: {{ compareMainParsed.error }}
                                        </div>
                                    </div>

                                    <textarea v-model="compareMainDraft"
                                              spellcheck="false"
                                              class="flex-1 resize-none bg-[#0d1117] px-4 py-3 font-mono text-sm leading-7 text-slate-300 outline-none"></textarea>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `
};
