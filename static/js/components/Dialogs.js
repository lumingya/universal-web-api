// ==================== 所有弹窗组件 ====================

// -------------------- JSON 预览弹窗 --------------------
window.JsonPreviewDialog = {
    name: 'JsonPreviewDialog',
    props: {
        show: { type: Boolean, default: false },
        jsonData: { type: Object, default: () => ({}) },
        title: { type: String, default: '配置 JSON' }
    },
    emits: ['close', 'copy', 'save'],
    data() {
        return {
            draft: ''
        };
    },
    watch: {
        show: {
            handler(value) {
                if (value) {
                    this.draft = JSON.stringify(this.jsonData || {}, null, 2);
                }
            },
            immediate: true
        },
        jsonData: {
            handler() {
                if (this.show) {
                    this.draft = JSON.stringify(this.jsonData || {}, null, 2);
                }
            },
            deep: true
        }
    },
    template: `
        <div v-if="show"
             class="fixed inset-0 bg-black/50 flex items-center justify-center z-40"
             @click.self="$emit('close')">
            <div class="bg-white dark:bg-gray-800 rounded-lg p-6 w-[96vw] max-w-[1600px] h-[90vh] flex flex-col shadow-2xl">
                <div class="flex justify-between items-center mb-4">
                    <h3 class="font-semibold dark:text-white">{{ title }}</h3>
                    <button @click="$emit('close')" 
                            class="text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300">
                        <span v-html="$icons.xMark"></span>
                    </button>
                </div>
                <textarea v-model="draft"
                          spellcheck="false"
                          class="flex-1 min-h-0 overflow-auto bg-gray-50 dark:bg-gray-900 p-4 rounded text-sm font-mono border dark:border-gray-700 dark:text-gray-300 resize-none focus:outline-none focus:ring-2 focus:ring-blue-400"></textarea>
                <div class="mt-4 flex justify-end gap-2">
                    <button @click="$emit('copy', draft)" 
                            class="border dark:border-gray-700 rounded hover:bg-gray-100 dark:hover:bg-gray-700 dark:text-white transition-colors px-2 py-0.5 text-sm">
                        复制到剪贴板
                    </button>
                    <button @click="$emit('save', draft)" 
                            class="border rounded transition-colors bg-blue-500 text-white hover:bg-blue-600 border-blue-500 px-3 py-1 text-sm">
                        保存修改
                    </button>
                </div>
            </div>
        </div>
    `
};

// -------------------- Token 配置弹窗 --------------------
window.TokenDialog = {
    name: 'TokenDialog',
    props: {
        show: { type: Boolean, default: false },
        modelValue: { type: String, default: '' }
    },
    emits: ['close', 'save', 'update:modelValue'],
    computed: {
        tempToken: {
            get() { return this.modelValue; },
            set(val) { this.$emit('update:modelValue', val); }
        }
    },
    template: `
        <div v-if="show"
             class="fixed inset-0 bg-black/50 flex items-center justify-center z-40"
             @click.self="$emit('close')">
            <div class="bg-white dark:bg-gray-800 rounded-lg p-6 w-96">
                <h3 class="font-semibold dark:text-white mb-4">配置认证 Token</h3>
                <div class="mb-4">
                    <label class="text-sm text-gray-600 dark:text-gray-400 mb-2 block">
                        Bearer Token（留空则清除）
                    </label>
                    <input v-model="tempToken"
                           type="password"
                           placeholder="your-secret-token"
                           class="border dark:border-gray-700 px-2 py-1 rounded focus:outline-none focus:border-blue-400 w-full bg-white dark:bg-gray-700 dark:text-white">
                </div>
                <div class="text-xs text-gray-500 dark:text-gray-400 mb-4">
                    Token 将保存在浏览器本地存储中
                </div>
                <div class="flex justify-end gap-2">
                    <button @click="$emit('close')" 
                            class="border dark:border-gray-700 rounded hover:bg-gray-100 dark:hover:bg-gray-700 dark:text-white transition-colors px-2 py-0.5 text-sm">
                        取消
                    </button>
                    <button @click="$emit('save')" 
                            class="border rounded transition-colors bg-blue-500 text-white hover:bg-blue-600 border-blue-500 px-2 py-0.5 text-sm">
                        保存
                    </button>
                </div>
            </div>
        </div>
    `
};

