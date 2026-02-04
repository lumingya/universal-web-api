// ==================== 图片提取配置面板 ====================

window.ImageConfigPanel = {
    name: 'ImageConfigPanel',
    props: {
        imageConfig: { type: Object, required: true },
        currentDomain: { type: String, default: null },
        collapsed: { type: Boolean, default: false }
    },
    emits: ['update:collapsed', 'update-image-config', 'test-image-extraction', 'reload-config'],
    data() {
        return {
            showPresetMenu: false,
            availablePresets: [],
            currentPreset: null,
            loadingPresets: false
        };
    },
    computed: {
        isEnabled() {
            return this.imageConfig.enabled;
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

        updateField(field, value) {
            const newConfig = { ...this.imageConfig, [field]: value };
            this.$emit('update-image-config', newConfig);
        },

        toggleEnabled() {
            this.updateField('enabled', !this.imageConfig.enabled);
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
            <!-- 标题栏 -->
            <div class="px-4 py-3 border-b dark:border-gray-700 flex justify-between items-center cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                 @click="toggle">
                <div class="flex items-center gap-2">
                    <span class="text-gray-500 dark:text-gray-400" v-html="collapsed ? $icons.chevronDown : $icons.chevronUp"></span>
                    <h3 class="font-semibold text-gray-900 dark:text-white">🖼️ 图片提取</h3>
                    <span v-if="isEnabled" class="px-2 py-0.5 text-xs bg-green-100 dark:bg-green-900/50 text-green-700 dark:text-green-300 rounded font-medium">已启用</span>
                    <span v-else class="px-2 py-0.5 text-xs bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 rounded">未启用</span>
                    <span v-if="currentPreset && currentPreset.available"
                          class="px-2 py-0.5 text-xs bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 rounded font-medium flex items-center gap-1">
                        <svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><path d="M10 2a6 6 0 00-6 6v3.586l-.707.707A1 1 0 004 14h12a1 1 0 00.707-1.707L16 11.586V8a6 6 0 00-6-6zM10 18a3 3 0 01-3-3h6a3 3 0 01-3 3z"/></svg>
                        预设
                    </span>
                </div>
                
                <div class="flex items-center gap-2" @click.stop>
                    <!-- 预设按钮 -->
                    <div class="relative">
                        <button @click="togglePresetMenu"
                                class="px-3 py-1.5 text-sm font-medium text-gray-700 dark:text-gray-200 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors flex items-center gap-1">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
                            </svg>
                            预设
                        </button>
                        
                        <!-- 预设菜单 -->
                        <div v-if="showPresetMenu" class="absolute right-0 mt-1 w-80 max-h-96 overflow-y-auto bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-lg z-10">
                            <div v-if="currentPreset && currentPreset.available" class="px-3 py-2 bg-blue-50 dark:bg-blue-900/30 border-b dark:border-gray-700 text-xs text-blue-700 dark:text-blue-300">
                                <div class="font-medium">当前使用预设</div>
                                <div class="mt-0.5">{{ currentPreset.name }}</div>
                            </div>
                            <div v-if="loadingPresets" class="px-3 py-6 text-center text-sm text-gray-400">加载中...</div>
                            <div v-else-if="availablePresets.length > 0" class="divide-y dark:divide-gray-700">
                                <template v-if="availablePresets.filter(p => !p.is_special).length > 0">
                                    <div class="px-3 py-1.5 bg-gray-50 dark:bg-gray-900/50 text-xs font-medium text-gray-500">站点预设</div>
                                    <button v-for="preset in availablePresets.filter(p => !p.is_special)" :key="preset.domain"
                                            @click="applyPreset(preset.domain)"
                                            class="w-full text-left px-3 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors">
                                        <div class="font-medium text-sm text-gray-900 dark:text-white truncate">{{ preset.name }}</div>
                                        <div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{{ preset.domain }}</div>
                                    </button>
                                </template>
                                <template v-if="availablePresets.filter(p => p.is_special).length > 0">
                                    <div class="px-3 py-1.5 bg-gray-50 dark:bg-gray-900/50 text-xs font-medium text-gray-500">通用预设</div>
                                    <button v-for="preset in availablePresets.filter(p => p.is_special)" :key="preset.domain"
                                            @click="applyPreset(preset.domain)"
                                            class="w-full text-left px-3 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors">
                                        <div class="font-medium text-sm text-gray-900 dark:text-white">{{ preset.name }}</div>
                                        <div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{{ preset.description }}</div>
                                    </button>
                                </template>
                            </div>
                            <div v-else class="px-3 py-6 text-center text-sm text-gray-400">暂无可用预设</div>
                        </div>
                    </div>
                    
                    <!-- 开关 -->
                    <label class="toggle-label scale-90">
                        <input type="checkbox" :checked="isEnabled" @change="toggleEnabled" class="sr-only peer">
                        <div class="toggle-bg"></div>
                    </label>
                </div>
            </div>

            <!-- 内容 -->
            <div v-show="!collapsed" class="p-4 space-y-4">
                <!-- 禁用提示 -->
                <div v-if="!isEnabled" class="bg-gray-50 dark:bg-gray-900/50 border border-gray-200 dark:border-gray-700 rounded-lg p-3 text-center">
                    <div class="text-gray-500 dark:text-gray-400 text-sm">图片提取功能已禁用。启用后，AI 回复中的图片将被自动提取并返回。</div>
                    <button @click="toggleEnabled" class="mt-2 px-4 py-1.5 text-sm font-medium text-white bg-blue-500 hover:bg-blue-600 rounded-lg transition-colors">启用图片提取</button>
                </div>

                <!-- 配置表单 -->
                <template v-else>
                    <!-- 基础配置 -->
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">图片选择器</label>
                            <input type="text" :value="imageConfig.selector" @input="updateField('selector', $event.target.value)" placeholder="img"
                                   class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                            <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">CSS 选择器，默认为 img</p>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">容器选择器 <span class="text-gray-400 font-normal">(可选)</span></label>
                            <input type="text" :value="imageConfig.container_selector || ''" @input="updateField('container_selector', $event.target.value || null)" placeholder="留空则使用响应容器"
                                   class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                            <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">限定图片查找范围</p>
                        </div>
                    </div>

                    <!-- 提取模式 -->
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">提取模式</label>
                            <select :value="imageConfig.mode" @change="updateField('mode', $event.target.value)"
                                    class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                <option value="all">全部图片</option>
                                <option value="first">仅第一张</option>
                                <option value="last">仅最后一张</option>
                            </select>
                        </div>
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
                    </div>

                    <!-- 高级选项 -->
                    <div class="border-t dark:border-gray-700 pt-4">
                        <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">高级选项</div>
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div class="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg">
                                <div>
                                    <div class="text-sm font-medium text-gray-700 dark:text-gray-300">等待图片加载</div>
                                    <div class="text-xs text-gray-500 dark:text-gray-400">等待图片加载完成后再提取</div>
                                </div>
                                <label class="toggle-label scale-90">
                                    <input type="checkbox" :checked="imageConfig.wait_for_load" @change="updateField('wait_for_load', $event.target.checked)" class="sr-only peer">
                                    <div class="toggle-bg"></div>
                                </label>
                            </div>
                            <div class="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg">
                                <div>
                                    <div class="text-sm font-medium text-gray-700 dark:text-gray-300">转换 Blob 图片</div>
                                    <div class="text-xs text-gray-500 dark:text-gray-400">将 blob: URL 转为 data URI</div>
                                </div>
                                <label class="toggle-label scale-90">
                                    <input type="checkbox" :checked="imageConfig.download_blobs" @change="updateField('download_blobs', $event.target.checked)" class="sr-only peer">
                                    <div class="toggle-bg"></div>
                                </label>
                            </div>
                            <div class="md:col-span-2 flex items-center gap-4 p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg">
                                <div class="flex-1">
                                    <div class="text-sm font-medium text-gray-700 dark:text-gray-300">加载超时时间</div>
                                    <div class="text-xs text-gray-500 dark:text-gray-400">等待单张图片加载的最长时间</div>
                                </div>
                                <div class="flex items-center gap-2">
                                    <input type="number" :value="imageConfig.load_timeout_seconds" @input="updateField('load_timeout_seconds', parseFloat($event.target.value) || 5)"
                                           min="1" max="60" step="1" :disabled="!imageConfig.wait_for_load"
                                           :class="['w-20 border dark:border-gray-600 px-2 py-1 rounded text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white', !imageConfig.wait_for_load ? 'opacity-50 cursor-not-allowed' : '']">
                                    <span class="text-sm text-gray-500 dark:text-gray-400">秒</span>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- 测试按钮 -->
                    <div class="border-t dark:border-gray-700 pt-4 flex justify-end">
                        <button @click="$emit('test-image-extraction')"
                                class="px-4 py-2 text-sm font-medium text-blue-600 dark:text-blue-400 border border-blue-300 dark:border-blue-600 rounded-lg hover:bg-blue-50 dark:hover:bg-blue-900/30 transition-colors flex items-center gap-2">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.348a1.125 1.125 0 010 1.971l-11.54 6.347a1.125 1.125 0 01-1.667-.985V5.653z"/>
                            </svg>
                            测试图片提取
                        </button>
                    </div>
                </template>
            </div>
        </div>
    `
};