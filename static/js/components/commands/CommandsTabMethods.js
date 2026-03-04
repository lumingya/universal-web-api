// ==================== CommandsTab Methods ====================
window.CommandsTabMethods = {
        async apiRequest(url, options) {
            const token = localStorage.getItem('api_token');
            const headers = { 'Content-Type': 'application/json', ...(options || {}).headers };
            if (token) headers['Authorization'] = 'Bearer ' + token;
            const response = await fetch(url, { ...options, headers });
            if (!response.ok) {
                const err = await response.json().catch(() => ({}));
                throw new Error(err.detail || 'HTTP ' + response.status);
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
            this.initProxyAction(next);
            this.initWebhookAction(next);
            this.initCommandGroupAction(next);
            this.initReleaseLockAction(next);
            return next;
        },

        normalizeCommand(command) {
            const normalized = JSON.parse(JSON.stringify(command || {}));
            normalized.trigger = normalized.trigger || {
                type: 'request_count',
                value: 10,
                command_id: '',
                action_ref: '',
                match_rule: 'equals',
                expected_value: '',
                match_mode: 'keyword',
                status_codes: '403,429,500,502,503,504',
                abort_on_match: true,
                scope: 'all',
                domain: '',
                tab_index: null
            };
            normalized.trigger = this.ensureTriggerDefaults(normalized.trigger);
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
            if (next.action_ref === undefined) next.action_ref = '';
            if (!next.match_rule) next.match_rule = 'equals';
            if (next.expected_value === undefined || next.expected_value === null) next.expected_value = '';
            if (!next.match_mode) next.match_mode = 'keyword';
            if (!next.status_codes) next.status_codes = '403,429,500,502,503,504';
            if (next.abort_on_match === undefined) next.abort_on_match = true;
            if (!next.url_pattern && next.type === 'network_request_error') {
                next.url_pattern = '';
            }
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
                return '切换预设/执行工作流仅建议用于“指定域名”或“指定标签页”。';
            }
            if (this.presetLoading) {
                return '正在加载预设列表...';
            }
            if (this.resolvedPresetDomain) {
                return '当前目标域名: ' + this.resolvedPresetDomain;
            }
            if (scope === 'tab') {
                return '所选标签页当前没有可识别域名，暂时无法列出预设。';
            }
            return '请输入已配置的域名后再选择预设。';
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
            return '请选择来源命令';
        },

        getTriggerTargetLabel(trigger) {
            const type = trigger?.type;
            if (type === 'page_check') return '检查文本';
            if (type === 'command_result_match') return '监听命令';
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
            if (trigger.type === 'command_result_match') {
                const sourceName = this.getCommandName(trigger.command_id);
                const actionLabel = this.getActionRefLabel(trigger.command_id, trigger.action_ref);
                const ruleLabel = this.getMatchRuleLabel(trigger.match_rule || 'equals');
                const expected = String(trigger.expected_value || '');
                return sourceName + ' / ' + actionLabel + ' ' + ruleLabel + ' ' + expected;
            }
            if (trigger.type === 'network_request_error') {
                const pattern = trigger.url_pattern || trigger.value || '';
                const codes = trigger.status_codes || '';
                return (pattern || '*') + ' [' + codes + ']';
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
                    if (this.availablePresets.length === 0) {
                        action.preset_name = '';
                        continue;
                    }
                    if (!this.availablePresets.includes(action.preset_name)) {
                        action.preset_name = this.availablePresets[0];
                    }
                }
            } catch (e) {
                console.error('加载预设列表失败:', e);
                this.availablePresets = [];
                for (const action of this.editingCommand.actions || []) {
                    if (['execute_preset', 'execute_workflow'].includes(action.type)) {
                        action.preset_name = '';
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

            if (trigger.type === 'command_triggered') {
                trigger.value = '';
                if (!this.sourceCommandOptions.some(opt => opt.value === trigger.command_id)) {
                    trigger.command_id = this.sourceCommandOptions[0]?.value || '';
                }
                trigger.action_ref = '';
                trigger.expected_value = '';
                return;
            }

            if (trigger.type === 'command_result_match') {
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
                return;
            }

            if (trigger.type === 'page_check') {
                trigger.command_id = '';
                if (currentValue === 10 || currentValue === '10' || typeof currentValue === 'number') {
                    trigger.value = '';
                }
                return;
            }

            trigger.command_id = '';

            if (['request_count', 'error_count', 'idle_timeout'].includes(trigger.type)) {
                const fallback = this.getNumericTriggerDefault(trigger.type);
                const numericValue = Number(currentValue);
                trigger.value = Number.isFinite(numericValue) && numericValue > 0 ? numericValue : fallback;
                return;
            }

            if (currentValue === '' || currentValue === null || currentValue === undefined) {
                trigger.value = 10;
            }
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
                mode: 'simple',
                trigger: {
                    type: 'request_count',
                    value: 10,
                    command_id: '',
                    action_ref: '',
                    match_rule: 'equals',
                    expected_value: '',
                    match_mode: 'keyword',
                    status_codes: '403,429,500,502,503,504',
                    abort_on_match: true,
                    scope: 'all',
                    domain: '',
                    tab_index: null
                },
                actions: [{ type: 'clear_cookies' }, { type: 'refresh_page' }],
                group_name: '',
                script: '',
                script_lang: 'javascript'
            });
            this.isNew = true;
            this.showEditor = true;
            this.fetchBindingMeta();
        },

        openEditCommand(cmd) {
            this.editingCommand = this.normalizeCommand(cmd);
            if (this.editingCommand?.trigger?.type === 'command_result_match') {
                this.handleResultSourceChange();
            }
            this.isNew = false;
            this.showEditor = true;
            this.fetchBindingMeta().then(() => this.loadPresetOptions());
        },

        addAction() {
            if (!this.editingCommand) return;
            const nextIndex = this.editingCommand.actions.length;
            this.editingCommand.actions.push(this.normalizeAction({ type: 'wait', seconds: 1 }, nextIndex));
        },

        async handleActionTypeChange(action) {
            this.initProxyAction(action);
            this.initWebhookAction(action);
            this.initCommandGroupAction(action);
            this.initReleaseLockAction(action);
            if (action.type === 'execute_workflow' && action.prompt === undefined) {
                action.prompt = '';
            }
            if (['execute_preset', 'execute_workflow'].includes(action.type)) {
                await this.loadPresetOptions();
                if (!action.preset_name && this.availablePresets.length > 0) {
                    action.preset_name = this.availablePresets[0];
                }
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

        initCommandGroupAction(action) {
            if (action.type !== 'execute_command_group') return;
            if (action.include_disabled === undefined) {
                action.include_disabled = false;
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
            if (trigger.type === 'command_result_match') {
                const sourceId = String(trigger.command_id || '').trim();
                if (!sourceId) {
                    this.$emit('notify', { type: 'error', message: '请先选择“监听命令”。' });
                    return;
                }
                if (this.editingCommand.id && sourceId === this.editingCommand.id) {
                    this.$emit('notify', { type: 'error', message: '监听命令不能是当前命令自己。' });
                    return;
                }
                const expected = String(trigger.expected_value || '').trim();
                if (!expected) {
                    this.$emit('notify', { type: 'error', message: '请填写“期望值”。' });
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
            const presetActions = (this.editingCommand.actions || []).filter(action => ['execute_preset', 'execute_workflow'].includes(action.type));
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
            const groupActions = (this.editingCommand.actions || []).filter(action => action.type === 'execute_command_group');
            const invalidGroupAction = groupActions.find(action => !String(action.group_name || '').trim());
            if (invalidGroupAction) {
                this.$emit('notify', { type: 'error', message: '“执行命令组”动作必须选择命令组。' });
                return;
            }
            if (trigger.type === 'network_request_error') {
                trigger.value = trigger.url_pattern || '';
            } else if (trigger.type === 'command_result_match') {
                trigger.value = '';
            }
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
        },

        isCommandSelected(commandId) {
            return (this.selectedCommandIds || []).includes(commandId);
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

        async assignSelectedToGroup() {
            if (!this.hasSelection) {
                this.$emit('notify', { type: 'error', message: '请先勾选命令。' });
                return;
            }
            const groupName = String(this.pendingGroupName || '').trim() || this.getNextDefaultGroupName();
            this.groupWorking = true;
            try {
                const result = await this.apiRequest('/api/command-groups', {
                    method: 'PUT',
                    body: JSON.stringify({
                        command_ids: this.selectedCommandIds,
                        group_name: groupName
                    })
                });
                this.pendingGroupName = groupName;
                this.$emit('notify', {
                    type: 'success',
                    message: '已收纳到命令组：' + groupName + '（' + (result.updated || 0) + ' 条）'
                });
                await this.fetchCommands();
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '收纳失败: ' + e.message });
            } finally {
                this.groupWorking = false;
            }
        },

        async ungroupSelectedCommands() {
            if (!this.hasSelection) {
                this.$emit('notify', { type: 'error', message: '请先勾选命令。' });
                return;
            }
            this.groupWorking = true;
            try {
                const result = await this.apiRequest('/api/command-groups', {
                    method: 'PUT',
                    body: JSON.stringify({
                        command_ids: this.selectedCommandIds,
                        group_name: ''
                    })
                });
                this.$emit('notify', {
                    type: 'success',
                    message: '已解散选中命令的分组（' + (result.updated || 0) + ' 条）'
                });
                await this.fetchCommands();
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '解散失败: ' + e.message });
            } finally {
                this.groupWorking = false;
            }
        },

        async disbandGroup(groupName) {
            const name = String(groupName || '').trim();
            if (!name) return;
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
            this.groupWorking = true;
            try {
                const result = await this.apiRequest('/api/command-groups/' + encodeURIComponent(name) + '/execute', {
                    method: 'POST',
                    body: JSON.stringify({ include_disabled: !!this.includeDisabledWhenRunGroup })
                });
                const executed = result.executed || 0;
                const total = result.total || 0;
                this.$emit('notify', {
                    type: executed > 0 ? 'success' : 'error',
                    message: '命令组已执行：' + name + '（成功 ' + executed + ' / ' + total + '）'
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
            return (this.meta.trigger_types || {})[type] || type;
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
