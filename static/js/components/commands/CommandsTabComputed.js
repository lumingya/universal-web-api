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
        actionTypeGroups() {
            const categoryOrder = ['页面操作', '预设与流程', '通知与集成', '任务控制'];
            const categoryMap = {
                clear_cookies: '页面操作',
                refresh_page: '页面操作',
                new_chat: '页面操作',
                run_js: '页面操作',
                wait: '页面操作',
                navigate: '页面操作',
                click_element: '页面操作',
                click_coordinates: '页面操作',
                execute_preset: '预设与流程',
                execute_workflow: '预设与流程',
                execute_command_group: '预设与流程',
                switch_proxy: '通知与集成',
                send_webhook: '通知与集成',
                send_napcat: '通知与集成',
                abort_task: '任务控制',
                release_tab_lock: '任务控制'
            };
            const grouped = new Map();
            for (const option of (this.actionTypeOptions || [])) {
                const category = categoryMap[option.value] || '其他';
                if (!grouped.has(category)) grouped.set(category, []);
                grouped.get(category).push(option);
            }
            const dynamicOrder = Array.from(grouped.keys()).filter(name => !categoryOrder.includes(name));
            return [...categoryOrder, ...dynamicOrder]
                .filter(name => grouped.has(name))
                .map(name => ({ label: name, options: grouped.get(name) }));
        },
        sourceCommandOptions() {
            const currentId = this.editingCommand?.id;
            return (this.commands || [])
                .filter(cmd => cmd?.id && cmd.id !== currentId)
                .map(cmd => ({
                    value: cmd.id,
                    label: cmd.name || cmd.id,
                    groupName: String(cmd.group_name || '').trim(),
                    enabled: cmd.enabled !== false,
                    searchText: [
                        cmd.name || '',
                        cmd.id || '',
                        String(cmd.group_name || '').trim()
                    ].join(' ').toLowerCase()
                }))
                .sort((a, b) => {
                    const aGrouped = a.groupName ? 0 : 1;
                    const bGrouped = b.groupName ? 0 : 1;
                    if (aGrouped !== bGrouped) return aGrouped - bGrouped;
                    if (a.groupName !== b.groupName) {
                        return a.groupName.localeCompare(b.groupName, 'zh-CN');
                    }
                    return a.label.localeCompare(b.label, 'zh-CN');
                });
        },
        selectedSourceCommandOptions() {
            const ids = new Set(
                Array.isArray(this.editingCommand?.trigger?.command_ids)
                    ? this.editingCommand.trigger.command_ids.map(id => String(id || '').trim()).filter(Boolean)
                    : []
            );
            return (this.sourceCommandOptions || []).filter(opt => ids.has(opt.value));
        },
        selectedSourceCommandOption() {
            const selectedId = String(this.editingCommand?.trigger?.command_id || '').trim();
            if (!selectedId) return null;
            return this.sourceCommandOptions.find(opt => opt.value === selectedId) || null;
        },
        filteredSourceCommandSections() {
            const keyword = String(this.sourceCommandSearch || '').trim().toLowerCase();
            const groupedMap = new Map();
            const ungrouped = [];

            for (const option of (this.sourceCommandOptions || [])) {
                if (keyword && !option.searchText.includes(keyword)) {
                    continue;
                }
                if (option.groupName) {
                    if (!groupedMap.has(option.groupName)) {
                        groupedMap.set(option.groupName, []);
                    }
                    groupedMap.get(option.groupName).push(option);
                } else {
                    ungrouped.push(option);
                }
            }

            const groupedSections = Array.from(groupedMap.entries())
                .sort((a, b) => a[0].localeCompare(b[0], 'zh-CN'))
                .map(([name, commands]) => ({
                    key: 'group:' + name,
                    name,
                    commands
                }));

            if (ungrouped.length > 0) {
                groupedSections.push({
                    key: 'group:__ungrouped__',
                    name: '未分组命令',
                    commands: ungrouped,
                    isUngrouped: true
                });
            }

            return groupedSections;
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
        displayRows() {
            const rows = [];
            const groupRowMap = {};
            for (const cmd of (this.commands || [])) {
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
        totalPages() {
            return Math.max(1, Math.ceil((this.displayRows || []).length / this.pageSize));
        },
        pageStartIndex() {
            if (!this.displayRows.length) return 0;
            return (this.currentPage - 1) * this.pageSize + 1;
        },
        pageEndIndex() {
            return Math.min(this.currentPage * this.pageSize, this.displayRows.length);
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
            const start = (this.currentPage - 1) * this.pageSize;
            return (this.displayRows || []).slice(start, start + this.pageSize);
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
