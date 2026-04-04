const WORKFLOW_KEY_PRESETS = [
    { value: 'Enter', label: 'Enter' },
    { value: 'Ctrl+Enter', label: 'Ctrl+Enter' },
    { value: 'Shift+Enter', label: 'Shift+Enter' },
    { value: 'Alt+Enter', label: 'Alt+Enter' },
    { value: 'Escape', label: 'Escape' },
    { value: 'Tab', label: 'Tab' },
    { value: 'Backspace', label: 'Backspace' },
    { value: 'Delete', label: 'Delete' },
    { value: 'ArrowUp', label: 'ArrowUp' },
    { value: 'ArrowDown', label: 'ArrowDown' },
    { value: 'ArrowLeft', label: 'ArrowLeft' },
    { value: 'ArrowRight', label: 'ArrowRight' },
    { value: 'Ctrl+A', label: 'Ctrl+A' },
    { value: 'Ctrl+C', label: 'Ctrl+C' },
    { value: 'Ctrl+V', label: 'Ctrl+V' },
    { value: 'Ctrl+X', label: 'Ctrl+X' },
    { value: 'Ctrl+L', label: 'Ctrl+L' },
];

window.WorkflowPanel = {
    name: 'WorkflowPanel',
    props: {
        workflow: { type: Array, required: true },
        selectors: { type: Object, required: true },
        currentDomain: { type: String, default: null },
        selectedPreset: { type: String, default: '主预设' },
        collapsed: { type: Boolean, default: true }
    },
    emits: ['update:collapsed', 'add-step', 'remove-step', 'move-step', 'action-change', 'show-templates'],
    data() {
        return {
            editorInjecting: false,
            keyPresets: WORKFLOW_KEY_PRESETS,
            expandedJsEditors: {},
            customKeyModes: {}
        };
    },
    methods: {
        toggle() {
            this.$emit('update:collapsed', !this.collapsed);
        },

        async launchVisualEditor() {
            if (this.editorInjecting) return;
            this.editorInjecting = true;
            try {
                const response = await fetch('/api/workflow-editor/inject', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        target_domain: this.currentDomain,
                        preset_name: this.selectedPreset
                    })
                });
                const result = await response.json();
                if (result.success) {
                    alert(result.already_existed
                        ? '编辑器已激活，请切换到浏览器窗口查看。'
                        : '编辑器已注入，请切换到浏览器窗口，使用右下角工具栏编辑工作流。');
                } else {
                    alert('注入失败: ' + (result.message || '未知错误'));
                }
            } catch (e) {
                alert('网络错误: ' + e.message);
            } finally {
                this.editorInjecting = false;
            }
        },

        isJsExpanded(index) {
            return !!this.expandedJsEditors[index];
        },

        toggleJsExpand(index) {
            this.expandedJsEditors = {
                ...this.expandedJsEditors,
                [index]: !this.expandedJsEditors[index]
            };
        },

        applyKeyPreset(index, step, value) {
            if (value === '__custom__') {
                this.customKeyModes = {
                    ...this.customKeyModes,
                    [index]: true
                };
                if (!step.target || this.keyPresets.some(item => item.value === step.target)) {
                    step.target = '';
                }
                return;
            }
            if (value) {
                this.customKeyModes = {
                    ...this.customKeyModes,
                    [index]: false
                };
                step.target = value;
            }
        },

        isCustomKeyPreset(index, step) {
            if (this.customKeyModes[index] === true) return true;
            return this.getKeyPresetValue(index, step) === '__custom__';
        },

        getKeyPresetValue(index, step) {
            if (this.customKeyModes[index] === true) return '__custom__';
            const target = String(step.target || '').trim();
            if (!target) return '';
            return this.keyPresets.some(item => item.value === target) ? target : '__custom__';
        }
    },
    template: `
        <div class="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-sm">
            <div class="px-4 py-3 border-b dark:border-gray-700 flex justify-between items-center cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                 @click="toggle">
                <div class="flex items-center gap-2">
                    <span class="w-4 inline-flex justify-center text-gray-500 dark:text-gray-400" v-html="collapsed ? $icons.chevronDown : $icons.chevronUp"></span>
                    <h3 class="font-semibold text-gray-900 dark:text-white">🔧 工作流</h3>
                    <span class="text-sm text-gray-500 dark:text-gray-400">({{ workflow.length }} 步)</span>
                </div>

                <div class="flex gap-2" @click.stop>
                    <button @click="launchVisualEditor" :disabled="editorInjecting"
                            :class="['px-3 py-1 rounded-md text-sm font-medium transition-colors flex items-center gap-1',
                                     editorInjecting ? 'bg-gray-300 dark:bg-gray-600 text-gray-500 dark:text-gray-400 cursor-wait'
                                     : 'text-purple-700 dark:text-purple-300 border border-purple-400 dark:border-purple-500 hover:bg-purple-50 dark:hover:bg-purple-900/30']">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                            <circle cx="12" cy="12" r="3"/><path d="M12 2v4m0 12v4m10-10h-4M6 12H2m15.364-6.364l-2.828 2.828M9.464 14.536l-2.828 2.828m12.728 0l-2.828-2.828M9.464 9.464L6.636 6.636"/>
                        </svg>
                        {{ editorInjecting ? '注入中...' : '可视化' }}
                    </button>
                    <button @click="$emit('show-templates')"
                            class="px-3 py-1 rounded-md text-sm font-medium transition-colors text-gray-700 dark:text-gray-200 border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-1">
                        <span v-html="$icons.clipboardList"></span> 模板
                    </button>
                    <button @click="$emit('add-step')"
                            class="px-3 py-1 rounded-md text-sm font-medium transition-colors bg-blue-500 text-white hover:bg-blue-600 border border-blue-500 flex items-center gap-1">
                        <span v-html="$icons.plusCircle"></span> 新增步骤
                    </button>
                </div>
            </div>

            <div v-show="!collapsed" class="p-4 space-y-3 max-h-96 overflow-auto">
                <div v-for="(step, index) in workflow" :key="index"
                     class="border dark:border-gray-700 rounded-lg p-3 hover:border-blue-300 dark:hover:border-blue-600 transition-colors bg-gray-50/50 dark:bg-gray-900/30">
                    <div class="flex gap-3 items-start">
                        <div class="flex flex-col items-center gap-0.5 pt-1">
                            <span class="text-xs font-bold text-gray-600 dark:text-gray-300 w-6 h-6 flex items-center justify-center bg-gray-200 dark:bg-gray-700 rounded-full">{{ index + 1 }}</span>
                            <div class="flex flex-col mt-1">
                                <button @click="$emit('move-step', index, -1)" :disabled="index === 0"
                                        :class="['p-1 rounded-md transition-all duration-150', index === 0 ? 'text-gray-300 dark:text-gray-600 cursor-not-allowed' : 'text-gray-600 dark:text-gray-300 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-900/40 active:scale-95']">
                                    <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5"/></svg>
                                </button>
                                <button @click="$emit('move-step', index, 1)" :disabled="index === workflow.length - 1"
                                        :class="['p-1 rounded-md transition-all duration-150', index === workflow.length - 1 ? 'text-gray-300 dark:text-gray-600 cursor-not-allowed' : 'text-gray-600 dark:text-gray-300 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-900/40 active:scale-95']">
                                    <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5"/></svg>
                                </button>
                            </div>
                        </div>

                        <div class="w-36">
                            <label class="text-xs font-medium text-gray-500 dark:text-gray-400">动作</label>
                            <select v-model="step.action" @change="$emit('action-change', step)"
                                    class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-full text-sm mt-1 bg-white dark:bg-gray-700 text-gray-900 dark:text-white">
                                <option value="FILL_INPUT">填入内容</option>
                                <option value="CLICK">点击元素</option>
                                <option value="COORD_CLICK">坐标点击</option>
                                <option value="STREAM_WAIT">流式等待</option>
                                <option value="WAIT">等待</option>
                                <option value="KEY_PRESS">按键</option>
                                <option value="JS_EXEC">执行 JavaScript</option>
                            </select>
                        </div>

                        <div class="flex-1 min-w-0">
                            <label class="text-xs font-medium text-gray-500 dark:text-gray-400">
                                {{ ['FILL_INPUT', 'CLICK', 'STREAM_WAIT'].includes(step.action) ? '目标选择器' : step.action === 'COORD_CLICK' ? '坐标参数' : step.action === 'JS_EXEC' ? 'JavaScript' : '参数' }}
                            </label>

                            <select v-if="['FILL_INPUT', 'CLICK', 'STREAM_WAIT'].includes(step.action)" v-model="step.target"
                                    class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-full mt-1 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white">
                                <option value="" disabled>选择选择器...</option>
                                <option v-for="(v, k) in selectors" :key="k" :value="k">{{ k }} ({{ v || '未设置' }})</option>
                            </select>

                            <div v-else-if="step.action === 'COORD_CLICK'" class="flex items-center gap-2 mt-1 flex-wrap">
                                <input :value="step.value?.x ?? ''"
                                       @input="step.value = { ...(step.value || {}), x: Number($event.target.value) }"
                                       type="number"
                                       step="1"
                                       class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-28 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                                       placeholder="X viewport">
                                <input :value="step.value?.y ?? ''"
                                       @input="step.value = { ...(step.value || {}), y: Number($event.target.value) }"
                                       type="number"
                                       step="1"
                                       class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-28 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                                       placeholder="Y viewport">
                                <input :value="step.value?.random_radius ?? 0"
                                       @input="step.value = { ...(step.value || {}), random_radius: Number($event.target.value) }"
                                       type="number"
                                       min="0"
                                       step="1"
                                       class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-28 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                                       placeholder="随机半径">
                                <div class="w-full text-xs text-gray-500 dark:text-gray-400">
                                    使用 viewport CSS 坐标，不是屏幕坐标。
                                </div>
                            </div>

                            <div v-else-if="step.action === 'KEY_PRESS'" class="mt-1 space-y-2">
                                <select :value="getKeyPresetValue(index, step)"
                                        @change="applyKeyPreset(index, step, $event.target.value)"
                                        class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-full text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white">
                                    <option value="">选择常用按键/组合键...</option>
                                    <option v-for="preset in keyPresets" :key="preset.value" :value="preset.value">{{ preset.label }}</option>
                                    <option value="__custom__">自定义...</option>
                                </select>
                                <input v-if="isCustomKeyPreset(index, step)"
                                       v-model="step.target"
                                       list="workflow-key-presets"
                                       placeholder="例如: Enter / Ctrl+Enter / Ctrl+Shift+P"
                                       class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-full text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white">
                                <div class="text-xs text-gray-500 dark:text-gray-400">
                                    支持直接选择，也支持手输任意按键或组合键。
                                </div>
                            </div>

                            <div v-else-if="step.action === 'JS_EXEC'" class="mt-1 space-y-2">
                                <div class="flex items-center justify-between gap-2">
                                    <span class="text-xs text-gray-500 dark:text-gray-400">在当前页面上下文执行对应的 JavaScript 脚本。</span>
                                    <button @click="toggleJsExpand(index)"
                                            type="button"
                                            class="px-2 py-1 text-xs rounded-md border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700">
                                        {{ isJsExpanded(index) ? '收起' : '展开' }}
                                    </button>
                                </div>
                                <textarea v-model="step.value"
                                          :rows="isJsExpanded(index) ? 16 : 4"
                                          :class="[
                                              'w-full rounded-md border dark:border-gray-600 px-3 py-2 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent bg-white dark:bg-gray-700 text-gray-900 dark:text-white resize-y',
                                              isJsExpanded(index) ? 'min-h-[22rem]' : 'min-h-[7rem]'
                                          ]"
                                          spellcheck="false"
                                          placeholder="return document.title;"></textarea>
                            </div>

                            <div v-else-if="step.action === 'WAIT'" class="flex items-center gap-2 mt-1">
                                <input v-model.number="step.value" type="number" step="0.1" min="0"
                                       class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-24 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white">
                                <span class="text-sm text-gray-500 dark:text-gray-400">秒</span>
                            </div>
                        </div>

                        <div class="pt-5">
                            <label class="flex items-center text-xs cursor-pointer whitespace-nowrap text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white transition-colors"
                                   title="勾选后找不到元素会报错；不勾选则跳过该步骤">
                                <input type="checkbox"
                                       :checked="!step.optional"
                                       @change="step.optional = !$event.target.checked"
                                       class="mr-1.5 rounded">
                                <span>必需步骤</span>
                            </label>
                        </div>

                        <div class="pt-4">
                            <button @click="$emit('remove-step', index)"
                                    class="p-1.5 rounded-md transition-all duration-150 text-gray-500 dark:text-gray-400 hover:text-red-600 dark:hover:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/40 active:scale-95">
                                <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
                                </svg>
                            </button>
                        </div>
                    </div>
                </div>

                <div v-if="workflow.length === 0" class="text-center text-gray-400 dark:text-gray-500 text-sm py-8">
                    暂无工作流步骤，点击新增步骤或使用模板。
                </div>

                <datalist id="workflow-key-presets">
                    <option v-for="preset in keyPresets" :key="'key-' + preset.value" :value="preset.value"></option>
                </datalist>
            </div>
        </div>
    `
};

