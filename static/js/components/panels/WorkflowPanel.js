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
        modelCatalog: { type: Object, default: () => ({}) },
        currentDomain: { type: String, default: null },
        selectedPreset: { type: String, default: '主预设' },
        collapsed: { type: Boolean, default: true }
    },
    emits: ['update:collapsed', 'update:modelCatalog', 'add-step', 'remove-step', 'move-step', 'action-change', 'show-templates'],
    data() {
        return {
            editorInjecting: false,
            editorBridgePolling: false,
            editorBridgeInFlight: false,
            editorBridgeTimer: null,
            editorBridgeIdleDelay: 250,
            keyPresets: WORKFLOW_KEY_PRESETS,
            expandedJsEditors: {},
            jsExecutionModes: {},
            availableScripts: [],
            loadingScripts: false,
            expandedJsPreviews: {},
            scriptPreviewContents: {},
            loadingScriptPreviews: {},
            customKeyModes: {},
            expandedHintEditors: {},
            expandedExecutionMenus: {},
            hintToneOptions: [
                { value: 'info', label: '提示' },
                { value: 'success', label: '成功' },
                { value: 'warning', label: '警告' },
                { value: 'danger', label: '注意' }
            ],
            jsonParamPlaceholder: '{\n  "target_endpoint": "/nextjs-api/stream/create-evaluation",\n  "override_model": "{{context.model}}"\n}',
            catalogKeywordsDraft: {
                include_keywords: '',
                exclude_keywords: ''
            }
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
        modelCatalog: {
            handler() {
                this.syncCatalogKeywordsDraft();
            },
            deep: true,
            immediate: true
        },
        selectedPreset() {
            this.expandedHintEditors = {};
            this.expandedExecutionMenus = {};
            this.customKeyModes = {};
            this.expandedJsEditors = {};
            this.expandedJsPreviews = {};
            this.syncHintEditorState();
            this.syncCatalogKeywordsDraft(true);
        },
        currentDomain() {
            this.syncCatalogKeywordsDraft(true);
        }
    },
    mounted() {
        this.loadAvailableScripts(false);
        this.syncCatalogKeywordsDraft(true);
    },
    beforeUnmount() {
        this.stopEditorBridgePolling();
    },
    methods: {
        toggle() {
            this.$emit('update:collapsed', !this.collapsed);
        },

        normalizeCatalogKeywords(value) {
            const items = Array.isArray(value)
                ? value
                : String(value || '').split(/[\n,]+/);
            return [...new Set(items.map(item => String(item || '').trim()).filter(Boolean))];
        },

        catalogKeywordsText(key) {
            return this.normalizeCatalogKeywords(this.modelCatalog && this.modelCatalog[key]).join('\n');
        },

        syncCatalogKeywordsDraft(force = false) {
            if (!this.catalogKeywordsDraft) {
                this.catalogKeywordsDraft = { include_keywords: '', exclude_keywords: '' };
            }
            ['include_keywords', 'exclude_keywords'].forEach(key => {
                const propArr = this.normalizeCatalogKeywords(this.modelCatalog && this.modelCatalog[key]);
                const draftArr = this.normalizeCatalogKeywords(this.catalogKeywordsDraft[key]);
                const isSame = JSON.stringify(propArr) === JSON.stringify(draftArr);
                if (force || !isSame || this.catalogKeywordsDraft[key] === undefined) {
                    this.catalogKeywordsDraft[key] = propArr.join('\n');
                }
            });
        },

        handleCatalogKeywordsInput(key, rawText) {
            if (!this.catalogKeywordsDraft) {
                this.catalogKeywordsDraft = { include_keywords: '', exclude_keywords: '' };
            }
            this.catalogKeywordsDraft[key] = rawText;
            this.updateModelCatalog({
                [key]: this.normalizeCatalogKeywords(rawText)
            });
        },

        updateModelCatalog(patch) {
            this.$emit('update:modelCatalog', {
                enabled: false,
                source: 'arena_direct',
                include_keywords: [],
                exclude_keywords: [],
                ...(this.modelCatalog || {}),
                ...(patch || {})
            });
        },

        async authJsonRequest(url, options = {}) {
            const token = window.getDashboardAuthToken ? window.getDashboardAuthToken() : '';
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
                // 修复：挂载业务负载，便于调用方区分「后端业务失败」与「真正的传输异常」
                error.payload = payload;
                error.isBusinessError = (response.status >= 400 && response.status < 500) || response.status === 503;
                throw error;
            }

            return payload;
        },

        startEditorBridgePolling() {
            if (this.editorBridgePolling || this.editorBridgeTimer) return;
            this.editorBridgePolling = true;
            this.editorBridgeIdleDelay = 250;
            console.debug('[WorkflowPanel] start editor bridge polling');
            this.scheduleEditorBridgePolling(0);
        },

        stopEditorBridgePolling() {
            this.editorBridgePolling = false;
            if (this.editorBridgeTimer) {
                window.clearTimeout(this.editorBridgeTimer);
                this.editorBridgeTimer = null;
            }
            console.debug('[WorkflowPanel] stop editor bridge polling');
        },

        scheduleEditorBridgePolling(delay = this.editorBridgeIdleDelay) {
            if (!this.editorBridgePolling || this.editorBridgeTimer) return;
            this.editorBridgeTimer = window.setTimeout(async () => {
                this.editorBridgeTimer = null;
                await this.consumeEditorBridgeActions();
            }, Math.max(0, Number(delay) || 0));
        },

        async consumeEditorBridgeActions() {
            if (this.editorBridgeInFlight) {
                this.scheduleEditorBridgePolling(this.editorBridgeIdleDelay);
                return;
            }
            this.editorBridgeInFlight = true;
            try {
                const result = await this.authJsonRequest('/api/workflow-editor/consume-actions', {
                    method: 'POST',
                    body: '{}'
                });
                const executedCount = Number(result && result.executed_count || 0);
                if (executedCount > 0) {
                    this.editorBridgeIdleDelay = 250;
                    console.debug('[WorkflowPanel] consumed editor bridge actions:', result);
                } else {
                    this.editorBridgeIdleDelay = Math.min(2000, Math.max(250, this.editorBridgeIdleDelay * 2));
                }
            } catch (e) {
                this.editorBridgeIdleDelay = Math.min(2000, Math.max(500, this.editorBridgeIdleDelay * 2));
                console.debug('[WorkflowPanel] consume editor bridge actions failed:', e);
            } finally {
                this.editorBridgeInFlight = false;
                this.scheduleEditorBridgePolling(this.editorBridgeIdleDelay);
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
                // 修复：后端所有失败路径都用非 2xx 承载 success:false（浏览器未连接/域名不匹配等），
                // 会被 authJsonRequest 抛出，此前一律提示「网络错误」，误导用户去排查网络
                const hasBusinessPayload = e && e.payload && typeof e.payload === 'object'
                    && Object.keys(e.payload).length > 0;
                if (e && (e.isBusinessError || hasBusinessPayload)) {
                    alert('注入失败: ' + (e.message || '未知错误'));
                } else {
                    alert('网络错误: ' + ((e && e.message) || '未知错误'));
                }
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

        isExecutionExpanded(index, step = null) {
            const expanded = !!this.expandedExecutionMenus[index];
            if (expanded && step) {
                this.ensureStepExecution(step);
            }
            return expanded;
        },

        ensureStepExecution(step) {
            if (!step.execution || typeof step.execution !== 'object' || Array.isArray(step.execution)) {
                step.execution = {};
            }
            if (!step.execution.retry || typeof step.execution.retry !== 'object') {
                step.execution.retry = {
                    enabled: false,
                    max_attempts: 2,
                    interval: 0.3
                };
            }
            if (!step.execution.verification || typeof step.execution.verification !== 'object') {
                step.execution.verification = {
                    enabled: false,
                    match: 'any',
                    timeout: 2,
                    poll_interval: 0.1,
                    conditions: []
                };
            }
            if (!Array.isArray(step.execution.verification.conditions)) {
                step.execution.verification.conditions = [];
            }
            if (!step.execution.click_mode) step.execution.click_mode = 'inherit';
            return step.execution;
        },

        toggleExecutionMenu(index, step) {
            this.ensureStepExecution(step);
            this.expandedExecutionMenus = {
                ...this.expandedExecutionMenus,
                [index]: !this.isExecutionExpanded(index)
            };
        },

        addVerificationCondition(step) {
            const execution = this.ensureStepExecution(step);
            execution.verification.conditions.push({
                target: step.target || '',
                state: 'absent'
            });
        },

        setVerificationEnabled(step, enabled) {
            const execution = this.ensureStepExecution(step);
            execution.verification.enabled = !!enabled;
            if (enabled && execution.verification.conditions.length === 0) {
                this.addVerificationCondition(step);
            }
        },

        removeVerificationCondition(step, conditionIndex) {
            const execution = this.ensureStepExecution(step);
            execution.verification.conditions.splice(conditionIndex, 1);
        },

        getExecutionSummary(step) {
            const execution = step.execution && typeof step.execution === 'object' ? step.execution : {};
            const parts = [];
            if (execution.click_mode === 'dom_safe') parts.push('后台 DOM');
            if (execution.click_mode === 'cdp_mouse') parts.push('CDP 鼠标');
            if (execution.retry?.enabled) parts.push(`最多 ${Number(execution.retry.max_attempts || 2)} 次`);
            if (execution.verification?.enabled) parts.push('结果验证');
            return parts.join(' · ') || '默认';
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
            // 语气配色统一走 uwa 主题层（dashboard-reference.css / dashboard-dark.css）
            const toneMap = {
                info: 'uwa-wf-hint is-info',
                success: 'uwa-wf-hint is-success',
                warning: 'uwa-wf-hint is-warning',
                danger: 'uwa-wf-hint is-danger'
            };
            return toneMap[tone] || toneMap.info;
        },

        getHintStepClasses(step) {
            return 'shadow-none ' + this.getHintToneClasses(step);
        },

        normalizeScriptTarget(target) {
            return String(target || '').trim().replace(/^file:\/+/i, '').replace(/^scripts:\/+/i, 'scripts/');
        },

        getJsMode(index, step) {
            const clean = this.normalizeScriptTarget(step?.target);
            if (clean) {
                return 'file';
            }
            if (step?.value && typeof step.value === 'object' && !Array.isArray(step.value)) {
                return 'file';
            }
            return 'inline';
        },

        setJsMode(index, step, mode) {
            if (mode === 'file') {
                if (!step.target && this.availableScripts.length > 0) {
                    step.target = this.availableScripts[0].path;
                }
                if (typeof step.value === 'string' && step.value.trim().startsWith('{')) {
                    try {
                        step.value = JSON.parse(step.value);
                    } catch (_) {}
                } else if (!step.value || typeof step.value === 'string') {
                    step.value = {};
                }
            } else {
                step.target = '';
                if (typeof step.value !== 'string') {
                    step.value = 'return document.title;';
                }
            }
        },

        async loadAvailableScripts(force = false) {
            if (this.availableScripts.length > 0 && !force) return;
            this.loadingScripts = true;
            try {
                const res = await this.authJsonRequest('/api/config/workflow/scripts');
                this.availableScripts = Array.isArray(res?.scripts) ? res.scripts : [];
            } catch (e) {
                console.error('[WorkflowPanel] 获取脚本列表失败:', e);
            } finally {
                this.loadingScripts = false;
            }
        },

        isJsPreviewExpanded(index) {
            return !!this.expandedJsPreviews[index];
        },

        async toggleJsPreview(index, step) {
            const next = !this.isJsPreviewExpanded(index);
            this.expandedJsPreviews = {
                ...this.expandedJsPreviews,
                [index]: next
            };
            if (next && step.target) {
                await this.fetchScriptPreviewContent(step.target);
            }
        },

        async fetchScriptPreviewContent(target) {
            const cleanPath = this.normalizeScriptTarget(target);
            if (!cleanPath || this.scriptPreviewContents[cleanPath]) return;
            this.loadingScriptPreviews = { ...this.loadingScriptPreviews, [cleanPath]: true };
            try {
                const res = await this.authJsonRequest('/api/config/workflow/script-content?path=' + encodeURIComponent(cleanPath));
                if (res && res.content !== undefined) {
                    this.scriptPreviewContents = {
                        ...this.scriptPreviewContents,
                        [cleanPath]: res
                    };
                }
            } catch (e) {
                console.warn('[WorkflowPanel] 获取脚本内容预览失败:', e);
                this.scriptPreviewContents = {
                    ...this.scriptPreviewContents,
                    [cleanPath]: { content: '// 无法加载脚本内容: ' + (e.message || e), description: '' }
                };
            } finally {
                this.loadingScriptPreviews = { ...this.loadingScriptPreviews, [cleanPath]: false };
            }
        },

        getScriptPreviewData(step) {
            const cleanPath = this.normalizeScriptTarget(step?.target);
            return this.scriptPreviewContents[cleanPath] || null;
        },

        insertMacro(index, step, macroText) {
            const textarea = document.getElementById('wf-js-param-' + index) || document.getElementById('wf-js-inline-' + index);
            if (textarea) {
                const start = textarea.selectionStart ?? textarea.value.length;
                const end = textarea.selectionEnd ?? textarea.value.length;
                const text = textarea.value || '';
                const next = text.slice(0, start) + macroText + text.slice(end);
                textarea.value = next;
                textarea.dispatchEvent(new Event('input'));
                this.$nextTick(() => {
                    textarea.focus();
                    const newPos = start + macroText.length;
                    textarea.setSelectionRange(newPos, newPos);
                });
            } else {
                if (typeof step.value === 'string') {
                    step.value = (step.value ? step.value + ' ' : '') + macroText;
                } else if (step.value && typeof step.value === 'object') {
                    step.value = { ...step.value, macro: macroText };
                }
            }
        },

        formatJsStepValue(step) {
            if (typeof step.value === 'string') return step.value;
            if (step.value && typeof step.value === 'object') {
                try {
                    return JSON.stringify(step.value, null, 2);
                } catch (_) {
                    return '{}';
                }
            }
            return '';
        },

        updateJsStepParamValue(step, rawText) {
            const trimmed = String(rawText || '').trim();
            if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
                try {
                    step.value = JSON.parse(trimmed);
                    return;
                } catch (_) {}
            }
            step.value = rawText;
        }
    },
    template: `
        <div class="uwa-workflow-panel bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-sm">
            <div class="px-4 py-3 border-b dark:border-gray-700 flex justify-between items-center cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                 @click="toggle">
                <div class="flex items-center gap-2">
                    <span class="w-4 inline-flex justify-center text-gray-500 dark:text-gray-400" v-html="collapsed ? $icons.chevronDown : $icons.chevronUp"></span>
                    <h3 class="font-semibold text-gray-900 dark:text-white">工作流</h3>
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

            <div v-show="!collapsed" class="p-4 space-y-4 max-h-[44rem] overflow-auto">
                <div class="border-b border-gray-200 dark:border-gray-700 pb-4 space-y-3">
                    <div class="flex items-center justify-between gap-4">
                        <div>
                            <div class="text-sm font-semibold text-gray-900 dark:text-white">页面模型目录</div>
                            <div class="text-xs text-gray-500 dark:text-gray-400 mt-1">启用后，此预设负责读取页面模型、过滤列表，并在请求时切换模型。</div>
                        </div>
                        <label class="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200 cursor-pointer">
                            <input type="checkbox"
                                   class="rounded"
                                   :checked="!!modelCatalog.enabled"
                                   @change="updateModelCatalog({ enabled: $event.target.checked })">
                            <span>启用目录</span>
                        </label>
                    </div>

                    <div v-if="modelCatalog.enabled" class="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <label class="block min-w-0">
                            <span class="block text-xs font-medium text-gray-600 dark:text-gray-300 mb-1">仅保留关键词（可选，每行一个）</span>
                            <textarea :value="catalogKeywordsDraft ? catalogKeywordsDraft.include_keywords : ''"
                                      @input="handleCatalogKeywordsInput('include_keywords', $event.target.value)"
                                      rows="4"
                                      spellcheck="false"
                                      class="w-full rounded-md border dark:border-gray-600 px-3 py-2 font-mono text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white resize-y focus:outline-none focus:ring-2 focus:ring-blue-400"
                                      placeholder="glm&#10;claude"></textarea>
                        </label>
                        <label class="block min-w-0">
                            <span class="block text-xs font-medium text-gray-600 dark:text-gray-300 mb-1">排除关键词（每行一个）</span>
                            <textarea :value="catalogKeywordsDraft ? catalogKeywordsDraft.exclude_keywords : ''"
                                      @input="handleCatalogKeywordsInput('exclude_keywords', $event.target.value)"
                                      rows="4"
                                      spellcheck="false"
                                      class="w-full rounded-md border dark:border-gray-600 px-3 py-2 font-mono text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white resize-y focus:outline-none focus:ring-2 focus:ring-blue-400"
                                      placeholder="image&#10;preview&#10;legacy"></textarea>
                        </label>
                    </div>
                </div>

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
                                <option value="SELECT_MODEL">选择请求模型</option>
                                <option value="PAGE_FETCH">页面直发</option>
                                <option value="CLICK">点击元素</option>
                                <option value="COORD_CLICK">坐标点击</option>
                                <option value="COORD_SCROLL">模拟滑动</option>
                                <option value="STREAM_WAIT">流式等待</option>
                                <option value="STREAM_OUTPUT">流式输出（同流式等待）</option>
                                <option value="WAIT">等待</option>
                                <option value="KEY_PRESS">按键</option>
                                <option value="JS_EXEC">执行 JavaScript</option>
                                <option value="READONLY_HINT">只读提示</option>
                            </select>
                        </div>

                        <div class="flex-1 min-w-0">
                            <label v-if="step.action !== 'READONLY_HINT'" class="text-xs font-medium text-gray-500 dark:text-gray-400">
                                {{ step.action === 'SELECT_MODEL' ? '模型选择器' : ['FILL_INPUT', 'CLICK', 'STREAM_WAIT', 'STREAM_OUTPUT'].includes(step.action) ? '目标选择器' : step.action === 'PAGE_FETCH' ? '发送方式' : ['COORD_CLICK', 'COORD_SCROLL'].includes(step.action) ? '坐标参数' : step.action === 'JS_EXEC' ? 'JavaScript' : step.action === 'READONLY_HINT' ? '提示内容' : '参数' }}
                            </label>

                            <select v-if="['FILL_INPUT', 'SELECT_MODEL', 'CLICK', 'STREAM_WAIT', 'STREAM_OUTPUT'].includes(step.action)" v-model="step.target"
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
                                    使用 viewport CSS 坐标。普通模式直接派发滚轮，低熵模式会按站点 stealth 配置走人类化轨迹。
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

                            <div v-else-if="step.action === 'JS_EXEC'" class="mt-1 space-y-3">
                                <!-- 模式选择器 -->
                                <div class="flex items-center justify-between gap-3 flex-wrap">
                                    <div class="flex items-center gap-4 text-xs font-medium text-gray-700 dark:text-gray-300">
                                        <label class="inline-flex items-center gap-1.5 cursor-pointer">
                                            <input type="radio"
                                                   :name="'js-mode-' + index"
                                                   value="file"
                                                   :checked="getJsMode(index, step) === 'file'"
                                                   @change="setJsMode(index, step, 'file')"
                                                   class="text-blue-600 focus:ring-blue-500">
                                            <span>外部脚本文件 (推荐)</span>
                                        </label>
                                        <label class="inline-flex items-center gap-1.5 cursor-pointer">
                                            <input type="radio"
                                                   :name="'js-mode-' + index"
                                                   value="inline"
                                                   :checked="getJsMode(index, step) === 'inline'"
                                                   @change="setJsMode(index, step, 'inline')"
                                                   class="text-blue-600 focus:ring-blue-500">
                                            <span>内联代码</span>
                                        </label>
                                    </div>

                                    <div class="flex items-center gap-2">
                                        <button v-if="getJsMode(index, step) === 'file' && step.target"
                                                @click="toggleJsPreview(index, step)"
                                                type="button"
                                                class="px-2 py-1 text-xs rounded-md border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-1">
                                            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                                                <path d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/>
                                                <path d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/>
                                            </svg>
                                            {{ isJsPreviewExpanded(index) ? '收起源码' : '预览源码' }}
                                        </button>
                                        <button v-if="getJsMode(index, step) === 'inline'"
                                                @click="toggleJsExpand(index)"
                                                type="button"
                                                class="px-2 py-1 text-xs rounded-md border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700">
                                            {{ isJsExpanded(index) ? '收起编辑器' : '展开编辑器' }}
                                        </button>
                                    </div>
                                </div>

                                <!-- 外部脚本模式 -->
                                <div v-if="getJsMode(index, step) === 'file'" class="space-y-3">
                                    <div>
                                        <div class="flex items-center justify-between gap-2 mb-1">
                                            <label class="block text-xs font-semibold text-gray-700 dark:text-gray-300">
                                                脚本文件 (custom_scripts/ 或 scripts/)
                                            </label>
                                            <button @click="loadAvailableScripts(true)"
                                                    type="button"
                                                    class="text-[11px] text-blue-600 dark:text-blue-400 hover:underline flex items-center gap-0.5">
                                                <span>刷新列表</span>
                                            </button>
                                        </div>
                                        <div class="relative">
                                            <select :value="normalizeScriptTarget(step.target)"
                                                    @change="step.target = $event.target.value; if (isJsPreviewExpanded(index)) fetchScriptPreviewContent(step.target)"
                                                    @focus="loadAvailableScripts(false)"
                                                    class="w-full border dark:border-gray-600 px-3 py-1.5 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-400">
                                                <option value="" disabled>-- 请选择脚本文件 --</option>
                                                <optgroup v-if="availableScripts.some(s => s.category === 'custom')" label="自定义扩展脚本 (custom_scripts/)">
                                                    <option v-for="s in availableScripts.filter(s => s.category === 'custom')"
                                                            :key="s.path"
                                                            :value="s.path">
                                                        {{ s.name }} ({{ s.description || s.path }})
                                                    </option>
                                                </optgroup>
                                                <optgroup v-if="availableScripts.some(s => s.category === 'scripts')" label="内置/系统脚本 (scripts/)">
                                                    <option v-for="s in availableScripts.filter(s => s.category === 'scripts')"
                                                            :key="s.path"
                                                            :value="s.path">
                                                        {{ s.name }} ({{ s.description || s.path }})
                                                    </option>
                                                </optgroup>
                                                <option v-if="normalizeScriptTarget(step.target) && !availableScripts.some(s => s.path === normalizeScriptTarget(step.target))"
                                                        :value="normalizeScriptTarget(step.target)">
                                                    {{ step.target }} (自定义路径)
                                                </option>
                                            </select>
                                        </div>
                                    </div>

                                    <!-- 宏变量快捷插入器与参数编辑 -->
                                    <div class="space-y-1.5">
                                         <div class="flex items-center justify-between gap-2 flex-wrap">
                                             <label class="block text-xs font-semibold text-gray-700 dark:text-gray-300">
                                                 运行入参 (__ARGS__ 参数对象 / JSON 模板):
                                             </label>
                                             <div class="flex items-center gap-1 text-[11px] flex-wrap">
                                                 <span class="text-gray-500 dark:text-gray-400">插入宏:</span>
                                                 <button type="button"
                                                         @click="insertMacro(index, step, '{{context.model}}')"
                                                         class="px-1.5 py-0.5 rounded bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 hover:bg-blue-100 border border-blue-200 dark:border-blue-800 transition">
                                                     <span v-text="'{{context.model}}'"></span>
                                                 </button>
                                                 <button type="button"
                                                         @click="insertMacro(index, step, '{{context.prompt}}')"
                                                         class="px-1.5 py-0.5 rounded bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 hover:bg-blue-100 border border-blue-200 dark:border-blue-800 transition">
                                                     <span v-text="'{{context.prompt}}'"></span>
                                                 </button>
                                                 <button type="button"
                                                         @click="insertMacro(index, step, '{{context.session_id}}')"
                                                         class="px-1.5 py-0.5 rounded bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 hover:bg-blue-100 border border-blue-200 dark:border-blue-800 transition">
                                                     <span v-text="'{{context.session_id}}'"></span>
                                                 </button>
                                                 <button type="button"
                                                         @click="insertMacro(index, step, '{{context.stream}}')"
                                                         class="px-1.5 py-0.5 rounded bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 hover:bg-blue-100 border border-blue-200 dark:border-blue-800 transition">
                                                     <span v-text="'{{context.stream}}'"></span>
                                                 </button>
                                             </div>
                                         </div>
                                         <textarea :id="'wf-js-param-' + index"
                                                   :value="formatJsStepValue(step)"
                                                   @input="updateJsStepParamValue(step, $event.target.value)"
                                                   rows="3"
                                                   class="w-full rounded-md border dark:border-gray-600 px-3 py-2 font-mono text-xs focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent bg-white dark:bg-gray-700 text-gray-900 dark:text-white resize-y min-h-[4.5rem]"
                                                   spellcheck="false"
                                                   :placeholder="jsonParamPlaceholder"></textarea>
                                     </div>

                                     <!-- 脚本源码预览折叠卡片 -->
                                     <div v-if="isJsPreviewExpanded(index)" class="border dark:border-gray-700 rounded-md bg-gray-50 dark:bg-gray-800/80 p-3 space-y-2">
                                         <div class="flex items-center justify-between text-xs text-gray-600 dark:text-gray-400 border-b dark:border-gray-700 pb-2">
                                             <span class="font-medium">只读源码预览: {{ step.target }}</span>
                                             <span v-if="(getScriptPreviewData(step) || {}).description" class="text-gray-500 italic">
                                                 {{ (getScriptPreviewData(step) || {}).description }}
                                             </span>
                                         </div>
                                         <div v-if="loadingScriptPreviews[normalizeScriptTarget(step.target)]"
                                              class="text-xs text-gray-500 py-4 text-center">
                                             加载脚本源码中...
                                         </div>
                                         <pre v-else
                                              class="text-xs font-mono max-h-60 overflow-auto bg-gray-900 text-gray-100 dark:bg-gray-950 p-3 rounded leading-5 whitespace-pre-wrap select-text">{{ (getScriptPreviewData(step) || {}).content || '// 点击上方预览按钮加载脚本内容' }}</pre>
                                     </div>
                                 </div>

                                 <!-- 内联代码模式 -->
                                 <div v-else class="space-y-2">
                                     <div class="flex items-center justify-between gap-2 flex-wrap">
                                         <span class="text-xs text-gray-500 dark:text-gray-400">在当前页面上下文直接执行原生 JavaScript 代码。</span>
                                         <div class="flex items-center gap-1 text-[11px]">
                                             <span class="text-gray-500 dark:text-gray-400">插入宏:</span>
                                             <button type="button"
                                                     @click="insertMacro(index, step, '{{context.model}}')"
                                                     class="px-1.5 py-0.5 rounded bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 hover:bg-blue-100 border border-blue-200 dark:border-blue-800 transition">
                                                 <span v-text="'{{context.model}}'"></span>
                                             </button>
                                             <button type="button"
                                                     @click="insertMacro(index, step, '{{context.prompt}}')"
                                                     class="px-1.5 py-0.5 rounded bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 hover:bg-blue-100 border border-blue-200 dark:border-blue-800 transition">
                                                 <span v-text="'{{context.prompt}}'"></span>
                                             </button>
                                         </div>
                                     </div>
                                    <textarea :id="'wf-js-inline-' + index"
                                              v-model="step.value"
                                              :rows="isJsExpanded(index) ? 16 : 4"
                                              :class="[
                                                  'w-full rounded-md border dark:border-gray-600 px-3 py-2 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent bg-white dark:bg-gray-700 text-gray-900 dark:text-white resize-y',
                                                  isJsExpanded(index) ? 'min-h-[22rem]' : 'min-h-[7rem]'
                                              ]"
                                              spellcheck="false"
                                              placeholder="return document.title;"></textarea>
                                </div>
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
                                                <option value="SELECT_MODEL">选择请求模型</option>
                                                <option value="PAGE_FETCH">页面直发</option>
                                                <option value="CLICK">点击元素</option>
                                                <option value="COORD_CLICK">坐标点击</option>
                                                <option value="COORD_SCROLL">模拟滑动</option>
                                                <option value="STREAM_WAIT">流式等待</option>
                                                <!-- 修复：STREAM_OUTPUT 是后端 STREAM_WAIT 的完整别名，缺失会导致动作列渲染空白 -->
                                                <option value="STREAM_OUTPUT">流式输出（同流式等待）</option>
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
                                <div class="uwa-wf-note rounded-md px-3 py-2 text-sm leading-6">
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

                        <div v-if="step.action === 'CLICK'" class="pt-4">
                            <button @click="toggleExecutionMenu(index, step)"
                                    type="button"
                                    :title="isExecutionExpanded(index) ? '收起执行设置' : '展开执行设置'"
                                    :class="[
                                        'p-1.5 rounded-md transition-all duration-150',
                                        isExecutionExpanded(index)
                                            ? 'text-blue-600 bg-blue-100 dark:text-blue-300 dark:bg-blue-900/40'
                                            : 'text-gray-500 dark:text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-900/40'
                                    ]">
                                <span v-html="$icons.cog"></span>
                            </button>
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

                    <div v-if="step.action === 'CLICK' && isExecutionExpanded(index, step)"
                         class="mt-3 ml-9 border-t border-gray-200 dark:border-gray-700 pt-3 space-y-4">
                        <div class="flex items-center justify-between gap-3">
                            <div class="text-sm font-medium text-gray-800 dark:text-gray-200">执行设置</div>
                            <div class="text-xs text-gray-500 dark:text-gray-400">{{ getExecutionSummary(step) }}</div>
                        </div>

                        <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                            <label class="block">
                                <span class="block text-xs text-gray-500 dark:text-gray-400 mb-1">点击方式</span>
                                <select v-model="step.execution.click_mode"
                                        class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-400">
                                    <option value="inherit">继承全局策略</option>
                                    <option value="cdp_mouse">CDP 鼠标点击</option>
                                    <option value="dom_safe">后台安全 DOM 点击</option>
                                </select>
                            </label>

                            <label class="flex items-center gap-2 pt-5 text-sm text-gray-700 dark:text-gray-200">
                                <input v-model="step.execution.retry.enabled" type="checkbox" class="rounded">
                                <span>验证失败后重试</span>
                            </label>

                            <label class="flex items-center gap-2 pt-5 text-sm text-gray-700 dark:text-gray-200">
                                <input :checked="step.execution.verification.enabled"
                                       @change="setVerificationEnabled(step, $event.target.checked)"
                                       type="checkbox" class="rounded">
                                <span>验证点击结果</span>
                            </label>
                        </div>

                        <div v-if="step.execution.retry.enabled" class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                            <label class="block">
                                <span class="block text-xs text-gray-500 dark:text-gray-400 mb-1">最多尝试次数</span>
                                <input v-model.number="step.execution.retry.max_attempts" type="number" min="1" max="10" step="1"
                                       class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-400">
                            </label>
                            <label class="block">
                                <span class="block text-xs text-gray-500 dark:text-gray-400 mb-1">重试间隔（秒）</span>
                                <input v-model.number="step.execution.retry.interval" type="number" min="0" max="30" step="0.1"
                                       class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-400">
                            </label>
                        </div>

                        <div v-if="step.execution.verification.enabled" class="space-y-3">
                            <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
                                <label class="block">
                                    <span class="block text-xs text-gray-500 dark:text-gray-400 mb-1">条件关系</span>
                                    <select v-model="step.execution.verification.match"
                                            class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-400">
                                        <option value="any">满足任一条件</option>
                                        <option value="all">满足全部条件</option>
                                    </select>
                                </label>
                                <label class="block">
                                    <span class="block text-xs text-gray-500 dark:text-gray-400 mb-1">验证超时（秒）</span>
                                    <input v-model.number="step.execution.verification.timeout" type="number" min="0" max="60" step="0.1"
                                           class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-400">
                                </label>
                                <label class="block">
                                    <span class="block text-xs text-gray-500 dark:text-gray-400 mb-1">轮询间隔（秒）</span>
                                    <input v-model.number="step.execution.verification.poll_interval" type="number" min="0.03" max="5" step="0.05"
                                           class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-400">
                                </label>
                            </div>

                            <div v-for="(condition, conditionIndex) in step.execution.verification.conditions"
                                 :key="conditionIndex"
                                 class="grid grid-cols-1 sm:grid-cols-[minmax(0,1fr)_minmax(150px,0.6fr)_36px] gap-2 items-end">
                                <label class="block min-w-0">
                                    <span class="block text-xs text-gray-500 dark:text-gray-400 mb-1">验证目标</span>
                                    <select v-model="condition.target"
                                            class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-400">
                                        <option value="" disabled>选择选择器...</option>
                                        <option v-for="(selectorValue, selectorKey) in selectors" :key="selectorKey" :value="selectorKey">
                                            {{ selectorKey }} ({{ selectorValue || '未设置' }})
                                        </option>
                                    </select>
                                </label>
                                <label class="block">
                                    <span class="block text-xs text-gray-500 dark:text-gray-400 mb-1">期望状态</span>
                                    <select v-model="condition.state"
                                            class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-400">
                                        <option value="present">存在</option>
                                        <option value="absent">不存在</option>
                                        <option value="visible">可见</option>
                                        <option value="hidden">不可见</option>
                                    </select>
                                </label>
                                <button @click="removeVerificationCondition(step, conditionIndex)"
                                        type="button" title="删除验证条件"
                                        class="h-9 w-9 inline-flex items-center justify-center rounded-md text-gray-500 hover:text-red-600 hover:bg-red-100 dark:text-gray-400 dark:hover:text-red-400 dark:hover:bg-red-900/30">
                                    <span v-html="$icons.trash"></span>
                                </button>
                            </div>

                            <button @click="addVerificationCondition(step)" type="button"
                                    class="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md border border-gray-300 dark:border-gray-600 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700">
                                <span v-html="$icons.plusCircle"></span>
                                添加验证条件
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

