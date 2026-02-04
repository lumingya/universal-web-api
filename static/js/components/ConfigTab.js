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
        'workflow-panel': window.WorkflowPanel
    },
    data() {
        return {
            // 折叠状态
            selectorCollapsed: false,
            workflowCollapsed: false,
            imageConfigCollapsed: false,
            streamConfigCollapsed: false,

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
        imageConfig() {
            if (!this.currentConfig) return this.defaultImageConfig;
            return { ...this.defaultImageConfig, ...(this.currentConfig.image_extraction || {}) };
        },
        streamConfig() {
            if (!this.currentConfig) return this.defaultStreamConfig;
            return { ...this.defaultStreamConfig, ...(this.currentConfig.stream_config || {}) };
        }
    },
    methods: {
        // 选择器值更新
        updateSelectorValue(key, value) {
            if (this.currentConfig && this.currentConfig.selectors) {
                this.currentConfig.selectors[key] = value;
            }
        },

        // 流式配置保存
        async saveStreamConfig(config) {
            if (!this.currentDomain) return;
            try {
                const response = await fetch('/api/sites/' + this.currentDomain + '/stream-config', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(config)
                });
                if (response.ok && this.currentConfig) {
                    this.currentConfig.stream_config = config;
                }
            } catch (e) {
                console.error('保存流式配置失败:', e);
                alert('保存失败: ' + e.message);
            }
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
                
                <!-- 选择器面板 -->
                <selector-panel
                    :selectors="currentConfig.selectors"
                    :collapsed="selectorCollapsed"
                    @update:collapsed="selectorCollapsed = $event"
                    @add-selector="$emit('add-selector', $event)"
                    @remove-selector="$emit('remove-selector', $event)"
                    @update-selector-key="(oldKey, newKey) => $emit('update-selector-key', oldKey, newKey)"
                    @update-selector-value="updateSelectorValue"
                    @test-selector="(key, val) => $emit('test-selector', key, val)"
                />

                <!-- 提取器面板 -->
                <extractor-panel
                    :extractor-id="currentConfig.extractor_id"
                    :extractor-verified="currentConfig.extractor_verified"
                />

                <!-- 图片配置面板 -->
                <image-config-panel
                    :image-config="imageConfig"
                    :current-domain="currentDomain"
                    :collapsed="imageConfigCollapsed"
                    @update:collapsed="imageConfigCollapsed = $event"
                    @update-image-config="$emit('update-image-config', $event)"
                    @test-image-extraction="$emit('test-image-extraction')"
                    @reload-config="$emit('reload-config')"
                />

                <!-- 流式配置面板 -->
                <stream-config-panel
                    :stream-config="streamConfig"
                    :current-domain="currentDomain"
                    :collapsed="streamConfigCollapsed"
                    @update:collapsed="streamConfigCollapsed = $event"
                    @save-stream-config="saveStreamConfig"
                />

                <!-- 工作流面板 -->
                <workflow-panel
                    :workflow="currentConfig.workflow"
                    :selectors="currentConfig.selectors"
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