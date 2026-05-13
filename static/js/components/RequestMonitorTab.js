window.RequestMonitorTab = {
    name: 'RequestMonitorTab',
    props: {
        records: { type: Array, default: () => [] },
        systemStats: {
            type: Object,
            default: () => ({ memory_mb: 0, disk_status: '未知', total_requests: 0 })
        },
        loading: { type: Boolean, default: false },
        error: { type: String, default: '' }
    },
    emits: ['refresh', 'load-detail'],
    data() {
        return {
            visibleCount: 20,
            selectedRecord: null,
            showErrorStack: false,
            expandedTextBlocks: {}
        }
    },
    computed: {
        recordSummary() {
            const items = Array.isArray(this.records)
                ? this.records.map((record, index) => this.toRecordView(record, index))
                : []
            const sorted = items.sort((a, b) => Number(b.created_at || 0) - Number(a.created_at || 0))
            const domains = new Map()
            let success = 0
            sorted.forEach(item => {
                if (item && item.success) {
                    success += 1
                }
                const domain = String(item && item.__domain || item && item.target_domain || item && item.route_domain || '未知域名').trim() || '未知域名'
                const current = domains.get(domain) || { domain, total: 0, success: 0, failed: 0, rate: 0 }
                current.total += 1
                if (item && item.success) {
                    current.success += 1
                } else {
                    current.failed += 1
                }
                domains.set(domain, current)
            })
            const domainStats = Array.from(domains.values())
                .map(item => ({
                    ...item,
                    rate: item.total ? Math.round((item.success / item.total) * 100) : 0
                }))
                .sort((a, b) => b.total - a.total || b.rate - a.rate)
                .slice(0, 10)

            return {
                sorted,
                success,
                failure: sorted.length - success,
                successRate: sorted.length ? Math.round((success / sorted.length) * 100) : 0,
                domainStats
            }
        },
        sortedRecords() {
            return this.recordSummary.sorted
        },
        visibleRecords() {
            return this.sortedRecords.slice(0, this.visibleCount)
        },
        hasMoreRecords() {
            return this.visibleCount < this.sortedRecords.length
        },
        successCount() {
            return this.recordSummary.success
        },
        failureCount() {
            return this.recordSummary.failure
        },
        globalSuccessRate() {
            return this.recordSummary.successRate
        },
        domainStats() {
            return this.recordSummary.domainStats
        },
        selectedTimingText() {
            if (!this.selectedRecord) return ''
            return '排队等待: ' + this.formatDurationMs(this.selectedRecord.queue_ms) + ' + 生成耗时: ' + this.formatDurationMs(this.selectedRecord.generation_ms)
        }
    },
    watch: {
        records() {
            if (this.visibleCount > this.sortedRecords.length) {
                this.visibleCount = Math.max(20, this.sortedRecords.length)
            }
            if (this.selectedRecord && this.selectedRecord.id) {
                const current = this.sortedRecords.find(item => item.id === this.selectedRecord.id)
                if (current) {
                    this.selectedRecord = current
                }
            }
        }
    },
    methods: {
        toRecordView(record, index) {
            const source = record && typeof record === 'object' ? record : {}
            const domain = String(source.target_domain || source.route_domain || '未知域名').trim() || '未知域名'
            const summarySource = source.summary || source.response_preview || source.response || source.error_message
            const success = !!source.success
            return {
                ...source,
                id: source.id || ('record-' + index),
                __domain: domain,
                __statusText: this.statusText(source),
                __statusIcon: success ? '🟢' : '🔴',
                __statusClasses: this.statusClasses(source),
                __statusPillClasses: this.statusPillClasses(source),
                __summaryText: this.compactText(summarySource, 52),
                __startedText: this.formatDateTime(source.started_at || source.created_at),
                __finishedText: this.formatDateTime(source.finished_at),
                __durationText: this.formatDurationMs(source.duration_ms),
                __tokenText: this.tokenEstimate(source),
                __tabLabel: this.tabLabel(source)
            }
        },
        refresh() {
            this.$emit('refresh')
        },
        loadMore() {
            this.visibleCount = Math.min(this.visibleCount + 20, this.sortedRecords.length)
        },
        openRecord(record) {
            this.selectedRecord = record
            this.showErrorStack = false
            this.expandedTextBlocks = {}
            if (record && record.id && record.has_detail && !record.detail_loaded) {
                this.$emit('load-detail', record.id)
            }
        },
        closeRecord() {
            this.selectedRecord = null
            this.showErrorStack = false
            this.expandedTextBlocks = {}
        },
        formatDurationMs(value) {
            const ms = Number(value || 0)
            if (!Number.isFinite(ms) || ms <= 0) return '0s'
            if (ms < 1000) return Math.round(ms) + 'ms'
            return (ms / 1000).toFixed(ms >= 10000 ? 1 : 2).replace(/\.0$/, '') + 's'
        },
        formatTime(value) {
            const timestamp = Number(value || 0)
            if (!timestamp) return '-'
            const date = new Date(timestamp * 1000)
            if (Number.isNaN(date.getTime())) return '-'
            return date.toLocaleTimeString('zh-CN', {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            })
        },
        formatDateTime(value) {
            const timestamp = Number(value || 0)
            if (!timestamp) return '-'
            const date = new Date(timestamp * 1000)
            if (Number.isNaN(date.getTime())) return '-'
            return date.toLocaleString('zh-CN', {
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            })
        },
        formatNumber(value) {
            return (Number(value) || 0).toLocaleString('zh-CN')
        },
        statusTone(record) {
            return record && record.success ? 'success' : 'failed'
        },
        statusText(record) {
            if (record && record.success) return '成功'
            if (record && record.status === 'cancelled') return '已取消'
            return '失败'
        },
        statusClasses(record) {
            if (record && record.success) {
                return 'border-emerald-200 bg-emerald-50/90 hover:bg-emerald-50 dark:border-emerald-500/25 dark:bg-emerald-900/10 dark:hover:bg-emerald-900/20'
            }
            return 'border-rose-200 bg-rose-50/90 hover:bg-rose-50 dark:border-rose-500/25 dark:bg-rose-900/10 dark:hover:bg-rose-900/20'
        },
        statusPillClasses(record) {
            if (record && record.success) {
                return 'bg-emerald-100 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-200 dark:ring-emerald-500/30'
            }
            return 'bg-rose-100 text-rose-700 ring-rose-200 dark:bg-rose-500/15 dark:text-rose-200 dark:ring-rose-500/30'
        },
        rateToneClass(rate) {
            if (rate >= 90) return 'bg-emerald-500'
            if (rate >= 70) return 'bg-amber-500'
            return 'bg-rose-500'
        },
        rateBadge(rate) {
            if (rate >= 90) return '🟢'
            if (rate >= 70) return '🟡'
            return '🔴'
        },
        compactText(value, max = 50) {
            const text = String(value || '').replace(/\s+/g, ' ').trim()
            if (!text) return '暂无响应摘要'
            return text.length > max ? text.slice(0, max) + '...' : text
        },
        getDetailText(record, key) {
            if (!record) return ''
            if (key === 'prompt') {
                return String(record.prompt || record.prompt_preview || '暂无请求上下文')
            }
            return String(record.response || record.response_preview || record.error_message || '暂无响应内容')
        },
        isTextBlockExpanded(key) {
            return !!this.expandedTextBlocks[key]
        },
        getTextBlockPreview(record, key) {
            const text = this.getDetailText(record, key)
            if (this.isTextBlockExpanded(key) || text.length <= 6000) {
                return text
            }
            return text.slice(0, 6000) + '\n\n[已截断预览，点击展开全文]'
        },
        shouldShowExpandTextButton(record, key) {
            const lengths = record && record.detail_text_lengths ? record.detail_text_lengths : {}
            return this.getDetailText(record, key).length > 6000 || Number(lengths[key] || 0) > 6000
        },
        toggleTextBlock(key) {
            this.expandedTextBlocks = {
                ...this.expandedTextBlocks,
                [key]: !this.expandedTextBlocks[key]
            }
        },
        tokenEstimate(record) {
            const estimate = record && record.token_estimate ? record.token_estimate : {}
            return this.formatNumber(estimate.total || 0)
        },
        tabLabel(record) {
            const tabIndex = Number(record && record.tab_index || 0)
            if (tabIndex > 0) return 'Tab #' + tabIndex
            const tabId = String(record && record.tab_id || '').trim()
            return tabId ? tabId : '未绑定'
        }
    },
    template: `
        <div class="min-h-full bg-slate-50 px-4 py-5 text-slate-900 dark:bg-slate-950 dark:text-slate-100 sm:px-6">
            <div class="mx-auto max-w-7xl space-y-5">
                <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                        <h2 class="text-xl font-bold text-slate-950 dark:text-white">📊 请求监控</h2>
                        <p class="mt-1 text-[11px] text-slate-400 dark:text-slate-500">最近 200 条请求，已自动过滤超大 Base64 图片数据。</p>
                    </div>
                    <button @click="refresh"
                            :disabled="loading"
                            class="inline-flex items-center justify-center rounded-xl border border-slate-200 bg-white/80 px-3 py-2 text-sm font-medium text-slate-600 shadow-sm backdrop-blur transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-60 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-200 dark:hover:bg-slate-800">
                        <span v-html="$icons.arrowPath"></span>
                        {{ loading ? '刷新中...' : '刷新数据' }}
                    </button>
                </div>

                <div v-if="error"
                     class="rounded-2xl border border-rose-200 bg-rose-50/90 px-4 py-3 text-sm text-rose-700 shadow-sm backdrop-blur dark:border-rose-500/30 dark:bg-rose-950/30 dark:text-rose-200">
                    {{ error }}
                </div>

                <section class="grid gap-4 xl:grid-cols-[1.05fr_1fr_1.45fr]">
                    <article class="rounded-2xl border border-slate-200 bg-white/80 p-4 shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-900/70">
                        <div class="flex items-center justify-between">
                            <div>
                                <div class="text-[11px] uppercase text-slate-400 dark:text-slate-500">系统占用</div>
                                <div class="mt-2 text-3xl font-bold text-slate-950 dark:text-white">{{ formatNumber(systemStats.memory_mb) }} MB</div>
                            </div>
                            <div class="rounded-2xl bg-blue-50 px-3 py-2 text-2xl dark:bg-blue-500/10">💻</div>
                        </div>
                        <div class="mt-4 grid grid-cols-2 gap-2 text-sm">
                            <div class="rounded-xl bg-slate-50 px-3 py-2 dark:bg-slate-950/50">
                                <div class="text-[11px] text-slate-400 dark:text-slate-500">硬盘状态</div>
                                <div class="mt-1 truncate font-semibold text-slate-700 dark:text-slate-200" :title="systemStats.disk_status">{{ systemStats.disk_status }}</div>
                            </div>
                            <div class="rounded-xl bg-slate-50 px-3 py-2 dark:bg-slate-950/50">
                                <div class="text-[11px] text-slate-400 dark:text-slate-500">整体请求数</div>
                                <div class="mt-1 font-semibold text-slate-700 dark:text-slate-200">{{ formatNumber(systemStats.total_requests) }}</div>
                            </div>
                        </div>
                    </article>

                    <article class="rounded-2xl border border-slate-200 bg-white/80 p-4 shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-900/70">
                        <div class="flex items-start justify-between gap-3">
                            <div>
                                <div class="text-[11px] uppercase text-slate-400 dark:text-slate-500">全局请求成功率</div>
                                <div class="mt-2 flex items-end gap-2">
                                    <span :class="['text-5xl font-black', globalSuccessRate >= 90 ? 'text-emerald-500' : (globalSuccessRate >= 70 ? 'text-amber-500' : 'text-rose-500')]">{{ globalSuccessRate }}%</span>
                                    <span class="pb-2 text-sm text-slate-400">{{ rateBadge(globalSuccessRate) }}</span>
                                </div>
                            </div>
                            <div class="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-300">
                                样本 {{ sortedRecords.length }}
                            </div>
                        </div>
                        <div class="mt-4 h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                            <div :class="['h-full rounded-full transition-all', rateToneClass(globalSuccessRate)]"
                                 :style="{ width: globalSuccessRate + '%' }"></div>
                        </div>
                        <div class="mt-3 flex gap-2 text-xs">
                            <span class="rounded-full bg-emerald-50 px-2.5 py-1 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-200">成功 {{ successCount }}</span>
                            <span class="rounded-full bg-rose-50 px-2.5 py-1 text-rose-700 dark:bg-rose-500/10 dark:text-rose-200">失败 {{ failureCount }}</span>
                        </div>
                    </article>

                    <article class="rounded-2xl border border-slate-200 bg-white/80 p-4 shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-900/70">
                        <div class="mb-3 flex items-center justify-between">
                            <div>
                                <div class="text-[11px] uppercase text-slate-400 dark:text-slate-500">分站点成功率统计</div>
                                <div class="mt-1 text-sm font-semibold text-slate-800 dark:text-slate-100">按域名聚合</div>
                            </div>
                            <span class="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] text-slate-500 dark:bg-slate-800 dark:text-slate-400">Top 10</span>
                        </div>
                        <div v-if="domainStats.length" class="space-y-2">
                            <div v-for="item in domainStats" :key="item.domain" class="rounded-xl bg-slate-50/80 px-3 py-2 dark:bg-slate-950/40">
                                <div class="flex items-center justify-between gap-3 text-xs">
                                    <span class="min-w-0 truncate font-medium text-slate-700 dark:text-slate-200">{{ item.domain }}</span>
                                    <span class="shrink-0 text-slate-500 dark:text-slate-400">{{ item.rate }}% {{ rateBadge(item.rate) }}</span>
                                </div>
                                <div class="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-200/80 dark:bg-slate-800">
                                    <div :class="['h-full rounded-full', rateToneClass(item.rate)]" :style="{ width: item.rate + '%' }"></div>
                                </div>
                            </div>
                        </div>
                        <div v-else class="rounded-xl bg-slate-50 px-4 py-6 text-center text-sm text-slate-400 dark:bg-slate-950/40 dark:text-slate-500">
                            暂无域名统计
                        </div>
                    </article>
                </section>

                <section class="rounded-2xl border border-slate-200 bg-white/80 shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-900/70">
                    <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-700">
                        <div>
                            <h3 class="text-base font-bold text-slate-950 dark:text-white">历史请求列表</h3>
                            <p class="mt-1 text-[11px] text-slate-400 dark:text-slate-500">默认展示最新 20 条，点击条目查看完整上下文。</p>
                        </div>
                        <span class="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-500 dark:bg-slate-800 dark:text-slate-300">{{ visibleRecords.length }} / {{ sortedRecords.length }}</span>
                    </div>

                    <div class="space-y-2 p-3">
                        <button v-for="record in visibleRecords"
                                :key="record.id"
                                @click="openRecord(record)"
                                :class="['w-full rounded-2xl border px-3 py-3 text-left shadow-sm transition', record.__statusClasses]">
                            <div class="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                                <div class="min-w-0 flex-1">
                                    <div class="flex flex-wrap items-center gap-2">
                                        <span :class="['rounded-full px-2.5 py-1 text-xs font-semibold ring-1', record.__statusPillClasses]">
                                            {{ record.__statusIcon }} {{ record.__statusText }}
                                        </span>
                                        <span class="truncate text-sm font-semibold text-slate-900 dark:text-white">{{ record.__domain }}</span>
                                        <span class="text-xs text-slate-400">/</span>
                                        <span class="text-xs text-slate-500 dark:text-slate-400">{{ record.preset_name || '默认预设' }}</span>
                                        <span class="rounded-full bg-white/70 px-2 py-0.5 text-[11px] text-slate-500 dark:bg-slate-950/40 dark:text-slate-400">{{ record.__tabLabel }}</span>
                                        <span v-if="record.is_multimodal" class="rounded-full bg-blue-50 px-2 py-0.5 text-[11px] text-blue-600 dark:bg-blue-500/10 dark:text-blue-200">🖼️ 多模态</span>
                                    </div>
                                    <p class="mt-2 truncate text-sm text-slate-600 dark:text-slate-300">{{ record.__summaryText }}</p>
                                    <div class="mt-2 flex flex-wrap gap-2 text-[11px] text-slate-400 dark:text-slate-500">
                                        <span>开始 {{ record.__startedText }}</span>
                                        <span>结束 {{ record.__finishedText }}</span>
                                        <span>{{ record.request_type || '请求' }}</span>
                                    </div>
                                </div>
                                <div class="flex shrink-0 flex-row items-center justify-between gap-3 lg:flex-col lg:items-end">
                                    <div class="text-2xl font-black text-slate-900 dark:text-white">{{ record.__durationText }}</div>
                                    <div class="text-[11px] text-slate-400 dark:text-slate-500">{{ record.__tokenText }} tokens</div>
                                </div>
                            </div>
                        </button>

                        <div v-if="!visibleRecords.length"
                             class="rounded-2xl border border-dashed border-slate-200 bg-slate-50/80 px-4 py-10 text-center text-sm text-slate-400 dark:border-slate-700 dark:bg-slate-950/30 dark:text-slate-500">
                            暂无请求历史
                        </div>

                        <button v-if="hasMoreRecords"
                                @click="loadMore"
                                class="w-full rounded-xl border border-slate-200 bg-white/80 px-4 py-2 text-sm font-medium text-slate-600 shadow-sm transition hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900/80 dark:text-slate-200 dark:hover:bg-slate-800">
                            加载更多
                        </button>
                    </div>
                </section>
            </div>

            <div v-if="selectedRecord"
                 class="fixed inset-0 z-50 flex justify-end bg-slate-950/55 p-3 backdrop-blur-sm"
                 @click.self="closeRecord">
                <aside class="flex h-full w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900">
                    <div class="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4 dark:border-slate-700">
                        <div class="min-w-0">
                            <div class="flex flex-wrap items-center gap-2">
                                <span :class="['rounded-full px-2.5 py-1 text-xs font-semibold ring-1', selectedRecord.__statusPillClasses || statusPillClasses(selectedRecord)]">{{ selectedRecord.__statusText || statusText(selectedRecord) }}</span>
                                <h3 class="truncate text-lg font-bold text-slate-950 dark:text-white">{{ selectedRecord.__domain || selectedRecord.target_domain || '未知域名' }}</h3>
                                <span class="text-xs text-slate-400">{{ selectedRecord.id }}</span>
                            </div>
                            <div class="mt-2 flex flex-wrap gap-2 text-[11px] text-slate-400 dark:text-slate-500">
                                <span>{{ selectedRecord.preset_name || '默认预设' }}</span>
                                <span>{{ selectedRecord.__tabLabel || tabLabel(selectedRecord) }}</span>
                                <span>{{ selectedRecord.request_type || '请求' }}</span>
                                <span v-if="selectedRecord.is_multimodal">🖼️ 包含多模态</span>
                                <span :title="selectedTimingText">总耗时 {{ formatDurationMs(selectedRecord.duration_ms) }}</span>
                            </div>
                        </div>
                        <button @click="closeRecord"
                                class="rounded-xl border border-slate-200 px-3 py-1.5 text-sm text-slate-600 transition hover:bg-slate-100 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800">
                            关闭
                        </button>
                    </div>

                    <div class="flex-1 overflow-auto px-5 py-4">
                        <div v-if="!selectedRecord.success"
                             class="mb-4 rounded-2xl border border-rose-200 bg-rose-50/90 p-4 text-rose-800 shadow-sm dark:border-rose-500/30 dark:bg-rose-950/30 dark:text-rose-100">
                            <div class="flex flex-wrap items-center justify-between gap-3">
                                <div>
                                    <div class="text-[11px] text-rose-500 dark:text-rose-300">错误码</div>
                                    <div class="mt-1 text-lg font-bold">{{ selectedRecord.error_code || selectedRecord.status || 'execution_error' }}</div>
                                </div>
                                <button @click="showErrorStack = !showErrorStack"
                                        class="rounded-xl bg-white/80 px-3 py-1.5 text-xs font-semibold text-rose-700 shadow-sm transition hover:bg-white dark:bg-rose-950/60 dark:text-rose-100">
                                    {{ showErrorStack ? '收起错误日志' : '查看完整错误日志' }}
                                </button>
                            </div>
                            <p class="mt-3 text-sm leading-6">{{ selectedRecord.error_message || '请求执行失败，暂无更多错误摘要。' }}</p>
                            <pre v-if="showErrorStack" class="mt-3 max-h-64 overflow-auto rounded-xl bg-white/80 p-3 text-xs leading-5 text-rose-900 dark:bg-slate-950/50 dark:text-rose-100">{{ selectedRecord.error_stack || selectedRecord.error_message || '暂无错误栈' }}</pre>
                        </div>

                        <div class="mb-4 grid gap-3 sm:grid-cols-4">
                            <div class="rounded-2xl border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-700 dark:bg-slate-950/40">
                                <div class="text-[11px] text-slate-400 dark:text-slate-500">排队等待</div>
                                <div class="mt-1 text-lg font-bold text-slate-900 dark:text-white">{{ formatDurationMs(selectedRecord.queue_ms) }}</div>
                            </div>
                            <div class="rounded-2xl border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-700 dark:bg-slate-950/40">
                                <div class="text-[11px] text-slate-400 dark:text-slate-500">生成耗时</div>
                                <div class="mt-1 text-lg font-bold text-slate-900 dark:text-white">{{ formatDurationMs(selectedRecord.generation_ms) }}</div>
                            </div>
                            <div class="rounded-2xl border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-700 dark:bg-slate-950/40">
                                <div class="text-[11px] text-slate-400 dark:text-slate-500">Token 估算</div>
                                <div class="mt-1 text-lg font-bold text-slate-900 dark:text-white">{{ tokenEstimate(selectedRecord) }}</div>
                            </div>
                            <div class="rounded-2xl border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-700 dark:bg-slate-950/40">
                                <div class="text-[11px] text-slate-400 dark:text-slate-500">开始 / 结束</div>
                                <div class="mt-1 text-xs font-semibold leading-5 text-slate-700 dark:text-slate-200">{{ formatTime(selectedRecord.started_at || selectedRecord.created_at) }} - {{ formatTime(selectedRecord.finished_at) }}</div>
                            </div>
                        </div>

                        <div class="grid gap-4 lg:grid-cols-2">
                            <section class="rounded-2xl border border-slate-200 bg-white/80 p-4 shadow-sm dark:border-slate-700 dark:bg-slate-950/20">
                                <div class="mb-3 flex items-center justify-between">
                                    <h4 class="text-sm font-bold text-slate-900 dark:text-white">用户请求上下文</h4>
                                    <div class="flex items-center gap-2">
                                        <button v-if="shouldShowExpandTextButton(selectedRecord, 'prompt')"
                                                @click="toggleTextBlock('prompt')"
                                                class="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-medium text-slate-500 transition hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700">
                                            {{ isTextBlockExpanded('prompt') ? '收起' : '展开全文' }}
                                        </button>
                                        <span class="cursor-help rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-400 dark:bg-slate-800 dark:text-slate-500" title="展示已过滤图片数据后的 Prompt 文本。">?</span>
                                    </div>
                                </div>
                                <textarea readonly
                                          :value="getTextBlockPreview(selectedRecord, 'prompt')"
                                          spellcheck="false"
                                          class="h-[32rem] w-full resize-none rounded-2xl border-0 bg-slate-50 p-4 font-mono text-sm leading-6 text-slate-700 outline-none dark:bg-slate-900/80 dark:text-slate-200"></textarea>
                            </section>
                            <section class="rounded-2xl border border-slate-200 bg-white/80 p-4 shadow-sm dark:border-slate-700 dark:bg-slate-950/20">
                                <div class="mb-3 flex items-center justify-between">
                                    <h4 class="text-sm font-bold text-slate-900 dark:text-white">AI 响应结果</h4>
                                    <div class="flex items-center gap-2">
                                        <button v-if="shouldShowExpandTextButton(selectedRecord, 'response')"
                                                @click="toggleTextBlock('response')"
                                                class="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-medium text-slate-500 transition hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700">
                                            {{ isTextBlockExpanded('response') ? '收起' : '展开全文' }}
                                        </button>
                                        <span class="cursor-help rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-400 dark:bg-slate-800 dark:text-slate-500" title="展示已过滤图片数据后的响应正文。">?</span>
                                    </div>
                                </div>
                                <textarea readonly
                                          :value="getTextBlockPreview(selectedRecord, 'response')"
                                          spellcheck="false"
                                          class="h-[32rem] w-full resize-none rounded-2xl border-0 bg-slate-50 p-4 font-mono text-sm leading-6 text-slate-700 outline-none dark:bg-slate-900/80 dark:text-slate-200"></textarea>
                            </section>
                        </div>
                    </div>
                </aside>
            </div>
        </div>
    `
}
