// ==================== 提示词首尾填充面板 ====================

window.PromptPaddingPanel = {
    name: 'PromptPaddingPanel',
    props: {
        promptPaddingConfig: { type: Object, required: true },
        currentDomain: { type: String, default: null },
        selectedPreset: { type: String, default: null },
        collapsed: { type: Boolean, default: true }
    },
    emits: ['update:collapsed'],
    data() {
        return {
            defaultPromptPadding: {
                enabled: false,
                marker_text: '测试号，无实际意义',
                segments_per_side: 12
            }
        };
    },
    computed: {
        resolvedPromptPadding() {
            const raw = this.promptPaddingConfig || {};
            return {
                ...this.defaultPromptPadding,
                ...raw
            };
        },
        currentPresetLabel() {
            return String(this.selectedPreset || '').trim() || '主预设';
        },
        statusText() {
            return this.resolvedPromptPadding.enabled ? '已启用' : '未启用';
        },
        markerPreview() {
            const markerText = String(this.resolvedPromptPadding.marker_text || '').trim();
            if (!markerText) {
                return '随机片段';
            }
            if (markerText.endsWith(':') || markerText.endsWith('：')) {
                return markerText + '随机片段';
            }
            return markerText + ':随机片段';
        }
    },
    methods: {
        toggle() {
            this.$emit('update:collapsed', !this.collapsed);
        },

        getMutablePromptPadding() {
            return this.promptPaddingConfig || {};
        },

        toggleEnabled() {
            const cfg = this.getMutablePromptPadding();
            cfg.enabled = !this.resolvedPromptPadding.enabled;
        },

        updateMarkerText(value) {
            this.getMutablePromptPadding().marker_text = String(value || '');
        },

        updateSegmentsPerSide(value) {
            const parsed = parseInt(value, 10);
            if (!Number.isFinite(parsed)) {
                return;
            }
            this.getMutablePromptPadding().segments_per_side = Math.max(1, Math.min(parsed, 64));
        }
    },
    template: `
        <div class="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-sm">
            <div class="px-4 py-3 border-b dark:border-gray-700 flex justify-between items-center cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                 @click="toggle">
                <div class="flex items-center gap-2">
                    <span class="w-4 inline-flex justify-center text-gray-500 dark:text-gray-400" v-html="collapsed ? $icons.chevronDown : $icons.chevronUp"></span>
                    <h3 class="font-semibold text-gray-900 dark:text-white">✳️ 首尾填充</h3>
                    <span class="text-sm text-gray-500 dark:text-gray-400">({{ statusText }})</span>
                </div>
            </div>

            <div v-show="!collapsed" class="p-4 space-y-4">
                <div class="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg px-4 py-3">
                    <div class="text-sm text-blue-700 dark:text-blue-300">
                        当前预设：{{ currentPresetLabel }}
                    </div>
                </div>

                <div class="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50/60 dark:bg-gray-900/30 p-4 space-y-4">
                    <div class="flex items-center justify-between gap-4">
                        <div>
                            <div class="text-sm font-medium text-gray-800 dark:text-gray-100">发送前首尾追加随机占位片段</div>
                            <p class="mt-1 text-xs text-gray-500 dark:text-gray-400 leading-5">
                                开启后，会在提示词开头和结尾各追加一段随机片段，不改动中间原始消息主体。修改会跟随当前站点配置一起保存。
                            </p>
                        </div>
                        <label class="toggle-label scale-90 flex-shrink-0">
                            <input type="checkbox" :checked="resolvedPromptPadding.enabled" @change="toggleEnabled" class="sr-only peer">
                            <div class="toggle-bg"></div>
                        </label>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">每侧片段数</label>
                            <div class="flex items-center gap-2">
                                <input type="number"
                                       :value="resolvedPromptPadding.segments_per_side"
                                       @input="updateSegmentsPerSide($event.target.value)"
                                       min="1"
                                       max="64"
                                       step="1"
                                       class="flex-1 border dark:border-gray-600 px-3 py-2 rounded-md text-sm text-right bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                <span class="text-sm text-gray-500 dark:text-gray-400">个</span>
                            </div>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">说明文本</label>
                            <input type="text"
                                   :value="resolvedPromptPadding.marker_text"
                                   @input="updateMarkerText($event.target.value)"
                                   placeholder="留空则只追加随机片段"
                                   class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                        </div>
                    </div>

                    <div class="rounded-lg border border-slate-200 dark:border-slate-700 bg-white/80 dark:bg-slate-900/40 px-4 py-3">
                        <div class="text-sm font-medium text-gray-800 dark:text-gray-100">发送效果</div>
                        <p class="mt-1 text-xs leading-5 text-gray-500 dark:text-gray-400">
                            实际发送时，会在提示词开头和结尾各生成一段类似
                            <code class="font-mono">{{ markerPreview }}</code>
                            的文本。随机片段会混合数字和单字符字母，尽量贴近当前需求的样式。
                        </p>
                    </div>
                </div>
            </div>
        </div>
    `
};
