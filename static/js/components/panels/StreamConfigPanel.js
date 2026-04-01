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
            guideExpanded: false,
            networkStepsExpanded: false,
            attachmentSensitivityOptions: [
                {
                    value: 'low',
                    label: '低',
                    description: '只认更强的发送信号，适合按钮状态经常乱跳的站点。'
                },
                {
                    value: 'medium',
                    label: '中',
                    description: '平衡等待时间和识别速度，适合作为大多数站点的默认值。'
                },
                {
                    value: 'high',
                    label: '高',
                    description: '更早接受附件发送成功信号，适合文件或图片上传后页面反馈较慢的站点。'
                }
            ],
            defaultNetworkConfig: {
                listen_pattern: '',
                parser: '',
                silence_threshold: 3.0,
                response_interval: 0.5
            },
            defaultSendConfirmationConfig: {
                attachment_sensitivity: 'medium'
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
        },

        sendConfirmationConfig() {
            return {
                ...this.defaultSendConfirmationConfig,
                ...(this.streamConfig.send_confirmation || {})
            };
        },

        attachmentSensitivityMeta() {
            return this.attachmentSensitivityOptions.find(
                option => option.value === this.sendConfirmationConfig.attachment_sensitivity
            ) || this.attachmentSensitivityOptions[1];
        },

        selectedParserMeta() {
            return this.findParserMeta(this.networkConfig.parser);
        },

        preferredPattern() {
            const parserId = String(this.networkConfig.parser || '').trim();
            if (!parserId) {
                return '';
            }
            return this.getPreferredListenPattern(parserId);
        },

        networkChecklist() {
            return [
                {
                    label: 'listen_pattern',
                    ready: String(this.networkConfig.listen_pattern || '').trim().length > 0
                },
                {
                    label: 'parser',
                    ready: String(this.networkConfig.parser || '').trim().length > 0
                },
                {
                    label: '超时参数',
                    ready: Number(this.streamConfig.hard_timeout) > 0 && Number(this.networkConfig.silence_threshold) > 0
                }
            ];
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

        updateSendConfirmationField(field, value) {
            const send_confirmation = {
                ...this.sendConfirmationConfig,
                [field]: value
            };
            const newConfig = { ...this.streamConfig, send_confirmation };
            this.$emit('save-stream-config', newConfig);
        },

        openInNewTab(url) {
            const target = String(url || '').trim();
            if (!target) {
                return;
            }
            window.open(target, '_blank', 'noopener,noreferrer');
        },

        openTutorial(anchor = 'non-stream-listener-basics') {
            this.openInNewTab('/static/tutorial.html#' + encodeURIComponent(anchor));
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

        usePreferredPattern() {
            if (!this.preferredPattern) {
                return;
            }
            this.updateNetworkField('listen_pattern', this.preferredPattern);
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
                    <h3 class="font-semibold text-gray-900 dark:text-white">📡 网络监听模式</h3>
                    <span v-if="isNetworkMode" class="text-xs font-medium px-2 py-0.5 rounded bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400">已启用</span>
                    <span v-else class="text-xs font-medium px-2 py-0.5 rounded bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400">DOM 流式</span>
                </div>
                <div class="flex items-center" @click.stop>
                    <label class="toggle-label scale-90 !m-0">
                        <input type="checkbox" :checked="isNetworkMode" @change="toggleNetworkMode" class="sr-only peer">
                        <div class="toggle-bg"></div>
                    </label>
                </div>
            </div>

            <!-- 内容 -->
            <div v-show="!collapsed" class="p-4 space-y-4">
                <div v-if="!guideExpanded">
                    <button @click="guideExpanded = true" type="button" class="dashboard-guide-toggle dashboard-guide-toggle--violet">
                        <span>非流式引导</span>
                        <span v-html="$icons.chevronDown"></span>
                    </button>
                </div>

                <div v-else class="dashboard-guide-card dashboard-guide-card--violet">
                    <div class="flex items-center justify-between gap-3">
                        <span class="dashboard-guide-badge">先判断场景，再动开关</span>
                        <button @click="guideExpanded = false" type="button" class="dashboard-guide-toggle dashboard-guide-toggle--violet">
                            <span>收起</span>
                            <span v-html="$icons.chevronUp"></span>
                        </button>
                    </div>
                    <div class="mt-3">
                        <div class="text-base font-semibold text-slate-900 dark:text-slate-50">
                            DOM 模式（流式）与 网络拦截（非流式）
                        </div>
                        <p class="mt-1.5 text-sm leading-6 text-slate-600 dark:text-slate-300">
                            网络拦截是<strong>非流式</strong>获取底层请求，而 DOM 监听是<strong>流式</strong>读取页面展示内容。如果遇到包含复杂 JSON 结构或 LaTeX 数学公式的场景，DOM 提取会比较困难，此时请使用网络拦截。如果站点默认未开启该模式，说明尚未为其适配好网络解析器。
                        </p>
                    </div>

                    <div class="dashboard-checklist">
                        <div v-for="item in networkChecklist"
                             :key="item.label"
                             :class="['dashboard-checklist-item', item.ready ? 'is-ready' : 'is-missing']">
                            <span>{{ item.ready ? '✓' : '•' }}</span>
                            <span>{{ item.label }}</span>
                        </div>
                    </div>

                    <div class="dashboard-guide-actions">
                        <button @click="openTutorial('non-stream-listener-basics')" class="dashboard-guide-btn">
                            <span v-html="$icons.arrowTopRightOnSquare"></span>
                            dom和非流式区别
                        </button>
                        <button @click="openTutorial('non-stream-parser-guide')" class="dashboard-guide-btn dashboard-guide-btn--secondary">
                            <span v-html="$icons.folderOpen"></span>
                            从零配置一个解析器教程
                        </button>
                    </div>
                </div>

                <!-- 网络模式配置 -->
                <div v-if="isNetworkMode" class="space-y-4 border-t dark:border-gray-700 pt-4">
                    <div>
                        <button v-if="!networkStepsExpanded"
                                @click="networkStepsExpanded = true"
                                type="button"
                                class="dashboard-guide-toggle dashboard-guide-toggle--violet">
                            <span>配置步骤</span>
                            <span v-html="$icons.chevronDown"></span>
                        </button>
                        <div v-else class="space-y-3">
                            <div class="flex items-center justify-between gap-3">
                                <div class="text-xs font-medium uppercase tracking-[0.18em] text-slate-400 dark:text-slate-500">
                                    三步看完再填
                                </div>
                                <button @click="networkStepsExpanded = false"
                                        type="button"
                                        class="dashboard-guide-toggle dashboard-guide-toggle--violet">
                                    <span>收起</span>
                                    <span v-html="$icons.chevronUp"></span>
                                </button>
                            </div>
                            <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                                <div class="dashboard-mini-card">
                                    <div class="dashboard-mini-card-title">第 1 步：锁定请求关键词</div>
                                    <div class="dashboard-mini-card-copy">先找目标请求 URL 里最稳定的一小段路径，填进 <code>listen_pattern</code>。</div>
                                </div>
                                <div class="dashboard-mini-card">
                                    <div class="dashboard-mini-card-title">第 2 步：选对解析器</div>
                                    <div class="dashboard-mini-card-copy">有内置解析器就直接选；没有时先按教程导出请求，让 AI 帮你写 parser。</div>
                                </div>
                                <div class="dashboard-mini-card">
                                    <div class="dashboard-mini-card-title">第 3 步：补超时</div>
                                    <div class="dashboard-mini-card-copy">非流式站点一般只需要调全局硬超时和静默超时，等慢站点时把整体等待时间拉高就行。</div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="text-sm font-medium text-gray-700 dark:text-gray-300 flex items-center gap-2">
                        <svg class="w-4 h-4 text-purple-500" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M8.288 15.038a5.25 5.25 0 017.424 0M5.106 11.856c3.807-3.808 9.98-3.808 13.788 0M1.924 8.674c5.565-5.565 14.587-5.565 20.152 0M12.53 18.22l-.53.53-.53-.53a.75.75 0 011.06 0z"/>
                        </svg>
                        网络拦截配置
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <div class="flex items-center justify-between gap-3 mb-1">
                                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">URL 匹配模式 <span class="text-red-500">*</span></label>
                                <button v-if="preferredPattern"
                                        @click="usePreferredPattern"
                                        type="button"
                                        class="text-xs font-medium text-purple-600 dark:text-purple-300 hover:underline">
                                    一键填入推荐值
                                </button>
                            </div>
                            <input type="text"
                                   :value="networkConfig.listen_pattern"
                                   @input="updateNetworkField('listen_pattern', $event.target.value)"
                                   placeholder="例如：GenerateContent"
                                   class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-purple-400 focus:border-transparent">
                            <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                                只要 URL 里包含这段字符串，监听器就会尝试拦截。先写窄一点，调通后再看要不要放宽。
                            </p>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">响应解析器 <span class="text-red-500">*</span></label>
                            <select :value="networkConfig.parser"
                                    @change="handleParserChange($event.target.value)"
                                    @focus="loadParsers"
                                    class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-purple-400 focus:border-transparent">
                                <option value="" disabled>{{ loadingParsers ? '加载解析器中...' : '选择解析器...' }}</option>
                                <option v-for="parser in availableParsers" :key="parser.id" :value="parser.id">{{ parser.name }}</option>
                            </select>
                            <p v-if="selectedParserMeta && Array.isArray(selectedParserMeta.patterns) && selectedParserMeta.patterns.length"
                               class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                                这个解析器常见监听关键词：<code>{{ preferredPattern }}</code>
                            </p>
                        </div>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
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

                    <div v-if="!networkConfig.listen_pattern || !networkConfig.parser"
                         class="bg-yellow-50 dark:bg-yellow-900/30 border border-yellow-200 dark:border-yellow-800 rounded-xl p-3">
                        <div class="flex items-start gap-2">
                            <svg class="w-5 h-5 text-yellow-500 flex-shrink-0 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
                                <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/>
                            </svg>
                            <div class="text-sm text-yellow-700 dark:text-yellow-300">
                                <span class="font-medium">还差关键字段</span>
                                <p class="mt-0.5 text-xs leading-5">
                                    先把 <code>listen_pattern</code> 和 <code>parser</code> 补齐，再开始调超时参数。当前直接测试，基本只会得到空结果或回退。
                                </p>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- DOM 模式说明 -->
                <div v-else class="dashboard-mini-card">
                    <div class="flex items-start justify-between gap-4">
                        <div>
                            <div class="dashboard-mini-card-title">DOM 轮询模式</div>
                            <div class="dashboard-mini-card-copy">
                                这个模式会盯着页面元素变化来判断回复是否结束。兼容性最好，第一次适配新站点时也更容易跑通。
                            </div>
                        </div>
                        <button @click="openTutorial('response-detection')"
                                type="button"
                                class="text-xs font-medium text-blue-600 dark:text-blue-300 hover:underline shrink-0">
                            打开章节
                        </button>
                    </div>
                </div>

                <!-- 通用配置 -->
                <div class="border-t dark:border-gray-700 pt-4">
                    <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">通用配置</div>
                    <div class="grid grid-cols-1 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">全局硬超时</label>
                            <div class="flex items-center gap-2">
                                <input type="number" :value="streamConfig.hard_timeout" @input="updateField('hard_timeout', parseInt($event.target.value) || 300)"
                                       min="10" max="600" step="10" class="flex-1 border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                <span class="text-sm text-gray-500 dark:text-gray-400">秒</span>
                            </div>
                            <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                                非流式监听里，这就是一次完整对话允许等待的最长时间。
                            </p>
                        </div>
                    </div>

                    <div class="mt-4 rounded-xl border border-blue-200/80 dark:border-blue-800/70 bg-blue-50/70 dark:bg-blue-900/20 p-4">
                        <div class="flex items-start justify-between gap-3">
                            <div>
                                <div class="text-sm font-medium text-gray-800 dark:text-gray-100">附件发送判定</div>
                                <p class="mt-1 text-xs leading-5 text-gray-600 dark:text-gray-300">
                                    这里会同时作用于文件粘贴和图片粘贴。点击发送后，系统会先观察页面信号，再决定这次发送是否已经成功。
                                </p>
                            </div>
                            <span class="px-2 py-0.5 text-xs rounded-full bg-white/80 dark:bg-gray-800/80 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-700">
                                当前：{{ attachmentSensitivityMeta.label }}
                            </span>
                        </div>

                        <div class="mt-3 grid grid-cols-1 md:grid-cols-3 gap-3 items-start">
                            <div>
                                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">敏感度</label>
                                <select :value="sendConfirmationConfig.attachment_sensitivity"
                                        @change="updateSendConfirmationField('attachment_sensitivity', $event.target.value)"
                                        class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                    <option v-for="option in attachmentSensitivityOptions"
                                            :key="option.value"
                                            :value="option.value">
                                        {{ option.label }}
                                    </option>
                                </select>
                            </div>
                            <div class="md:col-span-2 text-xs leading-6 text-gray-600 dark:text-gray-300 bg-white/70 dark:bg-gray-900/40 rounded-lg border border-blue-100 dark:border-blue-900/60 px-3 py-2">
                                {{ attachmentSensitivityMeta.description }}
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `
};
