// ==================== 日志 Tab 组件 ====================
window.LogsTab = {
    name: 'LogsTab',
    props: {
        logs: { type: Array, required: true },
        filter: { type: String, default: 'ALL' },
        paused: { type: Boolean, default: false }
    },
    emits: ['clear', 'change-filter', 'toggle-pause'],
    data() {
        return {
            expandedRawLogs: {},
            collapsedRequestGroups: {},
            selectedRequestId: 'ALL',
            viewMode: 'requests'
        };
    },
    computed: {
        filteredLogs() {
            if (this.filter === 'ALL') {
                return this.logs;
            }
            if (this.filter === 'INFO') {
                // 修复：normalizeLogLevel 会把带 [AI] / [OK] 标记的 INFO 日志重分类成 'AI' / 'OK'，
                // 此前只按重分类后的 level 过滤，导致 INFO 档漏掉全部 [AI] 日志。
                // rawLevel 保留了服务端原始级别，用它兜底把 INFO 还原成超集。
                return this.logs.filter(log =>
                    log.rawLevel === 'INFO' || log.level === 'INFO' || log.level === 'OK'
                );
            }
            return this.logs.filter(log => log.level === this.filter);
        },

        requestOptions() {
            const requests = new Map();
            this.filteredLogs.forEach(log => {
                const requestId = this.getRequestId(log);
                const existing = requests.get(requestId);
                if (existing) {
                    existing.count += 1;
                    existing.lastSeq = Math.max(existing.lastSeq, Number(log.seq || 0));
                    return;
                }
                requests.set(requestId, {
                    requestId,
                    requestTag: this.getRequestTag(log),
                    count: 1,
                    firstSeq: Number(log.seq || 0),
                    lastSeq: Number(log.seq || 0)
                });
            });
            return Array.from(requests.values()).sort((left, right) => {
                if (left.requestId === 'SYSTEM') return 1;
                if (right.requestId === 'SYSTEM') return -1;
                return right.firstSeq - left.firstSeq;
            });
        },

        requestFilteredLogs() {
            if (this.selectedRequestId === 'ALL') {
                return this.filteredLogs;
            }
            return this.filteredLogs.filter(log => this.getRequestId(log) === this.selectedRequestId);
        },

        timelineLogs() {
            return this.requestFilteredLogs;
        },

        requestGroups() {
            const groups = new Map();
            this.requestFilteredLogs.forEach(log => {
                const requestId = this.getRequestId(log);
                let group = groups.get(requestId);
                if (!group) {
                    group = {
                        requestId,
                        requestTag: this.getRequestTag(log),
                        logs: [],
                        firstTimestamp: log.timestamp || '',
                        lastTimestamp: log.timestamp || '',
                        errorCount: 0,
                        warningCount: 0
                    };
                    groups.set(requestId, group);
                }
                group.logs.push(log);
                group.lastTimestamp = log.timestamp || group.lastTimestamp;
                if (log.level === 'ERROR') group.errorCount += 1;
                if (log.level === 'WARN') group.warningCount += 1;
            });
            return Array.from(groups.values());
        }
    },
    watch: {
        logs(nextLogs) {
            if (
                this.selectedRequestId !== 'ALL'
                && !(nextLogs || []).some(log => this.getRequestId(log) === this.selectedRequestId)
            ) {
                this.selectedRequestId = 'ALL';
            }
        }
    },
    methods: {
        getRequestId(log) {
            return String(log && log.requestId || 'SYSTEM').trim() || 'SYSTEM';
        },

        getRequestTag(log) {
            return String(log && (log.requestTag || log.requestId) || 'SYSTEM').trim() || 'SYSTEM';
        },

        selectRequest(requestId) {
            this.selectedRequestId = String(requestId || 'ALL');
        },

        isRequestGroupCollapsed(group) {
            const key = String(group.requestId);
            return Object.prototype.hasOwnProperty.call(this.collapsedRequestGroups, key)
                ? Boolean(this.collapsedRequestGroups[key])
                : true;
        },

        toggleRequestGroup(group) {
            const key = String(group.requestId);
            this.collapsedRequestGroups = {
                ...this.collapsedRequestGroups,
                [key]: !this.isRequestGroupCollapsed(group)
            };
        },

        getRequestGroupStatus(group) {
            if (group.errorCount > 0) return `${group.errorCount} 条错误`;
            if (group.warningCount > 0) return `${group.warningCount} 条警告`;
            return '无异常';
        },

        getRequestGroupStatusClass(group) {
            if (group.errorCount > 0) return 'text-red-600 dark:text-red-400';
            if (group.warningCount > 0) return 'text-yellow-600 dark:text-yellow-400';
            return 'text-green-600 dark:text-green-400';
        },

        getLogText(log) {
            return log.messageText || log.message || '';
        },

        getRawLogText(log) {
            return log.originalMessageText || log.message || this.getLogText(log);
        },

        hasRawLogText(log) {
            return Boolean(log && log.messageAlias && this.getRawLogText(log));
        },

        isRawExpanded(log) {
            return Boolean(this.expandedRawLogs[String(log.id)]);
        },

        toggleRawLog(log) {
            const key = String(log.id);
            this.expandedRawLogs = {
                ...this.expandedRawLogs,
                [key]: !this.expandedRawLogs[key]
            };
        },

        isKeyCmdLog(message) {
            if (!message || !message.includes('[CMD]')) {
                return false;
            }

            const keyPatterns = [
                '[CMD] 执行:',
                '[CMD] 触发命令:',
                '[CMD] 链式触发:',
                '[CMD] 条件分支触发:',
                '[CMD] 结果事件触发:'
            ];

            return keyPatterns.some(pattern => message.includes(pattern));
        },

        getLogTone(log) {
            if (log.level === 'ERROR') return 'ERROR';
            if (log.level === 'WARN') return 'WARN';
            if (log.level === 'AI') return 'AI';
            if (log.level === 'OK') return 'OK';
            if (log.level === 'DEBUG') return 'DEBUG';
            if (log.level === 'INFO' && this.isKeyCmdLog(this.getLogText(log))) return 'KEY';
            return 'INFO';
        },

        getLogColorClass(log) {
            const tone = this.getLogTone(log);
            const colors = {
                'DEBUG': 'bg-slate-50 dark:bg-slate-900/30',
                'INFO': 'bg-green-50 dark:bg-green-900/20',
                'KEY': 'bg-sky-50 dark:bg-sky-900/20',
                'AI': 'bg-purple-50 dark:bg-purple-900/20',
                'OK': 'bg-green-50 dark:bg-green-900/20',
                'WARN': 'bg-yellow-50 dark:bg-yellow-900/20',
                'ERROR': 'bg-red-50 dark:bg-red-900/20'
            };
            return colors[tone] || colors['INFO'];
        },

        getLogLevelClass(log) {
            const tone = this.getLogTone(log);
            const colors = {
                'DEBUG': 'text-slate-500 dark:text-slate-400',
                'INFO': 'text-green-600 dark:text-green-400',
                'KEY': 'text-sky-500 dark:text-sky-300',
                'AI': 'text-purple-600 dark:text-purple-400',
                'OK': 'text-green-600 dark:text-green-400',
                'WARN': 'text-yellow-600 dark:text-yellow-400',
                'ERROR': 'text-red-600 dark:text-red-400'
            };
            return colors[tone] || colors['INFO'];
        },

        getLogTextClass(log) {
            // DEBUG 行正文做视觉降噪（灰色弱化），与控制台的暗灰 DEBUG 保持一致
            return this.getLogTone(log) === 'DEBUG'
                ? 'text-gray-500 dark:text-gray-400'
                : 'dark:text-gray-200';
        }
    },
    updated() {
        this.$nextTick(() => {
            const container = this.$refs.logContainer;
            if (container) {
                container.scrollTop = container.scrollHeight;
            }
        });
    },
    template: `
        <div class="logs-workspace h-full flex flex-col bg-white dark:bg-gray-800">
            <div class="p-3 sm:p-4 border-b dark:border-gray-700 space-y-3">
                <div class="flex flex-wrap items-center justify-between gap-3">
                    <div class="flex flex-wrap gap-2" aria-label="日志级别">
                    <button @click="$emit('change-filter', 'DEBUG')"
                            :class="['px-3 py-1 text-sm rounded', 
                                     filter === 'DEBUG' ? 'bg-slate-500 text-white' : 'border dark:border-gray-700 dark:text-gray-300']">
                        DEBUG
                    </button>
                    <button @click="$emit('change-filter', 'ALL')"
                            :class="['px-3 py-1 text-sm rounded', 
                                     filter === 'ALL' ? 'bg-blue-500 text-white' : 'border dark:border-gray-700 dark:text-gray-300']">
                        全部
                    </button>
                    <button @click="$emit('change-filter', 'INFO')"
                            :class="['px-3 py-1 text-sm rounded', 
                                     filter === 'INFO' ? 'bg-green-500 text-white' : 'border dark:border-gray-700 dark:text-gray-300']">
                        INFO
                    </button>
                    <button @click="$emit('change-filter', 'AI')"
                            :class="['px-3 py-1 text-sm rounded', 
                                     filter === 'AI' ? 'bg-purple-500 text-white' : 'border dark:border-gray-700 dark:text-gray-300']">
                        AI
                    </button>
                    <button @click="$emit('change-filter', 'WARN')"
                            :class="['px-3 py-1 text-sm rounded', 
                                     filter === 'WARN' ? 'bg-yellow-500 text-white' : 'border dark:border-gray-700 dark:text-gray-300']">
                        WARN
                    </button>
                    <button @click="$emit('change-filter', 'ERROR')"
                            :class="['px-3 py-1 text-sm rounded', 
                                     filter === 'ERROR' ? 'bg-red-500 text-white' : 'border dark:border-gray-700 dark:text-gray-300']">
                        ERROR
                    </button>
                    </div>
                    <div class="flex gap-2">
                        <button @click="$emit('toggle-pause')"
                                class="inline-flex h-9 items-center gap-1.5 border dark:border-gray-700 rounded px-3 text-sm dark:text-white hover:bg-gray-100 dark:hover:bg-gray-700">
                            <span v-html="paused ? $icons.play : $icons.pause"></span>
                            {{ paused ? '继续' : '暂停' }}
                        </button>
                        <button @click="$emit('clear')"
                                class="inline-flex h-9 items-center gap-1.5 border dark:border-gray-700 rounded px-3 text-sm dark:text-white hover:bg-gray-100 dark:hover:bg-gray-700">
                            <span v-html="$icons.trash"></span> 清除
                        </button>
                    </div>
                </div>

                <div class="flex flex-wrap items-center justify-between gap-3">
                    <label class="flex min-w-0 flex-1 items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
                        <span class="shrink-0">请求</span>
                        <select v-model="selectedRequestId"
                                class="h-9 min-w-0 w-full sm:w-72 rounded border border-gray-300 bg-white px-2 text-sm text-gray-800 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100">
                            <option value="ALL">全部请求（{{ requestOptions.length }}）</option>
                            <option v-for="option in requestOptions" :key="option.requestId" :value="option.requestId">
                                {{ option.requestTag }} · {{ option.count }} 条
                            </option>
                        </select>
                    </label>
                    <div class="inline-flex h-9 shrink-0 rounded border border-gray-300 p-0.5 dark:border-gray-600" aria-label="日志排列方式">
                        <button type="button"
                                @click="viewMode = 'requests'"
                                :class="['rounded px-3 text-sm', viewMode === 'requests' ? 'bg-gray-800 text-white dark:bg-gray-100 dark:text-gray-900' : 'text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700']">
                            按请求
                        </button>
                        <button type="button"
                                @click="viewMode = 'timeline'"
                                :class="['rounded px-3 text-sm', viewMode === 'timeline' ? 'bg-gray-800 text-white dark:bg-gray-100 dark:text-gray-900' : 'text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700']">
                            时间线
                        </button>
                    </div>
                </div>
            </div>

            <div ref="logContainer" class="flex-1 overflow-auto p-3 sm:p-4 font-mono text-sm">
                <div v-if="viewMode === 'requests' && requestGroups.length > 0" class="space-y-3">
                    <section v-for="group in requestGroups" :key="group.requestId"
                             class="overflow-hidden rounded border border-gray-200 dark:border-gray-700">
                        <button type="button"
                                @click="toggleRequestGroup(group)"
                                :aria-expanded="!isRequestGroupCollapsed(group)"
                                class="flex w-full flex-wrap items-center justify-between gap-2 bg-gray-50 px-3 py-2 text-left hover:bg-gray-100 dark:bg-gray-900/60 dark:hover:bg-gray-900">
                            <span class="flex min-w-0 items-center gap-2">
                                <span class="text-gray-400" v-html="isRequestGroupCollapsed(group) ? $icons.chevronDown : $icons.chevronUp"></span>
                                <strong class="text-gray-900 dark:text-gray-100">{{ group.requestTag }}</strong>
                                <span class="truncate text-xs text-gray-500 dark:text-gray-400">{{ group.requestId }}</span>
                            </span>
                            <span class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
                                <span>{{ group.logs.length }} 条</span>
                                <span>{{ group.firstTimestamp }}<template v-if="group.lastTimestamp && group.lastTimestamp !== group.firstTimestamp"> - {{ group.lastTimestamp }}</template></span>
                                <span :class="getRequestGroupStatusClass(group)">{{ getRequestGroupStatus(group) }}</span>
                            </span>
                        </button>

                        <div v-show="!isRequestGroupCollapsed(group)" class="divide-y divide-gray-200 dark:divide-gray-700">
                            <div v-for="log in group.logs" :key="log.id"
                                 :class="['px-3 py-2', getLogColorClass(log)]">
                                <div class="flex flex-wrap items-center gap-2">
                                    <span class="text-gray-500 dark:text-gray-300">{{ log.timestamp }}</span>
                                    <span :class="['font-bold', getLogLevelClass(log)]">[{{ log.level }}]</span>
                                    <span v-if="log.logger" class="px-1.5 py-0.5 rounded bg-white/70 dark:bg-gray-900/40 text-gray-600 dark:text-gray-300">
                                        {{ log.logger }}
                                    </span>
                                </div>
                                <div :class="['mt-1 break-all whitespace-pre-wrap', getLogTextClass(log)]">
                                    <span>{{ getLogText(log) }}</span>
                                    <button v-if="hasRawLogText(log)"
                                            @click="toggleRawLog(log)"
                                            class="ml-2 inline-flex items-center rounded border border-gray-300 px-1.5 py-0.5 text-xs text-gray-600 hover:bg-white/70 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-900/50">
                                        {{ isRawExpanded(log) ? '收起原文' : '展开原文' }}
                                    </button>
                                </div>
                                <pre v-if="hasRawLogText(log) && isRawExpanded(log)"
                                     class="mt-2 max-h-64 overflow-auto rounded bg-gray-950 p-2 text-xs text-gray-100 whitespace-pre-wrap break-words select-all">{{ getRawLogText(log) }}</pre>
                            </div>
                        </div>
                    </section>
                </div>

                <div v-else-if="viewMode === 'timeline' && timelineLogs.length > 0" class="space-y-1">
                    <div v-for="log in timelineLogs" :key="log.id"
                         :class="['p-2 rounded', getLogColorClass(log)]">
                        <div class="flex flex-wrap items-center gap-2">
                            <span class="text-gray-500 dark:text-gray-300">{{ log.timestamp }}</span>
                            <span :class="['font-bold', getLogLevelClass(log)]">[{{ log.level }}]</span>
                            <span v-if="log.logger" class="px-1.5 py-0.5 rounded bg-white/70 dark:bg-gray-900/40 text-gray-600 dark:text-gray-300">
                                {{ log.logger }}
                            </span>
                            <span v-if="log.requestTag || log.requestId" class="px-1.5 py-0.5 rounded bg-white/70 dark:bg-gray-900/40 text-gray-500 dark:text-gray-400">
                                {{ log.requestTag || log.requestId }}
                            </span>
                        </div>
                        <div :class="['mt-1 break-all whitespace-pre-wrap', getLogTextClass(log)]">
                            <span>{{ getLogText(log) }}</span>
                            <button v-if="hasRawLogText(log)"
                                    @click="toggleRawLog(log)"
                                    class="ml-2 inline-flex items-center rounded border border-gray-300 px-1.5 py-0.5 text-xs text-gray-600 hover:bg-white/70 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-900/50">
                                {{ isRawExpanded(log) ? '收起原文' : '展开原文' }}
                            </button>
                        </div>
                        <pre v-if="hasRawLogText(log) && isRawExpanded(log)"
                             class="mt-2 max-h-64 overflow-auto rounded bg-gray-950 p-2 text-xs text-gray-100 whitespace-pre-wrap break-words select-all">{{ getRawLogText(log) }}</pre>
                    </div>
                </div>

                <div v-else
                     class="text-center text-gray-400 dark:text-gray-500 py-8">
                    {{ selectedRequestId === 'ALL' ? '暂无日志' : '该请求暂无符合当前级别的日志' }}
                </div>
            </div>
        </div>
    `
};
