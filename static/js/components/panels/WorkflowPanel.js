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
            catalogCollapsed: false,
            darkPoolCollapsed: false,
            expandedSteps: {},
            localCatalog: {
                enabled: false,
                source: 'arena_direct',
                modality: 'text',
                include_keywords: [],
                exclude_keywords: [],
                enable_dark_pool: false,
                dark_pool_since: '',
                dark_pool_whitelist_keywords: [],
                dark_pool_blacklist_keywords: []
            },
            catalogLoading: false,
            catalogSaveTimer: null,
            draggedIndex: null,
            dragOverIndex: null,
            dragOverPosition: null,
            catalogKeywordsDraft: {
                include_keywords: '',
                exclude_keywords: '',
                dark_pool_whitelist_keywords: '',
                dark_pool_blacklist_keywords: ''
            }
        };
    },
    computed: {
        isArenaPreset() {
            const domain = String(this.currentDomain || '').trim().toLowerCase();
            return domain === 'arena.ai' || domain.endsWith('.arena.ai');
        }
    },
    watch: {
        workflow: {
            handler() {
                this.syncHintEditorState();
            },
            deep: true,
            immediate: true
        },
        selectedPreset: {
            handler() {
                if (this.catalogSaveTimer) {
                    clearTimeout(this.catalogSaveTimer);
                    this.catalogSaveTimer = null;
                }
                this.expandedHintEditors = {};
                this.expandedExecutionMenus = {};
                this.customKeyModes = {};
                this.expandedJsEditors = {};
                this.expandedJsPreviews = {};
                this.expandedSteps = {};
                this.draggedIndex = null;
                this.dragOverIndex = null;
                this.dragOverPosition = null;
                this.syncHintEditorState();
                this.loadModelCatalog();
            },
            immediate: true
        },
        currentDomain: {
            handler() {
                if (this.catalogSaveTimer) {
                    clearTimeout(this.catalogSaveTimer);
                    this.catalogSaveTimer = null;
                }
                this.loadModelCatalog();
            },
            immediate: true
        }
    },
    mounted() {
        this.loadAvailableScripts(false);
        this.loadModelCatalog();
    },
    beforeUnmount() {
        this.stopEditorBridgePolling();
        if (this.catalogSaveTimer) {
            clearTimeout(this.catalogSaveTimer);
            this.catalogSaveTimer = null;
        }
    },
    methods: {
        toggle() {
            this.$emit('update:collapsed', !this.collapsed);
        },

        hasStepDetail(step, index = null) {
            if (!step) return false;
            if (step.action === 'KEY_PRESS') {
                return index !== null && this.isCustomKeyPreset(index, step);
            }
            return ['JS_EXEC', 'COORD_CLICK', 'COORD_SCROLL', 'READONLY_HINT', 'PAGE_FETCH'].includes(step.action);
        },

        isStepExpanded(index, step = null) {
            if (step && !this.hasStepDetail(step, index)) return false;
            return !!this.expandedSteps[index];
        },

        toggleStepExpand(index, step = null) {
            if (step && !this.hasStepDetail(step, index)) return;
            this.expandedSteps = {
                ...this.expandedSteps,
                [index]: !this.isStepExpanded(index, step)
            };
        },

        expandAllSteps() {
            const next = {};
            (this.workflow || []).forEach((step, idx) => {
                if (this.hasStepDetail(step, idx)) {
                    next[idx] = true;
                }
            });
            this.expandedSteps = next;
        },

        collapseAllSteps() {
            this.expandedSteps = {};
        },

        reorderIndexMap(map, fromIndex, toIndex) {
            if (!map || typeof map !== 'object') return {};
            const newMap = {};
            Object.entries(map).forEach(([key, val]) => {
                const idx = Number(key);
                if (isNaN(idx)) return;
                let nextIdx = idx;
                if (idx === fromIndex) {
                    nextIdx = toIndex;
                } else if (fromIndex < toIndex && idx > fromIndex && idx <= toIndex) {
                    nextIdx = idx - 1;
                } else if (fromIndex > toIndex && idx >= toIndex && idx < fromIndex) {
                    nextIdx = idx + 1;
                }
                newMap[nextIdx] = val;
            });
            return newMap;
        },

        removeIndexFromMap(map, removeIndex) {
            if (!map || typeof map !== 'object') return {};
            const newMap = {};
            Object.entries(map).forEach(([key, val]) => {
                const idx = Number(key);
                if (isNaN(idx) || idx === removeIndex) return;
                const nextIdx = idx > removeIndex ? idx - 1 : idx;
                newMap[nextIdx] = val;
            });
            return newMap;
        },

        handleMoveStep(index, direction) {
            const newIndex = index + direction;
            if (newIndex < 0 || !this.workflow || newIndex >= this.workflow.length) return;
            this.reorderWorkflowStep(index, newIndex, direction > 0 ? 'after' : 'before');
        },

        handleRemoveStep(index) {
            if (Array.isArray(this.workflow)) {
                this.workflow.splice(index, 1);
            }
            this.expandedSteps = this.removeIndexFromMap(this.expandedSteps, index);
            this.expandedJsEditors = this.removeIndexFromMap(this.expandedJsEditors, index);
            this.expandedHintEditors = this.removeIndexFromMap(this.expandedHintEditors, index);
            this.expandedExecutionMenus = this.removeIndexFromMap(this.expandedExecutionMenus, index);
            this.customKeyModes = this.removeIndexFromMap(this.customKeyModes, index);
            this.expandedJsPreviews = this.removeIndexFromMap(this.expandedJsPreviews, index);
        },

        handleDragStart(index, event) {
            this.draggedIndex = index;
            this.dragOverIndex = null;
            this.dragOverPosition = null;
            if (event && event.dataTransfer) {
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('text/plain', String(index));
            }
        },

        handleDragOver(index, event) {
            if (this.draggedIndex === null || this.draggedIndex === index) {
                this.dragOverIndex = null;
                this.dragOverPosition = null;
                return;
            }
            event.preventDefault();
            if (event.dataTransfer) {
                event.dataTransfer.dropEffect = 'move';
            }
            const rect = event.currentTarget.getBoundingClientRect();
            const midY = rect.top + rect.height / 2;
            const pos = event.clientY < midY ? 'before' : 'after';
            this.dragOverIndex = index;
            this.dragOverPosition = pos;
        },

        handleDragLeave(index, event) {
            if (event.currentTarget && !event.currentTarget.contains(event.relatedTarget)) {
                if (this.dragOverIndex === index) {
                    this.dragOverIndex = null;
                    this.dragOverPosition = null;
                }
            }
        },

        handleDrop(targetIndex, event) {
            event.preventDefault();
            const fromIndex = this.draggedIndex;
            const position = this.dragOverPosition || 'before';
            this.draggedIndex = null;
            this.dragOverIndex = null;
            this.dragOverPosition = null;

            if (fromIndex === null || fromIndex === undefined || fromIndex === targetIndex) {
                return;
            }

            this.reorderWorkflowStep(fromIndex, targetIndex, position);
        },

        handleDragEnd() {
            this.draggedIndex = null;
            this.dragOverIndex = null;
            this.dragOverPosition = null;
        },

        reorderWorkflowStep(fromIndex, targetIndex, position) {
            if (!Array.isArray(this.workflow)) return;
            if (fromIndex === targetIndex) return;

            const [movedItem] = this.workflow.splice(fromIndex, 1);
            let insertIndex = targetIndex;
            if (fromIndex < targetIndex) {
                insertIndex = position === 'after' ? targetIndex : targetIndex - 1;
            } else {
                insertIndex = position === 'after' ? targetIndex + 1 : targetIndex;
            }

            if (insertIndex < 0) insertIndex = 0;
            if (insertIndex > this.workflow.length) insertIndex = this.workflow.length;

            this.workflow.splice(insertIndex, 0, movedItem);

            this.expandedSteps = this.reorderIndexMap(this.expandedSteps, fromIndex, insertIndex);
            this.expandedJsEditors = this.reorderIndexMap(this.expandedJsEditors, fromIndex, insertIndex);
            this.expandedHintEditors = this.reorderIndexMap(this.expandedHintEditors, fromIndex, insertIndex);
            this.expandedExecutionMenus = this.reorderIndexMap(this.expandedExecutionMenus, fromIndex, insertIndex);
            this.customKeyModes = this.reorderIndexMap(this.customKeyModes, fromIndex, insertIndex);
            this.expandedJsPreviews = this.reorderIndexMap(this.expandedJsPreviews, fromIndex, insertIndex);
        },



        getStepSummary(step) {
            if (!step) return '';
            switch (step.action) {
                case 'FILL_INPUT':
                    return step.target ? `填入目标: ${step.target}` : '未选择目标输入框';
                case 'SELECT_MODEL':
                    return step.target ? `选择模型器: ${step.target}` : '未选择模型选择器';
                case 'PAGE_FETCH':
                    return '页面直发 prompt 请求';
                case 'CLICK':
                    return step.target ? `点击: ${step.target}` : '未选择点击目标';
                case 'COORD_CLICK':
                    return `坐标 (${step.value?.x ?? 0}, ${step.value?.y ?? 0})`;
                case 'COORD_SCROLL':
                    return `滑动 (${step.value?.start_x ?? 0}, ${step.value?.start_y ?? 0}) → (${step.value?.end_x ?? 0}, ${step.value?.end_y ?? 0})`;
                case 'STREAM_WAIT':
                case 'STREAM_OUTPUT':
                    return step.target ? `等待流式容器: ${step.target}` : '流式等待';
                case 'WAIT':
                    return `等待 ${step.value ?? 0} 秒`;
                case 'KEY_PRESS':
                    return `按键: ${step.target || '未设置'}`;
                case 'JS_EXEC': {
                    const mode = this.getJsMode(-1, step);
                    if (mode === 'file') {
                        const file = this.normalizeScriptTarget(step.target);
                        return file ? `脚本: ${file.split('/').pop()}` : '未选择脚本文件';
                    }
                    const code = String(step.value || '').trim();
                    return code ? `内联: ${code.slice(0, 30)}${code.length > 30 ? '...' : ''}` : '空 JS 代码';
                }
                case 'READONLY_HINT': {
                    const hint = this.normalizeHintStepValue(step);
                    return `提示: ${hint.title || '无标题'}`;
                }
                default:
                    return '';
            }
        },

        normalizeCatalogKeywords(value) {
            const items = Array.isArray(value)
                ? value
                : String(value || '').split(/[\n,]+/);
            return [...new Set(items.map(item => String(item || '').trim()).filter(Boolean))];
        },

        catalogKeywordsText(key) {
            return this.normalizeCatalogKeywords(this.localCatalog && this.localCatalog[key]).join('\n');
        },

        syncCatalogKeywordsDraft(force = false) {
            if (!this.catalogKeywordsDraft) {
                this.catalogKeywordsDraft = {
                    include_keywords: '',
                    exclude_keywords: '',
                    dark_pool_whitelist_keywords: '',
                    dark_pool_blacklist_keywords: ''
                };
            }
            ['include_keywords', 'exclude_keywords', 'dark_pool_whitelist_keywords', 'dark_pool_blacklist_keywords'].forEach(key => {
                const propArr = this.normalizeCatalogKeywords(this.localCatalog && this.localCatalog[key]);
                const draftArr = this.normalizeCatalogKeywords(this.catalogKeywordsDraft[key]);
                const isSame = JSON.stringify(propArr) === JSON.stringify(draftArr);
                if (force || !isSame || this.catalogKeywordsDraft[key] === undefined) {
                    this.catalogKeywordsDraft[key] = propArr.join('\n');
                }
            });
        },

        handleCatalogKeywordsInput(key, rawText) {
            if (!this.catalogKeywordsDraft) {
                this.catalogKeywordsDraft = {
                    include_keywords: '',
                    exclude_keywords: '',
                    dark_pool_whitelist_keywords: '',
                    dark_pool_blacklist_keywords: ''
                };
            }
            this.catalogKeywordsDraft[key] = rawText;
            if (this.catalogSaveTimer) {
                clearTimeout(this.catalogSaveTimer);
                this.catalogSaveTimer = null;
            }
            const targetDomain = this.currentDomain;
            const targetPreset = this.selectedPreset || '主预设';
            this.catalogSaveTimer = setTimeout(() => {
                this.catalogSaveTimer = null;
                if (this.currentDomain !== targetDomain || (this.selectedPreset || '主预设') !== targetPreset) {
                    return;
                }
                this.saveModelCatalog({
                    [key]: this.normalizeCatalogKeywords(rawText)
                }, targetPreset, targetDomain);
            }, 300);
        },

        async loadModelCatalog() {
            if (!this.isArenaPreset || !this.currentDomain) return;
            this.catalogLoading = true;
            try {
                const domain = this.currentDomain;
                const preset = this.selectedPreset || '主预设';
                const res = await this.authJsonRequest(
                    '/api/sites/' + encodeURIComponent(domain) + '/model-catalog?preset_name=' + encodeURIComponent(preset)
                );
                if (res && res.catalog && domain === this.currentDomain && preset === (this.selectedPreset || '主预设')) {
                    this.localCatalog = {
                        enabled: false,
                        source: 'arena_direct',
                        modality: 'text',
                        include_keywords: [],
                        exclude_keywords: [],
                        enable_dark_pool: false,
                        dark_pool_since: '',
                        dark_pool_whitelist_keywords: [],
                        dark_pool_blacklist_keywords: [],
                        ...res.catalog
                    };
                    this.syncCatalogKeywordsDraft(true);
                }
            } catch (e) {
                console.warn('[WorkflowPanel] 加载独立模型目录配置失败:', e);
            } finally {
                this.catalogLoading = false;
            }
        },

        async saveModelCatalog(patch = {}, targetPreset = null, targetDomain = null) {
            const domain = targetDomain || this.currentDomain;
            const preset = targetPreset || this.selectedPreset || '主预设';
            if (!this.isArenaPreset || !domain) return;
            const isCurrent = domain === this.currentDomain && preset === (this.selectedPreset || '主预设');
            if (isCurrent) {
                this.localCatalog = {
                    enabled: false,
                    source: 'arena_direct',
                    include_keywords: [],
                    exclude_keywords: [],
                    enable_dark_pool: false,
                    dark_pool_since: '',
                    dark_pool_whitelist_keywords: [],
                    dark_pool_blacklist_keywords: [],
                    ...(this.localCatalog || {}),
                    ...(patch || {})
                };
                this.syncCatalogKeywordsDraft(false);
            }
            try {
                const payloadCatalog = isCurrent
                    ? this.localCatalog
                    : {
                        enabled: false,
                        source: 'arena_direct',
                        include_keywords: [],
                        exclude_keywords: [],
                        enable_dark_pool: false,
                        dark_pool_since: '',
                        dark_pool_whitelist_keywords: [],
                        dark_pool_blacklist_keywords: [],
                        ...(patch || {})
                    };
                await this.authJsonRequest('/api/sites/' + encodeURIComponent(domain) + '/model-catalog', {
                    method: 'PUT',
                    body: JSON.stringify({
                        preset_name: preset,
                        catalog: payloadCatalog
                    })
                });
                if (isCurrent) {
                    this.$emit('update:modelCatalog', { ...this.localCatalog });
                }
            } catch (e) {
                console.error('[WorkflowPanel] 保存模型目录配置失败:', e);
            }
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
                this.expandedSteps = {
                    ...this.expandedSteps,
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
                this.expandedSteps = {
                    ...this.expandedSteps,
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

        getJsLifecycle(step) {
            const execution = step && step.execution && typeof step.execution === 'object'
                ? step.execution : {};
            const value = String(execution.lifecycle || execution.script_lifecycle || 'workflow').toLowerCase();
            return ['workflow', 'resident', 'step'].includes(value) ? value : 'workflow';
        },

        setJsLifecycle(step, lifecycle) {
            if (!step) return;
            if (!step.execution || typeof step.execution !== 'object') step.execution = {};
            step.execution.lifecycle = ['workflow', 'resident', 'step'].includes(lifecycle) ? lifecycle : 'workflow';
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
        },

                getCoordProp(step, key, defaultVal = '') {
            if (!step || !step.value || typeof step.value !== 'object') {
                return defaultVal;
            }
            const val = step.value[key];
            return (val !== undefined && val !== null) ? val : defaultVal;
        },

        updateCoordValue(step, key, val) {
            const base = (step.value && typeof step.value === 'object' && !Array.isArray(step.value)) ? step.value : {};
            const num = val === '' ? '' : Number(val);
            step.value = { ...base, [key]: isNaN(num) ? val : num };
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
                <!-- 页面模型目录 (支持折叠) -->
                <div v-if="isArenaPreset" class="border border-gray-200 dark:border-gray-700 rounded-lg bg-gray-50/40 dark:bg-gray-900/20 overflow-hidden">
                    <div class="px-3.5 py-2.5 flex items-center justify-between gap-3 cursor-pointer hover:bg-gray-100/60 dark:hover:bg-gray-800/60 transition-colors select-none"
                         @click="catalogCollapsed = !catalogCollapsed">
                        <div class="flex items-center gap-2 min-w-0">
                            <span class="w-4 inline-flex justify-center text-gray-500 dark:text-gray-400"
                                  v-html="catalogCollapsed ? $icons.chevronDown : $icons.chevronUp"></span>
                            <div class="flex items-center gap-2 flex-wrap">
                                <span class="text-sm font-semibold text-gray-900 dark:text-white">页面模型目录</span>
                                <span :class="[
                                    'px-1.5 py-0.5 rounded text-[11px] font-medium',
                                    localCatalog.enabled
                                        ? 'bg-emerald-100 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300 border border-emerald-300 dark:border-emerald-800'
                                        : 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 border dark:border-gray-700'
                                ]">
                                    {{ localCatalog.enabled ? '已启用' : '未启用' }}
                                </span>
                            </div>
                        </div>
                        <div class="flex items-center gap-3" @click.stop>
                            <label class="inline-flex items-center gap-1.5 text-xs font-medium text-gray-700 dark:text-gray-300 cursor-pointer">
                                <input type="checkbox"
                                       class="rounded text-emerald-600 focus:ring-emerald-500"
                                       :checked="!!localCatalog.enabled"
                                       @change="saveModelCatalog({ enabled: $event.target.checked })">
                                <span>启用目录</span>
                            </label>
                        </div>
                    </div>

                    <div class="px-3.5 pb-2 text-xs text-gray-500 dark:text-gray-400">
                        独立持久化存储。启用后，此预设负责读取页面模型、过滤列表，并在请求时切换模型。
                    </div>
                    <div v-show="!catalogCollapsed && localCatalog.enabled" class="px-3.5 pb-3.5 pt-1 space-y-3 border-t border-gray-200/70 dark:border-gray-700/70">
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-3 pt-2">
                            <label class="block min-w-0">
                                <span class="block text-xs font-medium text-gray-600 dark:text-gray-300 mb-1">仅保留关键词（可选，每行一个）</span>
                                <textarea :value="catalogKeywordsDraft ? catalogKeywordsDraft.include_keywords : ''"
                                          @input="handleCatalogKeywordsInput('include_keywords', $event.target.value)"
                                          rows="3"
                                          spellcheck="false"
                                          class="w-full rounded-md border dark:border-gray-600 px-3 py-2 font-mono text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white resize-y focus:outline-none focus:ring-2 focus:ring-blue-400"
                                          placeholder="glm&#10;claude"></textarea>
                            </label>
                            <label class="block min-w-0">
                                <span class="block text-xs font-medium text-gray-600 dark:text-gray-300 mb-1">排除关键词（每行一个）</span>
                                <textarea :value="catalogKeywordsDraft ? catalogKeywordsDraft.exclude_keywords : ''"
                                          @input="handleCatalogKeywordsInput('exclude_keywords', $event.target.value)"
                                          rows="3"
                                          spellcheck="false"
                                          class="w-full rounded-md border dark:border-gray-600 px-3 py-2 font-mono text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white resize-y focus:outline-none focus:ring-2 focus:ring-blue-400"
                                          placeholder="image&#10;preview&#10;legacy"></textarea>
                            </label>
                        </div>

                        <!-- 暗池模型配置区块 (支持折叠) -->
                        <div class="border border-purple-200/80 dark:border-purple-800/60 rounded-lg bg-purple-50/30 dark:bg-purple-950/15 overflow-hidden">
                            <div class="px-3 py-2 flex items-center justify-between gap-3 cursor-pointer hover:bg-purple-100/40 dark:hover:bg-purple-900/30 transition-colors select-none"
                                 @click="darkPoolCollapsed = !darkPoolCollapsed">
                                <div class="flex items-center gap-2 min-w-0">
                                    <span class="w-3.5 inline-flex justify-center text-purple-500 dark:text-purple-400 text-xs"
                                          v-html="darkPoolCollapsed ? $icons.chevronDown : $icons.chevronUp"></span>
                                    <div class="flex items-center gap-2 flex-wrap">
                                        <span class="text-xs font-semibold text-purple-700 dark:text-purple-300">暗池模型池 (Dark Pool)</span>
                                        <span :class="[
                                            'px-1.5 py-0.2 rounded text-[10px] font-medium',
                                            localCatalog.enable_dark_pool
                                                ? 'bg-purple-100 dark:bg-purple-900/60 text-purple-700 dark:text-purple-300 border border-purple-300 dark:border-purple-700'
                                                : 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 border dark:border-gray-700'
                                        ]">
                                            {{ localCatalog.enable_dark_pool ? '已加入' : '未加入' }}
                                        </span>
                                    </div>
                                </div>
                                <div class="flex items-center gap-3" @click.stop>
                                    <label class="inline-flex items-center gap-1.5 text-xs font-medium text-purple-700 dark:text-purple-300 cursor-pointer">
                                        <input type="checkbox"
                                               class="rounded text-purple-600 focus:ring-purple-500"
                                               :checked="!!localCatalog.enable_dark_pool"
                                               @change="saveModelCatalog({ enable_dark_pool: $event.target.checked })">
                                        <span>加入暗池模型</span>
                                    </label>
                                </div>
                            </div>
                            <div class="px-3 pb-2 text-[11px] text-gray-500 dark:text-gray-400">
                                聚合离线模型元数据与页面抓取隐藏模型。明池模型不受此规则影响。
                            </div>

                            <div v-show="!darkPoolCollapsed && localCatalog.enable_dark_pool"
                                 class="px-3 pb-3 pt-2 space-y-3 border-t border-purple-200/50 dark:border-purple-800/40">
                                <div>
                                    <label class="inline-flex items-center gap-2 text-xs text-gray-700 dark:text-gray-300 flex-wrap">
                                        <span class="font-medium">暗池起始日期（在此日期之后入库的模型放行）：</span>
                                        <input type="date"
                                               :value="localCatalog.dark_pool_since || ''"
                                               @change="saveModelCatalog({ dark_pool_since: $event.target.value })"
                                               class="rounded border dark:border-gray-600 px-2 py-1 text-xs bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-purple-400">
                                        <span class="text-gray-400 dark:text-gray-500 text-[11px]">留空则不限制日期</span>
                                    </label>
                                </div>

                                <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                                    <label class="block min-w-0">
                                        <span class="block text-xs font-medium text-gray-600 dark:text-gray-300 mb-1">暗池白名单（命中直接放行，每行一个）</span>
                                        <textarea :value="catalogKeywordsDraft ? catalogKeywordsDraft.dark_pool_whitelist_keywords : ''"
                                                  @input="handleCatalogKeywordsInput('dark_pool_whitelist_keywords', $event.target.value)"
                                                  rows="3"
                                                  spellcheck="false"
                                                  class="w-full rounded-md border dark:border-gray-600 px-3 py-2 font-mono text-xs bg-white dark:bg-gray-700 text-gray-900 dark:text-white resize-y focus:outline-none focus:ring-2 focus:ring-purple-400"
                                                  placeholder="deepseek&#10;claude"></textarea>
                                    </label>
                                    <label class="block min-w-0">
                                        <span class="block text-xs font-medium text-gray-600 dark:text-gray-300 mb-1">暗池黑名单（命中最终否决，每行一个）</span>
                                        <textarea :value="catalogKeywordsDraft ? catalogKeywordsDraft.dark_pool_blacklist_keywords : ''"
                                                  @input="handleCatalogKeywordsInput('dark_pool_blacklist_keywords', $event.target.value)"
                                                  rows="3"
                                                  spellcheck="false"
                                                  class="w-full rounded-md border dark:border-gray-600 px-3 py-2 font-mono text-xs bg-white dark:bg-gray-700 text-gray-900 dark:text-white resize-y focus:outline-none focus:ring-2 focus:ring-purple-400"
                                                  placeholder="internal&#10;test&#10;dlp"></textarea>
                                    </label>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- 步骤列表顶部控制条 -->
                <div v-if="workflow.length > 0" class="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400 px-1 pt-1">
                    <span class="font-medium text-gray-600 dark:text-gray-300">执行步骤 ({{ workflow.length }})</span>
                    <div class="flex items-center gap-2">
                        <button type="button" @click="expandAllSteps" class="hover:text-blue-500 transition-colors">全部展开</button>
                        <span>·</span>
                        <button type="button" @click="collapseAllSteps" class="hover:text-blue-500 transition-colors">全部折叠</button>
                    </div>
                </div>
                <!-- 步骤卡片 (所有步骤展开前高度严格一致) -->
                <div v-for="(step, index) in workflow" :key="'step-' + index"
                     @dragover="handleDragOver(index, $event)"
                     @dragleave="handleDragLeave(index, $event)"
                     @drop="handleDrop(index, $event)"
                     :class="[
                         'border rounded-lg transition-colors overflow-hidden relative',
                         step.action === 'READONLY_HINT'
                             ? getHintStepClasses(step)
                             : 'border-gray-200 dark:border-gray-700 hover:border-blue-300 dark:hover:border-blue-600 bg-gray-50/50 dark:bg-gray-900/30',
                         draggedIndex === index ? 'opacity-40 border-dashed border-blue-400 dark:border-blue-500 bg-blue-50/20 scale-[0.99]' : '',
                         dragOverIndex === index && dragOverPosition === 'before' ? 'border-t-2 border-t-blue-500 shadow-sm' : '',
                         dragOverIndex === index && dragOverPosition === 'after' ? 'border-b-2 border-b-blue-500 shadow-sm' : ''
                     ]">
                    <!-- 步骤卡片头部 (统一高度 h-12，所有步骤展开前高度完全一致) -->
                    <div class="h-12 px-3 py-1.5 flex items-center gap-2.5 flex-nowrap"
                         :class="isStepExpanded(index, step) && hasStepDetail(step, index) ? 'border-b border-gray-200/80 dark:border-gray-700/80 bg-white/40 dark:bg-gray-800/40' : ''">

                        <!-- 步骤序号、拖拽抓手与上下移动 -->
                        <div class="flex items-center gap-1.5 flex-shrink-0">
                            <!-- 拖拽抓手 -->
                            <div draggable="true"
                                 @dragstart="handleDragStart(index, $event)"
                                 @dragend="handleDragEnd"
                                 title="按住拖拽调整步骤顺序"
                                 class="cursor-grab active:cursor-grabbing p-1 -ml-0.5 text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 rounded hover:bg-gray-200/60 dark:hover:bg-gray-700/60 transition-colors flex items-center justify-center select-none">
                                <span class="w-3.5 h-3.5 inline-flex items-center justify-center text-gray-400 dark:text-gray-500" v-html="$icons.gripVertical"></span>
                            </div>

                            <!-- 步骤序号 -->
                            <span class="text-xs font-bold text-gray-600 dark:text-gray-300 w-6 h-6 flex items-center justify-center bg-gray-200 dark:bg-gray-700 rounded-full select-none">{{ index + 1 }}</span>

                            <!-- 上下微调按钮 -->
                            <div class="flex items-center">
                                <button @click="handleMoveStep(index, -1)" :disabled="index === 0"
                                        title="上移步骤"
                                        :class="['p-0.5 rounded transition-all duration-150', index === 0 ? 'text-gray-300 dark:text-gray-600 cursor-not-allowed' : 'text-gray-500 dark:text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-900/40 active:scale-95']">
                                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5"/></svg>
                                </button>
                                <button @click="handleMoveStep(index, 1)" :disabled="index === workflow.length - 1"
                                        title="下移步骤"
                                        :class="['p-0.5 rounded transition-all duration-150', index === workflow.length - 1 ? 'text-gray-300 dark:text-gray-600 cursor-not-allowed' : 'text-gray-500 dark:text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-900/40 active:scale-95']">
                                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5"/></svg>
                                </button>
                            </div>
                        </div>

                        <!-- 动作选择下拉框 (全部动作统一保留) -->
                        <div v-if="step.action !== 'READONLY_HINT'" class="w-32 sm:w-36 flex-shrink-0">
                            <select v-model="step.action" @change="$emit('action-change', step)"
                                    class="border dark:border-gray-600 px-2 py-1 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-full text-xs sm:text-sm h-8 bg-white dark:bg-gray-700 text-gray-900 dark:text-white">
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

                        <!-- 中间参数/摘要区 (单行，高度 h-8) -->
                        <div class="flex-1 min-w-0 flex items-center">
                            <!-- 选择器类 -->
                            <select v-if="['FILL_INPUT', 'SELECT_MODEL', 'CLICK', 'STREAM_WAIT', 'STREAM_OUTPUT'].includes(step.action)"
                                    v-model="step.target"
                                    class="border dark:border-gray-600 px-2 py-1 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-full text-xs sm:text-sm h-8 bg-white dark:bg-gray-700 text-gray-900 dark:text-white truncate">
                                <option value="" disabled>选择选择器...</option>
                                <option v-for="(v, k) in selectors" :key="k" :value="k">{{ k }} ({{ v || '未设置' }})</option>
                            </select>

                            <!-- WAIT 等待 -->
                            <div v-else-if="step.action === 'WAIT'" class="flex items-center gap-1.5">
                                <input v-model.number="step.value" type="number" step="0.1" min="0"
                                       class="border dark:border-gray-600 px-2 py-1 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-24 text-xs sm:text-sm h-8 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                                       placeholder="秒数">
                                <span class="text-xs text-gray-500 dark:text-gray-400">秒</span>
                            </div>

                            <!-- KEY_PRESS 按键 -->
                            <div v-else-if="step.action === 'KEY_PRESS'" class="w-full flex items-center gap-2">
                                <select :value="getKeyPresetValue(index, step)"
                                        @change="applyKeyPreset(index, step, $event.target.value)"
                                        class="border dark:border-gray-600 px-2 py-1 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-full text-xs sm:text-sm h-8 bg-white dark:bg-gray-700 text-gray-900 dark:text-white truncate">
                                    <option value="">选择常用按键/组合键...</option>
                                    <option v-for="preset in keyPresets" :key="preset.value" :value="preset.value">{{ preset.label }}</option>
                                    <option value="__custom__">自定义: {{ step.target || '未输入' }}</option>
                                </select>
                                <button v-if="isCustomKeyPreset(index, step)"
                                        type="button"
                                        @click="toggleStepExpand(index, step)"
                                        class="text-[11px] text-blue-600 dark:text-blue-400 hover:underline flex-shrink-0 px-1">
                                    {{ isStepExpanded(index, step) ? '收起' : '展开输入' }}
                                </button>
                            </div>

                            <!-- JS_EXEC 执行 JavaScript (单行胶囊与摘要，点击整行展开/收起) -->
                            <div v-else-if="step.action === 'JS_EXEC'"
                                 @click="toggleStepExpand(index, step)"
                                 class="w-full flex items-center gap-2 cursor-pointer group py-0.5 min-w-0">
                                <span :class="[
                                    'px-2 py-0.5 rounded text-[11px] font-medium flex-shrink-0',
                                    getJsMode(index, step) === 'file'
                                        ? 'bg-blue-100 dark:bg-blue-950/60 text-blue-700 dark:text-blue-300 border border-blue-300 dark:border-blue-800'
                                        : 'bg-amber-100 dark:bg-amber-950/60 text-amber-700 dark:text-amber-300 border border-amber-300 dark:border-amber-800'
                                ]">
                                    {{ getJsMode(index, step) === 'file' ? '外部脚本' : '内联代码' }}
                                </span>
                                <span class="text-xs text-gray-700 dark:text-gray-300 font-mono truncate flex-1 group-hover:text-blue-500 transition-colors">
                                    {{ getStepSummary(step) }}
                                </span>
                                <span class="text-[11px] text-gray-400 dark:text-gray-500 flex-shrink-0 group-hover:text-blue-400">
                                    {{ isStepExpanded(index, step) ? '收起' : '展开配置' }}
                                </span>
                            </div>

                            <!-- COORD_CLICK 坐标点击 (点击整行展开/收起) -->
                            <div v-else-if="step.action === 'COORD_CLICK'"
                                 @click="toggleStepExpand(index, step)"
                                 class="w-full flex items-center gap-2 cursor-pointer group min-w-0">
                                <span class="text-xs font-mono text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700/60 px-2 py-1 rounded border dark:border-gray-600 truncate">
                                    X: {{ getCoordProp(step, 'x', 0) }}, Y: {{ getCoordProp(step, 'y', 0) }} (半径: {{ getCoordProp(step, 'random_radius', 0) }})
                                </span>
                                <span class="text-[11px] text-gray-400 dark:text-gray-500 flex-shrink-0 group-hover:text-blue-400">
                                    {{ isStepExpanded(index, step) ? '收起' : '展开编辑' }}
                                </span>
                            </div>

                            <!-- COORD_SCROLL 模拟滑动 (点击整行展开/收起) -->
                            <div v-else-if="step.action === 'COORD_SCROLL'"
                                 @click="toggleStepExpand(index, step)"
                                 class="w-full flex items-center gap-2 cursor-pointer group min-w-0">
                                <span class="text-xs font-mono text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700/60 px-2 py-1 rounded border dark:border-gray-600 truncate">
                                    ({{ getCoordProp(step, 'start_x', 0) }}, {{ getCoordProp(step, 'start_y', 0) }}) → ({{ getCoordProp(step, 'end_x', 0) }}, {{ getCoordProp(step, 'end_y', 0) }})
                                </span>
                                <span class="text-[11px] text-gray-400 dark:text-gray-500 flex-shrink-0 group-hover:text-blue-400">
                                    {{ isStepExpanded(index, step) ? '收起' : '展开编辑' }}
                                </span>
                            </div>

                            <!-- PAGE_FETCH 页面直发 (点击整行展开/收起) -->
                            <div v-else-if="step.action === 'PAGE_FETCH'"
                                 @click="toggleStepExpand(index, step)"
                                 class="w-full flex items-center gap-2 cursor-pointer group min-w-0">
                                <span class="px-2 py-0.5 rounded text-[11px] font-medium bg-purple-100 dark:bg-purple-950/60 text-purple-700 dark:text-purple-300 border border-purple-300 dark:border-purple-800">
                                    页面直发
                                </span>
                                <span class="text-xs text-gray-500 dark:text-gray-400 truncate">使用当前预设直接在页面发请求</span>
                                <span class="text-[11px] text-gray-400 dark:text-gray-500 flex-shrink-0 group-hover:text-blue-400">
                                    {{ isStepExpanded(index, step) ? '收起' : '说明' }}
                                </span>
                            </div>

                            <!-- READONLY_HINT 只读提示 (点击整行展开/收起) -->
                            <div v-else-if="step.action === 'READONLY_HINT'"
                                 @click="toggleStepExpand(index, step)"
                                 class="w-full flex items-center gap-2 cursor-pointer group min-w-0">
                                <span class="text-xs font-semibold text-gray-800 dark:text-gray-200">
                                    {{ normalizeHintStepValue(step).title || '提示' }}
                                </span>
                                <span class="text-xs text-gray-500 dark:text-gray-400 truncate flex-1">
                                    {{ normalizeHintStepValue(step).text || '展示只读提示' }}
                                </span>
                                <span class="text-[11px] text-gray-400 flex-shrink-0 group-hover:text-blue-400">
                                    {{ isStepExpanded(index, step) ? '收起编辑' : '展开编辑' }}
                                </span>
                            </div>
                        </div>

                        <!-- 右侧操作区 -->
                        <div class="flex items-center gap-2 flex-shrink-0">
                            <div v-if="!['READONLY_HINT', 'PAGE_FETCH'].includes(step.action)">
                                <label class="flex items-center text-xs cursor-pointer whitespace-nowrap text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white transition-colors"
                                       title="勾选后找不到元素会报错；不勾选则跳过该步骤">
                                    <input type="checkbox"
                                           :checked="!step.optional"
                                           @change="step.optional = !$event.target.checked"
                                           class="mr-1 rounded">
                                    <span class="hidden sm:inline">必需步骤</span>
                                    <span class="sm:hidden">必需</span>
                                </label>
                            </div>

                            <button v-if="step.action === 'CLICK'"
                                    @click="toggleExecutionMenu(index, step)"
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

                            <button @click="handleRemoveStep(index)"
                                    title="删除该步骤"
                                    class="p-1.5 rounded-md transition-all duration-150 text-gray-400 hover:text-red-600 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/30 active:scale-95">
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
                                </svg>
                            </button>
                        </div>
                    </div>

                    <!-- 步骤展开详细编辑区 (Body, 仅在具有详细编辑项的步骤且展开时显示) -->
                    <div v-show="isStepExpanded(index, step) && hasStepDetail(step, index)" class="p-3.5 space-y-3 bg-white/60 dark:bg-gray-800/60 border-t border-gray-100 dark:border-gray-700/60">
                        <!-- JS_EXEC 展开详细配置 -->
                        <div v-if="step.action === 'JS_EXEC'" class="space-y-3">
                            <!-- 模式选择器与生命周期 -->
                            <div class="flex items-center justify-between gap-3 flex-wrap border-b dark:border-gray-700/60 pb-2.5">
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
                            </div>

                            <!-- 外部脚本模式详细配置 -->
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

                                <!-- 宏变量快捷插入器与运行入参 -->
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

                            <!-- 内联代码模式详细配置 -->
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
                                          rows="6"
                                          class="w-full rounded-md border dark:border-gray-600 px-3 py-2 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent bg-white dark:bg-gray-700 text-gray-900 dark:text-white resize-y min-h-[8rem]"
                                          spellcheck="false"
                                          placeholder="return document.title;"></textarea>
                            </div>
                        </div>

                        <!-- COORD_CLICK 展开详细参数 -->
                        <div v-else-if="step.action === 'COORD_CLICK'" class="space-y-2">
                            <div class="text-xs font-semibold text-gray-700 dark:text-gray-300">点击坐标参数</div>
                            <div class="flex items-center gap-2 flex-wrap">
                                <label class="text-xs text-gray-600 dark:text-gray-400">X:
                                    <input :value="getCoordProp(step, 'x', '')"
                                           @input="updateCoordValue(step, 'x', $event.target.value)"
                                           type="number" step="1"
                                           class="border dark:border-gray-600 px-2 py-1 rounded-md w-24 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                                           placeholder="X viewport">
                                </label>
                                <label class="text-xs text-gray-600 dark:text-gray-400">Y:
                                    <input :value="getCoordProp(step, 'y', '')"
                                           @input="updateCoordValue(step, 'y', $event.target.value)"
                                           type="number" step="1"
                                           class="border dark:border-gray-600 px-2 py-1 rounded-md w-24 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                                           placeholder="Y viewport">
                                </label>
                                <label class="text-xs text-gray-600 dark:text-gray-400">随机半径:
                                    <input :value="getCoordProp(step, 'random_radius', 0)"
                                           @input="updateCoordValue(step, 'random_radius', $event.target.value)"
                                           type="number" min="0" step="1"
                                           class="border dark:border-gray-600 px-2 py-1 rounded-md w-24 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                                           placeholder="半径">
                                </label>
                            </div>
                            <div class="text-xs text-gray-500 dark:text-gray-400">
                                使用 viewport CSS 坐标，不是屏幕坐标。
                            </div>
                        </div>

                        <!-- COORD_SCROLL 展开详细参数 -->
                        <div v-else-if="step.action === 'COORD_SCROLL'" class="space-y-2">
                            <div class="text-xs font-semibold text-gray-700 dark:text-gray-300">模拟滑动参数</div>
                            <div class="flex items-center gap-2 flex-wrap">
                                <label class="text-xs text-gray-600 dark:text-gray-400">起点 X:
                                    <input :value="getCoordProp(step, 'start_x', '')"
                                           @input="updateCoordValue(step, 'start_x', $event.target.value)"
                                           type="number" step="1"
                                           class="border dark:border-gray-600 px-2 py-1 rounded-md w-24 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                                           placeholder="起点 X">
                                </label>
                                <label class="text-xs text-gray-600 dark:text-gray-400">起点 Y:
                                    <input :value="getCoordProp(step, 'start_y', '')"
                                           @input="updateCoordValue(step, 'start_y', $event.target.value)"
                                           type="number" step="1"
                                           class="border dark:border-gray-600 px-2 py-1 rounded-md w-24 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                                           placeholder="起点 Y">
                                </label>
                                <label class="text-xs text-gray-600 dark:text-gray-400">终点 X:
                                    <input :value="getCoordProp(step, 'end_x', '')"
                                           @input="updateCoordValue(step, 'end_x', $event.target.value)"
                                           type="number" step="1"
                                           class="border dark:border-gray-600 px-2 py-1 rounded-md w-24 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                                           placeholder="终点 X">
                                </label>
                                <label class="text-xs text-gray-600 dark:text-gray-400">终点 Y:
                                    <input :value="getCoordProp(step, 'end_y', '')"
                                           @input="updateCoordValue(step, 'end_y', $event.target.value)"
                                           type="number" step="1"
                                           class="border dark:border-gray-600 px-2 py-1 rounded-md w-24 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                                           placeholder="终点 Y">
                                </label>
                            </div>
                            <div class="text-xs text-gray-500 dark:text-gray-400">
                                使用 viewport CSS 坐标。普通模式直接派发滚轮，低熵模式会按站点 stealth 配置走人类化轨迹。
                            </div>
                        </div>

                        <!-- KEY_PRESS 自定义按键展开 -->
                        <div v-else-if="step.action === 'KEY_PRESS'" class="space-y-2">
                            <div class="text-xs font-semibold text-gray-700 dark:text-gray-300">自定义按键输入</div>
                            <input v-model="step.target"
                                   list="workflow-key-presets"
                                   placeholder="例如: Enter / Ctrl+Enter / Ctrl+Shift+P"
                                   class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 w-full text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white">
                            <div class="text-xs text-gray-500 dark:text-gray-400">
                                支持手输任意按键或组合键。
                            </div>
                        </div>

                        <!-- READONLY_HINT 只读提示编辑 -->
                        <div v-else-if="step.action === 'READONLY_HINT'" class="space-y-3">
                            <div class="grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_180px] gap-3">
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">标题</label>
                                    <input :value="normalizeHintStepValue(step).title"
                                           @input="step.value = { ...normalizeHintStepValue(step), title: $event.target.value }"
                                           type="text"
                                           class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-400"
                                           placeholder="例如：实验功能说明">
                                </div>
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">语气</label>
                                    <select :value="normalizeHintStepValue(step).tone"
                                            @change="step.value = { ...normalizeHintStepValue(step), tone: $event.target.value }"
                                            class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-400">
                                        <option v-for="tone in hintToneOptions" :key="tone.value" :value="tone.value">{{ tone.label }}</option>
                                    </select>
                                </div>
                            </div>
                            <div>
                                <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">正文</label>
                                <textarea :value="normalizeHintStepValue(step).text"
                                          @input="step.value = { ...normalizeHintStepValue(step), text: $event.target.value }"
                                          rows="3"
                                          class="w-full rounded-md border dark:border-gray-600 px-3 py-2 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white resize-y focus:outline-none focus:ring-2 focus:ring-blue-400"
                                          placeholder="这里填写给用户看的只读提示内容。"></textarea>
                            </div>
                        </div>

                        <!-- PAGE_FETCH 说明 -->
                        <div v-else-if="step.action === 'PAGE_FETCH'" class="space-y-2">
                            <div class="uwa-wf-note rounded-md px-3 py-2 text-sm leading-6">
                                使用当前预设的页面直发配置发送已构造的 prompt。失败且回退模式为工作流时，会继续执行后续填入 / 按键 / 等待步骤。
                            </div>
                        </div>
                    </div>

                    <!-- CLICK 执行设置面板 (独立折叠区) -->
                    <div v-if="step.action === 'CLICK' && isExecutionExpanded(index, step)"
                         class="p-3.5 space-y-4 bg-gray-50/80 dark:bg-gray-900/50 border-t border-gray-200 dark:border-gray-700">
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


