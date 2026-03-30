// ==================== CommandsTab Methods ====================
window.CommandsTabMethods = {
        async apiRequest(url, options) {
            const token = localStorage.getItem('api_token');
            const headers = { 'Content-Type': 'application/json', ...(options || {}).headers };
            if (token) headers['Authorization'] = 'Bearer ' + token;
            const response = await fetch(url, { ...options, headers });
            if (!response.ok) {
                const err = await response.json().catch(() => ({}));
                const detail = err.detail;
                let message = 'HTTP ' + response.status;
                if (typeof detail === 'string' && detail) {
                    message = detail;
                } else if (detail && typeof detail === 'object') {
                    try {
                        message = JSON.stringify(detail, null, 2);
                    } catch (_) {
                        message = String(detail);
                    }
                }
                throw new Error(message);
            }
            return response.json();
        },

        async fetchCommands() {
            this.loading = true;
            try {
                const data = await this.apiRequest('/api/commands');
                this.commands = (data.commands || []).map(cmd => this.normalizeCommand(cmd));
                const validIds = new Set(this.commands.map(cmd => cmd.id));
                this.selectedCommandIds = (this.selectedCommandIds || []).filter(id => validIds.has(id));
                this.syncGroupCollapseState();
                this.syncSourceCommandPickerState();
                const hasExistingSelection = (this.commandGroups || []).some(group => group.name === this.selectedExistingGroupName);
                if (!hasExistingSelection) {
                    this.selectedExistingGroupName = this.commandGroups[0]?.name || '';
                }
                this.clearGroupDragState();
                this.ensureValidPage();
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '加载命令失败: ' + e.message });
            } finally {
                this.loading = false;
            }
        },

        normalizeAction(action, index = 0) {
            const next = { ...(action || {}) };
            if (!next.action_id) {
                next.action_id = 'step_' + (index + 1);
            }
            if (next.type === 'switch_preset') {
                next.type = 'execute_preset';
            }
            if (next.type === 'execute_workflow' && next.prompt === undefined) {
                next.prompt = '';
            }
            if (['execute_preset', 'execute_workflow'].includes(next.type)) {
                next.preset_name = this.normalizePresetActionValue(next.preset_name);
            }
            this.initClickAction(next);
            this.initProxyAction(next);
            this.initWebhookAction(next);
            this.initNapcatAction(next);
            this.initCommandGroupAction(next);
            this.initReleaseLockAction(next);
            return next;
        },

        getFollowDefaultPresetValue() {
            return '__DEFAULT__';
        },

        normalizePresetActionValue(value) {
            const normalized = String(value || '').trim();
            return normalized || this.getFollowDefaultPresetValue();
        },

        getFollowDefaultPresetLabel() {
            return '跟随站点默认预设';
        },

        normalizeCommandLogLevel(level) {
            const normalized = String(level || 'GLOBAL').trim().toUpperCase();
            return ['GLOBAL', 'DEBUG', 'INFO', 'WARNING', 'ERROR'].includes(normalized)
                ? normalized
                : 'GLOBAL';
        },

        normalizeCommand(command) {
            const normalized = JSON.parse(JSON.stringify(command || {}));
            normalized.trigger = normalized.trigger || {
                type: 'request_count',
                value: 10,
                command_id: '',
                command_ids: [],
                listen_all_commands: false,
                informative_only: true,
                action_ref: '',
                match_rule: 'equals',
                expected_value: '',
                match_mode: 'keyword',
                status_codes: '403,429,500,502,503,504',
                abort_on_match: true,
                scope: 'all',
                domain: '',
                tab_index: null,
                priority: 2,
                fire_mode: 'edge',
                cooldown_sec: 0,
                stable_for_sec: 0,
                check_while_busy_workflow: true,
                allow_during_workflow: false,
                interrupt_policy: 'auto',
                interrupt_message: '',
                probe_js: '',
                periodic_enabled: true,
                periodic_interval_sec: 8,
                periodic_jitter_sec: 2
            };
            normalized.trigger = this.ensureTriggerDefaults(normalized.trigger);
            if (normalized.stop_on_error === undefined) {
                normalized.stop_on_error = false;
            }
            if (normalized.log_enabled === undefined) {
                normalized.log_enabled = true;
            } else {
                normalized.log_enabled = !!normalized.log_enabled;
            }
            normalized.log_level = this.normalizeCommandLogLevel(normalized.log_level);
            if (normalized.trigger.command_id === undefined) {
                normalized.trigger.command_id = '';
            }
            if (normalized.group_name === undefined || normalized.group_name === null) {
                normalized.group_name = '';
            } else {
                normalized.group_name = String(normalized.group_name).trim();
            }
            normalized.actions = (normalized.actions || []).map((action, index) => this.normalizeAction(action, index));
            return normalized;
        },

        ensureTriggerDefaults(trigger) {
            const next = { ...(trigger || {}) };
            if (next.command_id === undefined) next.command_id = '';
            if (!Array.isArray(next.command_ids)) {
                if (typeof next.command_ids === 'string' && next.command_ids.trim()) {
                    next.command_ids = next.command_ids.split(',').map(item => item.trim()).filter(Boolean);
                } else {
                    next.command_ids = [];
                }
            }
            if (next.listen_all_commands === undefined) next.listen_all_commands = false;
            if (next.informative_only === undefined) next.informative_only = true;
            if (next.action_ref === undefined) next.action_ref = '';
            if (!next.match_rule) next.match_rule = 'equals';
            if (next.expected_value === undefined || next.expected_value === null) next.expected_value = '';
            if (!next.match_mode) next.match_mode = 'keyword';
            if (!next.status_codes) next.status_codes = '403,429,500,502,503,504';
            if (next.abort_on_match === undefined) next.abort_on_match = true;
            if (!next.fire_mode) next.fire_mode = 'edge';
            const cooldown = Number(next.cooldown_sec);
            next.cooldown_sec = Number.isFinite(cooldown) && cooldown >= 0 ? cooldown : 0;
            const stableFor = Number(next.stable_for_sec);
            next.stable_for_sec = Number.isFinite(stableFor) && stableFor >= 0 ? stableFor : 0;
            if (next.check_while_busy_workflow === undefined) next.check_while_busy_workflow = true;
            if (next.allow_during_workflow === undefined) next.allow_during_workflow = false;
            if (!next.interrupt_policy) next.interrupt_policy = 'auto';
            if (next.interrupt_message === undefined || next.interrupt_message === null) next.interrupt_message = '';
            if (next.probe_js === undefined || next.probe_js === null) next.probe_js = '';
            const priority = Number(next.priority);
            next.priority = Number.isInteger(priority) ? priority : 2;
            if (!next.url_pattern && next.type === 'network_request_error') {
                next.url_pattern = '';
            }
            if (next.periodic_enabled === undefined) next.periodic_enabled = true;
            const periodicInterval = Number(next.periodic_interval_sec);
            next.periodic_interval_sec = Number.isFinite(periodicInterval) && periodicInterval >= 1
                ? periodicInterval
                : 8;
            const periodicJitter = Number(next.periodic_jitter_sec);
            next.periodic_jitter_sec = Number.isFinite(periodicJitter) && periodicJitter >= 0
                ? periodicJitter
                : 2;
            return next;
        },

        async fetchMeta() {
            try {
                this.meta = await this.apiRequest('/api/commands/meta');
            } catch (e) {
                console.error('加载元信息失败:', e);
            }
        },

        async fetchBindingMeta() {
            await Promise.all([
                this.fetchAvailableDomains(),
                this.fetchAvailableTabs()
            ]);
        },

        async fetchAvailableDomains() {
            try {
                const data = await this.apiRequest('/api/config');
                this.availableDomains = Object.keys(data || {}).sort();
            } catch (e) {
                console.error('加载域名列表失败:', e);
                this.availableDomains = [];
            }
        },

        async fetchAvailableTabs() {
            try {
                const data = await this.apiRequest('/api/tab-pool/tabs');
                this.availableTabs = data.tabs || [];
            } catch (e) {
                console.error('加载标签页列表失败:', e);
                this.availableTabs = [];
            }
        },

        getBoundDomain(command = this.editingCommand) {
            const trigger = command?.trigger || {};
            if (trigger.scope === 'domain') {
                return (trigger.domain || '').trim();
            }
            if (trigger.scope === 'tab') {
                const targetTab = this.availableTabs.find(tab => tab.persistent_index === trigger.tab_index);
                return (targetTab?.current_domain || '').trim();
            }
            return '';
        },

        getTabLabel(tab) {
            if (!tab) return '';
            const domain = tab.current_domain || '未识别域名';
            return '#' + tab.persistent_index + ' · ' + domain;
        },

        getPresetHint() {
            if (!this.editingCommand) return '先选择绑定域名或标签页，再选择要执行的预设。';
            const scope = this.editingCommand.trigger?.scope;
            if (scope === 'all') {
                return '切换预设/执行工作流仅建议用于“指定域名”或“指定标签页”，也可以直接保持“跟随站点默认预设”。';
            }
            if (this.presetLoading) {
                return '正在加载预设列表...';
            }
            if (this.resolvedPresetDomain) {
                return '当前目标域名: ' + this.resolvedPresetDomain + '，也可保持“跟随站点默认预设”。';
            }
            if (scope === 'tab') {
                return '所选标签页当前没有可识别域名，暂时无法列出预设，但仍可保持“跟随站点默认预设”。';
            }
            return '请输入已配置的域名后再选择预设，或保持“跟随站点默认预设”。';
        },

        getPresetSelectPlaceholder() {
            if (!this.editingCommand) return '请先配置触发范围';
            if (this.presetLoading) return '正在加载预设列表...';
            if (!this.resolvedPresetDomain) {
                return this.editingCommand.trigger?.scope === 'all'
                    ? '请先切换到指定域名或指定标签页'
                    : '请先选择有效域名';
            }
            if (this.availablePresets.length === 0) {
                return '当前域名没有可用预设';
            }
            return '请选择预设';
        },

        getCommandTriggerPlaceholder() {
            if (this.sourceCommandOptions.length === 0) {
                return '没有可选命令';
            }
            if (this.editingCommand?.trigger?.type === 'command_result_event') {
                return '请选择要监听的命令';
            }
            if (this.editingCommand?.trigger?.type === 'command_check') {
                return '请选择检查命令';
            }
            return '请选择来源命令';
        },

        getTriggerTypeMeta(type) {
            const builtins = window.CommandsTriggerTypeBuiltinMeta || {};
            return {
                label: (this.meta.trigger_types || {})[type] || builtins[type]?.label || type || '',
                description: builtins[type]?.description || '用于在满足指定条件后触发动作。'
            };
        },

        getTriggerTypeDescription(type) {
            return this.getTriggerTypeMeta(type).description;
        },

        toggleTriggerTypePicker() {
            if (!this.editingCommand?.trigger) return;
            this.resetSourceCommandPicker();
            this.triggerTypePickerOpen = !this.triggerTypePickerOpen;
            this.triggerTypeTooltipType = this.triggerTypePickerOpen
                ? String(this.editingCommand.trigger.type || '').trim()
                : '';
        },

        setTriggerTypeTooltip(type) {
            this.triggerTypeTooltipType = String(type || '').trim();
        },

        clearTriggerTypeTooltip() {
            this.triggerTypeTooltipType = this.triggerTypePickerOpen
                ? String(this.editingCommand?.trigger?.type || '').trim()
                : '';
        },

        selectTriggerType(type) {
            if (!this.editingCommand?.trigger || !type) return;
            this.editingCommand.trigger.type = type;
            this.triggerTypeTooltipType = String(type).trim();
            this.triggerTypePickerOpen = false;
            this.handleTriggerTypeChange();
        },

        getSourceCommandButtonLabel() {
            if (this.editingCommand?.trigger?.type === 'command_result_event') {
                const trigger = this.editingCommand.trigger || {};
                if (trigger.listen_all_commands) {
                    return '监听全部命令';
                }
                const selected = this.selectedSourceCommandOptions || [];
                if (selected.length === 1) return selected[0].label;
                if (selected.length > 1) return '已选择 ' + selected.length + ' 条命令';
            }
            const selected = this.selectedSourceCommandOption;
            if (selected) {
                return selected.label;
            }
            return this.getCommandTriggerPlaceholder();
        },

        syncSourceCommandPickerState() {
            const next = {};
            for (const section of (this.filteredSourceCommandSections || [])) {
                if (section.isUngrouped) continue;
                if (Object.prototype.hasOwnProperty.call(this.sourcePickerExpandedGroups, section.name)) {
                    next[section.name] = !!this.sourcePickerExpandedGroups[section.name];
                } else {
                    next[section.name] = false;
                }
            }
            this.sourcePickerExpandedGroups = next;
        },

        resetSourceCommandPicker() {
            this.sourceCommandPickerOpen = false;
            this.sourceCommandSearch = '';
            this.sourcePickerShowUngrouped = false;
            this.syncSourceCommandPickerState();
        },

        syncPageProbeExpanded() {
            const trigger = this.editingCommand?.trigger;
            if (!trigger || trigger.type !== 'page_check') {
                this.pageProbeExpanded = false;
                return;
            }
            this.pageProbeExpanded = !!String(trigger.probe_js || '').trim();
        },

        togglePageProbeExpanded() {
            if (this.editingCommand?.trigger?.type !== 'page_check') return;
            this.pageProbeExpanded = !this.pageProbeExpanded;
        },

        toggleSourceCommandPicker() {
            if (this.sourceCommandOptions.length === 0) return;
            this.triggerTypePickerOpen = false;
            this.triggerTypeTooltipType = '';
            this.sourceCommandPickerOpen = !this.sourceCommandPickerOpen;
            if (this.sourceCommandPickerOpen) {
                this.syncSourceCommandPickerState();
            }
        },

        isSourceCommandSectionExpanded(section) {
            if (!section) return false;
            if (String(this.sourceCommandSearch || '').trim()) {
                return true;
            }
            if (section.isUngrouped) {
                return !!this.sourcePickerShowUngrouped;
            }
            return !!this.sourcePickerExpandedGroups[section.name];
        },

        toggleSourceCommandSection(section) {
            if (!section) return;
            if (section.isUngrouped) {
                this.sourcePickerShowUngrouped = !this.sourcePickerShowUngrouped;
                return;
            }
            this.sourcePickerExpandedGroups = {
                ...this.sourcePickerExpandedGroups,
                [section.name]: !this.isSourceCommandSectionExpanded(section)
            };
        },

        selectSourceCommand(commandId) {
            if (!this.editingCommand?.trigger) return;
            if (this.editingCommand.trigger.type === 'command_result_event') {
                const selected = new Set(this.editingCommand.trigger.command_ids || []);
                if (selected.has(commandId)) selected.delete(commandId);
                else selected.add(commandId);
                this.editingCommand.trigger.command_ids = Array.from(selected);
                this.editingCommand.trigger.listen_all_commands = false;
                return;
            }
            this.editingCommand.trigger.command_id = commandId;
            if (['command_check', 'command_result_match'].includes(this.editingCommand.trigger.type)) {
                this.handleResultSourceChange();
            }
            this.sourceCommandPickerOpen = false;
        },

        toggleListenAllCommands() {
            if (!this.editingCommand?.trigger) return;
            const next = !this.editingCommand.trigger.listen_all_commands;
            this.editingCommand.trigger.listen_all_commands = next;
            if (next) {
                this.editingCommand.trigger.command_ids = [];
            }
        },

        isSourceCommandSelected(commandId) {
            if (this.editingCommand?.trigger?.type === 'command_result_event') {
                return (this.editingCommand.trigger.command_ids || []).includes(commandId);
            }
            return this.editingCommand?.trigger?.command_id === commandId;
        },

        getTriggerTargetLabel(trigger) {
            const type = trigger?.type;
            if (type === 'page_check') return '检查条件';
            if (type === 'command_check') return '检查命令';
            if (type === 'command_result_match') return '来源命令结果';
            if (type === 'command_result_event') return '来源命令';
            if (type === 'network_request_error') {
                return trigger?.match_mode === 'regex' ? '正则表达式' : '监听 URL 规则';
            }
            if (type === 'command_triggered') return '来源命令';
            return '阈值';
        },

        getCommandName(commandId) {
            if (!commandId) return '';
            const match = (this.commands || []).find(cmd => cmd.id === commandId);
            return match?.name || commandId;
        },

        getCommandActionOptions(commandId) {
            const command = (this.commands || []).find(cmd => cmd.id === commandId);
            if (!command) return [];
            const actions = command.actions || [];
            return actions.map((action, idx) => {
                const ref = action.action_id || ('step_' + (idx + 1));
                return {
                    value: ref,
                    label: '#' + (idx + 1) + ' · ' + this.getActionLabel(action.type)
                };
            });
        },

        getActionRefLabel(commandId, actionRef) {
            if (!actionRef) return '命令最终返回值';
            const match = this.getCommandActionOptions(commandId).find(opt => opt.value === actionRef);
            return match?.label || actionRef;
        },

        getMatchRuleLabel(rule) {
            const map = { equals: '等于', contains: '包含', not_equals: '不等于' };
            return map[rule] || rule;
        },

        getTriggerValueDisplay(trigger) {
            if (!trigger) return '';
            if (trigger.type === 'command_triggered') {
                return this.getCommandName(trigger.command_id);
            }
            if (trigger.type === 'command_check' || trigger.type === 'command_result_match') {
                const sourceName = this.getCommandName(trigger.command_id);
                const actionLabel = this.getActionRefLabel(trigger.command_id, trigger.action_ref);
                const ruleLabel = this.getMatchRuleLabel(trigger.match_rule || 'equals');
                const expected = String(trigger.expected_value || '');
                return sourceName + ' / ' + actionLabel + ' ' + ruleLabel + ' ' + expected;
            }
            if (trigger.type === 'command_result_event') {
                if (trigger.listen_all_commands) return '全部命令';
                const ids = Array.isArray(trigger.command_ids) ? trigger.command_ids : [];
                const labels = ids.map(id => this.getCommandName(id)).filter(Boolean);
                return labels.length > 0 ? labels.join('、') : '未选择命令';
            }
            if (trigger.type === 'network_request_error') {
                const pattern = trigger.url_pattern || trigger.value || '';
                const codes = trigger.status_codes || '';
                return (pattern || '*') + ' [' + codes + ']';
            }
            if (trigger.type === 'page_check') {
                const textRule = String(trigger.value || '').trim();
                const hasProbe = String(trigger.probe_js || '').trim();
                if (textRule && hasProbe) return textRule + ' + JS探测';
                if (textRule) return textRule;
                if (hasProbe) return '仅 JS 探测';
                return '';
            }
            return trigger.value;
        },

        async loadPresetOptions() {
            const domain = this.resolvedPresetDomain;
            this.availablePresets = [];

            if (!domain || !this.editingCommand) return;
            if (!this.editingCommand.actions?.some(action => ['execute_preset', 'execute_workflow'].includes(action.type))) return;

            this.presetLoading = true;
            try {
                const data = await this.apiRequest('/api/presets/' + encodeURIComponent(domain));
                this.availablePresets = data.presets || [];

                for (const action of this.editingCommand.actions) {
                    if (!['execute_preset', 'execute_workflow'].includes(action.type)) continue;
                    action.preset_name = this.normalizePresetActionValue(action.preset_name);
                    if (
                        action.preset_name !== this.getFollowDefaultPresetValue()
                        && !this.availablePresets.includes(action.preset_name)
                    ) {
                        action.preset_name = this.getFollowDefaultPresetValue();
                    }
                }
            } catch (e) {
                console.error('加载预设列表失败:', e);
                this.availablePresets = [];
                for (const action of this.editingCommand.actions || []) {
                    if (['execute_preset', 'execute_workflow'].includes(action.type)) {
                        action.preset_name = this.getFollowDefaultPresetValue();
                    }
                }
            } finally {
                this.presetLoading = false;
            }
        },

        async handleTriggerScopeChange() {
            if (!this.editingCommand) return;

            if (this.editingCommand.trigger.scope !== 'domain') {
                this.editingCommand.trigger.domain = '';
            }
            if (this.editingCommand.trigger.scope !== 'tab') {
                this.editingCommand.trigger.tab_index = null;
            }

            await this.loadPresetOptions();
        },

        async handleTriggerTargetChange() {
            await this.loadPresetOptions();
        },

        getNumericTriggerDefault(triggerType) {
            const defaults = {
                request_count: 10,
                error_count: 3,
                idle_timeout: 300
            };
            return defaults[triggerType] ?? 10;
        },

        handleTriggerTypeChange() {
            if (!this.editingCommand?.trigger) return;

            const trigger = this.editingCommand.trigger;
            const currentValue = trigger.value;
            this.triggerTypeTooltipType = String(trigger.type || '').trim();
            this.resetSourceCommandPicker();

            if (trigger.type === 'command_triggered') {
                trigger.value = '';
                if (!this.sourceCommandOptions.some(opt => opt.value === trigger.command_id)) {
                    trigger.command_id = this.sourceCommandOptions[0]?.value || '';
                }
                trigger.action_ref = '';
                trigger.expected_value = '';
                this.syncPageProbeExpanded();
                return;
            }

            if (trigger.type === 'command_check' || trigger.type === 'command_result_match') {
                trigger.value = '';
                if (!this.sourceCommandOptions.some(opt => opt.value === trigger.command_id)) {
                    trigger.command_id = this.sourceCommandOptions[0]?.value || '';
                }
                if (!trigger.match_rule) trigger.match_rule = 'equals';
                if (trigger.expected_value === undefined || trigger.expected_value === null) {
                    trigger.expected_value = '';
                }
                if (trigger.action_ref === undefined) trigger.action_ref = '';
                this.handleResultSourceChange();
                this.syncPageProbeExpanded();
                return;
            }

            if (trigger.type === 'command_result_event') {
                trigger.value = '';
                trigger.command_id = '';
                trigger.action_ref = '';
                trigger.expected_value = '';
                trigger.command_ids = Array.isArray(trigger.command_ids) ? trigger.command_ids : [];
                if (trigger.listen_all_commands === undefined) trigger.listen_all_commands = false;
                if (trigger.informative_only === undefined) trigger.informative_only = true;
                this.syncPageProbeExpanded();
                return;
            }

            if (trigger.type === 'network_request_error') {
                trigger.value = '';
                trigger.command_id = '';
                if (!trigger.match_mode) trigger.match_mode = 'keyword';
                if (!trigger.status_codes) trigger.status_codes = '403,429,500,502,503,504';
                if (trigger.abort_on_match === undefined) trigger.abort_on_match = true;
                if (trigger.url_pattern === undefined || trigger.url_pattern === null) {
                    trigger.url_pattern = '';
                }
                this.syncPageProbeExpanded();
                return;
            }

            if (trigger.type === 'page_check') {
                trigger.command_id = '';
                if (currentValue === 10 || currentValue === '10' || typeof currentValue === 'number') {
                    trigger.value = '';
                }
                this.syncPageProbeExpanded();
                return;
            }

            trigger.command_id = '';

            if (['request_count', 'error_count', 'idle_timeout'].includes(trigger.type)) {
                const fallback = this.getNumericTriggerDefault(trigger.type);
                const numericValue = Number(currentValue);
                trigger.value = Number.isFinite(numericValue) && numericValue > 0 ? numericValue : fallback;
                this.syncPageProbeExpanded();
                return;
            }

            if (currentValue === '' || currentValue === null || currentValue === undefined) {
                trigger.value = 10;
            }
            this.syncPageProbeExpanded();
        },

        handleResultSourceChange() {
            if (!this.editingCommand?.trigger) return;
            const trigger = this.editingCommand.trigger;
            const options = this.getCommandActionOptions(trigger.command_id);
            if (trigger.action_ref && !options.some(opt => opt.value === trigger.action_ref)) {
                trigger.action_ref = '';
            }
        },

        openNewCommand() {
            this.editingCommand = this.normalizeCommand({
                name: '新命令',
                enabled: true,
                log_enabled: true,
                log_level: 'GLOBAL',
                mode: 'simple',
                trigger: {
                    type: 'request_count',
                    value: 10,
                    command_id: '',
                    command_ids: [],
                    listen_all_commands: false,
                    informative_only: true,
                    action_ref: '',
                    match_rule: 'equals',
                    expected_value: '',
                    match_mode: 'keyword',
                    status_codes: '403,429,500,502,503,504',
                    abort_on_match: true,
                    scope: 'all',
                    domain: '',
                    tab_index: null,
                    fire_mode: 'edge',
                    cooldown_sec: 0,
                    allow_during_workflow: false,
                    interrupt_policy: 'auto',
                    interrupt_message: '',
                    probe_js: '',
                    periodic_enabled: true,
                    periodic_interval_sec: 8,
                    periodic_jitter_sec: 2
                },
                stop_on_error: false,
                actions: [{ type: 'clear_cookies' }, { type: 'refresh_page' }],
                group_name: '',
                script: '',
                script_lang: 'javascript'
            });
            this.isNew = true;
            this.showEditor = true;
            this.triggerTypePickerOpen = false;
            this.triggerTypeTooltipType = '';
            this.resetSourceCommandPicker();
            this.syncPageProbeExpanded();
            this.fetchBindingMeta();
        },

        openEditCommand(cmd) {
            this.editingCommand = this.normalizeCommand(cmd);
            if (['command_check', 'command_result_match', 'command_result_event'].includes(this.editingCommand?.trigger?.type)) {
                this.handleResultSourceChange();
            }
            this.isNew = false;
            this.showEditor = true;
            this.triggerTypePickerOpen = false;
            this.triggerTypeTooltipType = '';
            this.resetSourceCommandPicker();
            this.syncPageProbeExpanded();
            this.fetchBindingMeta().then(() => this.loadPresetOptions());
        },

        addAction() {
            if (!this.editingCommand) return;
            const nextIndex = this.editingCommand.actions.length;
            this.editingCommand.actions.push(this.normalizeAction({ type: 'wait', seconds: 1 }, nextIndex));
        },

        async handleActionTypeChange(action) {
            this.initClickAction(action);
            this.initProxyAction(action);
            this.initWebhookAction(action);
            this.initNapcatAction(action);
            this.initCommandGroupAction(action);
            this.initReleaseLockAction(action);
            if (action.type === 'execute_workflow' && action.prompt === undefined) {
                action.prompt = '';
            }
            if (['execute_preset', 'execute_workflow'].includes(action.type)) {
                await this.loadPresetOptions();
                action.preset_name = this.normalizePresetActionValue(action.preset_name);
            }
        },

        initClickAction(action) {
            if (action.type === 'click_element') {
                action.selector = String(action.selector || '').trim();
            }
            if (action.type === 'click_coordinates') {
                const x = Number(action.x);
                const y = Number(action.y);
                action.x = Number.isFinite(x) ? x : '';
                action.y = Number.isFinite(y) ? y : '';
            }
        },

        initProxyAction(action) {
            if (action.type === 'switch_proxy') {
                action.clash_api = action.clash_api || this.proxyDefaults.clash_api;
                action.clash_secret = action.clash_secret || '';
                action.selector = action.selector || this.proxyDefaults.selector;
                action.mode = action.mode || 'random';
                action.node_name = action.node_name || '';
                action.exclude_keywords = action.exclude_keywords || this.proxyDefaults.exclude_keywords;
                if (action.refresh_after === undefined) {
                    action.refresh_after = true;
                }
            }
        },

        initWebhookAction(action) {
            if (action.type === 'send_webhook') {
                action.method = action.method || this.webhookDefaults.method;
                action.url = action.url || this.webhookDefaults.url;
                if (action.payload === undefined) {
                    action.payload = this.webhookDefaults.payload;
                }
                if (action.headers === undefined) {
                    action.headers = this.webhookDefaults.headers;
                }
                if (action.timeout === undefined) {
                    action.timeout = this.webhookDefaults.timeout;
                }
                if (action.raise_for_status === undefined) {
                    action.raise_for_status = this.webhookDefaults.raise_for_status;
                }
            }
        },

        initNapcatAction(action) {
            if (action.type !== 'send_napcat') return;
            action.base_url = action.base_url || this.napcatDefaults.base_url;
            action.target_type = action.target_type || this.napcatDefaults.target_type;
            action.user_id = action.user_id || this.napcatDefaults.user_id;
            action.group_id = action.group_id || this.napcatDefaults.group_id;
            if (action.message === undefined) action.message = this.napcatDefaults.message;
            if (action.access_token === undefined) action.access_token = this.napcatDefaults.access_token;
            if (action.timeout === undefined) action.timeout = this.napcatDefaults.timeout;
            if (action.raise_for_status === undefined) action.raise_for_status = this.napcatDefaults.raise_for_status;
        },

        useNapcatPreset(action, targetType) {
            if (!action) return;
            action.type = 'send_napcat';
            this.initNapcatAction(action);
            action.target_type = targetType === 'group' ? 'group' : 'private';
        },

        initCommandGroupAction(action) {
            if (action.type !== 'execute_command_group') return;
            if (action.include_disabled === undefined) {
                action.include_disabled = false;
            }
            if (!action.acquire_policy) {
                action.acquire_policy = 'inherit_session';
            }
            const current = String(action.group_name || '').trim();
            if (current) {
                action.group_name = current;
                return;
            }
            action.group_name = this.commandGroupOptions[0]?.value || '';
        },

        initReleaseLockAction(action) {
            if (action.type === 'release_tab_lock') {
                if (action.reason === undefined || action.reason === null || action.reason === '') {
                    action.reason = this.releaseLockDefaults.reason;
                }
                if (action.clear_page === undefined) {
                    action.clear_page = this.releaseLockDefaults.clear_page;
                }
                if (action.stop_actions === undefined) {
                    action.stop_actions = this.releaseLockDefaults.stop_actions;
                }
            }
        },

        removeAction(index) {
            if (!this.editingCommand) return;
            this.editingCommand.actions.splice(index, 1);
        },

        moveAction(index, direction) {
            if (!this.editingCommand) return;
            const arr = this.editingCommand.actions;
            const newIndex = index + direction;
            if (newIndex < 0 || newIndex >= arr.length) return;
            const temp = arr[index];
            arr[index] = arr[newIndex];
            arr[newIndex] = temp;
        },

        async saveCommand() {
            if (!this.editingCommand) return;
            const trigger = this.editingCommand.trigger || {};
            if (['request_count', 'error_count', 'idle_timeout'].includes(trigger.type)) {
                const numericValue = Number(trigger.value);
                if (!Number.isFinite(numericValue) || numericValue <= 0) {
                    this.$emit('notify', { type: 'error', message: '计数/超时阈值必须是大于 0 的数字。' });
                    return;
                }
                trigger.value = numericValue;
            }
            if (trigger.type === 'command_triggered') {
                const sourceId = String(trigger.command_id || '').trim();
                if (!sourceId) {
                    this.$emit('notify', { type: 'error', message: '请先在“命令触发后执行”里选择来源命令。' });
                    return;
                }
                if (this.editingCommand.id && sourceId === this.editingCommand.id) {
                    this.$emit('notify', { type: 'error', message: '来源命令不能选择当前命令自己。' });
                    return;
                }
            }
            if (trigger.type === 'command_check' || trigger.type === 'command_result_match') {
                const sourceId = String(trigger.command_id || '').trim();
                if (!sourceId) {
                    this.$emit('notify', { type: 'error', message: trigger.type === 'command_check' ? '请先选择“检查命令”。' : '请先选择“来源命令”。' });
                    return;
                }
                if (this.editingCommand.id && sourceId === this.editingCommand.id) {
                    this.$emit('notify', { type: 'error', message: trigger.type === 'command_check' ? '检查命令不能是当前命令自己。' : '来源命令不能是当前命令自己。' });
                    return;
                }
                const expected = String(trigger.expected_value || '').trim();
                if (!expected) {
                    this.$emit('notify', { type: 'error', message: '请填写“结果值”。' });
                    return;
                }
            }
            if (trigger.type === 'command_result_event') {
                const ids = Array.isArray(trigger.command_ids)
                    ? trigger.command_ids.map(id => String(id || '').trim()).filter(Boolean)
                    : [];
                trigger.command_ids = ids;
                if (!trigger.listen_all_commands && ids.length === 0) {
                    this.$emit('notify', { type: 'error', message: '请至少选择一个来源命令，或切换为“全部命令”。' });
                    return;
                }
                if (this.editingCommand.id && ids.includes(this.editingCommand.id)) {
                    this.$emit('notify', { type: 'error', message: '来源命令不能包含当前命令自己。' });
                    return;
                }
            }
            if (trigger.type === 'network_request_error') {
                const urlPattern = String(trigger.url_pattern || trigger.value || '').trim();
                if (!urlPattern) {
                    this.$emit('notify', { type: 'error', message: '网络异常拦截需要填写 URL 监听规则。' });
                    return;
                }
                const statusCodes = String(trigger.status_codes || '').trim();
                if (!statusCodes) {
                    this.$emit('notify', { type: 'error', message: '请填写要拦截的状态码（如 403,429,500）。' });
                    return;
                }
            }
            if (trigger.type === 'page_check') {
                const textRule = String(trigger.value || '').trim();
                const probeJs = String(trigger.probe_js || '').trim();
                trigger.value = textRule;
                trigger.probe_js = probeJs;
                if (!textRule && !probeJs) {
                    this.$emit('notify', { type: 'error', message: '页面检查至少要填写“检查文本”或“高级页面探测 JS”其中一项。' });
                    return;
                }
            }
            const periodicInterval = Number(trigger.periodic_interval_sec);
            if (!Number.isFinite(periodicInterval) || periodicInterval < 1) {
                this.$emit('notify', { type: 'error', message: '周期检测间隔必须是大于等于 1 秒的数字。' });
                return;
            }
            const periodicJitter = Number(trigger.periodic_jitter_sec);
            if (!Number.isFinite(periodicJitter) || periodicJitter < 0) {
                this.$emit('notify', { type: 'error', message: '周期检测抖动必须是大于等于 0 的数字。' });
                return;
            }
            const stableFor = Number(trigger.stable_for_sec);
            if (!Number.isFinite(stableFor) || stableFor < 0) {
                this.$emit('notify', { type: 'error', message: '页面稳定命中时长必须是大于等于 0 的数字。' });
                return;
            }
            const priority = Number(trigger.priority);
            if (!Number.isInteger(priority)) {
                this.$emit('notify', { type: 'error', message: '命令优先级必须是整数。' });
                return;
            }
            trigger.periodic_interval_sec = periodicInterval;
            trigger.periodic_jitter_sec = periodicJitter;
            trigger.stable_for_sec = stableFor;
            trigger.periodic_enabled = !!trigger.periodic_enabled;
            trigger.check_while_busy_workflow = !!trigger.check_while_busy_workflow;
            trigger.priority = priority;
            const presetActions = (this.editingCommand.actions || []).filter(action => ['execute_preset', 'execute_workflow'].includes(action.type));
            for (const action of presetActions) {
                action.preset_name = this.normalizePresetActionValue(action.preset_name);
            }
            const missingPreset = presetActions.some(action => !String(action.preset_name || '').trim());
            if (missingPreset) {
                this.$emit('notify', { type: 'error', message: '“切换预设/执行工作流”动作必须从预设列表中选择一个预设。' });
                return;
            }
            const webhookActions = (this.editingCommand.actions || []).filter(action => action.type === 'send_webhook');
            const invalidWebhook = webhookActions.find(action => !String(action.url || '').trim());
            if (invalidWebhook) {
                this.$emit('notify', { type: 'error', message: 'Webhook 动作必须填写请求 URL。' });
                return;
            }
            const napcatActions = (this.editingCommand.actions || []).filter(action => action.type === 'send_napcat');
            const invalidNapcat = napcatActions.find(action => {
                const targetType = action.target_type === 'group' ? 'group' : 'private';
                const targetId = String(targetType === 'group' ? action.group_id : action.user_id || '').trim();
                return !String(action.base_url || '').trim() || !targetId || !String(action.message || '').trim();
            });
            if (invalidNapcat) {
                this.$emit('notify', { type: 'error', message: 'NapCat 动作必须填写接口地址、目标 QQ/群号和消息内容。' });
                return;
            }
            const groupActions = (this.editingCommand.actions || []).filter(action => action.type === 'execute_command_group');
            const invalidGroupAction = groupActions.find(action => !String(action.group_name || '').trim());
            if (invalidGroupAction) {
                this.$emit('notify', { type: 'error', message: '“执行命令组”动作必须选择命令组。' });
                return;
            }
            const clickElementAction = (this.editingCommand.actions || []).find(action =>
                action.type === 'click_element' && !String(action.selector || '').trim()
            );
            if (clickElementAction) {
                this.$emit('notify', { type: 'error', message: '“点击元素”动作必须填写元素选择器。' });
                return;
            }
            const clickCoordinateAction = (this.editingCommand.actions || []).find(action => {
                if (action.type !== 'click_coordinates') return false;
                return !Number.isFinite(Number(action.x)) || !Number.isFinite(Number(action.y));
            });
            if (clickCoordinateAction) {
                this.$emit('notify', { type: 'error', message: '“点击坐标”动作必须填写有效的 X / Y 坐标。' });
                return;
            }
            if (trigger.type === 'network_request_error') {
                trigger.value = trigger.url_pattern || '';
            } else if (trigger.type === 'command_check' || trigger.type === 'command_result_match') {
                trigger.value = '';
            } else if (trigger.type === 'command_result_event') {
                trigger.value = '';
            }
            this.editingCommand.log_enabled = !!this.editingCommand.log_enabled;
            this.editingCommand.log_level = this.normalizeCommandLogLevel(this.editingCommand.log_level);
            this.editingCommand.group_name = String(this.editingCommand.group_name || '').trim();
            this.editingCommand.actions = (this.editingCommand.actions || [])
                .map((action, index) => this.normalizeAction(action, index));
            try {
                if (this.isNew) {
                    await this.apiRequest('/api/commands', {
                        method: 'POST',
                        body: JSON.stringify(this.editingCommand)
                    });
                    this.$emit('notify', { type: 'success', message: '命令已创建' });
                } else {
                    await this.apiRequest('/api/commands/' + this.editingCommand.id, {
                        method: 'PUT',
                        body: JSON.stringify(this.editingCommand)
                    });
                    this.$emit('notify', { type: 'success', message: '命令已更新' });
                }
                this.showEditor = false;
                await this.fetchCommands();
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '保存失败: ' + e.message });
            }
        },

        async deleteCommand(cmd) {
            if (!confirm('确定删除命令「' + cmd.name + '」吗？')) return;
            try {
                await this.apiRequest('/api/commands/' + cmd.id, { method: 'DELETE' });
                this.$emit('notify', { type: 'success', message: '命令已删除' });
                await this.fetchCommands();
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '删除失败: ' + e.message });
            }
        },

        async toggleCommand(cmd) {
            try {
                await this.apiRequest('/api/commands/' + cmd.id, {
                    method: 'PUT',
                    body: JSON.stringify({ enabled: !cmd.enabled })
                });
                await this.fetchCommands();
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '切换失败: ' + e.message });
            }
        },

        async testCommand(cmd) {
            try {
                const result = await this.apiRequest('/api/commands/' + cmd.id + '/test', { method: 'POST' });
                this.$emit('notify', { type: 'success', message: result.message || '命令已执行' });
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '执行失败: ' + e.message });
            }
        },

        syncGroupCollapseState() {
            const next = {};
            for (const group of (this.commandGroups || [])) {
                if (Object.prototype.hasOwnProperty.call(this.collapsedGroups, group.name)) {
                    next[group.name] = !!this.collapsedGroups[group.name];
                } else {
                    next[group.name] = true;
                }
            }
            this.collapsedGroups = next;
        },

        isGroupCollapsed(groupName) {
            const key = String(groupName || '').trim();
            if (!key) return false;
            if (!Object.prototype.hasOwnProperty.call(this.collapsedGroups, key)) {
                return true;
            }
            return !!this.collapsedGroups[key];
        },

        toggleGroupCollapse(groupName) {
            const key = String(groupName || '').trim();
            if (!key) return;
            this.collapsedGroups = {
                ...this.collapsedGroups,
                [key]: !this.isGroupCollapsed(key)
            };
            this.closeBulkActionMenu();
            this.closeGroupActionMenu();
        },

        isBulkActionMenuOpen() {
            return !!this.bulkActionMenuOpen;
        },

        toggleBulkActionMenu() {
            const next = !this.bulkActionMenuOpen;
            this.bulkActionMenuOpen = next;
            if (next) {
                this.closeGroupActionMenu();
            }
        },

        closeBulkActionMenu() {
            this.bulkActionMenuOpen = false;
        },

        isGroupActionMenuOpen(groupName) {
            return String(this.groupActionMenuOpen || '').trim() === String(groupName || '').trim();
        },

        toggleGroupActionMenu(groupName) {
            const key = String(groupName || '').trim();
            if (!key) return;
            this.closeBulkActionMenu();
            this.groupActionMenuOpen = this.isGroupActionMenuOpen(key) ? '' : key;
        },

        closeGroupActionMenu() {
            this.groupActionMenuOpen = '';
        },

        isCommandSelected(commandId) {
            return (this.selectedCommandIds || []).includes(commandId);
        },

        getSelectedCount(items) {
            const ids = Array.isArray(items)
                ? items.map(item => typeof item === 'object' ? item?.id : item).filter(Boolean)
                : [];
            if (ids.length === 0) return 0;
            const selectedSet = new Set(this.selectedCommandIds || []);
            return ids.filter(id => selectedSet.has(id)).length;
        },

        isGroupFullySelected(commands) {
            const ids = Array.isArray(commands)
                ? commands.map(cmd => cmd?.id).filter(Boolean)
                : [];
            if (ids.length === 0) return false;
            const selectedSet = new Set(this.selectedCommandIds || []);
            return ids.every(id => selectedSet.has(id));
        },

        getGroupSelectionActionLabel(commands) {
            return this.isGroupFullySelected(commands) ? '取消整组选中' : '整组选中';
        },

        toggleGroupSelection(commands) {
            const ids = Array.isArray(commands)
                ? commands.map(cmd => cmd?.id).filter(Boolean)
                : [];
            if (ids.length === 0) return;
            const selectedSet = new Set(this.selectedCommandIds || []);
            const allSelected = ids.every(id => selectedSet.has(id));
            if (allSelected) {
                ids.forEach(id => selectedSet.delete(id));
            } else {
                ids.forEach(id => selectedSet.add(id));
            }
            this.selectedCommandIds = Array.from(selectedSet);
            this.showGroupTools = true;
            this.closeGroupActionMenu();
        },

        toggleCommandSelection(commandId) {
            const selectedSet = new Set(this.selectedCommandIds || []);
            if (selectedSet.has(commandId)) {
                selectedSet.delete(commandId);
            } else {
                selectedSet.add(commandId);
            }
            this.selectedCommandIds = Array.from(selectedSet);
            this.showGroupTools = true;
        },

        toggleCurrentPageSelection() {
            const pageIds = this.visiblePageCommandIds || [];
            if (pageIds.length === 0) return;
            const selectedSet = new Set(this.selectedCommandIds || []);
            const allSelected = pageIds.every(id => selectedSet.has(id));
            if (allSelected) {
                pageIds.forEach(id => selectedSet.delete(id));
            } else {
                pageIds.forEach(id => selectedSet.add(id));
            }
            this.selectedCommandIds = Array.from(selectedSet);
            this.showGroupTools = true;
        },

        clearSelection() {
            this.selectedCommandIds = [];
        },

        getNextDefaultGroupName() {
            const existing = new Set(this.commandGroups.map(group => group.name));
            let idx = 1;
            while (existing.has('命令组' + idx)) {
                idx += 1;
            }
            return '命令组' + idx;
        },

        clearGroupDragState() {
            this.draggingCommandId = '';
            this.dragOverGroupName = '';
        },

        beginGroupDrag(commandId, event) {
            if (this.groupWorking) return;
            this.draggingCommandId = String(commandId || '').trim();
            this.dragOverGroupName = '';
            if (this.draggingCommandId) {
                this.showGroupTools = true;
            }
            if (event?.dataTransfer && this.draggingCommandId) {
                event.dataTransfer.setData('text/plain', this.draggingCommandId);
                event.dataTransfer.effectAllowed = 'move';
            }
        },

        isGroupDropTarget(groupName) {
            const name = String(groupName || '').trim();
            return !!name && name === String(this.dragOverGroupName || '').trim();
        },

        onGroupDragOver(groupName, event) {
            if (this.groupWorking) return;
            if (!String(this.draggingCommandId || '').trim()) return;
            const name = String(groupName || '').trim();
            if (!name) return;
            this.dragOverGroupName = name;
            if (event?.dataTransfer) {
                event.dataTransfer.dropEffect = 'move';
            }
        },

        onGroupDragLeave(groupName) {
            const name = String(groupName || '').trim();
            if (!name) return;
            if (this.dragOverGroupName === name) {
                this.dragOverGroupName = '';
            }
        },

        async assignCommandsToGroup(commandIds, groupName, successPrefix = '命令分组已更新') {
            const ids = Array.isArray(commandIds)
                ? commandIds.map(id => String(id || '').trim()).filter(Boolean)
                : [];
            const normalizedGroup = String(groupName || '').trim();
            if (ids.length === 0) {
                this.$emit('notify', { type: 'error', message: '没有可更新的命令。' });
                return 0;
            }

            this.groupWorking = true;
            try {
                const result = await this.apiRequest('/api/command-groups', {
                    method: 'PUT',
                    body: JSON.stringify({
                        command_ids: ids,
                        group_name: normalizedGroup
                    })
                });
                const updated = Number(result.updated || 0);
                this.$emit('notify', {
                    type: updated > 0 ? 'success' : 'error',
                    message: successPrefix + '（' + updated + ' 条）'
                });
                if (normalizedGroup) {
                    this.pendingGroupName = normalizedGroup;
                    this.selectedExistingGroupName = normalizedGroup;
                }
                await this.fetchCommands();
                return updated;
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '分组更新失败: ' + e.message });
                return 0;
            } finally {
                this.groupWorking = false;
                this.clearGroupDragState();
            }
        },

        async onGroupDrop(groupName) {
            const targetGroup = String(groupName || '').trim();
            const commandId = String(this.draggingCommandId || '').trim();
            this.clearGroupDragState();
            if (!targetGroup || !commandId || this.groupWorking) return;
            const command = (this.commands || []).find(item => item.id === commandId);
            if (!command) return;

            const currentGroup = String(command.group_name || '').trim();
            if (currentGroup === targetGroup) {
                this.$emit('notify', { type: 'success', message: '该命令已经在命令组：' + targetGroup });
                return;
            }

            await this.assignCommandsToGroup(
                [commandId],
                targetGroup,
                '已拖动加入命令组：' + targetGroup
            );
        },

        async assignSelectedToGroup() {
            if (!this.hasSelection) {
                this.$emit('notify', { type: 'error', message: '请先勾选命令。' });
                return;
            }
            const groupName = String(this.pendingGroupName || '').trim() || this.getNextDefaultGroupName();
            await this.assignCommandsToGroup(
                this.selectedCommandIds,
                groupName,
                '已收纳到命令组：' + groupName
            );
        },

        async assignSelectedToExistingGroup() {
            if (!this.hasSelection) {
                this.$emit('notify', { type: 'error', message: '请先勾选命令。' });
                return;
            }
            const groupName = String(this.selectedExistingGroupName || '').trim();
            if (!groupName) {
                this.$emit('notify', { type: 'error', message: '请先选择已有命令组。' });
                return;
            }
            await this.assignCommandsToGroup(
                this.selectedCommandIds,
                groupName,
                '已加入已有命令组：' + groupName
            );
        },

        async renameSelectedGroup() {
            const sourceName = String(this.selectedExistingGroupName || '').trim();
            const targetName = String(this.pendingGroupName || '').trim();
            if (!sourceName) {
                this.$emit('notify', { type: 'error', message: '请先选择要重命名的命令组。' });
                return;
            }
            if (!targetName) {
                this.$emit('notify', { type: 'error', message: '请输入新的命令组名称。' });
                return;
            }
            if (sourceName === targetName) {
                this.$emit('notify', { type: 'warning', message: '新旧命令组名称相同，无需重命名。' });
                return;
            }
            if ((this.commandGroups || []).some(group => group.name === targetName)) {
                this.$emit('notify', { type: 'error', message: '目标命令组名称已存在。' });
                return;
            }

            const sourceGroup = (this.commandGroups || []).find(group => group.name === sourceName);
            const commandIds = Array.isArray(sourceGroup?.commandIds) ? sourceGroup.commandIds : [];
            if (commandIds.length === 0) {
                this.$emit('notify', { type: 'error', message: '未找到要重命名的命令组内容。' });
                return;
            }

            const updated = await this.assignCommandsToGroup(
                commandIds,
                targetName,
                '命令组已重命名：' + sourceName + ' -> ' + targetName
            );
            if (updated > 0) {
                this.selectedExistingGroupName = targetName;
                this.pendingGroupName = targetName;
            }
        },

        async ungroupSelectedCommands() {
            if (!this.hasSelection) {
                this.$emit('notify', { type: 'error', message: '请先勾选命令。' });
                return;
            }
            await this.assignCommandsToGroup(
                this.selectedCommandIds,
                '',
                '已解散选中命令的分组'
            );
        },

        async setCommandsEnabled(commandIds, enabled, successPrefix, errorPrefix = '批量更新命令状态失败') {
            const ids = Array.isArray(commandIds)
                ? commandIds.map(id => String(id || '').trim()).filter(Boolean)
                : [];
            if (ids.length === 0) {
                this.$emit('notify', { type: 'error', message: '没有可更新的命令。' });
                return 0;
            }

            this.groupWorking = true;
            try {
                const result = await this.apiRequest('/api/commands/enabled', {
                    method: 'PUT',
                    body: JSON.stringify({
                        command_ids: ids,
                        enabled: !!enabled
                    })
                });
                const updated = Number(result.updated || 0);
                this.$emit('notify', {
                    type: updated > 0 ? 'success' : 'warning',
                    message: successPrefix + '（' + updated + ' 条）'
                });
                await this.fetchCommands();
                return updated;
            } catch (e) {
                this.$emit('notify', { type: 'error', message: errorPrefix + ': ' + e.message });
                return 0;
            } finally {
                this.groupWorking = false;
            }
        },

        async disableAllCommands() {
            const commandIds = (this.commands || [])
                .map(cmd => cmd?.id)
                .filter(Boolean);
            if (commandIds.length === 0) {
                this.$emit('notify', { type: 'warning', message: '当前没有可禁用命令。' });
                return;
            }
            if (!confirm('确定全部禁用当前所有命令吗？')) return;
            this.closeBulkActionMenu();
            await this.setCommandsEnabled(
                commandIds,
                false,
                '已全部禁用命令',
                '全部禁用失败'
            );
        },

        async enableAllDisabledCommands() {
            const disabledIds = (this.commands || [])
                .filter(cmd => cmd && cmd.enabled === false)
                .map(cmd => cmd.id)
                .filter(Boolean);
            if (disabledIds.length === 0) {
                this.$emit('notify', { type: 'warning', message: '当前没有禁用命令。' });
                return;
            }
            if (!confirm('确定全部解禁当前所有禁用命令吗？')) return;
            this.closeBulkActionMenu();
            await this.setCommandsEnabled(
                disabledIds,
                true,
                '已全部解禁命令',
                '全部解禁失败'
            );
        },

        async setGroupEnabled(groupName, enabled) {
            const name = String(groupName || '').trim();
            if (!name) return 0;
            const actionText = enabled ? '启用' : '禁用';
            if (!confirm('确定' + actionText + '命令组「' + name + '」吗？')) return 0;

            this.groupWorking = true;
            try {
                const result = await this.apiRequest('/api/command-groups/' + encodeURIComponent(name) + '/enabled', {
                    method: 'PUT',
                    body: JSON.stringify({
                        enabled: !!enabled
                    })
                });
                const updated = Number(result.updated || 0);
                this.$emit('notify', {
                    type: updated > 0 ? 'success' : 'warning',
                    message: '命令组已' + actionText + '：' + name + '（' + updated + ' 条）'
                });
                await this.fetchCommands();
                return updated;
            } catch (e) {
                this.$emit('notify', { type: 'error', message: actionText + '命令组失败: ' + e.message });
                return 0;
            } finally {
                this.groupWorking = false;
            }
        },

        async disableGroup(groupName) {
            this.closeGroupActionMenu();
            await this.setGroupEnabled(groupName, false);
        },

        async enableGroup(groupName) {
            this.closeGroupActionMenu();
            await this.setGroupEnabled(groupName, true);
        },

        async disbandGroup(groupName) {
            const name = String(groupName || '').trim();
            if (!name) return;
            this.closeGroupActionMenu();
            if (!confirm('确定解散命令组「' + name + '」吗？')) return;
            this.groupWorking = true;
            try {
                const result = await this.apiRequest('/api/command-groups/' + encodeURIComponent(name), {
                    method: 'DELETE'
                });
                this.$emit('notify', {
                    type: 'success',
                    message: '命令组已解散：' + name + '（' + (result.updated || 0) + ' 条）'
                });
                await this.fetchCommands();
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '解散命令组失败: ' + e.message });
            } finally {
                this.groupWorking = false;
            }
        },

        async runGroup(groupName) {
            const name = String(groupName || '').trim();
            if (!name) return;
            this.closeGroupActionMenu();
            this.groupWorking = true;
            try {
                const result = await this.apiRequest('/api/command-groups/' + encodeURIComponent(name) + '/execute', {
                    method: 'POST',
                    body: JSON.stringify({
                        include_disabled: !!this.includeDisabledWhenRunGroup,
                        acquire_policy: this.runGroupAcquirePolicy || 'inherit_session'
                    })
                });
                const executed = result.executed || 0;
                const total = result.total || 0;
                const failures = result.failures || 0;
                this.$emit('notify', {
                    type: result.ok ? 'success' : (result.partial_ok ? 'warning' : 'error'),
                    message: result.ok
                        ? ('命令组已执行：' + name + '（成功 ' + executed + ' / ' + total + '）')
                        : (result.partial_ok
                            ? ('命令组部分成功：' + name + '（成功 ' + executed + ' / ' + total + '，失败 ' + failures + '）')
                            : ('命令组执行失败：' + name + '（成功 ' + executed + ' / ' + total + '，失败 ' + failures + '）'))
                });
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '执行命令组失败: ' + e.message });
            } finally {
                this.groupWorking = false;
            }
        },

        ensureValidPage() {
            if (this.currentPage > this.totalPages) {
                this.currentPage = this.totalPages;
            }
            if (this.currentPage < 1) {
                this.currentPage = 1;
            }
        },

        applyPageSize() {
            const value = Number(this.pageSize);
            if (!Number.isFinite(value) || value <= 0) {
                this.pageSize = 16;
            } else {
                this.pageSize = Math.min(500, Math.floor(value));
            }
            this.changePage(1);
        },

        changePage(page) {
            const nextPage = Math.min(this.totalPages, Math.max(1, page));
            this.currentPage = nextPage;
        },

        getCommandOrder(commandId) {
            return this.commands.findIndex(cmd => cmd.id === commandId) + 1;
        },

        toggleHelp() {
            this.showHelpTip = !this.showHelpTip;
        },

        async moveCommand(cmd, direction) {
            if (this.reordering) return;
            const index = this.commands.findIndex(item => item.id === cmd.id);
            if (index < 0) return;

            const targetIndex = index + direction;
            if (targetIndex < 0 || targetIndex >= this.commands.length) return;

            const previous = this.commands.slice();
            const next = this.commands.slice();
            const [moved] = next.splice(index, 1);
            next.splice(targetIndex, 0, moved);
            this.commands = next;
            this.reordering = true;

            try {
                await this.apiRequest('/api/commands/reorder', {
                    method: 'PUT',
                    body: JSON.stringify({ command_ids: next.map(item => item.id) })
                });
                this.ensureValidPage();
            } catch (e) {
                this.commands = previous;
                this.$emit('notify', { type: 'error', message: '排序更新失败: ' + e.message });
            } finally {
                this.reordering = false;
            }
        },

        getTriggerLabel(type) {
            return this.getTriggerTypeMeta(type).label;
        },

        getActionLabel(type) {
            return (this.meta.action_types || {})[type] || type;
        },

        getScopeLabel(scope) {
            const map = { all: '所有标签页', domain: '指定域名', tab: '指定标签页' };
            return map[scope] || scope;
        },

        formatTime(ts) {
            if (!ts) return '从未';
            return new Date(ts * 1000).toLocaleString();
        }
};
