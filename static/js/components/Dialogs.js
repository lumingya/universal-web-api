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
        result: { type: Object, default: null },
        testing: { type: Boolean, default: false }
    },
    emits: ['close', 'test'],
    data() {
        return {
            selectorInput: '',
            timeout: 3,
            highlight: false
        };
    },
    template: `
        <div v-if="show"
             class="fixed inset-0 bg-black/50 flex items-center justify-center z-40"
             @click.self="$emit('close')">
            <div class="bg-white dark:bg-gray-800 rounded-lg p-6 w-[600px]">
                <h3 class="font-semibold dark:text-white mb-4">选择器测试</h3>
                <div class="mb-4">
                    <label class="text-sm text-gray-600 dark:text-gray-400 mb-2 block">选择器</label>
                    <input v-model="selectorInput"
                           class="border dark:border-gray-700 px-2 py-1 rounded focus:outline-none focus:border-blue-400 w-full font-mono text-sm bg-white dark:bg-gray-700 dark:text-white"
                           placeholder="例如: textarea">
                </div>
                <div class="mb-4">
                    <label class="text-sm text-gray-600 dark:text-gray-400 mb-2 block">超时时间（秒）</label>
                    <input v-model.number="timeout"
                           type="number"
                           min="1"
                           max="10"
                           class="border dark:border-gray-700 px-2 py-1 rounded focus:outline-none focus:border-blue-400 w-24 bg-white dark:bg-gray-700 dark:text-white">
                </div>

                <div class="mb-4">
                    <label class="flex items-center text-sm cursor-pointer">
                        <input type="checkbox" v-model="highlight" class="mr-2">
                        <span class="dark:text-gray-300">🎨 在浏览器中高亮显示</span>
                        <span class="ml-2 text-xs text-gray-500 dark:text-gray-400">（可能触发风控，谨慎使用）</span>
                    </label>
                </div>

                <div v-if="result" class="mb-4 p-3 rounded border"
                     :class="result.success ? 'bg-green-50 dark:bg-green-900/30 border-green-200 dark:border-green-800' : 'bg-red-50 dark:bg-red-900/30 border-red-200 dark:border-red-800'">
                    <div class="font-semibold text-sm mb-2 dark:text-white">
                        {{ result.success ? '✅ 找到' + (result.count > 1 ? ' ' + result.count + ' 个' : '') + '元素' : '❌ 未找到元素' }}
                    </div>
                    <div v-if="result.success" class="text-xs space-y-1 dark:text-gray-300">
                        <div><span class="text-gray-600 dark:text-gray-400">标签:</span> &lt;{{ result.tag }}&gt;</div>
                        <div v-if="result.text">
                            <span class="text-gray-600 dark:text-gray-400">文本:</span> {{ result.text }}
                        </div>
                        <div v-if="result.count > 1" class="mt-2">
                            <span class="text-gray-600 dark:text-gray-400">找到 {{ result.count }} 个元素:</span>
                            <div class="max-h-32 overflow-auto mt-1">
                                <div v-for="(el, idx) in result.elements" :key="idx"
                                     class="text-xs p-1 border-b dark:border-gray-700">
                                    {{ idx + 1 }}. &lt;{{ el.tag }}&gt; {{ el.text ? '- ' + el.text.substring(0, 50) : '' }}
                                </div>
                            </div>
                        </div>
                        <div v-if="result.attributes && Object.keys(result.attributes).length > 0">
                            <span class="text-gray-600 dark:text-gray-400">属性:</span>
                            <pre class="text-xs mt-1 bg-white dark:bg-gray-800 p-2 rounded dark:text-gray-300">{{ JSON.stringify(result.attributes, null, 2) }}</pre>
                        </div>
                    </div>
                    <div v-else class="text-xs text-red-600 dark:text-red-400">
                        {{ result.message }}
                    </div>
                </div>

                <div class="flex justify-end gap-2">
                    <button @click="$emit('close')" 
                            class="border dark:border-gray-700 rounded hover:bg-gray-100 dark:hover:bg-gray-700 dark:text-white transition-colors px-2 py-0.5 text-sm">
                        关闭
                    </button>
                    <button @click="$emit('test', { selector: selectorInput, timeout, highlight })"
                            :disabled="!selectorInput || testing"
                            class="border rounded transition-colors bg-blue-500 text-white hover:bg-blue-600 border-blue-500 px-2 py-0.5 text-sm"
                            :class="{'opacity-50 cursor-not-allowed': !selectorInput || testing}">
                        {{ testing ? '测试中...' : '测试' }}
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
// ==================== 提取器验证弹窗 ====================
window.ExtractorVerifyDialog = {
    name: 'ExtractorVerifyDialog',
    props: {
        show: { type: Boolean, default: false },
        domain: { type: String, default: '' },
        extractorName: { type: String, default: '' }
    },
    emits: ['close', 'verify'],
    data() {
        return {
            extractedText: '',
            expectedText: '',
            result: null,
            isVerifying: false
        };
    },
    watch: {
        show(val) {
            if (val) {
                this.extractedText = '';
                this.expectedText = '';
                this.result = null;
            }
        }
    },
    methods: {
        async handleVerify() {
            if (!this.extractedText.trim() || !this.expectedText.trim()) {
                return;
            }

            this.isVerifying = true;
            this.result = null;

            try {
                const response = await fetch('/api/extractors/verify', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        extracted_text: this.extractedText,
                        expected_text: this.expectedText,
                        threshold: 0.95
                    })
                });

                this.result = await response.json();

                if (this.result.passed) {
                    this.$emit('verify', { domain: this.domain, passed: true });
                }
            } catch (error) {
                this.result = {
                    passed: false,
                    message: '验证请求失败: ' + error.message
                };
            } finally {
                this.isVerifying = false;
            }
        }
    },
    template: `
        <div v-if="show" 
             class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
             @click.self="$emit('close')">
            <div class="bg-white dark:bg-gray-800 rounded-lg shadow-xl w-full max-w-2xl mx-4 max-h-[90vh] flex flex-col">
                <div class="px-4 py-3 border-b dark:border-gray-700 flex justify-between items-center flex-shrink-0">
                    <div>
                        <h3 class="font-semibold text-gray-900 dark:text-white">验证提取器效果</h3>
                        <div class="text-sm text-gray-500 dark:text-gray-400">
                            站点: {{ domain }} | 提取器: {{ extractorName }}
                        </div>
                    </div>
                    <button @click="$emit('close')"
                            class="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200">
                        ✕
                    </button>
                </div>
                
                <div class="p-4 space-y-4 overflow-auto flex-1">
                    <div class="bg-blue-50 dark:bg-blue-900/30 border border-blue-200 dark:border-blue-700 rounded-lg p-3 text-sm">
                        <div class="font-medium text-blue-800 dark:text-blue-200 mb-1">📋 使用说明</div>
                        <ol class="list-decimal list-inside text-blue-700 dark:text-blue-300 space-y-1">
                            <li>在浏览器中向 AI 发送一条消息，等待回复完成</li>
                            <li>手动复制 AI 的完整回复内容到「预期结果」框</li>
                            <li>将脚本提取到的内容粘贴到「提取结果」框</li>
                            <li>点击验证，相似度 ≥95% 即为通过</li>
                        </ol>
                    </div>
                    
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                                提取结果（脚本输出）
                            </label>
                            <textarea v-model="extractedText"
                                      rows="8"
                                      class="w-full border dark:border-gray-600 rounded-md p-2 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-400"
                                      placeholder="粘贴脚本提取到的文本..."></textarea>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                                预期结果（手动复制）
                            </label>
                            <textarea v-model="expectedText"
                                      rows="8"
                                      class="w-full border dark:border-gray-600 rounded-md p-2 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-400"
                                      placeholder="粘贴 AI 原始回复..."></textarea>
                        </div>
                    </div>
                    
                    <!-- 验证结果 -->
                    <div v-if="result" 
                         :class="['p-4 rounded-lg border', 
                                  result.passed 
                                  ? 'bg-green-50 dark:bg-green-900/30 border-green-200 dark:border-green-700' 
                                  : 'bg-red-50 dark:bg-red-900/30 border-red-200 dark:border-red-700']">
                        <div class="flex items-center gap-2">
                            <span class="text-2xl">{{ result.passed ? '✅' : '❌' }}</span>
                            <div>
                                <div :class="['font-semibold', 
                                              result.passed ? 'text-green-800 dark:text-green-200' : 'text-red-800 dark:text-red-200']">
                                    {{ result.passed ? '验证通过！' : '验证未通过' }}
                                </div>
                                <div :class="['text-sm', 
                                              result.passed ? 'text-green-700 dark:text-green-300' : 'text-red-700 dark:text-red-300']">
                                    相似度: {{ (result.similarity * 100).toFixed(1) }}%
                                    <span class="mx-2">|</span>
                                    {{ result.message }}
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="px-4 py-3 border-t dark:border-gray-700 flex justify-end gap-2 flex-shrink-0">
                    <button @click="$emit('close')"
                            class="px-4 py-2 text-sm text-gray-700 dark:text-gray-200 border dark:border-gray-600 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors">
                        关闭
                    </button>
                    <button @click="handleVerify"
                            :disabled="isVerifying || !extractedText.trim() || !expectedText.trim()"
                            :class="['px-4 py-2 text-sm text-white rounded-md transition-colors',
                                     isVerifying || !extractedText.trim() || !expectedText.trim()
                                     ? 'bg-gray-400 cursor-not-allowed'
                                     : 'bg-blue-500 hover:bg-blue-600']">
                        {{ isVerifying ? '验证中...' : '验证' }}
                    </button>
                </div>
            </div>
        </div>
    `
};