// -------------------- 步骤模板弹窗 --------------------
window.StepTemplatesDialog = {
    name: 'StepTemplatesDialog',
    props: {
        show: { type: Boolean, default: false }
    },
    emits: ['close', 'apply'],
    template: `
        <div v-if="show"
             class="fixed inset-0 bg-black/50 flex items-center justify-center z-40"
             @click.self="$emit('close')">
            <div class="bg-white dark:bg-gray-800 rounded-lg p-6 w-[500px]">
                <h3 class="font-semibold dark:text-white mb-4">工作流模板</h3>
                <div class="space-y-2 mb-4">
                    <button @click="$emit('apply', 'default')"
                            class="w-full text-left p-3 border dark:border-gray-700 rounded hover:border-blue-400 dark:hover:border-blue-500 transition-colors">
                        <div class="font-semibold text-sm dark:text-white">标准对话流程</div>
                        <div class="text-xs text-gray-500 dark:text-gray-400 mt-1">
                            点击新建 → 填入 → 点击发送 → 等待 → 流式监听
                        </div>
                    </button>
                    <button @click="$emit('apply', 'simple')"
                            class="w-full text-left p-3 border dark:border-gray-700 rounded hover:border-blue-400 dark:hover:border-blue-500 transition-colors">
                        <div class="font-semibold text-sm dark:text-white">简化流程</div>
                        <div class="text-xs text-gray-500 dark:text-gray-400 mt-1">
                            填入 → 回车 → 流式监听
                        </div>
                    </button>
                </div>
                <div class="flex justify-end">
                    <button @click="$emit('close')" 
                            class="border dark:border-gray-700 rounded hover:bg-gray-100 dark:hover:bg-gray-700 dark:text-white transition-colors px-2 py-0.5 text-sm">
                        关闭
                    </button>
                </div>
            </div>
        </div>
    `
};

