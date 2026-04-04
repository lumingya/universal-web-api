// ==================== 文件粘贴配置面板 ====================

window.FilePastePanel = {
    name: 'FilePastePanel',
    props: {
        sites: { type: Object, required: true },
        currentDomain: { type: String, default: null },
        collapsed: { type: Boolean, default: true }
    },
    emits: ['update:collapsed'],
    data() {
        return {
            defaultFilePaste: {
                enabled: false,
                threshold: 50000,
                hint_text: '完全专注于文件内容'
            }
        };
    },
    computed: {
        domains() {
            return Object.keys(this.sites).sort();
        },
        enabledCount() {
            let count = 0;
            for (const domain of this.domains) {
                const fp = this.getFilePaste(domain);
                if (fp.enabled) count++;
            }
            return count;
        }
    },
    methods: {
        toggle() {
            this.$emit('update:collapsed', !this.collapsed);
        },

        /**
         * 获取指定站点的活跃预设配置（可变引用）
         * 查找顺序：ConfigTab 选中的预设 → 站点默认预设 → 主预设 → 第一个预设
         */
        _getActivePresetData(domain) {
            const site = this.sites[domain];
            if (!site) return null;

            const presets = site.presets;
            if (!presets) return site; // 兼容旧格式（无 presets 的扁平结构）

            // 如果是当前选中的站点，尝试使用 ConfigTab 选中的预设
            if (domain === this.currentDomain) {
                try {
                    const configTab = this.$parent?.$refs?.configTab;
                    if (configTab && configTab.selectedPreset && presets[configTab.selectedPreset]) {
                        return presets[configTab.selectedPreset];
                    }
                } catch (e) { /* 忽略 */ }
            }

            // 回退：默认预设 → 主预设 → 第一个预设
            const defaultPreset = typeof site.default_preset === 'string' ? site.default_preset : null;
            if (defaultPreset && presets[defaultPreset]) return presets[defaultPreset];
            if (presets['主预设']) return presets['主预设'];
            const keys = Object.keys(presets);
            return keys.length > 0 ? presets[keys[0]] : null;
        },

        getFilePaste(domain) {
            const presetData = this._getActivePresetData(domain);
            if (!presetData) return { ...this.defaultFilePaste };

            if (!presetData.file_paste) {
                presetData.file_paste = { ...this.defaultFilePaste };
            }
            return presetData.file_paste;
        },

        toggleEnabled(domain) {
            const fp = this.getFilePaste(domain);
            fp.enabled = !fp.enabled;
        },

        updateThreshold(domain, value) {
            const num = parseInt(value);
            if (!isNaN(num) && num >= 1000) {
                this.getFilePaste(domain).threshold = num;
            }
        },

        updateHintText(domain, value) {
            this.getFilePaste(domain).hint_text = value;
        },

        enableAll() {
            for (const domain of this.domains) {
                this.getFilePaste(domain).enabled = true;
            }
        },

        disableAll() {
            for (const domain of this.domains) {
                this.getFilePaste(domain).enabled = false;
            }
        }
    },
    template: `
        <div class="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-sm">
            <!-- 标题栏 -->
            <div class="px-4 py-3 border-b dark:border-gray-700 flex justify-between items-center cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                 @click="toggle">
                <div class="flex items-center gap-2">
                    <span class="w-4 inline-flex justify-center text-gray-500 dark:text-gray-400" v-html="collapsed ? $icons.chevronDown : $icons.chevronUp"></span>
                    <h3 class="font-semibold text-gray-900 dark:text-white">📄 文件粘贴</h3>
                    <span class="text-sm text-gray-500 dark:text-gray-400">({{ enabledCount }}/{{ domains.length }} 启用)</span>
                </div>
            </div>

            <!-- 内容 -->
            <div v-show="!collapsed" class="p-4 space-y-4">

                <!-- 说明 -->
                <div class="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-3">
                    <div class="flex items-start gap-2">
                        <svg class="w-5 h-5 text-blue-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
                        </svg>
                        <div class="text-sm text-blue-700 dark:text-blue-300">
                            <span class="font-medium">文件粘贴模式</span>
                            <p class="mt-0.5 text-xs text-blue-600 dark:text-blue-400">
                                当文本长度超过阈值时，将文本写入临时 .txt 文件，然后以文件形式粘贴到输入框。
                                适用于支持文件上传的 AI 网站。修改后请点击右上角「保存」按钮。
                            </p>
                        </div>
                    </div>
                </div>

                <!-- 批量操作 -->
                <div class="flex gap-2">
                    <button @click="enableAll"
                            class="px-2 py-1 rounded text-xs font-medium border border-green-300 dark:border-green-700 text-green-600 dark:text-green-400 hover:bg-green-50 dark:hover:bg-green-900/30 transition-colors">
                        全部启用
                    </button>
                    <button @click="disableAll"
                            class="px-2 py-1 rounded text-xs font-medium border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors">
                        全部禁用
                    </button>
                </div>

                <!-- 空状态 -->
                <div v-if="domains.length === 0" class="text-center text-gray-400 dark:text-gray-500 text-sm py-6">
                    暂无站点配置
                </div>

                <!-- 站点列表 -->
                <div v-else class="space-y-2 max-h-96 overflow-auto">
                    <div v-for="domain in domains" :key="domain"
                         :class="['border dark:border-gray-700 rounded-lg p-3 transition-colors bg-gray-50/50 dark:bg-gray-900/30',
                                  domain === currentDomain ? 'border-blue-400 dark:border-blue-500 ring-1 ring-blue-200 dark:ring-blue-800' : 'hover:border-blue-300 dark:hover:border-blue-600']">
                        <div class="flex items-center gap-4">
                            <!-- 启用开关 -->
                            <label class="toggle-label scale-75 flex-shrink-0">
                                <input type="checkbox" :checked="getFilePaste(domain).enabled" @change="toggleEnabled(domain)" class="sr-only peer">
                                <div class="toggle-bg"></div>
                            </label>

                            <!-- 域名 -->
                            <div class="flex-1 min-w-0">
                                <span class="text-sm font-medium text-gray-900 dark:text-white truncate block">{{ domain }}</span>
                            </div>

                            <!-- 阈值输入 -->
                            <div class="flex items-center gap-2 flex-shrink-0">
                                <label class="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">阈值</label>
                                <input type="number"
                                       :value="getFilePaste(domain).threshold"
                                       @input="updateThreshold(domain, $event.target.value)"
                                       :disabled="!getFilePaste(domain).enabled"
                                       min="1000"
                                       step="1000"
                                       :class="['w-28 border dark:border-gray-600 px-2 py-1 rounded text-sm text-right bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent',
                                                !getFilePaste(domain).enabled ? 'opacity-50 cursor-not-allowed' : '']">
                                <span class="text-xs text-gray-400 dark:text-gray-500 whitespace-nowrap">字符</span>
                            </div>
                        </div>
                        <!-- 引导文本（启用时展开） -->
                        <div v-if="getFilePaste(domain).enabled" class="mt-2 pl-10">
                            <div class="flex items-center gap-2">
                                <label class="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">引导文本</label>
                                <input type="text"
                                       :value="getFilePaste(domain).hint_text"
                                       @input="updateHintText(domain, $event.target.value)"
                                       placeholder="粘贴文件后追加的文字，留空则不追加"
                                       class="flex-1 border dark:border-gray-600 px-2 py-1 rounded text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                            </div>
                            <p class="text-xs text-gray-400 dark:text-gray-500 mt-1 pl-12">粘贴文件后自动在输入框中输入此文本，确保能正常发送</p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `
};

