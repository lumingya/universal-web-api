// ==================== CommandsTab Computed ====================
window.CommandsTabComputed = {
        triggerTypeOptions() {
            return Object.entries(this.meta.trigger_types || {}).map(([k, v]) => ({ value: k, label: v }));
        },
        actionTypeOptions() {
            return Object.entries(this.meta.action_types || {})
                .filter(([k]) => k !== 'switch_preset')
                .map(([k, v]) => ({ value: k, label: v }));
        },
        sourceCommandOptions() {
            const currentId = this.editingCommand?.id;
            return (this.commands || [])
                .filter(cmd => cmd?.id && cmd.id !== currentId)
                .map(cmd => ({ value: cmd.id, label: cmd.name || cmd.id }));
        },
        resultSourceActionOptions() {
            const sourceId = this.editingCommand?.trigger?.command_id;
            if (!sourceId) return [];
            return this.getCommandActionOptions(sourceId);
        },
        enabledCount() {
            return (this.commands || []).filter(cmd => cmd.enabled).length;
        },
        disabledCount() {
            return (this.commands || []).filter(cmd => !cmd.enabled).length;
        },
        totalPages() {
            return Math.max(1, Math.ceil((this.commands || []).length / this.pageSize));
        },
        paginatedCommands() {
            const start = (this.currentPage - 1) * this.pageSize;
            return (this.commands || []).slice(start, start + this.pageSize);
        },
        pageStartIndex() {
            if (!this.commands.length) return 0;
            return (this.currentPage - 1) * this.pageSize + 1;
        },
        pageEndIndex() {
            return Math.min(this.currentPage * this.pageSize, this.commands.length);
        },
        visiblePageNumbers() {
            const total = this.totalPages;
            const current = this.currentPage;
            const start = Math.max(1, current - 2);
            const end = Math.min(total, start + 4);
            const adjustedStart = Math.max(1, end - 4);
            return Array.from({ length: end - adjustedStart + 1 }, (_, idx) => adjustedStart + idx);
        },
        selectedCommands() {
            if (!Array.isArray(this.selectedCommandIds) || this.selectedCommandIds.length === 0) return [];
            const selectedSet = new Set(this.selectedCommandIds);
            return (this.commands || []).filter(cmd => selectedSet.has(cmd.id));
        },
        hasSelection() {
            return this.selectedCommands.length > 0;
        },
        commandGroups() {
            const bucket = {};
            for (const cmd of (this.commands || [])) {
                const groupName = String(cmd?.group_name || '').trim();
                if (!groupName) continue;
                if (!bucket[groupName]) {
                    bucket[groupName] = {
                        name: groupName,
                        count: 0,
                        enabledCount: 0,
                        commandIds: []
                    };
                }
                bucket[groupName].count += 1;
                bucket[groupName].enabledCount += cmd.enabled ? 1 : 0;
                bucket[groupName].commandIds.push(cmd.id);
            }
            return Object.values(bucket).sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'));
        },
        commandGroupOptions() {
            return this.commandGroups.map(group => ({ value: group.name, label: group.name }));
        },
        paginatedDisplayRows() {
            const rows = [];
            const groupRowMap = {};
            for (const cmd of (this.paginatedCommands || [])) {
                const groupName = String(cmd?.group_name || '').trim();
                if (!groupName) {
                    rows.push({
                        key: 'cmd_' + cmd.id,
                        isGroup: false,
                        groupName: '',
                        commands: [cmd]
                    });
                    continue;
                }
                if (!groupRowMap[groupName]) {
                    const row = {
                        key: 'group_' + groupName,
                        isGroup: true,
                        groupName,
                        commands: []
                    };
                    groupRowMap[groupName] = row;
                    rows.push(row);
                }
                groupRowMap[groupName].commands.push(cmd);
            }
            return rows;
        },
        visiblePageCommandIds() {
            const ids = [];
            for (const row of (this.paginatedDisplayRows || [])) {
                if (row.isGroup && this.isGroupCollapsed(row.groupName)) continue;
                for (const cmd of (row.commands || [])) {
                    ids.push(cmd.id);
                }
            }
            return ids;
        },
        resolvedPresetDomain() {
            return this.getBoundDomain(this.editingCommand);
        },
        scriptPlaceholder() {
            if (!this.editingCommand) return '';
            if (this.editingCommand.script_lang === 'javascript') {
                return '// 在页面中执行的 JavaScript\n' +
                    '// 清除 cookies 并刷新页面\n' +
                    'document.cookie.split(";").forEach(c => {\n' +
                    '  document.cookie = c.trim().split("=")[0] + "=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/";\n' +
                    '});\n' +
                    'location.reload();';
            } else {
                return '# Python 脚本\n' +
                    '# 可用变量: tab, session, browser, config_engine, logger, time, json\n\n' +
                    'logger.info(f"当前 URL: {tab.url}")\n' +
                    'logger.info(f"请求次数: {session.request_count}")\n\n' +
                    '# 清除 cookies 并刷新\n' +
                    'tab.run_js("document.cookie.split(\\";\\").forEach(c => document.cookie = c.trim().split(\\"=\\")[0] + \\"=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/\\");")\n' +
                    'time.sleep(0.5)\n' +
                    'tab.refresh()';
            }
        }
};
