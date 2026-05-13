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
            editorBridgePolling: false,
            editorBridgeInFlight: false,
            editorBridgeTimer: null,
            keyPresets: WORKFLOW_KEY_PRESETS,
            expandedJsEditors: {},
            customKeyModes: {},
            expandedHintEditors: {},
            hintToneOptions: [
                { value: 'info', label: '提示' },
                { value: 'success', label: '成功' },
                { value: 'warning', label: '警告' },
                { value: 'danger', label: '注意' }
            ]
        };
    },
    watch: {
        workflow: {
            handler() {
                this.syncHintEditorState();
            },
            deep: true,
            immediate: true
        },
        selectedPreset() {
            this.expandedHintEditors = {};
            this.syncHintEditorState();
        }
    },
    beforeUnmount() {
        this.stopEditorBridgePolling();
    },
    methods: {
        toggle() {
            this.$emit('update:collapsed', !this.collapsed);
        },

        async authJsonRequest(url, options = {}) {
            const token = localStorage.getItem('api_token');
            const headers = {
                'Content-Type': 'application/json',
                ...(options.headers || {})
            };

            if (token) {
                headers['Authorization'] = 'Bearer ' + token;
            }

            const response = await fetch(url, {
                ...options,
                headers
            });

            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                const message = payload.detail || payload.message || ('HTTP ' + response.status);
                const error = new Error(message);
                error.status = response.status;
                throw error;
            }

            return payload;
        },

        startEditorBridgePolling() {
            if (this.editorBridgePolling || this.editorBridgeTimer) return;
            this.editorBridgePolling = true;
            console.debug('[WorkflowPanel] start editor bridge polling');
            this.consumeEditorBridgeActions();
            this.editorBridgeTimer = window.setInterval(() => {
                this.consumeEditorBridgeActions();
            }, 250);
        },

        stopEditorBridgePolling() {
            this.editorBridgePolling = false;
            if (this.editorBridgeTimer) {
                window.clearInterval(this.editorBridgeTimer);
                this.editorBridgeTimer = null;
            }
            console.debug('[WorkflowPanel] stop editor bridge polling');
        },

        async consumeEditorBridgeActions() {
            if (this.editorBridgeInFlight) return;
            this.editorBridgeInFlight = true;
            try {
                const result = await this.authJsonRequest('/api/workflow-editor/consume-actions', {
                    method: 'POST',
                    body: '{}'
                });
                if (result && Number(result.executed_count || 0) > 0) {
                    console.debug('[WorkflowPanel] consumed editor bridge actions:', result);
                }
            } catch (e) {
                console.debug('[WorkflowPanel] consume editor bridge actions failed:', e);
            } finally {
                this.editorBridgeInFlight = false;
            }
        },

        async launchVisualEditor() {
            if (this.editorInjecting) return;
            this.editorInjecting = true;
            try {
                console.debug('[WorkflowPanel] launch visual editor', {
                    domain: this.currentDomain,
                    preset: this.selectedPreset
                });
                const result = await this.authJsonRequest('/api/workflow-editor/inject', {
                    method: 'POST',
                    body: JSON.stringify({
                        target_domain: this.currentDomain,
                        preset_name: this.selectedPreset
                    })
                });
                if (result.success) {
                    this.startEditorBridgePolling();
                    console.debug('[WorkflowPanel] visual editor launch result:', result);
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
        },

        normalizeHintStepValue(step) {
            const current = (step && step.value && typeof step.value === 'object' && !Array.isArray(step.value))
                ? step.value
                : {};
            const tone = String(current.tone || '').trim().toLowerCase();
            return {
                title: String(current.title || '提示'),
                text: String(current.text || ''),
                tone: ['info', 'success', 'warning', 'danger'].includes(tone) ? tone : 'info'
            };
        },

        isDefaultHintStepValue(step) {
            const normalized = this.normalizeHintStepValue(step);
            return (
                normalized.title === '提示'
                && normalized.text === '这是一条只读提示，不会在执行时触发页面操作。'
                && normalized.tone === 'info'
            );
        },

        syncHintEditorState() {
            const next = { ...this.expandedHintEditors };
            (this.workflow || []).forEach((step, index) => {
                if (step.action !== 'READONLY_HINT') {
                    delete next[index];
                    return;
                }
                if (!Object.prototype.hasOwnProperty.call(next, index)) {
                    next[index] = this.isDefaultHintStepValue(step);
                }
            });
            Object.keys(next).forEach(key => {
                const idx = Number(key);
                if (!Number.isInteger(idx) || !(this.workflow || [])[idx] || (this.workflow || [])[idx].action !== 'READONLY_HINT') {
                    delete next[key];
                }
            });
            const prevKeys = Object.keys(this.expandedHintEditors);
            const nextKeys = Object.keys(next);
            if (
                prevKeys.length === nextKeys.length
                && nextKeys.every(key => this.expandedHintEditors[key] === next[key])
            ) {
                return;
            }
            this.expandedHintEditors = next;
        },

        isHintExpanded(index) {
            return !!this.expandedHintEditors[index];
        },

        toggleHintExpand(index) {
            this.expandedHintEditors = {
                ...this.expandedHintEditors,
                [index]: !this.isHintExpanded(index)
            };
        },

        getHintToneClasses(step) {
            const tone = this.normalizeHintStepValue(step).tone;
            const toneMap = {
                info: 'border-sky-200 bg-sky-50/90 text-sky-800 dark:border-sky-800 dark:bg-sky-900/20 dark:text-sky-200',
                success: 'border-emerald-200 bg-emerald-50/90 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-900/20 dark:text-emerald-200',
                warning: 'border-amber-200 bg-amber-50/90 text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200',
                danger: 'border-rose-200 bg-rose-50/90 text-rose-800 dark:border-rose-800 dark:bg-rose-900/20 dark:text-rose-200'
            };
            return toneMap[tone] || toneMap.info;
        },

        getHintStepClasses(step) {
            return 'shadow-none ' + this.getHintToneClasses(step);
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
                     :class="[
                         'border rounded-lg p-3 transition-colors',
                         step.action === 'READONLY_HINT'
                             ? getHintStepClasses(step)
                             : 'dark:border-gray-700 hover:border-blue-300 dark:hover:border-blue-600 bg-gray-50/50 dark:bg-gray-900/30'
                     ]">
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

                        <div v-if="step.action !== 'READONLY_HINT'" class="w-36">
                            <label class="text-xs font-medium text-gray-500 dark:text-gray-400">动作</label>
                            <select v-model="step.action" @change="$emit('action-change', step)"
                                    class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-full text-sm mt-1 bg-white dark:bg-gray-700 text-gray-900 dark:text-white">
                                <option value="FILL_INPUT">填入内容</option>
                                <option value="PAGE_FETCH">页面直发</option>
                                <option value="CLICK">点击元素</option>
                                <option value="COORD_CLICK">坐标点击</option>
                                <option value="COORD_SCROLL">模拟滑动</option>
                                <option value="STREAM_WAIT">流式等待</option>
                                <option value="WAIT">等待</option>
                                <option value="KEY_PRESS">按键</option>
                                <option value="JS_EXEC">执行 JavaScript</option>
                                <option value="READONLY_HINT">只读提示</option>
                            </select>
                        </div>

                        <div class="flex-1 min-w-0">
                            <label v-if="step.action !== 'READONLY_HINT'" class="text-xs font-medium text-gray-500 dark:text-gray-400">
                                {{ ['FILL_INPUT', 'CLICK', 'STREAM_WAIT'].includes(step.action) ? '目标选择器' : step.action === 'PAGE_FETCH' ? '发送方式' : ['COORD_CLICK', 'COORD_SCROLL'].includes(step.action) ? '坐标参数' : step.action === 'JS_EXEC' ? 'JavaScript' : step.action === 'READONLY_HINT' ? '提示内容' : '参数' }}
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

                            <div v-else-if="step.action === 'COORD_SCROLL'" class="flex items-center gap-2 mt-1 flex-wrap">
                                <input :value="step.value?.start_x ?? ''"
                                       @input="step.value = { ...(step.value || {}), start_x: Number($event.target.value) }"
                                       type="number"
                                       step="1"
                                       class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-28 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                                       placeholder="起点 X">
                                <input :value="step.value?.start_y ?? ''"
                                       @input="step.value = { ...(step.value || {}), start_y: Number($event.target.value) }"
                                       type="number"
                                       step="1"
                                       class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-28 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                                       placeholder="起点 Y">
                                <input :value="step.value?.end_x ?? ''"
                                       @input="step.value = { ...(step.value || {}), end_x: Number($event.target.value) }"
                                       type="number"
                                       step="1"
                                       class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-28 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                                       placeholder="终点 X">
                                <input :value="step.value?.end_y ?? ''"
                                       @input="step.value = { ...(step.value || {}), end_y: Number($event.target.value) }"
                                       type="number"
                                       step="1"
                                       class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-28 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                                       placeholder="终点 Y">
                                <div class="w-full text-xs text-gray-500 dark:text-gray-400">
                                    使用 viewport CSS 坐标。普通模式直接派发滚轮，隐身模式会按站点 stealth 配置走人类化轨迹。
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

                            <div v-else-if="step.action === 'READONLY_HINT'" class="space-y-3">
                                <div class="relative min-h-[4.5rem]">
                                    <button @click="toggleHintExpand(index)"
                                            type="button"
                                            class="absolute right-0 top-0 z-10 inline-flex h-8 w-8 items-center justify-center rounded-md border border-current/20 bg-white/45 text-[11px] font-semibold shadow-sm transition hover:bg-white/70 dark:bg-slate-950/20 dark:hover:bg-slate-950/35">
                                        {{ isHintExpanded(index) ? '−' : '✎' }}
                                    </button>
                                    <div class="w-full pr-12 text-sm leading-7">
                                        <div class="text-base font-semibold">
                                            {{ normalizeHintStepValue(step).title || '提示' }}
                                        </div>
                                        <div class="mt-1 whitespace-pre-wrap">
                                            {{ normalizeHintStepValue(step).text || '这里会展示提示预览，执行时不会触发页面操作。' }}
                                        </div>
                                    </div>
                                </div>

                                <div v-if="isHintExpanded(index)" class="space-y-3 border-t border-current/15 pt-3">
                                    <div class="grid grid-cols-1 md:grid-cols-[160px_minmax(0,1fr)_180px] gap-3">
                                        <div>
                                            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">动作</label>
                                            <select v-model="step.action" @change="$emit('action-change', step)"
                                                    class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                                <option value="FILL_INPUT">填入内容</option>
                                                <option value="PAGE_FETCH">页面直发</option>
                                                <option value="CLICK">点击元素</option>
                                                <option value="COORD_CLICK">坐标点击</option>
                                                <option value="COORD_SCROLL">模拟滑动</option>
                                                <option value="STREAM_WAIT">流式等待</option>
                                                <option value="WAIT">等待</option>
                                                <option value="KEY_PRESS">按键</option>
                                                <option value="JS_EXEC">执行 JavaScript</option>
                                                <option value="READONLY_HINT">只读提示</option>
                                            </select>
                                        </div>
                                        <div>
                                            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">标题</label>
                                            <input :value="normalizeHintStepValue(step).title"
                                                   @input="step.value = { ...normalizeHintStepValue(step), title: $event.target.value }"
                                                   type="text"
                                                   class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent"
                                                   placeholder="例如：实验功能说明">
                                        </div>
                                        <div>
                                            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">语气</label>
                                            <select :value="normalizeHintStepValue(step).tone"
                                                    @change="step.value = { ...normalizeHintStepValue(step), tone: $event.target.value }"
                                                    class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                                <option v-for="tone in hintToneOptions" :key="tone.value" :value="tone.value">{{ tone.label }}</option>
                                            </select>
                                        </div>
                                    </div>
                                    <div>
                                        <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">正文</label>
                                        <textarea :value="normalizeHintStepValue(step).text"
                                                  @input="step.value = { ...normalizeHintStepValue(step), text: $event.target.value }"
                                                  rows="4"
                                                  class="w-full rounded-md border dark:border-gray-600 px-3 py-2 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white resize-y focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent"
                                                  placeholder="这里填写给用户看的只读提示内容。"></textarea>
                                    </div>
                                    <div class="text-xs text-gray-500 dark:text-gray-400">
                                        这一步只用于给用户展示说明，不会在执行时点击、输入或等待页面。
                                    </div>
                                </div>
                            </div>

                            <div v-else-if="step.action === 'WAIT'" class="flex items-center gap-2 mt-1">
                                <input v-model.number="step.value" type="number" step="0.1" min="0"
                                       class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-24 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white">
                                <span class="text-sm text-gray-500 dark:text-gray-400">秒</span>
                            </div>

                            <div v-else-if="step.action === 'PAGE_FETCH'" class="mt-1 space-y-2">
                                <div class="rounded-md border border-sky-200 bg-sky-50 px-3 py-2 text-sm leading-6 text-sky-800 dark:border-sky-800 dark:bg-sky-900/20 dark:text-sky-200">
                                    使用当前预设的页面直发配置发送已构造的 prompt。失败且回退模式为工作流时，会继续执行后续填入 / 按键 / 等待步骤。
                                </div>
                            </div>
                        </div>

                        <div v-if="!['READONLY_HINT', 'PAGE_FETCH'].includes(step.action)" class="pt-5">
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

