// ==================== 多模态提取配置面板 ====================

window.ImageConfigPanel = {
    name: 'ImageConfigPanel',
    props: {
        imageConfig: { type: Object, required: true },
        currentDomain: { type: String, default: null },
        collapsed: { type: Boolean, default: true }
    },
    emits: ['update:collapsed', 'update-image-config', 'reload-config'],
    data() {
        return {
            showPresetMenu: false,
            availablePresets: [],
            currentPreset: null,
            loadingPresets: false
        };
    },
    computed: {
        modalities() {
            return {
                image: false,
                audio: false,
                video: false,
                ...(this.imageConfig.modalities || {})
            };
        },
        isEnabled() {
            return ['image', 'audio', 'video'].some(key => !!this.modalities[key]);
        },
        enabledLabels() {
            const labels = [];
            if (this.modalities.image) labels.push('图片');
            if (this.modalities.audio) labels.push('音频');
            if (this.modalities.video) labels.push('视频');
            return labels;
        }
    },
    watch: {
        currentDomain(newVal) {
            if (newVal) this.checkCurrentPreset();
        }
    },
    mounted() {
        this.loadPresets();
        if (this.currentDomain) this.checkCurrentPreset();
    },
    methods: {
        toggle() {
            this.$emit('update:collapsed', !this.collapsed);
        },

        buildNextConfig(patch = {}) {
            const next = {
                ...this.imageConfig,
                ...patch,
                modalities: {
                    ...this.modalities,
                    ...((patch && patch.modalities) || {})
                }
            };
            next.enabled = ['image', 'audio', 'video'].some(key => !!next.modalities[key]);
            return next;
        },

        updateField(field, value) {
            const newConfig = this.buildNextConfig({ [field]: value });
            this.$emit('update-image-config', newConfig);
        },

        toggleModality(type) {
            const nextModalities = {
                ...this.modalities,
                [type]: !this.modalities[type]
            };
            this.updateField('modalities', nextModalities);
        },

        modalityCardClass(type) {
            return this.modalities[type]
                ? 'border-blue-400 bg-blue-50 dark:bg-blue-900/20 dark:border-blue-500/50'
                : 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40';
        },

        inputWrapClass(enabled) {
            return enabled ? '' : 'opacity-50';
        },

        async loadPresets() {
            if (this.loadingPresets) return;
            this.loadingPresets = true;
            try {
                const response = await fetch('/api/image-presets');
                if (response.ok) {
                    const data = await response.json();
                    this.availablePresets = data.presets || [];
                }
            } catch (e) {
                console.error('加载预设失败:', e);
            } finally {
                this.loadingPresets = false;
            }
        },

        async checkCurrentPreset() {
            if (!this.currentDomain) return;
            try {
                const response = await fetch('/api/sites/' + this.currentDomain + '/image-preset');
                if (response.ok) {
                    const data = await response.json();
                    this.currentPreset = data.available ? data : null;
                }
            } catch (e) {
                this.currentPreset = null;
            }
        },

        async applyPreset(presetDomain) {
            if (!this.currentDomain) return;
            try {
                const response = await fetch('/api/sites/' + this.currentDomain + '/apply-image-preset', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ preset_domain: presetDomain })
                });
                if (response.ok) {
                    this.showPresetMenu = false;
                    this.$emit('reload-config');
                    await this.checkCurrentPreset();
                }
            } catch (e) {
                alert('应用预设失败: ' + e.message);
            }
        },

        togglePresetMenu(e) {
            e.stopPropagation();
            this.showPresetMenu = !this.showPresetMenu;
            if (this.showPresetMenu && this.availablePresets.length === 0) {
                this.loadPresets();
            }
        },

        closeMenu() {
            this.showPresetMenu = false;
        }
    },
    template: `
        <div class="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-sm" @click="closeMenu">
            <div class="px-4 py-3 border-b dark:border-gray-700 flex justify-between items-center cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                 @click="toggle">
                <div class="flex items-center gap-2 flex-wrap">
                    <span class="w-4 inline-flex justify-center text-gray-500 dark:text-gray-400" v-html="collapsed ? $icons.chevronDown : $icons.chevronUp"></span>
                    <h3 class="font-semibold text-gray-900 dark:text-white">🎞️ 多模态提取</h3>
                    <span v-if="isEnabled" class="px-2 py-0.5 text-xs bg-green-100 dark:bg-green-900/50 text-green-700 dark:text-green-300 rounded font-medium">已启用</span>
                    <span v-else class="px-2 py-0.5 text-xs bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 rounded">未启用</span>
                    <span v-for="label in enabledLabels" :key="label"
                          class="px-2 py-0.5 text-xs bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 rounded font-medium">
                        {{ label }}
                    </span>
                    <span v-if="currentPreset && currentPreset.available"
                          class="px-2 py-0.5 text-xs bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 rounded font-medium flex items-center gap-1">
                        <svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><path d="M10 2a6 6 0 00-6 6v3.586l-.707.707A1 1 0 004 14h12a1 1 0 00.707-1.707L16 11.586V8a6 6 0 00-6-6zM10 18a3 3 0 01-3-3h6a3 3 0 01-3 3z"/></svg>
                        预设
                    </span>
                </div>

                <div class="flex items-center gap-2" @click.stop>
                    <div class="relative">
                        <button @click="togglePresetMenu"
                                class="px-3 py-1.5 text-sm font-medium text-gray-700 dark:text-gray-200 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors flex items-center gap-1">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
                            </svg>
                            预设
                        </button>

                        <div v-if="showPresetMenu" class="absolute right-0 mt-1 w-80 max-h-96 overflow-y-auto bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-lg z-10">
                            <div v-if="currentPreset && currentPreset.available" class="px-3 py-2 bg-blue-50 dark:bg-blue-900/30 border-b dark:border-gray-700 text-xs text-blue-700 dark:text-blue-300">
                                <div class="font-medium">当前使用预设</div>
                                <div class="mt-0.5">{{ currentPreset.name }}</div>
                            </div>
                            <div v-if="loadingPresets" class="px-3 py-6 text-center text-sm text-gray-400">加载中...</div>
                            <div v-else-if="availablePresets.length > 0" class="divide-y dark:divide-gray-700">
                                <div class="px-3 py-1.5 bg-gray-50 dark:bg-gray-900/50 text-xs font-medium text-gray-500">站点预设</div>
                                <button v-for="preset in availablePresets" :key="preset.domain"
                                        @click="applyPreset(preset.domain)"
                                        class="w-full text-left px-3 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors">
                                    <div class="font-medium text-sm text-gray-900 dark:text-white truncate">{{ preset.name }}</div>
                                    <div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{{ preset.domain }}</div>
                                </button>
                            </div>
                            <div v-else class="px-3 py-6 text-center text-sm text-gray-400">暂无可用预设</div>
                        </div>
                    </div>
                </div>
            </div>

            <div v-show="!collapsed" class="p-4 space-y-4">
                <div>
                    <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">提取类型</div>
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                        <div :class="['border rounded-xl p-4 transition-colors', modalityCardClass('image')]">
                            <div class="flex items-start justify-between gap-3">
                                <div>
                                    <div class="text-sm font-medium text-gray-900 dark:text-white">图片提取</div>
                                    <div class="mt-1 text-xs text-gray-500 dark:text-gray-400">提取回复中的图片资源，并以图片形式返回。</div>
                                </div>
                                <label class="toggle-label scale-90">
                                    <input type="checkbox" :checked="modalities.image" @change="toggleModality('image')" class="sr-only peer">
                                    <div class="toggle-bg"></div>
                                </label>
                            </div>
                        </div>

                        <div :class="['border rounded-xl p-4 transition-colors', modalityCardClass('audio')]">
                            <div class="flex items-start justify-between gap-3">
                                <div>
                                    <div class="text-sm font-medium text-gray-900 dark:text-white">音频文件提取</div>
                                    <div class="mt-1 text-xs text-gray-500 dark:text-gray-400">提取回复里的音频节点或音频源链接。</div>
                                </div>
                                <label class="toggle-label scale-90">
                                    <input type="checkbox" :checked="modalities.audio" @change="toggleModality('audio')" class="sr-only peer">
                                    <div class="toggle-bg"></div>
                                </label>
                            </div>
                        </div>

                        <div :class="['border rounded-xl p-4 transition-colors', modalityCardClass('video')]">
                            <div class="flex items-start justify-between gap-3">
                                <div>
                                    <div class="text-sm font-medium text-gray-900 dark:text-white">视频提取</div>
                                    <div class="mt-1 text-xs text-gray-500 dark:text-gray-400">提取回复里的视频节点或视频源链接。</div>
                                </div>
                                <label class="toggle-label scale-90">
                                    <input type="checkbox" :checked="modalities.video" @change="toggleModality('video')" class="sr-only peer">
                                    <div class="toggle-bg"></div>
                                </label>
                            </div>
                        </div>
                    </div>
                </div>

                <div v-if="!isEnabled" class="bg-gray-50 dark:bg-gray-900/50 border border-gray-200 dark:border-gray-700 rounded-lg p-3 text-center">
                    <div class="text-gray-500 dark:text-gray-400 text-sm">当前未启用任何提取类型。请至少开启一种媒体提取能力。</div>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div :class="inputWrapClass(modalities.image)">
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">图片选择器</label>
                        <input type="text" :value="imageConfig.selector" @input="updateField('selector', $event.target.value)" placeholder="img"
                               :disabled="!modalities.image"
                               class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent disabled:cursor-not-allowed">
                        <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">默认 <code>img</code></p>
                    </div>
                    <div :class="inputWrapClass(modalities.audio)">
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">音频选择器</label>
                        <input type="text" :value="imageConfig.audio_selector" @input="updateField('audio_selector', $event.target.value)" placeholder="audio, audio source"
                               :disabled="!modalities.audio"
                               class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent disabled:cursor-not-allowed">
                        <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">默认 <code>audio, audio source</code></p>
                    </div>
                    <div :class="inputWrapClass(modalities.video)">
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">视频选择器</label>
                        <input type="text" :value="imageConfig.video_selector" @input="updateField('video_selector', $event.target.value)" placeholder="video, video source"
                               :disabled="!modalities.video"
                               class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent disabled:cursor-not-allowed">
                        <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">默认 <code>video, video source</code></p>
                    </div>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">容器选择器 <span class="text-gray-400 font-normal">(可选)</span></label>
                        <input type="text" :value="imageConfig.container_selector || ''" @input="updateField('container_selector', $event.target.value || null)" placeholder="留空则使用响应容器"
                               class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                        <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">统一限定媒体查找范围</p>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">提取模式</label>
                        <select :value="imageConfig.mode" @change="updateField('mode', $event.target.value)"
                                class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                            <option value="all">全部提取</option>
                            <option value="first">仅第一项</option>
                            <option value="last">仅最后一项</option>
                        </select>
                    </div>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">最大大小 (MB)</label>
                        <select :value="imageConfig.max_size_mb" @change="updateField('max_size_mb', parseInt($event.target.value))"
                                class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                            <option :value="5">5 MB</option>
                            <option :value="10">10 MB</option>
                            <option :value="20">20 MB</option>
                            <option :value="50">50 MB</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">防抖延迟 (秒)</label>
                        <input type="number" :value="imageConfig.debounce_seconds" @input="updateField('debounce_seconds', parseFloat($event.target.value) || 2)" min="0" max="30" step="0.5"
                               class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">加载超时时间 (秒)</label>
                        <input type="number" :value="imageConfig.load_timeout_seconds" @input="updateField('load_timeout_seconds', parseFloat($event.target.value) || 5)"
                               min="1" max="60" step="1" :disabled="!imageConfig.wait_for_load"
                               :class="['w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent', !imageConfig.wait_for_load ? 'opacity-50 cursor-not-allowed' : '']">
                    </div>
                </div>

                <div class="border-t dark:border-gray-700 pt-4">
                    <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">高级选项</div>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div class="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg">
                            <div>
                                <div class="text-sm font-medium text-gray-700 dark:text-gray-300">等待媒体加载</div>
                                <div class="text-xs text-gray-500 dark:text-gray-400">等待音视频或图片完成加载后再提取</div>
                            </div>
                            <label class="toggle-label scale-90">
                                <input type="checkbox" :checked="imageConfig.wait_for_load" @change="updateField('wait_for_load', $event.target.checked)" class="sr-only peer">
                                <div class="toggle-bg"></div>
                            </label>
                        </div>
                        <div class="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg">
                            <div>
                                <div class="text-sm font-medium text-gray-700 dark:text-gray-300">转换 Blob 媒体</div>
                                <div class="text-xs text-gray-500 dark:text-gray-400">将 blob: 资源转为可返回的数据 URI 或本地文件</div>
                            </div>
                            <label class="toggle-label scale-90">
                                <input type="checkbox" :checked="imageConfig.download_blobs" @change="updateField('download_blobs', $event.target.checked)" class="sr-only peer">
                                <div class="toggle-bg"></div>
                            </label>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `
};
