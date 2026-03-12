// ==================== 流式配置面板 ====================

window.StreamConfigPanel = {
    name: 'StreamConfigPanel',
    props: {
        streamConfig: { type: Object, required: true },
        currentDomain: { type: String, default: null },
        collapsed: { type: Boolean, default: true }
    },
    emits: ['update:collapsed', 'save-stream-config'],
    data() {
        return {
            availableParsers: [],
            loadingParsers: false,
            defaultNetworkConfig: {
                listen_pattern: '',
                parser: '',
                first_response_timeout: 300.0,
                silence_threshold: 3.0,
                response_interval: 0.5
            }
        };
    },
    computed: {
        isNetworkMode() {
            return this.streamConfig.mode === 'network';
        },
        networkConfig() {
            return {
                ...this.defaultNetworkConfig,
                ...(this.streamConfig.network || {})
            };
        }
    },
    mounted() {
        if (this.isNetworkMode) {
            this.loadParsers();
        }
    },
    methods: {
        toggle() {
            this.$emit('update:collapsed', !this.collapsed);
        },

        updateField(field, value) {
            const newConfig = { ...this.streamConfig, [field]: value };
            this.$emit('save-stream-config', newConfig);
        },

        updateNetworkField(field, value) {
            const network = { ...this.networkConfig, [field]: value };
            const newConfig = { ...this.streamConfig, network };
            this.$emit('save-stream-config', newConfig);
        },

        findParserMeta(parserId) {
            return this.availableParsers.find(parser => parser.id === parserId) || null;
        },

        getPreferredListenPattern(parserId) {
            const parser = this.findParserMeta(parserId);
            if (!parser || !Array.isArray(parser.patterns) || parser.patterns.length === 0) {
                return '';
            }
            return String(parser.patterns[0] || '').replace(/^\*\*\//, '');
        },

        handleParserChange(parserId) {
            const currentPattern = (this.networkConfig.listen_pattern || '').trim();
            const nextPattern = currentPattern || this.getPreferredListenPattern(parserId);
            const network = {
                ...this.networkConfig,
                parser: parserId,
                listen_pattern: nextPattern
            };
            const newConfig = { ...this.streamConfig, network };
            this.$emit('save-stream-config', newConfig);
        },

        autofillListenPatternFromCurrentParser() {
            const parserId = (this.networkConfig.parser || '').trim();
            const currentPattern = (this.networkConfig.listen_pattern || '').trim();
            if (!parserId || currentPattern) {
                return;
            }

            const suggestedPattern = this.getPreferredListenPattern(parserId);
            if (!suggestedPattern) {
                return;
            }

            const network = {
                ...this.networkConfig,
                listen_pattern: suggestedPattern
            };
            const newConfig = { ...this.streamConfig, network };
            this.$emit('save-stream-config', newConfig);
        },

        toggleNetworkMode() {
            if (this.isNetworkMode) {
                this.updateField('mode', 'dom');
            } else {
                const newConfig = {
                    ...this.streamConfig,
                    mode: 'network',
                    network: this.streamConfig.network || { ...this.defaultNetworkConfig }
                };
                this.$emit('save-stream-config', newConfig);
                if (this.availableParsers.length === 0) {
                    this.loadParsers();
                }
            }
        },

        async loadParsers() {
            if (this.loadingParsers) return;
            this.loadingParsers = true;
            try {
                const response = await fetch('/api/parsers');
                if (response.ok) {
                    const data = await response.json();
                    this.availableParsers = data.parsers || [];
                    this.autofillListenPatternFromCurrentParser();
                }
            } catch (e) {
                console.error('加载解析器失败:', e);
            } finally {
                this.loadingParsers = false;
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
                    <h3 class="font-semibold text-gray-900 dark:text-white">📡 非流式监听</h3>
                    <span :class="[
                        'px-2 py-0.5 text-xs rounded font-medium',
                        isNetworkMode ? 'bg-purple-100 dark:bg-purple-900/50 text-purple-700 dark:text-purple-300' : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400'
                    ]">
                        {{ isNetworkMode ? '网络拦截' : 'DOM 轮询' }}
                    </span>
                </div>
            </div>

            <!-- 内容 -->
            <div v-show="!collapsed" class="p-4 space-y-4">
                <!-- 模式切换 -->
                <div class="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg">
                    <div>
                        <div class="text-sm font-medium text-gray-700 dark:text-gray-300">网络拦截模式</div>
                        <div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">监听网络请求（如果默认没开的站点不要开）</div>
                    </div>
                    <label class="toggle-label scale-90">
                        <input type="checkbox" :checked="isNetworkMode" @change="toggleNetworkMode" class="sr-only peer">
                        <div class="toggle-bg"></div>
                    </label>
                </div>

                <!-- 网络模式配置 -->
                <div v-if="isNetworkMode" class="space-y-4 border-t dark:border-gray-700 pt-4">
                    <div class="text-sm font-medium text-gray-700 dark:text-gray-300 flex items-center gap-2">
                        <svg class="w-4 h-4 text-purple-500" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M8.288 15.038a5.25 5.25 0 017.424 0M5.106 11.856c3.807-3.808 9.98-3.808 13.788 0M1.924 8.674c5.565-5.565 14.587-5.565 20.152 0M12.53 18.22l-.53.53-.53-.53a.75.75 0 011.06 0z"/>
                        </svg>
                        网络拦截配置
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">URL 匹配模式 <span class="text-red-500">*</span></label>
                            <input type="text" :value="networkConfig.listen_pattern" @input="updateNetworkField('listen_pattern', $event.target.value)" placeholder="例如: StreamGenerate"
                                   class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-purple-400 focus:border-transparent">
                            <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">网络请求 URL 中包含此字符串时拦截</p>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">响应解析器 <span class="text-red-500">*</span></label>
                            <select :value="networkConfig.parser" @change="handleParserChange($event.target.value)" @focus="loadParsers"
                                    class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-purple-400 focus:border-transparent">
                                <option value="" disabled>选择解析器...</option>
                                <option v-for="parser in availableParsers" :key="parser.id" :value="parser.id">{{ parser.name }}</option>
                            </select>
                        </div>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">首次响应超时</label>
                            <div class="flex items-center gap-2">
                                <input type="number" :value="networkConfig.first_response_timeout" @input="updateNetworkField('first_response_timeout', parseFloat($event.target.value) || 300)"
                                       min="1" max="300" step="0.5" class="flex-1 border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-purple-400 focus:border-transparent">
                                <span class="text-sm text-gray-500 dark:text-gray-400">秒</span>
                            </div>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">静默超时</label>
                            <div class="flex items-center gap-2">
                                <input type="number" :value="networkConfig.silence_threshold" @input="updateNetworkField('silence_threshold', parseFloat($event.target.value) || 3)"
                                       min="0.5" max="30" step="0.5" class="flex-1 border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-purple-400 focus:border-transparent">
                                <span class="text-sm text-gray-500 dark:text-gray-400">秒</span>
                            </div>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">轮询间隔</label>
                            <div class="flex items-center gap-2">
                                <input type="number" :value="networkConfig.response_interval" @input="updateNetworkField('response_interval', parseFloat($event.target.value) || 0.5)"
                                       min="0.1" max="5" step="0.1" class="flex-1 border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-purple-400 focus:border-transparent">
                                <span class="text-sm text-gray-500 dark:text-gray-400">秒</span>
                            </div>
                        </div>
                    </div>

                    <!-- 警告 -->
                    <div v-if="!networkConfig.listen_pattern || !networkConfig.parser"
                         class="bg-yellow-50 dark:bg-yellow-900/30 border border-yellow-200 dark:border-yellow-800 rounded-lg p-3">
                        <div class="flex items-start gap-2">
                            <svg class="w-5 h-5 text-yellow-500 flex-shrink-0 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
                                <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/>
                            </svg>
                            <div class="text-sm text-yellow-700 dark:text-yellow-300">
                                <span class="font-medium">配置不完整</span>
                                <p class="mt-0.5 text-xs">请填写 URL 匹配模式和选择解析器，否则将自动回退到 DOM 模式</p>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- DOM 模式说明 -->
                <div v-else class="text-sm text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3">
                    <div class="flex items-start gap-2">
                        <svg class="w-5 h-5 text-gray-400 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
                        </svg>
                        <div>
                            <span class="font-medium text-gray-700 dark:text-gray-300">DOM 轮询模式</span>
                            <p class="mt-0.5">通过监控页面元素变化提取响应内容，兼容性最佳</p>
                        </div>
                    </div>
                </div>

                <!-- 通用配置 -->
                <div class="border-t dark:border-gray-700 pt-4">
                    <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">通用配置</div>
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">全局硬超时</label>
                            <div class="flex items-center gap-2">
                                <input type="number" :value="streamConfig.hard_timeout" @input="updateField('hard_timeout', parseInt($event.target.value) || 300)"
                                       min="10" max="600" step="10" class="flex-1 border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                <span class="text-sm text-gray-500 dark:text-gray-400">秒</span>
                            </div>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">静默超时 (DOM)</label>
                            <div class="flex items-center gap-2">
                                <input type="number" :value="streamConfig.silence_threshold" @input="updateField('silence_threshold', parseFloat($event.target.value) || 2.5)"
                                       min="0.5" max="30" step="0.5" class="flex-1 border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                <span class="text-sm text-gray-500 dark:text-gray-400">秒</span>
                            </div>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">初始等待</label>
                            <div class="flex items-center gap-2">
                                <input type="number" :value="streamConfig.initial_wait" @input="updateField('initial_wait', parseFloat($event.target.value) || 30)"
                                       min="5" max="120" step="5" class="flex-1 border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                <span class="text-sm text-gray-500 dark:text-gray-400">秒</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `
};