// -------------------- 选择器测试弹窗 --------------------
window.TestDialog = {
    name: 'TestDialog',
    props: {
        show: { type: Boolean, default: false },
        selector: { type: String, default: '' },
        selectorKey: { type: String, default: '' },
        timeoutValue: { type: Number, default: 2 },
        highlightEnabled: { type: Boolean, default: false },
        result: { type: Object, default: null },
        testing: { type: Boolean, default: false }
    },
    emits: ['close', 'test', 'apply-selector'],
    data() {
        return {
            selectorInput: '',
            timeout: 3,
            highlight: false,
            copyFeedback: ''
        };
    },
    watch: {
        show: {
            handler(value) {
                if (value) {
                    this.syncFromProps();
                }
            },
            immediate: true
        },
        selector(value) {
            if (this.show) {
                this.selectorInput = value || '';
            }
        },
        timeoutValue(value) {
            if (this.show && Number.isFinite(value)) {
                this.timeout = value;
            }
        },
        highlightEnabled(value) {
            if (this.show) {
                this.highlight = !!value;
            }
        }
    },
    computed: {
        diagnosis() {
            return this.result && this.result.diagnosis ? this.result.diagnosis : null;
        },
        topCandidates() {
            return Array.isArray(this.result && this.result.top_candidates) ? this.result.top_candidates : [];
        },
        resultElements() {
            return Array.isArray(this.result && this.result.elements) ? this.result.elements : [];
        },
        selectorLabel() {
            const key = String(this.selectorKey || '').trim();
            const labelMap = {
                input_box: '输入框',
                send_btn: '发送按钮',
                result_container: '回复容器',
                new_chat_btn: '新建对话按钮',
                message_wrapper: '消息外层',
                generating_indicator: '生成中指示器',
                upload_btn: '上传按钮',
                file_input: '原生文件输入框',
                drop_zone: '拖拽区域'
            };
            return labelMap[key] || '当前字段';
        },
        canApplyCurrent() {
            return !!String(this.selectorInput || '').trim() && !!String(this.selectorKey || '').trim();
        }
    },
    methods: {
        syncFromProps() {
            this.selectorInput = this.selector || '';
            this.timeout = Number.isFinite(this.timeoutValue) ? this.timeoutValue : 3;
            this.highlight = !!this.highlightEnabled;
            this.copyFeedback = '';
        },
        async copyText(value) {
            const text = String(value || '').trim();
            if (!text) return;
            try {
                await navigator.clipboard.writeText(text);
                this.copyFeedback = '已复制到剪贴板';
                window.setTimeout(() => {
                    if (this.copyFeedback === '已复制到剪贴板') {
                        this.copyFeedback = '';
                    }
                }, 1600);
            } catch (error) {
                this.copyFeedback = '复制失败';
                window.setTimeout(() => {
                    if (this.copyFeedback === '复制失败') {
                        this.copyFeedback = '';
                    }
                }, 1600);
            }
        },
        useCandidate(selector) {
            this.selectorInput = String(selector || '').trim();
        },
        retestCandidate(selector) {
            this.selectorInput = String(selector || '').trim();
            if (!this.selectorInput) return;
            this.$emit('test', {
                selector: this.selectorInput,
                timeout: this.timeout,
                highlight: this.highlight
            });
        },
        applyCandidate(selector, rerun = false) {
            const value = String(selector || '').trim();
            if (!value) return;
            this.selectorInput = value;
            this.$emit('apply-selector', {
                selector: value,
                rerun
            });
        },
        applyCurrentSelector(rerun = false) {
            if (!this.canApplyCurrent) return;
            this.$emit('apply-selector', {
                selector: this.selectorInput,
                rerun
            });
        },
        resultToneClass() {
            const status = this.diagnosis && this.diagnosis.status;
            if (status === 'unique') {
                return 'bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800';
            }
            if (status === 'multiple') {
                return 'bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-800';
            }
            return 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800';
        },
        uniqueBadgeClass(candidate) {
            return candidate && candidate.unique
                ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
                : 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300';
        }
    },
    template: `
        <div v-if="show"
             class="fixed inset-0 bg-black/50 flex items-center justify-center z-40"
             @click.self="$emit('close')">
            <div class="bg-white dark:bg-gray-800 rounded-lg p-6 w-[92vw] max-w-[980px] max-h-[88vh] overflow-hidden flex flex-col shadow-2xl">
                <div class="flex justify-between items-start gap-4 mb-4">
                    <div>
                        <h3 class="font-semibold dark:text-white text-lg">选择器测试工作台</h3>
                        <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">
                            不只是看“找到了没”，还会给出候选写法、风险提示和回填入口。
                        </p>
                    </div>
                    <button @click="$emit('close')"
                            class="text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300">
                        <span v-html="$icons.xMark"></span>
                    </button>
                </div>

                <div class="grid grid-cols-1 lg:grid-cols-[minmax(0,1.15fr)_minmax(320px,0.85fr)] gap-4 min-h-0 flex-1">
                    <div class="min-h-0 overflow-auto pr-1 space-y-4">
                        <div class="rounded-xl border border-slate-200 dark:border-slate-700 p-4 bg-slate-50/70 dark:bg-slate-900/30">
                            <div class="flex flex-wrap items-center gap-2 mb-3">
                                <span v-if="selectorKey"
                                      class="inline-flex items-center rounded-full bg-blue-50 dark:bg-blue-900/30 border border-blue-200 dark:border-blue-800 px-2.5 py-1 text-xs font-medium text-blue-700 dark:text-blue-300">
                                    当前字段：{{ selectorKey }} · {{ selectorLabel }}
                                </span>
                                <span v-if="copyFeedback"
                                      class="inline-flex items-center rounded-full bg-emerald-50 dark:bg-emerald-900/30 border border-emerald-200 dark:border-emerald-800 px-2.5 py-1 text-xs font-medium text-emerald-700 dark:text-emerald-300">
                                    {{ copyFeedback }}
                                </span>
                            </div>

                            <label class="text-sm text-gray-600 dark:text-gray-400 mb-2 block">测试选择器</label>
                            <textarea v-model="selectorInput"
                                      rows="3"
                                      spellcheck="false"
                                      class="border dark:border-gray-700 px-3 py-2 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-full font-mono text-sm bg-white dark:bg-gray-700 dark:text-white resize-none"
                                      placeholder='例如: button[data-testid="send-button"]'></textarea>

                            <div class="mt-3 flex flex-wrap items-center gap-4">
                                <label class="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
                                    <span>超时</span>
                                    <input v-model.number="timeout"
                                           type="number"
                                           min="1"
                                           max="10"
                                           class="border dark:border-gray-700 px-2 py-1 rounded w-20 bg-white dark:bg-gray-700 dark:text-white">
                                    <span class="text-xs text-gray-400">秒</span>
                                </label>
                                <label class="flex items-center text-sm cursor-pointer">
                                    <input type="checkbox" v-model="highlight" class="mr-2">
                                    <span class="dark:text-gray-300">🎨 高亮浏览器中的命中元素</span>
                                </label>
                            </div>

                            <div class="mt-4 flex flex-wrap gap-2">
                                <button @click="$emit('test', { selector: selectorInput, timeout, highlight })"
                                        :disabled="!selectorInput.trim() || testing"
                                        class="border rounded-lg transition-colors bg-blue-500 text-white hover:bg-blue-600 border-blue-500 px-3 py-1.5 text-sm font-medium"
                                        :class="{'opacity-50 cursor-not-allowed': !selectorInput.trim() || testing}">
                                    {{ testing ? '测试中...' : '开始测试' }}
                                </button>
                                <button @click="applyCurrentSelector(false)"
                                        :disabled="!canApplyCurrent"
                                        class="border dark:border-gray-700 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 dark:text-white transition-colors px-3 py-1.5 text-sm"
                                        :class="{'opacity-50 cursor-not-allowed': !canApplyCurrent}">
                                    回填当前字段
                                </button>
                                <button @click="applyCurrentSelector(true)"
                                        :disabled="!canApplyCurrent || testing"
                                        class="border dark:border-gray-700 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 dark:text-white transition-colors px-3 py-1.5 text-sm"
                                        :class="{'opacity-50 cursor-not-allowed': !canApplyCurrent || testing}">
                                    回填并重测
                                </button>
                                <button @click="copyText(selectorInput)"
                                        :disabled="!selectorInput.trim()"
                                        class="border dark:border-gray-700 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 dark:text-white transition-colors px-3 py-1.5 text-sm"
                                        :class="{'opacity-50 cursor-not-allowed': !selectorInput.trim()}">
                                    复制当前写法
                                </button>
                            </div>
                        </div>

                        <div v-if="result"
                             class="rounded-xl border p-4"
                             :class="resultToneClass()">
                            <div class="flex flex-wrap items-center gap-2 mb-2">
                                <span class="text-sm font-semibold dark:text-white">
                                    {{ result.success ? (result.count === 1 ? '✅ 唯一命中' : '⚠️ 命中多个元素') : '❌ 没有命中元素' }}
                                </span>
                                <span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs bg-white/70 dark:bg-slate-800/70 text-slate-700 dark:text-slate-200">
                                    匹配数量：{{ result.count }}
                                </span>
                                <span v-if="result.locator_used"
                                      class="inline-flex items-center rounded-full px-2 py-0.5 text-xs bg-white/70 dark:bg-slate-800/70 text-slate-700 dark:text-slate-200">
                                    实际查询：{{ result.locator_used }}
                                </span>
                            </div>

                            <p v-if="diagnosis" class="text-sm leading-6 text-slate-700 dark:text-slate-200">
                                {{ diagnosis.summary }}
                            </p>
                            <p v-else-if="result.message" class="text-sm leading-6 text-slate-700 dark:text-slate-200">
                                {{ result.message }}
                            </p>

                            <div v-if="diagnosis && diagnosis.warnings && diagnosis.warnings.length" class="mt-3 space-y-2">
                                <div class="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500 dark:text-slate-400">风险提示</div>
                                <div v-for="(warning, idx) in diagnosis.warnings" :key="'warn-' + idx"
                                     class="rounded-lg bg-white/70 dark:bg-slate-800/70 border border-white/80 dark:border-slate-700 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
                                    {{ warning }}
                                </div>
                            </div>

                            <div v-if="diagnosis && diagnosis.tips && diagnosis.tips.length" class="mt-3 space-y-2">
                                <div class="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500 dark:text-slate-400">下一步建议</div>
                                <div v-for="(tip, idx) in diagnosis.tips" :key="'tip-' + idx"
                                     class="rounded-lg bg-white/70 dark:bg-slate-800/70 border border-white/80 dark:border-slate-700 px-3 py-2 text-sm text-slate-700 dark:text-slate-200">
                                    {{ tip }}
                                </div>
                            </div>
                        </div>

                        <div v-if="topCandidates.length"
                             class="rounded-xl border border-slate-200 dark:border-slate-700 p-4 bg-white dark:bg-slate-900/20">
                            <div class="flex items-center justify-between gap-3 mb-3">
                                <div>
                                    <div class="text-sm font-semibold text-slate-900 dark:text-white">推荐候选选择器</div>
                                    <div class="text-xs text-slate-500 dark:text-slate-400">优先把更稳、更容易唯一命中的写法拿去复测。</div>
                                </div>
                            </div>

                            <div class="space-y-3">
                                <div v-for="(candidate, idx) in topCandidates"
                                     :key="candidate.selector + '-' + idx"
                                     class="rounded-xl border border-slate-200 dark:border-slate-700 p-3 bg-slate-50/70 dark:bg-slate-800/60">
                                    <div class="flex flex-wrap items-center gap-2 mb-2">
                                        <span class="text-xs font-semibold text-slate-400 dark:text-slate-500">候选 {{ idx + 1 }}</span>
                                        <span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
                                              :class="uniqueBadgeClass(candidate)">
                                            {{ candidate.unique ? '唯一命中' : '匹配 ' + candidate.count + ' 个' }}
                                        </span>
                                        <span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300">
                                            {{ candidate.reason }}
                                        </span>
                                    </div>
                                    <code class="block whitespace-pre-wrap break-all rounded-lg bg-slate-950 px-3 py-2 text-xs leading-6 text-slate-100">{{ candidate.selector }}</code>
                                    <div class="mt-3 flex flex-wrap gap-2">
                                        <button @click="useCandidate(candidate.selector)"
                                                class="border dark:border-gray-700 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 dark:text-white transition-colors px-3 py-1 text-xs">
                                            代入输入框
                                        </button>
                                        <button @click="retestCandidate(candidate.selector)"
                                                :disabled="testing"
                                                class="border dark:border-gray-700 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 dark:text-white transition-colors px-3 py-1 text-xs"
                                                :class="{'opacity-50 cursor-not-allowed': testing}">
                                            代入并重测
                                        </button>
                                        <button v-if="selectorKey"
                                                @click="applyCandidate(candidate.selector, false)"
                                                class="border dark:border-gray-700 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 dark:text-white transition-colors px-3 py-1 text-xs">
                                            回填字段
                                        </button>
                                        <button @click="copyText(candidate.selector)"
                                                class="border dark:border-gray-700 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 dark:text-white transition-colors px-3 py-1 text-xs">
                                            复制
                                        </button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="min-h-0 overflow-auto pl-1 space-y-4">
                        <div class="rounded-xl border border-slate-200 dark:border-slate-700 p-4 bg-white dark:bg-slate-900/20">
                            <div class="text-sm font-semibold text-slate-900 dark:text-white mb-2">命中详情</div>
                            <div class="text-xs text-slate-500 dark:text-slate-400 mb-3">
                                {{ result ? '已展开 ' + resultElements.length + ' 个元素详情' + (result.truncated ? '（其余命中已省略）' : '') : '先跑一次测试，这里会显示命中的元素摘要和属性。' }}
                            </div>

                            <div v-if="!result" class="rounded-lg border border-dashed border-slate-300 dark:border-slate-700 px-3 py-6 text-sm text-slate-500 dark:text-slate-400 text-center">
                                暂无测试结果
                            </div>

                            <div v-else-if="!resultElements.length" class="rounded-lg border border-dashed border-slate-300 dark:border-slate-700 px-3 py-6 text-sm text-slate-500 dark:text-slate-400 text-center">
                                这次没有拿到可展示的元素摘要
                            </div>

                            <div v-else class="space-y-3">
                                <div v-for="element in resultElements"
                                     :key="'element-' + element.index"
                                     class="rounded-xl border border-slate-200 dark:border-slate-700 p-3 bg-slate-50/70 dark:bg-slate-800/60">
                                    <div class="flex flex-wrap items-center gap-2 mb-2">
                                        <span class="text-xs font-semibold text-slate-400 dark:text-slate-500">#{{ element.index + 1 }}</span>
                                        <span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-200">
                                            &lt;{{ element.tag || 'unknown' }}&gt;
                                        </span>
                                        <span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs"
                                              :class="element.visible ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300' : 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300'">
                                            {{ element.visible ? '可见' : '不可见/尺寸为 0' }}
                                        </span>
                                        <span v-if="element.rect"
                                              class="inline-flex items-center rounded-full px-2 py-0.5 text-xs bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300">
                                            {{ element.rect.width }}×{{ element.rect.height }}
                                        </span>
                                    </div>

                                    <div v-if="element.text"
                                         class="text-sm leading-6 text-slate-700 dark:text-slate-200 mb-2">
                                        {{ element.text }}
                                    </div>

                                    <div v-if="element.attributes && Object.keys(element.attributes).length"
                                         class="flex flex-wrap gap-2 mb-2">
                                        <span v-for="(value, key) in element.attributes"
                                              :key="key"
                                              class="inline-flex max-w-full items-center rounded-full border border-slate-200 dark:border-slate-700 bg-white/80 dark:bg-slate-900/70 px-2.5 py-1 text-[11px] text-slate-600 dark:text-slate-300">
                                            <strong class="mr-1">{{ key }}</strong>
                                            <span class="truncate">{{ value }}</span>
                                        </span>
                                    </div>

                                    <details v-if="element.html_preview" class="mb-2">
                                        <summary class="cursor-pointer text-xs font-medium text-blue-600 dark:text-blue-300">查看 HTML 摘要</summary>
                                        <pre class="mt-2 whitespace-pre-wrap break-all rounded-lg bg-slate-950 px-3 py-2 text-[11px] leading-5 text-slate-100">{{ element.html_preview }}</pre>
                                    </details>

                                    <div v-if="element.warnings && element.warnings.length" class="space-y-1 mb-2">
                                        <div v-for="(warning, idx) in element.warnings"
                                             :key="'element-warning-' + element.index + '-' + idx"
                                             class="rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
                                            {{ warning }}
                                        </div>
                                    </div>

                                    <div v-if="element.candidate_selectors && element.candidate_selectors.length" class="space-y-2">
                                        <div class="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400 dark:text-slate-500">这个元素的候选</div>
                                        <div v-for="candidate in element.candidate_selectors"
                                             :key="candidate.selector"
                                             class="rounded-lg border border-slate-200 dark:border-slate-700 bg-white/80 dark:bg-slate-900/70 p-2">
                                            <div class="flex flex-wrap items-center gap-2 mb-1">
                                                <span class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
                                                      :class="uniqueBadgeClass(candidate)">
                                                    {{ candidate.unique ? '唯一命中' : '匹配 ' + candidate.count + ' 个' }}
                                                </span>
                                                <span class="text-[11px] text-slate-500 dark:text-slate-400">{{ candidate.reason }}</span>
                                            </div>
                                            <code class="block whitespace-pre-wrap break-all text-[11px] leading-5 text-slate-700 dark:text-slate-100">{{ candidate.selector }}</code>
                                            <div class="mt-2 flex flex-wrap gap-2">
                                                <button @click="useCandidate(candidate.selector)"
                                                        class="border dark:border-gray-700 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 dark:text-white transition-colors px-2.5 py-1 text-[11px]">
                                                    代入
                                                </button>
                                                <button @click="copyText(candidate.selector)"
                                                        class="border dark:border-gray-700 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 dark:text-white transition-colors px-2.5 py-1 text-[11px]">
                                                    复制
                                                </button>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="mt-4 flex justify-end gap-2">
                    <button @click="$emit('close')"
                            class="border dark:border-gray-700 rounded hover:bg-gray-100 dark:hover:bg-gray-700 dark:text-white transition-colors px-3 py-1.5 text-sm">
                        关闭
                    </button>
                    <button @click="$emit('test', { selector: selectorInput, timeout, highlight })"
                            :disabled="!selectorInput.trim() || testing"
                            class="border rounded transition-colors bg-blue-500 text-white hover:bg-blue-600 border-blue-500 px-3 py-1.5 text-sm font-medium"
                            :class="{'opacity-50 cursor-not-allowed': !selectorInput.trim() || testing}">
                        {{ testing ? '测试中...' : '重新测试' }}
                    </button>
                </div>
            </div>
        </div>
    `
};

