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
        'update-image-config', 'test-image-extraction', 'reload-config'
    ],
        // 注册子组件（确保模板可解析）
    components: {
        'selector-panel': window.SelectorPanel,
        'extractor-panel': window.ExtractorPanel,
        'image-config-panel': window.ImageConfigPanel,
        'stream-config-panel': window.StreamConfigPanel,
        'workflow-panel': window.WorkflowPanel,
        'file-paste-panel': window.FilePastePanel
    },
    data() {
        return {
            // 🆕 预设管理
            selectedPreset: '主预设',
            availablePresets: [],
            presetLoading: false,
            newPresetName: '',
            showNewPresetInput: false,

            // 折叠状态
            selectorCollapsed: false,
            workflowCollapsed: false,
            imageConfigCollapsed: false,
            streamConfigCollapsed: false,
            filePasteCollapsed: false,

            // 默认配置
            defaultImageConfig: {
                enabled: false,
                selector: 'img',
                container_selector: null,
                debounce_seconds: 2.0,
                wait_for_load: true,
                load_timeout_seconds: 5.0,
                download_blobs: true,
                max_size_mb: 10,
                mode: 'all'
            },
            defaultStreamConfig: {
                mode: 'dom',
                hard_timeout: 300,
                silence_threshold: 2.5,
                initial_wait: 30.0,
                enable_wrapper_search: true,
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
            return presets[this.selectedPreset] || presets['主预设'] || Object.values(presets)[0] || null;
        },
        imageConfig() {
            if (!this.presetConfig) return this.defaultImageConfig;
            return { ...this.defaultImageConfig, ...(this.presetConfig.image_extraction || {}) };
        },
        streamConfig() {
            if (!this.presetConfig) return this.defaultStreamConfig;
            return { ...this.defaultStreamConfig, ...(this.presetConfig.stream_config || {}) };
        }
    },
    methods: {
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
                const response = await fetch('/api/sites/' + this.currentDomain + '/stream-config', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (response.ok) {
                    const pc = this.presetConfig;
                    if (pc) pc.stream_config = config;
                }
            } catch (e) {
                console.error('保存流式配置失败:', e);
                alert('保存失败: ' + e.message);
            }
        },

        // ===== 🆕 预设管理方法 =====

        async loadPresets() {
            if (!this.currentDomain) return;
            this.presetLoading = true;
            try {
                const response = await fetch('/api/presets/' + encodeURIComponent(this.currentDomain));
                if (response.ok) {
                    const data = await response.json();
                    this.availablePresets = data.presets || ['主预设'];
                } else {
                    this.availablePresets = ['主预设'];
                }
                // 确保选中的预设仍然有效
                if (!this.availablePresets.includes(this.selectedPreset)) {
                    this.selectedPreset = this.availablePresets[0] || '主预设';
                }
            } catch (e) {
                console.error('加载预设列表失败:', e);
                this.availablePresets = ['主预设'];
            } finally {
                this.presetLoading = false;
            }
        },

        switchPreset(presetName) {
            this.selectedPreset = presetName;
            // 触发父组件重新加载该预设的配置
            this.$emit('reload-config');
        },

        async createPreset() {
            const name = this.newPresetName.trim();
            if (!name) return;
            if (!this.currentDomain) return;

            try {
                const response = await fetch('/api/presets/' + encodeURIComponent(this.currentDomain), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        new_name: name,
                        source_name: this.selectedPreset
                    })
                });

                if (response.ok) {
                    this.newPresetName = '';
                    this.showNewPresetInput = false;
                    await this.loadPresets();
                    this.selectedPreset = name;
                    this.$emit('reload-config');
                    alert('✅ 预设 "' + name + '" 已创建（克隆自 "' + this.selectedPreset + '"）');
                } else {
                    const err = await response.json();
                    alert('❌ 创建失败: ' + (err.detail || '未知错误'));
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
                    { method: 'DELETE' }
                );

                if (response.ok) {
                    await this.loadPresets();
                    this.selectedPreset = this.availablePresets[0] || '主预设';
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
                    this.selectedPreset = '主预设';
                    this.loadPresets();
                } else {
                    this.availablePresets = [];
                    this.selectedPreset = '主预设';
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
                        </div>

                        <div class="flex items-center gap-2">
                            <!-- 新建预设 -->
                            <div v-if="showNewPresetInput" class="flex items-center gap-2">
                                <input v-model="newPresetName"
                                       @keyup.enter="createPreset"
                                       @keyup.escape="showNewPresetInput = false"
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
                            <button v-else @click="showNewPresetInput = true"
                                    class="px-3 py-1 text-xs font-medium bg-blue-500 text-white rounded hover:bg-blue-600 flex items-center gap-1">
                                ＋ 新建预设
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
                        新建预设会克隆当前选中的预设配置。在标签页池中可为不同标签页选择不同预设。
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

                <!-- 提取器面板 -->
                <extractor-panel v-if="presetConfig"
                    :extractor-id="presetConfig.extractor_id"
                    :extractor-verified="presetConfig.extractor_verified"
                />

                <!-- 图片配置面板 -->
                <image-config-panel v-if="presetConfig"
                    :image-config="imageConfig"
                    :current-domain="currentDomain"
                    :collapsed="imageConfigCollapsed"
                    @update:collapsed="imageConfigCollapsed = $event"
                    @update-image-config="$emit('update-image-config', $event)"
                    @test-image-extraction="$emit('test-image-extraction')"
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
                <!-- 工作流面板 -->
                <workflow-panel v-if="presetConfig"
                    :workflow="presetConfig.workflow || []"
                    :selectors="presetConfig.selectors || {}"
                    :current-domain="currentDomain"
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