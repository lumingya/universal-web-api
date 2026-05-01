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
                        新建预设会克隆当前选中的预设配置。在标签页池中可为不同标签页选择不同预设。未手动指定时会自动使用“默认预设”。
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
            </div>
        </div>
    `
};