// -------------------- 导入确认弹窗 --------------------
window.ImportDialog = {
    name: 'ImportDialog',
    props: {
        show: { type: Boolean, default: false },
        fileName: { type: String, default: '' },
        importType: { type: String, default: 'full' },
        suggestedDomain: { type: String, default: '' },
        importedConfig: { type: Object, default: null }
    },
    emits: ['close', 'confirm'],
    data() {
        return {
            mode: 'merge',
            singleDomain: ''
        };
    },
    watch: {
        show(val) {
            if (val) {
                this.mode = 'merge';
                this.singleDomain = this.suggestedDomain || '';
            }
        },
        suggestedDomain(val) {
            if (this.show && !this.singleDomain.trim()) {
                this.singleDomain = val || '';
            }
        }
    },
    computed: {
        singleImportHint() {
            if (this.suggestedDomain) {
                return '已从导入文件推断站点名，直接确认即可，也可以手动修改。';
            }
            return '未识别出站点名时，再手动补充即可。';
        },
        mergeDescription() {
            if (this.importType === 'single') {
                return '保留当前站点里未导入的预设；导入文件中的同名预设会被覆盖。';
            }
            return '只导入文件里的站点；同名站点整站覆盖，未出现在文件中的站点会保留。';
        },
        replaceDescription() {
            if (this.importType === 'single') {
                return '用导入文件完整替换这个站点，当前站点已有预设和设置都会被清掉。';
            }
            return '先清空当前全部站点配置，再写入导入文件中的站点。';
        }
    },
    template: `
        <div v-if="show"
             class="fixed inset-0 bg-black/50 flex items-center justify-center z-40"
             @click.self="$emit('close')">
            <div class="bg-white dark:bg-gray-800 rounded-lg p-6 w-[450px]">
                <h3 class="font-semibold dark:text-white mb-4">导入配置</h3>

                <div class="mb-4 p-3 bg-gray-50 dark:bg-gray-900 rounded border dark:border-gray-700">
                    <div class="text-sm dark:text-gray-300">
                        <span class="text-gray-600 dark:text-gray-400">文件:</span> {{ fileName }}
                    </div>
                    <div class="text-sm dark:text-gray-300 mt-1">
                        <span class="text-gray-600 dark:text-gray-400">类型:</span>
                        {{ importType === 'single' ? '单站点配置' : '全量配置 (' + Object.keys(importedConfig || {}).length + ' 个站点)' }}
                    </div>
                    <div v-if="importType === 'full' && importedConfig" 
                         class="text-xs text-gray-500 dark:text-gray-400 mt-2 max-h-24 overflow-auto">
                        {{ Object.keys(importedConfig).join(', ') }}
                    </div>
                </div>

                <!-- 单站点导入时需要输入域名 -->
                <div v-if="importType === 'single'" class="mb-4">
                    <label class="text-sm text-gray-600 dark:text-gray-400 mb-2 block">站点名</label>
                    <input v-model="singleDomain"
                           placeholder="例如: chat.openai.com"
                           class="border dark:border-gray-700 px-3 py-2 rounded w-full text-sm bg-white dark:bg-gray-700 dark:text-white">
                    <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">{{ singleImportHint }}</p>
                </div>

                <div class="mb-4">
                    <label class="text-sm text-gray-600 dark:text-gray-400 mb-2 block">导入模式</label>
                    <div class="space-y-2">
                        <label class="flex items-center cursor-pointer">
                            <input type="radio" v-model="mode" value="merge" class="mr-2">
                            <span class="dark:text-gray-300">合并导入</span>
                            <span class="text-xs text-gray-500 dark:text-gray-400 ml-2">（{{ mergeDescription }}）</span>
                        </label>
                        <label class="flex items-center cursor-pointer">
                            <input type="radio" v-model="mode" value="replace" class="mr-2">
                            <span class="dark:text-gray-300">完全替换</span>
                            <span class="text-xs text-gray-500 dark:text-gray-400 ml-2">（{{ replaceDescription }}）</span>
                        </label>
                    </div>
                </div>

                <div class="flex justify-end gap-2">
                    <button @click="$emit('close')" 
                            class="border dark:border-gray-700 rounded hover:bg-gray-100 dark:hover:bg-gray-700 dark:text-white transition-colors px-3 py-1 text-sm">
                        取消
                    </button>
                    <button @click="$emit('confirm', { mode, domain: singleDomain })"
                            :disabled="importType === 'single' && !singleDomain.trim()"
                            :class="['border rounded transition-colors px-3 py-1 text-sm',
                                     importType === 'single' && !singleDomain.trim()
                                     ? 'bg-blue-400 cursor-not-allowed opacity-70 text-white border-blue-400'
                                     : 'bg-blue-500 text-white hover:bg-blue-600 border-blue-500']">
                        确认导入
                    </button>
                </div>
            </div>
        </div>
    `
};

