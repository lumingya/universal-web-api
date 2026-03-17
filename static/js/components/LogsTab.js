// ==================== 日志 Tab 组件 ====================
window.LogsTab = {
    name: 'LogsTab',
    props: {
        logs: { type: Array, required: true },
        filter: { type: String, default: 'ALL' },
        paused: { type: Boolean, default: false }
    },
    emits: ['clear', 'change-filter', 'toggle-pause'],
    computed: {
        filteredLogs() {
            if (this.filter === 'ALL') {
                return this.logs;
            }
            return this.logs.filter(log => log.level === this.filter);
        }
    },
    methods: {
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
            if (log.level === 'INFO' && this.isKeyCmdLog(log.message)) return 'KEY';
            return 'INFO';
        },

        getLogColorClass(log) {
            const tone = this.getLogTone(log);
            const colors = {
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
                'INFO': 'text-green-600 dark:text-green-400',
                'KEY': 'text-sky-500 dark:text-sky-300',
                'AI': 'text-purple-600 dark:text-purple-400',
                'OK': 'text-green-600 dark:text-green-400',
                'WARN': 'text-yellow-600 dark:text-yellow-400',
                'ERROR': 'text-red-600 dark:text-red-400'
            };
            return colors[tone] || colors['INFO'];
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
        <div class="h-full flex flex-col bg-white dark:bg-gray-800">
            <div class="p-4 border-b dark:border-gray-700 flex justify-between items-center">
                <div class="flex gap-2">
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
                            class="border dark:border-gray-700 rounded px-3 py-1 text-sm dark:text-white hover:bg-gray-100 dark:hover:bg-gray-700">
                        {{ paused ? '▶️ 继续' : '⏸ 暂停' }}
                    </button>
                    <button @click="$emit('clear')" 
                            class="border dark:border-gray-700 rounded px-3 py-1 text-sm dark:text-white hover:bg-gray-100 dark:hover:bg-gray-700">
                        <span v-html="$icons.trash"></span> 清除
                    </button>
                </div>
            </div>

            <div ref="logContainer" class="flex-1 overflow-auto p-4 font-mono text-sm space-y-1">
                <div v-for="log in filteredLogs" :key="log.id"
                     :class="['p-2 rounded', getLogColorClass(log)]">
                    <span class="text-gray-500 dark:text-gray-300">{{ log.timestamp }}</span>
                    <span :class="['font-bold mx-2', getLogLevelClass(log)]">[{{ log.level }}]</span>
                    <span class="dark:text-gray-200">{{ log.message }}</span>
                </div>
                <div v-if="filteredLogs.length === 0" 
                     class="text-center text-gray-400 dark:text-gray-500 py-8">
                    暂无日志
                </div>
            </div>
        </div>
    `
};
