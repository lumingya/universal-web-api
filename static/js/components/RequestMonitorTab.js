var REQUEST_MONITOR_RECORD_VIEW_CACHE_LIMIT = 260

window.RequestMonitorTab = {
    name: 'RequestMonitorTab',
    props: {
        records: { type: Array, default: () => [] },
        detailLoading: { type: Object, default: () => ({}) },
        systemStats: {
            type: Object,
            default: () => ({
                memory_mb: 0,
                disk_status: '未知',
                total_requests: 0,
                total_input_tokens: 0,
                total_output_tokens: 0,
                cpu_percent: 0,
                project_cpu: 0,
                memory_percent: 0,
                project_memory_percent: 0
            })
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
    created() {
        this.recordViewCacheStore = new Map()
        this.recordViewCacheKeyOrder = []
    },
    computed: {
        recordSummary() {
            const items = Array.isArray(this.records)
                ? this.records.map((record, index) => this.toCachedRecordView(record, index))
                : []
            const sorted = this.recordsAreNewestFirst(items)
                ? items
                : items.slice().sort((a, b) => this.compareRecordsNewestFirst(a, b))
            const domains = new Map()
            let success = 0
            let successDurationTotal = 0
            sorted.forEach(item => {
                if (item && item.success) {
                    success += 1
                    successDurationTotal += Number(item.duration_ms || 0)
                }
                const domain = this.recordDomain(item)
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
                avgDuration: success ? Math.round(successDurationTotal / success) : 0,
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
        },
        inputRatio() {
            const total = (this.systemStats.total_input_tokens || 0) + (this.systemStats.total_output_tokens || 0)
            return total ? Math.round((this.systemStats.total_input_tokens / total) * 100) : 50
        },
        outputRatio() {
            const total = (this.systemStats.total_input_tokens || 0) + (this.systemStats.total_output_tokens || 0)
            return total ? (100 - this.inputRatio) : 50
        },
        avgDuration() {
            return this.recordSummary.avgDuration
        }
    },
    watch: {
        records() {
            if (this.visibleCount > this.sortedRecords.length) {
                this.visibleCount = Math.max(20, this.sortedRecords.length)
            }
            if (this.selectedRecord && this.selectedRecord.id) {
                const selectedKey = String(this.selectedRecord.__historyKey || this.selectedRecord.history_key || '').trim()
                const current = this.resolveRecordForDetail(selectedKey, this.selectedRecord)
                if (current) {
                    this.selectedRecord = current
                }
            }
        }
    },
    methods: {
        toCachedRecordView(record, index) {
            const source = record && typeof record === 'object' ? record : {}
            const cache = this.ensureRecordViewCache()
            const historyKey = this.recordKey(source, index)
            const signature = this.recordViewSignature(source, historyKey)
            const cached = cache.get(historyKey)
            if (cached && cached.signature === signature) {
                this.touchRecordViewCacheKey(historyKey)
                return cached.view
            }

            const view = this.toRecordView(record, index)
            cache.set(historyKey, { signature, view })
            this.touchRecordViewCacheKey(historyKey)
            this.pruneRecordViewCache()
            return view
        },
        ensureRecordViewCache() {
            if (!(this.recordViewCacheStore instanceof Map)) {
                this.recordViewCacheStore = new Map()
            }
            if (!Array.isArray(this.recordViewCacheKeyOrder)) {
                this.recordViewCacheKeyOrder = []
            }
            return this.recordViewCacheStore
        },
        touchRecordViewCacheKey(key) {
            const cacheKeys = Array.isArray(this.recordViewCacheKeyOrder)
                ? this.recordViewCacheKeyOrder
                : []
            if (cacheKeys !== this.recordViewCacheKeyOrder) {
                this.recordViewCacheKeyOrder = cacheKeys
            }
            const existingIndex = cacheKeys.indexOf(key)
            if (existingIndex >= 0) {
                cacheKeys.splice(existingIndex, 1)
            }
            cacheKeys.push(key)
        },
        pruneRecordViewCache() {
            const cache = this.ensureRecordViewCache()
            while (this.recordViewCacheKeyOrder.length > REQUEST_MONITOR_RECORD_VIEW_CACHE_LIMIT) {
                const staleKey = this.recordViewCacheKeyOrder.shift()
                cache.delete(staleKey)
            }
        },
        recordViewSignature(record, historyKey) {
            const source = record && typeof record === 'object' ? record : {}
            const estimate = source.token_estimate && typeof source.token_estimate === 'object'
                ? source.token_estimate
                : {}
            const detailLengths = source.detail_text_lengths && typeof source.detail_text_lengths === 'object'
                ? source.detail_text_lengths
                : {}
            return [
                historyKey,
                source.id,
                source.status,
                source.success ? 1 : 0,
                source.target_domain,
                source.route_domain,
                source.preset_name,
                source.tab_index,
                source.tab_id,
                source.model,
                source.endpoint,
                source.request_type,
                source.is_stream ? 1 : 0,
                source.is_multimodal ? 1 : 0,
                source.created_at,
                source.started_at,
                source.finished_at,
                source.duration_ms,
                source.queue_ms,
                source.generation_ms,
                source.summary,
                source.prompt_preview,
                source.response_preview,
                source.error_message,
                source.error_code,
                source.cancel_reason,
                source.media_count,
                source.has_detail ? 1 : 0,
                source.detail_loaded ? 1 : 0,
                detailLengths.prompt,
                detailLengths.response,
                detailLengths.error_stack,
                estimate.prompt,
                estimate.response,
                estimate.total,
                estimate.chars,
                this.recordViewTextSignature(source.prompt),
                this.recordViewTextSignature(source.response),
                this.recordViewTextSignature(source.error_stack)
            ].map(value => String(value ?? '')).join('\u001f')
        },
        recordViewTextSignature(value) {
            if (value === undefined || value === null || value === '') return ''
            let text = ''
            if (typeof value === 'string') {
                text = value
            } else {
                try {
                    text = JSON.stringify(value)
                } catch (error) {
                    text = String(value)
                }
            }
            if (text.length <= 160) return text
            return text.length + ':' + this.recordViewTextHash(text)
        },
        recordViewTextHash(text) {
            let hash = 2166136261
            for (let index = 0; index < text.length; index += 1) {
                hash ^= text.charCodeAt(index)
                hash = Math.imul(hash, 16777619)
            }
            return (hash >>> 0).toString(36)
        },
        toRecordView(record, index) {
            const source = record && typeof record === 'object' ? record : {}
            const domain = this.recordDomain(source)
            const toolCallingErrorInfo = this.toolCallingErrorInfo(source)
            const summarySource = toolCallingErrorInfo
                ? toolCallingErrorInfo.summary
                : (source.summary || source.response_preview || source.response || source.error_message)
            const success = !!source.success
            const historyKey = this.recordKey(source, index)
            const {
                payload,
                response_payload,
                prompt,
                response,
                error_stack,
                ...viewSource
            } = source
            return {
                ...viewSource,
                id: source.id || historyKey,
                __historyKey: historyKey,
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
                __tabLabel: this.tabLabel(source),
                __toolCallingErrorInfo: toolCallingErrorInfo
            }
        },
        recordDomain(record) {
            const source = record && typeof record === 'object' ? record : {}
            return String(source.route_domain || source.__domain || source.target_domain || '未知域名').trim() || '未知域名'
        },
        recordKey(record, index) {
            const source = record && typeof record === 'object' ? record : {}
            const key = String(source.history_key || '').trim()
            if (key) return key
            const id = String(source.id || '').trim() || ('record-' + index)
            return [
                id,
                this.normalizeTimestamp(source.created_at),
                this.normalizeTimestamp(source.finished_at)
            ].join(':')
        },
        normalizeTimestamp(value) {
            if (typeof value === 'number') {
                if (!Number.isFinite(value) || value <= 0) return 0
                return value > 1000000000000 ? value / 1000 : value
            }
            const text = String(value || '').trim()
            if (!text) return 0
            const numeric = Number(text)
            if (Number.isFinite(numeric) && numeric > 0) {
                return numeric > 1000000000000 ? numeric / 1000 : numeric
            }
            const parsed = Date.parse(text)
            return Number.isNaN(parsed) ? 0 : parsed / 1000
        },
        recordSortTimestamp(record) {
            if (!record) return 0
            return this.normalizeTimestamp(record.finished_at)
                || this.normalizeTimestamp(record.started_at)
                || this.normalizeTimestamp(record.created_at)
        },
        compareRecordsNewestFirst(left, right) {
            const delta = this.recordSortTimestamp(right) - this.recordSortTimestamp(left)
            if (delta !== 0) return delta
            return String(right && right.__historyKey || '').localeCompare(String(left && left.__historyKey || ''))
        },
        recordsAreNewestFirst(items) {
            if (!Array.isArray(items) || items.length < 2) return true
            for (let index = 1; index < items.length; index += 1) {
                if (this.compareRecordsNewestFirst(items[index - 1], items[index]) > 0) {
                    return false
                }
            }
            return true
        },
        isRecordDetailLoading(record) {
            const keys = [
                record && record.__historyKey,
                record && record.history_key,
                record && record.id
            ].map(value => String(value || '').trim()).filter(Boolean)
            return keys.some(key => !!this.detailLoading[key])
        },
        detailKey(record) {
            return String(
                record && (record.__historyKey || record.history_key || record.id) || ''
            ).trim()
        },
        resolveRecordForDetail(recordOrKey, fallback = null) {
            const records = Array.isArray(this.records) ? this.records : []
            const requestedKey = typeof recordOrKey === 'string'
                ? String(recordOrKey || '').trim()
                : this.detailKey(recordOrKey)
            if (requestedKey) {
                const current = records.find(item => {
                    const itemKey = String(item && (item.__historyKey || item.history_key || '')).trim()
                    if (itemKey && itemKey === requestedKey) return true
                    const itemId = String(item && item.id || '').trim()
                    return itemId && itemId === requestedKey
                })
                if (current) {
                    return current
                }
            }

            const fallbackKey = this.detailKey(fallback)
            if (fallbackKey) {
                const current = records.find(item => {
                    const itemKey = String(item && (item.__historyKey || item.history_key || '')).trim()
                    if (itemKey && itemKey === fallbackKey) return true
                    const itemId = String(item && item.id || '').trim()
                    return itemId && itemId === fallbackKey
                })
                if (current) {
                    return current
                }
            }

            return fallback
        },
        refresh() {
            this.$emit('refresh')
        },
        loadMore() {
            this.visibleCount = Math.min(this.visibleCount + 20, this.sortedRecords.length)
        },
        openRecord(record) {
            this.selectedRecord = this.resolveRecordForDetail(record, record)
            this.showErrorStack = false
            this.expandedTextBlocks = {}
            const key = this.detailKey(this.selectedRecord || record)
            if (record && key && record.has_detail && !record.detail_loaded) {
                this.$emit('load-detail', key)
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
        formatPercent(value) {
            const num = Number(value) || 0
            return num.toFixed(1).replace(/\.0$/, '')
        },
        meterWidth(value) {
            const num = Number(value) || 0
            return Math.max(0, Math.min(100, num)) + '%'
        },
        formatTokenNumber(value) {
            const num = Number(value) || 0
            if (num >= 1e9) {
                return parseFloat((num / 1e9).toFixed(2)) + 'B'
            }
            if (num >= 1e6) {
                return parseFloat((num / 1e6).toFixed(2)) + 'M'
            }
            if (num >= 1e4) {
                return parseFloat((num / 1e3).toFixed(2)) + 'K'
            }
            return num.toLocaleString('zh-CN')
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
        toolCallingErrorInfo(record) {
            const source = record && typeof record === 'object' ? record : {}
            const errorCode = String(source.error_code || source.status || '').trim()
            const errorText = [
                source.error_message,
                source.error_stack,
                source.summary,
                source.response_preview,
                source.response
            ].map(value => String(value || '')).join('\n')
            if (!errorText.includes('tool_call_validation_exhausted')) {
                return null
            }
            const marker = 'tool_call_validation_exhausted:'
            const markerIndex = errorText.indexOf(marker)
            const detail = markerIndex >= 0
                ? errorText.slice(markerIndex + marker.length).split('\n')[0].trim()
                : ''
            return {
                code: errorCode || 'tool_calling_failed',
                title: '工具调用重试耗尽',
                summary: '工具调用重试耗尽：模型没有返回可执行的 tool_calls，系统已停止并返回错误，未降级为普通文本。',
                detail: detail || '没有可保留的工具调用，已阻止纯文本兜底。'
            }
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
        expandTextButtonLabel(record, key) {
            if (this.isRecordDetailLoading(record)) return '加载中...'
            return this.isTextBlockExpanded(key) ? '收起' : '展开全文'
        },
        toggleTextBlock(key) {
            if (this.isRecordDetailLoading(this.selectedRecord)) {
                return
            }
            this.expandedTextBlocks = {
                ...this.expandedTextBlocks,
                [key]: !this.expandedTextBlocks[key]
            }
        },
        tokenEstimate(record) {
            const estimate = record && record.token_estimate ? record.token_estimate : {}
            return this.formatTokenNumber(estimate.total || 0)
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
                        <p class="mt-1 text-[11px] text-slate-400 dark:text-slate-500">最近 200 条请求，已自动过滤超大 Base64 图片数据并进行智能输入输出统计。</p>
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

                <!-- 顶部 4 KPI 卡片横向排列 -->
                <section class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                    <!-- KPI 卡片 1: 系统负载 -->
                    <article class="relative rounded-2xl border border-slate-200 bg-white/80 p-4 shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-900/70">
                        <div>
                            <div class="text-[11px] uppercase text-slate-400 dark:text-slate-500">系统负载</div>
                            <div class="mt-2 text-3xl font-bold text-slate-950 dark:text-white">{{ systemStats.cpu_percent }}% <span class="text-xs font-normal text-slate-400">CPU</span></div>
                        </div>
                        <div class="absolute top-3.5 right-3.5 w-8 h-8 flex items-center justify-center rounded-lg bg-blue-50 text-base dark:bg-blue-500/10">💻</div>
                        <div class="mt-4 space-y-2 text-xs">
                            <div>
                                <div class="flex items-center justify-between text-[11px] mb-0.5">
                                    <span class="text-slate-400 dark:text-slate-500">系统 CPU 占用</span>
                                    <span class="font-medium text-slate-700 dark:text-slate-200">{{ formatPercent(systemStats.cpu_percent) }}%</span>
                                </div>
                                <div class="w-full bg-slate-100 dark:bg-slate-800 h-1 rounded-full overflow-hidden">
                                    <div class="bg-blue-500 h-full rounded-full transition-all duration-300" :style="{ width: meterWidth(systemStats.cpu_percent) }"></div>
                                </div>
                            </div>
                            <div>
                                <div class="flex items-center justify-between text-[11px] mb-0.5">
                                    <span class="text-slate-400 dark:text-slate-500">反代进程 CPU</span>
                                    <span class="font-medium text-slate-700 dark:text-slate-200">{{ formatPercent(systemStats.project_cpu) }}%</span>
                                </div>
                                <div class="w-full bg-slate-100 dark:bg-slate-800 h-1 rounded-full overflow-hidden">
                                    <div class="bg-indigo-500 h-full rounded-full transition-all duration-300" :style="{ width: meterWidth(systemStats.project_cpu) }"></div>
                                </div>
                            </div>
                            <div>
                                <div class="flex items-center justify-between text-[11px] mb-0.5">
                                    <span class="text-slate-400 dark:text-slate-500">系统内存占用</span>
                                    <span class="font-medium text-slate-700 dark:text-slate-200">{{ formatPercent(systemStats.memory_percent) }}%</span>
                                </div>
                                <div class="w-full bg-slate-100 dark:bg-slate-800 h-1 rounded-full overflow-hidden">
                                    <div class="bg-purple-500 h-full rounded-full transition-all duration-300" :style="{ width: meterWidth(systemStats.memory_percent) }"></div>
                                </div>
                            </div>
                            <div>
                                <div class="flex items-center justify-between text-[11px] mb-0.5">
                                    <span class="text-slate-400 dark:text-slate-500">反代进程内存</span>
                                    <span class="font-medium text-slate-700 dark:text-slate-200">{{ formatNumber(systemStats.memory_mb) }} MB ({{ formatPercent(systemStats.project_memory_percent) }}%)</span>
                                </div>
                                <div class="w-full bg-slate-100 dark:bg-slate-800 h-1 rounded-full overflow-hidden">
                                    <div class="bg-fuchsia-500 h-full rounded-full transition-all duration-300" :style="{ width: meterWidth(systemStats.project_memory_percent) }"></div>
                                </div>
                            </div>
                        </div>
                    </article>

                    <!-- KPI 卡片 2: 运行统计 -->
                    <article class="relative rounded-2xl border border-slate-200 bg-white/80 p-4 shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-900/70">
                        <div>
                            <div class="text-[11px] uppercase text-slate-400 dark:text-slate-500">运行统计</div>
                            <div class="mt-2 text-3xl font-bold text-slate-950 dark:text-white">{{ formatNumber(systemStats.total_requests) }} <span class="text-xs font-normal text-slate-400">次请求</span></div>
                        </div>
                        <div class="absolute top-3.5 right-3.5 w-8 h-8 flex items-center justify-center rounded-lg bg-indigo-50 text-base dark:bg-indigo-500/10">📦</div>
                        <div class="mt-4 space-y-2.5 text-xs">
                            <div class="rounded-xl bg-slate-50/80 px-3 py-1.5 dark:bg-slate-950/40">
                                <div class="text-[10px] text-slate-400 dark:text-slate-500">项目磁盘占用</div>
                                <div class="mt-0.5 font-semibold text-slate-700 dark:text-slate-200 truncate" :title="systemStats.disk_status">{{ systemStats.disk_status }}</div>
                            </div>
                            <div class="rounded-xl bg-slate-50/80 px-3 py-1.5 dark:bg-slate-950/40">
                                <div class="text-[10px] text-slate-400 dark:text-slate-500">成功平均耗时 (样本)</div>
                                <div class="mt-0.5 font-semibold text-slate-700 dark:text-slate-200">{{ formatDurationMs(avgDuration) }}</div>
                            </div>
                        </div>
                    </article>

                    <!-- KPI 卡片 3: Token 分开统计 -->
                    <article class="relative rounded-2xl border border-slate-200 bg-white/80 p-4 shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-900/70"
                             :title="'精确总计: ' + formatNumber(systemStats.total_input_tokens + systemStats.total_output_tokens) + ' Tokens'">
                        <div>
                            <div class="text-[11px] uppercase text-slate-400 dark:text-slate-500">Token 吞吐量</div>
                            <div class="mt-2 text-3xl font-bold text-slate-950 dark:text-white">{{ formatTokenNumber(systemStats.total_input_tokens + systemStats.total_output_tokens) }} <span class="text-xs font-normal text-slate-400">Tokens</span></div>
                        </div>
                        <div class="absolute top-3.5 right-3.5 w-8 h-8 flex items-center justify-center rounded-lg bg-emerald-50 text-base dark:bg-emerald-500/10">🎫</div>
                        <div class="mt-4 space-y-2 text-xs">
                            <div class="flex items-center justify-between" :title="'精确输入: ' + formatNumber(systemStats.total_input_tokens)">
                                <span class="text-slate-400 dark:text-slate-500 flex items-center gap-1">
                                    <span class="w-1.5 h-1.5 rounded-full bg-blue-500 inline-block"></span> 累计输入 (Prompt)
                                </span>
                                <span class="font-bold text-blue-600 dark:text-blue-400">{{ formatTokenNumber(systemStats.total_input_tokens) }}</span>
                            </div>
                            <div class="flex items-center justify-between" :title="'精确输出: ' + formatNumber(systemStats.total_output_tokens)">
                                <span class="text-slate-400 dark:text-slate-500 flex items-center gap-1">
                                    <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block"></span> 累计输出 (Completion)
                                </span>
                                <span class="font-bold text-emerald-600 dark:text-emerald-400">{{ formatTokenNumber(systemStats.total_output_tokens) }}</span>
                            </div>
                            <div class="mt-1">
                                <div class="flex items-center justify-between text-[10px] text-slate-400 dark:text-slate-500 mb-0.5">
                                    <span>输入: {{ inputRatio }}%</span>
                                    <span>输出: {{ outputRatio }}%</span>
                                </div>
                                <div class="w-full h-1.5 rounded-full overflow-hidden flex bg-slate-100 dark:bg-slate-800">
                                    <div class="bg-blue-500 h-full transition-all duration-300" :style="{ width: inputRatio + '%' }"></div>
                                    <div class="bg-emerald-500 h-full transition-all duration-300" :style="{ width: outputRatio + '%' }"></div>
                                </div>
                            </div>
                        </div>
                    </article>

                    <!-- KPI 卡片 4: 成功率统计 -->
                    <article class="rounded-2xl border border-slate-200 bg-white/80 p-4 shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-900/70">
                        <div class="flex items-start justify-between gap-3">
                            <div>
                                <div class="text-[11px] uppercase text-slate-400 dark:text-slate-500">全局请求成功率</div>
                                <div class="mt-2 flex items-end gap-1.5">
                                    <span :class="['text-3xl font-black', globalSuccessRate >= 90 ? 'text-emerald-500' : (globalSuccessRate >= 70 ? 'text-amber-500' : 'text-rose-500')]">{{ globalSuccessRate }}%</span>
                                    <span class="pb-1 text-sm text-slate-400">{{ rateBadge(globalSuccessRate) }}</span>
                                </div>
                            </div>
                            <div class="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-300">
                                样本 {{ sortedRecords.length }}
                            </div>
                        </div>
                        <div class="mt-3.5 h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                            <div :class="['h-full rounded-full transition-all', rateToneClass(globalSuccessRate)]"
                                 :style="{ width: globalSuccessRate + '%' }"></div>
                        </div>
                        <div class="mt-3 flex gap-2 text-xs">
                            <span class="rounded-full bg-emerald-50 px-2.5 py-1 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-200">成功 {{ successCount }}</span>
                            <span class="rounded-full bg-rose-50 px-2.5 py-1 text-rose-700 dark:bg-rose-500/10 dark:text-rose-200">失败 {{ failureCount }}</span>
                        </div>
                    </article>
                </section>

                <!-- 底部双栏排版布局：左侧为请求历史，右侧为分站点成功率统计 -->
                <section class="grid gap-4 lg:grid-cols-[2.2fr_1fr]">
                    <!-- 左栏：历史请求列表 -->
                    <div class="rounded-2xl border border-slate-200 bg-white/80 shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-900/70 overflow-hidden flex flex-col">
                        <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-700 bg-slate-50/50 dark:bg-slate-900/40">
                            <div>
                                <h3 class="text-sm font-bold text-slate-950 dark:text-white">历史请求列表</h3>
                                <p class="mt-0.5 text-[10px] text-slate-400 dark:text-slate-500">默认展示最新 20 条，点击条目查看完整上下文。</p>
                            </div>
                            <span class="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-500 dark:bg-slate-800 dark:text-slate-300">{{ visibleRecords.length }} / {{ sortedRecords.length }}</span>
                        </div>

                        <div class="space-y-2 p-3 overflow-y-auto max-h-[42rem]">
                            <button v-for="record in visibleRecords"
                                    :key="record.__historyKey"
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
                                    <!-- 右侧耗时与 Token 统计 (输入输出分开) -->
                                    <div class="flex shrink-0 flex-row items-center justify-between gap-3 lg:flex-col lg:items-end">
                                        <div class="text-2xl font-black text-slate-900 dark:text-white">{{ record.__durationText }}</div>
                                        <div class="text-[10px] text-slate-400 dark:text-slate-500 flex flex-col items-end gap-0.5 font-semibold">
                                            <span class="flex items-center gap-1" :title="'精确输入: ' + formatNumber(record.token_estimate ? record.token_estimate.prompt : 0)">
                                                <span class="w-1.5 h-1.5 rounded-full bg-blue-500 inline-block"></span>
                                                输入: {{ formatTokenNumber(record.token_estimate ? record.token_estimate.prompt : 0) }}
                                            </span>
                                            <span class="flex items-center gap-1" :title="'精确输出: ' + formatNumber(record.token_estimate ? record.token_estimate.response : 0)">
                                                <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block"></span>
                                                输出: {{ formatTokenNumber(record.token_estimate ? record.token_estimate.response : 0) }}
                                            </span>
                                        </div>
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
                    </div>

                    <!-- 右栏：分站点成功率统计 (Top 10) -->
                    <article class="rounded-2xl border border-slate-200 bg-white/80 p-4 shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-900/70 overflow-hidden flex flex-col h-fit">
                        <div class="mb-3 flex items-center justify-between pb-2 border-b dark:border-gray-700 bg-slate-50/10 dark:bg-slate-900/20">
                            <div>
                                <h3 class="text-sm font-bold text-slate-950 dark:text-white">分站点成功率统计</h3>
                                <p class="mt-0.5 text-[10px] text-slate-400 dark:text-slate-500">按域名聚合统计成功率</p>
                            </div>
                            <span class="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500 dark:bg-slate-800 dark:text-slate-400">Top 10</span>
                        </div>
                        <div v-if="domainStats.length" class="space-y-2 overflow-y-auto max-h-[42rem] pr-1">
                            <div v-for="item in domainStats" :key="item.domain" class="rounded-xl bg-slate-50/80 px-3 py-2 dark:bg-slate-950/40">
                                <div class="flex items-center justify-between gap-3 text-xs">
                                    <span class="min-w-0 truncate font-semibold text-slate-700 dark:text-slate-200" :title="item.domain">{{ item.domain }}</span>
                                    <span class="shrink-0 text-slate-500 dark:text-slate-400 font-bold">{{ item.rate }}% {{ rateBadge(item.rate) }}</span>
                                </div>
                                <div class="mt-2 h-1 overflow-hidden rounded-full bg-slate-200/80 dark:bg-slate-800">
                                    <div :class="['h-full rounded-full', rateToneClass(item.rate)]" :style="{ width: item.rate + '%' }"></div>
                                </div>
                                <div class="mt-1 flex justify-between text-[9px] text-slate-400">
                                    <span>成功: {{ item.success }} 次</span>
                                    <span>失败: {{ item.failed }} 次</span>
                                </div>
                            </div>
                        </div>
                        <div v-else class="rounded-xl bg-slate-50 px-4 py-6 text-center text-sm text-slate-400 dark:bg-slate-950/40 dark:text-slate-500">
                            暂无域名统计
                        </div>
                    </article>
                </section>
            </div>

            <!-- 请求详情抽屉 -->
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
                            <p class="mt-3 text-sm leading-6">{{ selectedRecord.__toolCallingErrorInfo ? selectedRecord.__toolCallingErrorInfo.summary : (selectedRecord.error_message || '请求执行失败，暂无更多错误摘要。') }}</p>
                            <div v-if="selectedRecord.__toolCallingErrorInfo"
                                 class="mt-3 border-t border-rose-200/70 pt-3 text-xs leading-5 text-rose-700 dark:border-rose-400/25 dark:text-rose-100">
                                <div class="font-semibold">{{ selectedRecord.__toolCallingErrorInfo.title }}</div>
                                <div class="mt-1 opacity-90">{{ selectedRecord.__toolCallingErrorInfo.detail }}</div>
                            </div>
                            <pre v-if="showErrorStack" class="mt-3 max-h-64 overflow-auto rounded-xl bg-white/80 p-3 text-xs leading-5 text-rose-900 dark:bg-slate-950/50 dark:text-rose-100">{{ selectedRecord.error_stack || selectedRecord.error_message || '暂无错误栈' }}</pre>
                        </div>

                        <!-- 详情属性网格 (修改为 5 列，展示输入和输出 Token) -->
                        <div class="mb-4 grid gap-3 sm:grid-cols-5">
                            <div class="rounded-2xl border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-700 dark:bg-slate-950/40">
                                <div class="text-[11px] text-slate-400 dark:text-slate-500">排队等待</div>
                                <div class="mt-1 text-lg font-bold text-slate-900 dark:text-white">{{ formatDurationMs(selectedRecord.queue_ms) }}</div>
                            </div>
                            <div class="rounded-2xl border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-700 dark:bg-slate-950/40">
                                <div class="text-[11px] text-slate-400 dark:text-slate-500">生成耗时</div>
                                <div class="mt-1 text-lg font-bold text-slate-900 dark:text-white">{{ formatDurationMs(selectedRecord.generation_ms) }}</div>
                            </div>
                            <div class="rounded-2xl border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-700 dark:bg-slate-950/40"
                                 :title="'精确输入: ' + formatNumber(selectedRecord.token_estimate ? selectedRecord.token_estimate.prompt : 0)">
                                <div class="text-[11px] text-slate-400 dark:text-slate-500">输入 Token (Prompt)</div>
                                <div class="mt-1 text-lg font-bold text-blue-600 dark:text-blue-400">{{ formatTokenNumber(selectedRecord.token_estimate ? selectedRecord.token_estimate.prompt : 0) }}</div>
                            </div>
                            <div class="rounded-2xl border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-700 dark:bg-slate-950/40"
                                 :title="'精确输出: ' + formatNumber(selectedRecord.token_estimate ? selectedRecord.token_estimate.response : 0)">
                                <div class="text-[11px] text-slate-400 dark:text-slate-500">输出 Token (Completion)</div>
                                <div class="mt-1 text-lg font-bold text-emerald-600 dark:text-emerald-400">{{ formatTokenNumber(selectedRecord.token_estimate ? selectedRecord.token_estimate.response : 0) }}</div>
                            </div>
                            <div class="rounded-2xl border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-700 dark:bg-slate-950/40">
                                <div class="text-[11px] text-slate-400 dark:text-slate-500">开始 / 结束时间</div>
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
                                                :disabled="isRecordDetailLoading(selectedRecord)"
                                                class="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-medium text-slate-500 transition hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700">
                                            {{ expandTextButtonLabel(selectedRecord, 'prompt') }}
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
                                                :disabled="isRecordDetailLoading(selectedRecord)"
                                                class="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-medium text-slate-500 transition hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700">
                                            {{ expandTextButtonLabel(selectedRecord, 'response') }}
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