// -------------------- 新增/编辑元素定义弹窗 --------------------
window.DefinitionDialog = {
    name: 'DefinitionDialog',
    props: {
        show: { type: Boolean, default: false },
        editIndex: { type: Number, default: null },
        definition: { type: Object, default: () => ({ key: '', description: '', enabled: true }) }
    },
    emits: ['close', 'save'],
    data() {
        return {
            form: { key: '', description: '', enabled: true }
        };
    },
    watch: {
        show(val) {
            if (val) {
                this.form = { ...this.definition };
            }
        }
    },
    computed: {
        isEdit() {
            return this.editIndex !== null;
        }
    },
    template: `
        <div v-if="show"
             class="fixed inset-0 bg-black/50 flex items-center justify-center z-40"
             @click.self="$emit('close')">
            <div class="bg-white dark:bg-gray-800 rounded-lg p-6 w-[500px]">
                <h3 class="font-semibold dark:text-white mb-4">
                    {{ isEdit ? '编辑元素定义' : '新增元素定义' }}
                </h3>

                <div class="space-y-4">
                    <div>
                        <label class="text-sm text-gray-600 dark:text-gray-400 mb-2 block">关键词 (key)</label>
                        <input v-model="form.key"
                               :disabled="isEdit && definition.required"
                               placeholder="例如: temp_chat_btn"
                               class="border dark:border-gray-700 px-3 py-2 rounded w-full font-mono text-sm bg-white dark:bg-gray-700 dark:text-white disabled:opacity-50">
                        <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">
                            用于工作流配置中引用此元素
                        </p>
                    </div>

                    <div>
                        <label class="text-sm text-gray-600 dark:text-gray-400 mb-2 block">描述 (发送给 AI)</label>
                        <textarea v-model="form.description"
                                  placeholder="例如: 临时对话/隐私模式的切换按钮"
                                  rows="3"
                                  class="border dark:border-gray-700 px-3 py-2 rounded w-full text-sm bg-white dark:bg-gray-700 dark:text-white resize-none"></textarea>
                        <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">
                            AI 会根据这个描述在页面中查找对应的元素
                        </p>
                    </div>

                    <div class="flex items-center justify-between">
                        <span class="text-sm dark:text-gray-300">默认启用</span>
                        <label class="toggle-label">
                            <input type="checkbox" v-model="form.enabled" class="sr-only peer">
                            <div class="toggle-bg"></div>
                        </label>
                    </div>
                </div>

                <div class="flex justify-end gap-2 mt-6">
                    <button @click="$emit('close')"
                            class="border dark:border-gray-700 rounded hover:bg-gray-100 dark:hover:bg-gray-700 dark:text-white transition-colors px-4 py-2 text-sm">
                        取消
                    </button>
                    <button @click="$emit('save', form)"
                            class="border rounded transition-colors bg-blue-500 text-white hover:bg-blue-600 border-blue-500 px-4 py-2 text-sm">
                        {{ isEdit ? '保存' : '添加' }}
                    </button>
                </div>
            </div>
        </div>
    `
};
