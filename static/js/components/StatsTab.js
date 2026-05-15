// ==================== 统计 Tab 组件 ====================
window.StatsTab = {
    name: 'StatsTab',
    props: {
        statsSummary: { type: Object, default: () => ({}) },
        statsDaily: { type: Array, default: () => [] },
        statsHistory: { type: Object, default: () => ({ items: [], total: 0, page: 1, page_size: 50, total_pages: 1 }) },
        isLoadingStats: { type: Boolean, default: false },
        statsFilter: { type: Object, default: () => ({ domain: '', status: '', page: 1 }) },
    },
    emits: ['load-stats', 'load-history', 'clear-stats', 'change-filter'],
    data() {
        return {
            expandedRow: null,
        };
    },
    computed: {
        summary() {
            return this.statsSummary || {};
        },
        topDomains() {
            return (this.summary.top_domains || []).slice(0, 6);
        },
        maxDomainCount() {
            const items = this.topDomains;
            if (items.length === 0) return 1;
            return Math.max(...items.map(d => d.count), 1);
        },
        todayReq() {
            return this.summary.today_requests || 0;
        },
        totalChars() {
            const c = this.summary.total_characters || 0;
            if (c > 1000000) return (c / 1000000).toFixed(1) + 'M';
            if (c > 1000) return (c / 1000).toFixed(1) + 'K';
            return c;
        },
        successRate() {
            return this.summary.success_rate || 0;
        },
        avgDuration() {
            return this.summary.avg_duration_ms || 0;
        },
        historyItems() {
            return this.statsHistory.items || [];
        },
        totalPages() {
            return this.statsHistory.total_pages || 1;
        },
        currentPage() {
            return this.statsFilter.page || 1;
        },
        maxDayCount() {
            const days = this.statsDaily || [];
            if (days.length === 0) return 1;
            return Math.max(...days.map(d => d.count), 1);
        },
    },
    methods: {
        formatTime(ts) {
            if (!ts) return '-';
            const d = new Date(ts * 1000);
            const pad = (n) => String(n).padStart(2, '0');
            return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
        },
        formatDuration(ms) {
            if (!ms) return '-';
            if (ms < 1000) return ms + 'ms';
            return (ms / 1000).toFixed(1) + 's';
        },
        statusClass(status) {
            if (status === 'success') return 'text-green-600 dark:text-green-400';
            if (status === 'failed') return 'text-red-600 dark:text-red-400';
            return 'text-yellow-600 dark:text-yellow-400';
        },
        statusLabel(status) {
            if (status === 'success') return '成功';
            if (status === 'failed') return '失败';
            if (status === 'cancelled') return '已取消';
            return status;
        },
        prevPage() {
            if (this.currentPage > 1) {
                this.$emit('change-filter', { ...this.statsFilter, page: this.currentPage - 1 });
            }
        },
        nextPage() {
            if (this.currentPage < this.totalPages) {
                this.$emit('change-filter', { ...this.statsFilter, page: this.currentPage + 1 });
            }
        },
        toggleRow(id) {
            this.expandedRow = this.expandedRow === id ? null : id;
        },
        onFilterChange() {
            this.$emit('change-filter', { ...this.statsFilter, page: 1 });
        },
        loadStats() {
            this.$emit('load-stats');
        },
        loadHistory() {
            this.$emit('load-history');
        },
        clearStats() {
            if (confirm('确定要清理所有统计历史记录吗？')) {
                this.$emit('clear-stats');
            }
        },
    },
    template: `
        <div class="h-full overflow-auto p-4 md:p-6 bg-gray-50 dark:bg-gray-900">
            <div class="max-w-7xl mx-auto space-y-6">

                <!-- ========== 操作栏 ========== -->
                <div class="flex justify-between items-center">
                    <h2 class="text-xl font-bold text-gray-900 dark:text-white">📊 请求统计</h2>
                    <div class="flex gap-2">
                        <button @click="loadStats"
                                class="px-3 py-1.5 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors flex items-center gap-1 shadow-sm">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182"/></svg>
                            刷新
                        </button>
                        <button @click="clearStats"
                                class="px-3 py-1.5 text-sm font-medium text-gray-700 bg-white dark:text-gray-200 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 rounded-lg transition-colors flex items-center gap-1 shadow-sm">
                            清理数据
                        </button>
                    </div>
                </div>

                <div v-if="isLoadingStats" class="text-center py-12 text-gray-400">
                    加载中...
                </div>

                <template v-else>
                    <!-- ========== 概览卡片 ========== -->
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                        <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
                            <div class="text-xs text-gray-500 dark:text-gray-400 mb-1">今日请求</div>
                            <div class="text-2xl font-bold text-gray-900 dark:text-white">{{ todayReq }}</div>
                            <div class="text-xs text-gray-400 mt-1">本周 {{ summary.week_requests || 0 }}</div>
                        </div>
                        <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
                            <div class="text-xs text-gray-500 dark:text-gray-400 mb-1">总字符数</div>
                            <div class="text-2xl font-bold text-gray-900 dark:text-white">{{ totalChars }}</div>
                            <div class="text-xs text-gray-400 mt-1">累计 {{ summary.total_requests || 0 }} 次请求</div>
                        </div>
                        <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
                            <div class="text-xs text-gray-500 dark:text-gray-400 mb-1">成功率</div>
                            <div class="text-2xl font-bold" :class="successRate >= 90 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'">{{ successRate }}%</div>
                            <div class="text-xs text-gray-400 mt-1">平均耗时 {{ avgDuration }}ms</div>
                        </div>
                        <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
                            <div class="text-xs text-gray-500 dark:text-gray-400 mb-1">平均耗时</div>
                            <div class="text-2xl font-bold text-gray-900 dark:text-white">{{ formatDuration(avgDuration) }}</div>
                            <div class="text-xs text-gray-400 mt-1">成功请求</div>
                        </div>
                    </div>

                    <!-- ========== 每日趋势图 ========== -->
                    <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
                        <div class="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-3">📈 每日请求趋势（近7天）</div>
                        <div class="relative" style="height: 120px;">
                            <div v-if="statsDaily.length === 0" class="absolute inset-0 flex items-center justify-center text-xs text-gray-400">
                                暂无数据
                            </div>
                            <div v-else class="flex items-end gap-2 h-full pt-2 pb-1">
                                <div v-for="(day, idx) in statsDaily" :key="idx"
                                     class="flex-1 flex flex-col items-center gap-1 h-full justify-end">
                                    <div class="text-xs text-gray-400 dark:text-gray-500 leading-none">{{ day.count }}</div>
                                    <div :style="{ height: Math.max(4, (day.count / maxDayCount) * 70) + 'px' }"
                                         class="w-full rounded-t bg-blue-500 dark:bg-blue-400 opacity-80 hover:opacity-100 transition-opacity cursor-pointer"
                                         :title="new Date(day.timestamp * 1000).toLocaleDateString() + ': ' + day.count + ' 请求, ' + day.characters + ' 字符'">
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- ========== 域名排名 + 历史记录 ========== -->
                    <div class="grid grid-cols-1 xl:grid-cols-2 gap-6">
                        <!-- 域名排名 -->
                        <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
                            <div class="p-4 border-b border-gray-100 dark:border-gray-700 text-sm font-semibold text-gray-800 dark:text-gray-200">
                                🌐 域名排名
                            </div>
                            <div class="p-4 space-y-3">
                                <div v-if="topDomains.length === 0" class="text-xs text-gray-400 text-center py-4">
                                    暂无数据
                                </div>
                                <div v-for="(d, idx) in topDomains" :key="d.domain"
                                     class="flex items-center gap-3">
                                    <span class="text-xs font-mono text-gray-400 w-4">{{ idx + 1 }}</span>
                                    <div class="flex-1 min-w-0">
                                        <div class="flex justify-between text-xs">
                                            <span class="font-medium text-gray-700 dark:text-gray-300 truncate">{{ d.domain }}</span>
                                            <span class="text-gray-400 ml-2">{{ d.count }} 次</span>
                                        </div>
                                        <div class="mt-1 h-1.5 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden">
                                            <div class="h-full bg-blue-500 dark:bg-blue-400 rounded-full transition-all"
                                                 :style="{ width: (d.count / maxDomainCount * 100) + '%' }">
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- 历史记录 -->
                        <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 flex flex-col">
                            <div class="p-4 border-b border-gray-100 dark:border-gray-700">
                                <div class="flex justify-between items-center mb-3">
                                    <span class="text-sm font-semibold text-gray-800 dark:text-gray-200">📋 最近请求</span>
                                    <span class="text-xs text-gray-400">共 {{ statsHistory.total || 0 }} 条</span>
                                </div>
                                <div class="flex gap-2">
                                    <input v-model="statsFilter.domain" @input="onFilterChange"
                                           placeholder="过滤域名..."
                                           class="flex-1 text-xs px-2 py-1.5 border border-gray-200 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-900 text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-500">
                                    <select v-model="statsFilter.status" @change="onFilterChange"
                                            class="text-xs px-2 py-1.5 border border-gray-200 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-900 text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-500">
                                        <option value="">全部</option>
                                        <option value="success">成功</option>
                                        <option value="failed">失败</option>
                                        <option value="cancelled">已取消</option>
                                    </select>
                                    <button @click="loadHistory"
                                            class="text-xs px-2 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
                                        查询
                                    </button>
                                </div>
                            </div>
                            <div class="overflow-auto flex-1" style="max-height: 360px;">
                                <div v-if="historyItems.length === 0" class="text-xs text-gray-400 text-center py-8">
                                    暂无记录
                                </div>
                                <div v-for="item in historyItems" :key="item.id"
                                     class="border-b border-gray-50 dark:border-gray-700/50 last:border-0">
                                    <div @click="toggleRow(item.id)"
                                         class="flex items-center gap-3 px-4 py-2.5 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/30 transition-colors text-xs">
                                        <span :class="statusClass(item.status)">{{ statusLabel(item.status) }}</span>
                                        <span class="flex-1 truncate text-gray-700 dark:text-gray-300">{{ item.domain || '-' }}</span>
                                        <span class="text-gray-400 hidden sm:inline">{{ formatDuration(item.duration_ms) }}</span>
                                        <span class="text-gray-400">{{ formatTime(item.timestamp) }}</span>
                                    </div>
                                    <div v-if="expandedRow === item.id"
                                         class="px-4 py-2 bg-gray-50 dark:bg-gray-900/30 text-xs text-gray-500 dark:text-gray-400 space-y-1 border-t border-gray-100 dark:border-gray-700/50">
                                        <div><span class="font-medium">请求ID:</span> {{ item.request_id }}</div>
                                        <div><span class="font-medium">模型:</span> {{ item.model || '-' }}</div>
                                        <div><span class="font-medium">输入字符:</span> {{ item.message_length || 0 }}</div>
                                        <div><span class="font-medium">输出字符:</span> {{ item.response_length || 0 }}</div>
                                        <div><span class="font-medium">耗时:</span> {{ formatDuration(item.duration_ms) }}</div>
                                        <div v-if="item.error_message"><span class="font-medium text-red-500">错误:</span> {{ item.error_message }}</div>
                                    </div>
                                </div>
                            </div>
                            <div v-if="totalPages > 1" class="flex justify-between items-center px-4 py-2 border-t border-gray-100 dark:border-gray-700">
                                <button @click="prevPage" :disabled="currentPage <= 1"
                                        class="text-xs px-2 py-1 rounded border border-gray-200 dark:border-gray-700 disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors">
                                    上一页
                                </button>
                                <span class="text-xs text-gray-400">{{ currentPage }} / {{ totalPages }}</span>
                                <button @click="nextPage" :disabled="currentPage >= totalPages"
                                        class="text-xs px-2 py-1 rounded border border-gray-200 dark:border-gray-700 disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors">
                                    下一页
                                </button>
                            </div>
                        </div>
                    </div>
                </template>
            </div>
        </div>
    `
};
