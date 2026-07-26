var REQUEST_MONITOR_RECORD_VIEW_CACHE_LIMIT = 2200

window.RequestMonitorTab = {
    name: 'RequestMonitorTab',
    props: {
        records: { type: Array, default: () => [] },
        maxRecords: { type: Number, default: 0 },
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
            currentPage: 1,
            pageSize: 20,
            selectedRecord: null,
            showErrorStack: false,
            expandedTextBlocks: {},
            query: '',
            statusFilter: 'all',
            includeMultimodal: true,
            analyticsRange: '7d',
            trendRange: '24h',
            showSystemLoad: false,
            rankingDimension: 'domain'
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
            const models = new Map()
            const durations = []
            let success = 0
            let successDurationTotal = 0
            let promptTokens = 0
            let responseTokens = 0
            sorted.forEach(item => {
                if (item && item.success) {
                    success += 1
                    successDurationTotal += Number(item.duration_ms || 0)
                }
                const duration = Number(item && item.duration_ms || 0)
                if (Number.isFinite(duration) && duration > 0) {
                    durations.push(duration)
                }
                const estimate = item && item.token_estimate && typeof item.token_estimate === 'object'
                    ? item.token_estimate
                    : {}
                const itemPromptTokens = Math.max(0, Number(estimate.prompt || 0))
                const itemResponseTokens = Math.max(0, Number(estimate.response || 0))
                promptTokens += Number.isFinite(itemPromptTokens) ? itemPromptTokens : 0
                responseTokens += Number.isFinite(itemResponseTokens) ? itemResponseTokens : 0
                const domain = this.recordDomain(item)
                const current = domains.get(domain) || { domain, total: 0, success: 0, failed: 0, rate: 0 }
                current.total += 1
                if (item && item.success) {
                    current.success += 1
                } else {
                    current.failed += 1
                }
                domains.set(domain, current)

                const model = String(item && (item.model || item.preset_name) || '默认模型').trim() || '默认模型'
                const modelCurrent = models.get(model) || { model, total: 0, success: 0, failed: 0, tokens: 0, rate: 0 }
                modelCurrent.total += 1
                modelCurrent.tokens += (Number.isFinite(itemPromptTokens) ? itemPromptTokens : 0)
                    + (Number.isFinite(itemResponseTokens) ? itemResponseTokens : 0)
                if (item && item.success) {
                    modelCurrent.success += 1
                } else {
                    modelCurrent.failed += 1
                }
                models.set(model, modelCurrent)
            })
            const domainStats = Array.from(domains.values())
                .map(item => ({
                    ...item,
                    rate: item.total ? Math.round((item.success / item.total) * 100) : 0
                }))
                .sort((a, b) => b.total - a.total || b.rate - a.rate)
                .slice(0, 10)
            const modelStats = Array.from(models.values())
                .map(item => ({
                    ...item,
                    rate: item.total ? Math.round((item.success / item.total) * 100) : 0
                }))
                .sort((a, b) => b.total - a.total || b.tokens - a.tokens || b.rate - a.rate)
                .slice(0, 10)
            durations.sort((a, b) => a - b)
            const p95Index = durations.length ? Math.max(0, Math.ceil(durations.length * 0.95) - 1) : 0

            return {
                sorted,
                success,
                failure: sorted.length - success,
                successRate: sorted.length ? Math.round((success / sorted.length) * 100) : 0,
                avgDuration: success ? Math.round(successDurationTotal / success) : 0,
                p95Duration: durations.length ? Math.round(durations[p95Index]) : 0,
                promptTokens,
                responseTokens,
                domainStats,
                modelStats
            }
        },
        sortedRecords() {
            return this.recordSummary.sorted
        },
        visibleRecords() {
            const page = Math.min(Math.max(1, this.currentPage), this.totalPages)
            const start = (page - 1) * this.pageSize
            return this.filteredRecords.slice(start, start + this.pageSize)
        },
        hasMoreRecords() {
            return this.currentPage < this.totalPages
        },
        totalPages() {
            return Math.max(1, Math.ceil(this.filteredRecords.length / this.pageSize))
        },
        paginationItems() {
            const total = this.totalPages
            const current = Math.min(Math.max(1, this.currentPage), total)
            const pages = new Set([1, total, current - 1, current, current + 1])
            if (current <= 4) {
                [2, 3, 4, 5].forEach(page => pages.add(page))
            }
            if (current >= total - 3) {
                [total - 4, total - 3, total - 2, total - 1].forEach(page => pages.add(page))
            }
            const validPages = Array.from(pages)
                .filter(page => page >= 1 && page <= total)
                .sort((a, b) => a - b)
            const items = []
            validPages.forEach((page, index) => {
                if (index > 0 && page - validPages[index - 1] > 1) {
                    items.push({ key: 'ellipsis-' + page, page: null, label: '...' })
                }
                items.push({ key: 'page-' + page, page, label: String(page) })
            })
            return items
        },
        filteredRecords() {
            const query = String(this.query || '').trim().toLowerCase()
            return this.sortedRecords.filter(record => {
                if (!this.includeMultimodal && record.is_multimodal) return false
                if (this.statusFilter === 'success' && !record.success) return false
                if (this.statusFilter === 'failed' && (record.success || record.status === 'cancelled')) return false
                if (this.statusFilter === 'cancelled' && record.status !== 'cancelled') return false
                if (!query) return true
                return [
                    record.id,
                    record.__historyKey,
                    record.__domain,
                    record.model,
                    record.preset_name,
                    record.route_group,
                    record.endpoint,
                    record.request_type,
                    record.__summaryText
                ].some(value => String(value || '').toLowerCase().includes(query))
            })
        },
        runningCount() {
            return this.sortedRecords.filter(record => ['running', 'pending', 'processing'].includes(String(record.status || '').toLowerCase())).length
        },
        trendRangeSeconds() {
            if (this.trendRange === '1h') return 60 * 60
            if (this.trendRange === '7d') return 7 * 24 * 60 * 60
            return 24 * 60 * 60
        },
        trendBuckets() {
            const bucketCount = 10
            const now = Math.max(Date.now() / 1000, this.sortedRecords.length ? this.recordSortTimestamp(this.sortedRecords[0]) : 0)
            const start = now - this.trendRangeSeconds
            const bucketSize = this.trendRangeSeconds / bucketCount
            const buckets = Array.from({ length: bucketCount }, (_, index) => ({
                start: start + index * bucketSize,
                total: 0,
                success: 0,
                failed: 0
            }))
            this.sortedRecords.forEach(record => {
                const timestamp = this.recordSortTimestamp(record)
                if (!timestamp || timestamp < start || timestamp > now) return
                const index = Math.min(bucketCount - 1, Math.max(0, Math.floor((timestamp - start) / bucketSize)))
                buckets[index].total += 1
                if (record && record.success) {
                    buckets[index].success += 1
                } else {
                    buckets[index].failed += 1
                }
            })
            return buckets
        },
        trendMax() {
            return Math.max(1, ...this.trendBuckets.map(bucket => bucket.total))
        },
        trendLinePoints() {
            return this.trendBuckets.map((bucket, index) => {
                const x = 20 + index * (760 / Math.max(1, this.trendBuckets.length - 1))
                const y = 205 - (bucket.total / this.trendMax) * 160
                return x.toFixed(1) + ',' + y.toFixed(1)
            }).join(' ')
        },
        trendAreaPoints() {
            return '20,220 ' + this.trendLinePoints + ' 780,220'
        },
        trendTotal() {
            return this.trendBuckets.reduce((total, bucket) => total + bucket.total, 0)
        },
        trendSuccessTotal() {
            return this.trendBuckets.reduce((total, bucket) => total + bucket.success, 0)
        },
        trendFailureTotal() {
            return this.trendBuckets.reduce((total, bucket) => total + bucket.failed, 0)
        },
        trendLabels() {
            const newest = this.trendBuckets[this.trendBuckets.length - 1]
            const oldest = this.trendBuckets[0]
            const format = value => new Date(value * 1000).toLocaleString('zh-CN', this.trendRange === '7d'
                ? { month: '2-digit', day: '2-digit' }
                : { hour: '2-digit', minute: '2-digit' })
            return [format(oldest.start), format(oldest.start + this.trendRangeSeconds / 2), format(newest.start)]
        },
        maxDomainTotal() {
            return Math.max(1, ...this.domainStats.map(item => item.total))
        },
        modelStats() {
            return this.recordSummary.modelStats
        },
        rankingStats() {
            if (this.rankingDimension === 'model') {
                return this.modelStats.map(item => ({
                    key: item.model,
                    label: item.model,
                    total: item.total,
                    success: item.success,
                    failed: item.failed,
                    rate: item.rate,
                    meta: this.formatTokenNumber(item.tokens) + ' Token'
                }))
            }
            return this.domainStats.map(item => ({
                key: item.domain,
                label: item.domain,
                total: item.total,
                success: item.success,
                failed: item.failed,
                rate: item.rate,
                meta: item.rate + '% 成功'
            }))
        },
        maxRankingTotal() {
            return Math.max(1, ...this.rankingStats.map(item => item.total))
        },
        successCount() {
            return this.recordSummary.success
        },
        failureCount() {
            return this.recordSummary.failure
        },
        cancelledCount() {
            return this.sortedRecords.filter(record => String(record.status || '').toLowerCase() === 'cancelled').length
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
        },
        p95Duration() {
            return this.recordSummary.p95Duration
        },
        cumulativeTokens() {
            return Number(this.systemStats.total_input_tokens || 0) + Number(this.systemStats.total_output_tokens || 0)
        },
        sampleTokens() {
            return Number(this.recordSummary.promptTokens || 0) + Number(this.recordSummary.responseTokens || 0)
        },
        retentionLimit() {
            return Math.max(Number(this.maxRecords || 0), this.sortedRecords.length)
        },
        retentionUsage() {
            return this.retentionLimit
                ? Math.min(100, Math.round((this.sortedRecords.length / this.retentionLimit) * 100))
                : 0
        },
        analyticsRangeDays() {
            if (this.analyticsRange === '7d') return 7
            if (this.analyticsRange === '30d') return 30
            return null
        },
        analyticsRecords() {
            if (!this.analyticsRangeDays) return this.sortedRecords
            const cutoff = Date.now() - this.analyticsRangeDays * 24 * 60 * 60 * 1000
            return this.sortedRecords.filter(record => this.recordDate(record).getTime() >= cutoff)
        },
        hourlyTokenBuckets() {
            const now = new Date()
            const buckets = Array.from({ length: 24 }, (_, hour) => ({ hour, prompt: 0, response: 0, total: 0, calls: 0 }))
            this.sortedRecords.forEach(record => {
                const date = this.recordDate(record)
                if (date.getFullYear() !== now.getFullYear() || date.getMonth() !== now.getMonth() || date.getDate() !== now.getDate()) return
                const tokens = this.recordTokens(record)
                const bucket = buckets[date.getHours()]
                bucket.prompt += tokens.prompt
                bucket.response += tokens.response
                bucket.total += tokens.total
                bucket.calls += 1
            })
            return buckets
        },
        hourlyTokenMax() {
            return Math.max(1, ...this.hourlyTokenBuckets.map(bucket => Math.max(bucket.prompt, bucket.response)))
        },
        hourlyPromptPoints() {
            return this.linePoints(this.hourlyTokenBuckets, 'prompt', this.hourlyTokenMax)
        },
        hourlyResponsePoints() {
            return this.linePoints(this.hourlyTokenBuckets, 'response', this.hourlyTokenMax)
        },
        dailyTokenBuckets() {
            const totals = new Map()
            this.analyticsRecords.forEach(record => {
                const date = this.recordDate(record)
                const key = this.localDateKey(date)
                const current = totals.get(key) || { key, date, prompt: 0, response: 0, total: 0, calls: 0 }
                const tokens = this.recordTokens(record)
                current.prompt += tokens.prompt
                current.response += tokens.response
                current.total += tokens.total
                current.calls += 1
                totals.set(key, current)
            })
            if (!this.analyticsRangeDays) {
                return Array.from(totals.values()).sort((a, b) => a.key.localeCompare(b.key))
            }
            const buckets = []
            const today = new Date()
            today.setHours(12, 0, 0, 0)
            for (let offset = this.analyticsRangeDays - 1; offset >= 0; offset -= 1) {
                const date = new Date(today)
                date.setDate(today.getDate() - offset)
                const key = this.localDateKey(date)
                buckets.push(totals.get(key) || { key, date, prompt: 0, response: 0, total: 0, calls: 0 })
            }
            return buckets
        },
        dailyTokenMax() {
            return Math.max(1, ...this.dailyTokenBuckets.map(bucket => bucket.total))
        },
        dailyChartBars() {
            const count = Math.max(1, this.dailyTokenBuckets.length)
            const slot = 760 / count
            const width = Math.max(3, Math.min(26, slot * 0.58))
            return this.dailyTokenBuckets.map((bucket, index) => {
                const promptHeight = (bucket.prompt / this.dailyTokenMax) * 145
                const responseHeight = (bucket.response / this.dailyTokenMax) * 145
                return {
                    ...bucket,
                    x: 20 + slot * index + (slot - width) / 2,
                    width,
                    promptHeight,
                    responseHeight,
                    promptY: 190 - promptHeight,
                    responseY: 190 - promptHeight - responseHeight,
                    pointX: 20 + slot * index + slot / 2,
                    pointY: 190 - (bucket.total / this.dailyTokenMax) * 145
                }
            })
        },
        dailyTotalPoints() {
            return this.dailyChartBars.map(bar => bar.pointX.toFixed(1) + ',' + bar.pointY.toFixed(1)).join(' ')
        },
        dailyChartLabels() {
            const buckets = this.dailyTokenBuckets
            if (!buckets.length) return []
            const indexes = Array.from(new Set([0, Math.floor((buckets.length - 1) / 2), buckets.length - 1]))
            return indexes.map(index => ({
                index,
                label: this.shortDate(buckets[index].date)
            }))
        },
        analyticsModelStats() {
            const models = new Map()
            this.analyticsRecords.forEach(record => {
                const name = String(record && (record.model || record.preset_name) || '默认模型').trim() || '默认模型'
                const current = models.get(name) || { name, calls: 0, tokens: 0 }
                current.calls += 1
                current.tokens += this.recordTokens(record).total
                models.set(name, current)
            })
            return Array.from(models.values())
                .sort((a, b) => b.tokens - a.tokens || b.calls - a.calls)
                .slice(0, 8)
        },
        analyticsModelTotal() {
            return this.analyticsModelStats.reduce((total, item) => total + item.tokens, 0)
        },
        modelDonutSegments() {
            const circumference = 364.425
            const valueTotal = this.analyticsModelTotal || this.analyticsModelStats.reduce((total, item) => total + item.calls, 0)
            let consumed = 0
            return this.analyticsModelStats.map((item, index) => {
                const value = this.analyticsModelTotal ? item.tokens : item.calls
                const length = valueTotal ? (value / valueTotal) * circumference : 0
                const segment = {
                    ...item,
                    color: this.modelColor(index),
                    dasharray: length + ' ' + Math.max(0, circumference - length),
                    dashoffset: -consumed,
                    percent: valueTotal ? Math.round((value / valueTotal) * 100) : 0
                }
                consumed += length
                return segment
            })
        },
        analyticsSampleTotals() {
            return this.analyticsRecords.reduce((totals, record) => {
                const tokens = this.recordTokens(record)
                totals.prompt += tokens.prompt
                totals.response += tokens.response
                totals.total += tokens.total
                totals.calls += 1
                return totals
            }, { prompt: 0, response: 0, total: 0, calls: 0 })
        }
    },
    watch: {
        records() {
            if (this.currentPage > this.totalPages) {
                this.currentPage = this.totalPages
            }
            if (this.selectedRecord && this.selectedRecord.id) {
                const selectedKey = String(this.selectedRecord.__historyKey || this.selectedRecord.history_key || '').trim()
                const current = this.resolveRecordForDetail(selectedKey, this.selectedRecord)
                if (current) {
                    this.selectedRecord = current
                }
            }
        },
        query() {
            this.currentPage = 1
        },
        statusFilter() {
            this.currentPage = 1
        },
        includeMultimodal() {
            this.currentPage = 1
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
                source.route_group,
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
            this.goToPage(this.currentPage + 1)
        },
        goToPage(page) {
            const normalized = Math.min(Math.max(1, Number(page) || 1), this.totalPages)
            this.currentPage = normalized
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
        trendBucketTitle(bucket) {
            const start = new Date(Number(bucket.start || 0) * 1000).toLocaleString('zh-CN')
            return start + ' · 成功 ' + bucket.success + ' · 失败 ' + bucket.failed
        },
        trendBucketSuccessWidth(bucket) {
            if (!bucket || !bucket.total) return '0%'
            return Math.round((bucket.success / bucket.total) * 100) + '%'
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
        recordDate(record) {
            const timestamp = this.recordSortTimestamp(record)
            const date = new Date(timestamp > 0 ? timestamp * 1000 : 0)
            return Number.isNaN(date.getTime()) ? new Date(0) : date
        },
        recordTokens(record) {
            const estimate = record && record.token_estimate && typeof record.token_estimate === 'object'
                ? record.token_estimate
                : {}
            const prompt = Math.max(0, Number(estimate.prompt || 0))
            const response = Math.max(0, Number(estimate.response || 0))
            return {
                prompt: Number.isFinite(prompt) ? prompt : 0,
                response: Number.isFinite(response) ? response : 0,
                total: (Number.isFinite(prompt) ? prompt : 0) + (Number.isFinite(response) ? response : 0)
            }
        },
        localDateKey(date) {
            const pad = value => String(value).padStart(2, '0')
            return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate())
        },
        shortDate(date) {
            return String(date.getMonth() + 1).padStart(2, '0') + '-' + String(date.getDate()).padStart(2, '0')
        },
        linePoints(buckets, field, maximum) {
            const count = Math.max(1, buckets.length - 1)
            return buckets.map((bucket, index) => {
                const x = 20 + index * (760 / count)
                const y = 190 - (Number(bucket[field] || 0) / Math.max(1, maximum)) * 145
                return x.toFixed(1) + ',' + y.toFixed(1)
            }).join(' ')
        },
        modelColor(index) {
            return ['#5d6b4d', '#8fbc8f', '#c8b89e', '#d4e4c1', '#3e4a32', '#a8b89a', '#b18f5e', '#7f8c78'][index % 8]
        },
        analyticsRangeLabel() {
            if (this.analyticsRange === '7d') return '近 7 天'
            if (this.analyticsRange === '30d') return '近 30 天'
            return '全部历史'
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
        <div class="request-monitor-workspace min-h-full bg-slate-50 px-4 py-5 text-slate-900 dark:bg-slate-950 dark:text-slate-100 sm:px-6">
            <section class="uwa-page uwa-monitor-page">
                <header class="uwa-page-header uwa-monitor-header">
                    <div>
                        <p class="uwa-eyebrow">ACTIVITY</p>
                        <h1>请求监控</h1>
                        <p>追踪模型路由、响应耗时、Token 消耗与异常详情。</p>
                    </div>
                    <div class="uwa-page-actions">
                        <button type="button" class="uwa-button" @click="showSystemLoad = !showSystemLoad"><span v-html="$icons.server"></span>系统负载</button>
                        <button type="button" class="uwa-button" @click="refresh" :disabled="loading"><span v-html="$icons.arrowPath"></span>{{ loading ? '刷新中' : '刷新' }}</button>
                    </div>
                </header>

                <div v-if="showSystemLoad" class="uwa-system-load-panel">
                    <div><span>系统 CPU</span><strong>{{ formatPercent(systemStats.cpu_percent) }}%</strong><i><b :style="{ width: meterWidth(systemStats.cpu_percent) }"></b></i></div>
                    <div><span>进程 CPU</span><strong>{{ formatPercent(systemStats.project_cpu) }}%</strong><i><b :style="{ width: meterWidth(systemStats.project_cpu) }"></b></i></div>
                    <div><span>系统内存</span><strong>{{ formatPercent(systemStats.memory_percent) }}%</strong><i><b :style="{ width: meterWidth(systemStats.memory_percent) }"></b></i></div>
                    <div><span>进程内存</span><strong>{{ formatNumber(systemStats.memory_mb) }} MB</strong><i><b :style="{ width: meterWidth(systemStats.project_memory_percent) }"></b></i></div>
                    <div><span>磁盘状态</span><strong :title="systemStats.disk_status">{{ systemStats.disk_status }}</strong></div>
                </div>

                <div v-if="error" class="uwa-inline-error">{{ error }}</div>

                <div class="uwa-analytics-toolbar">
                    <div>
                        <p class="uwa-eyebrow">USAGE ANALYTICS</p>
                        <h2>Token 用量分析</h2>
                        <p>累计数据来自服务统计，趋势与模型分布基于当前保留的请求历史。</p>
                    </div>
                    <div class="uwa-segmented" aria-label="Token 统计时间范围">
                        <button type="button" :class="{ 'is-active': analyticsRange === '7d' }" @click="analyticsRange = '7d'">近 7 天</button>
                        <button type="button" :class="{ 'is-active': analyticsRange === '30d' }" @click="analyticsRange = '30d'">近 30 天</button>
                        <button type="button" :class="{ 'is-active': analyticsRange === 'all' }" @click="analyticsRange = 'all'">全部</button>
                    </div>
                </div>

                <div class="uwa-token-kpis" aria-label="Token 用量概览">
                    <article class="is-total"><span class="uwa-token-kpi-icon" v-html="$icons.activity"></span><small>总 Token</small><strong>{{ formatTokenNumber(cumulativeTokens) }}</strong><p>累计服务用量</p></article>
                    <article class="is-input"><span class="uwa-token-kpi-icon" v-html="$icons.arrowDownTray"></span><small>输入 Token</small><strong>{{ formatTokenNumber(systemStats.total_input_tokens) }}</strong><p>{{ inputRatio }}% 的累计用量</p></article>
                    <article class="is-output"><span class="uwa-token-kpi-icon" v-html="$icons.arrowUpTray"></span><small>输出 Token</small><strong>{{ formatTokenNumber(systemStats.total_output_tokens) }}</strong><p>{{ outputRatio }}% 的累计用量</p></article>
                    <article class="is-calls"><span class="uwa-token-kpi-icon" v-html="$icons.chartBar"></span><small>模型调用</small><strong>{{ formatNumber(systemStats.total_requests) }}</strong><p>当前样本 {{ formatNumber(analyticsSampleTotals.calls) }} 次</p></article>
                </div>

                <section class="uwa-hourly-panel">
                    <div class="uwa-panel-heading">
                        <div><h2>当天每小时用量</h2><p>按本地时间统计当前保留历史中的输入与输出 Token</p></div>
                        <div class="uwa-token-legend"><span><i class="is-input"></i>输入 Token</span><span><i class="is-output"></i>输出 Token</span></div>
                    </div>
                    <div class="uwa-token-line-chart">
                        <svg viewBox="0 0 800 220" role="img" aria-label="当天每小时 Token 用量" preserveAspectRatio="none">
                            <line x1="20" y1="45" x2="780" y2="45"></line><line x1="20" y1="95" x2="780" y2="95"></line><line x1="20" y1="145" x2="780" y2="145"></line><line x1="20" y1="190" x2="780" y2="190"></line>
                            <polyline class="is-input" :points="hourlyPromptPoints"></polyline>
                            <polyline class="is-output" :points="hourlyResponsePoints"></polyline>
                        </svg>
                        <div class="uwa-chart-axis"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>23:00</span></div>
                    </div>
                </section>

                <section class="uwa-token-analysis-grid">
                    <div class="uwa-daily-token-panel">
                        <div class="uwa-panel-heading">
                            <div><h2>每日 Token 趋势</h2><p>{{ analyticsRangeLabel() }} · {{ formatTokenNumber(analyticsSampleTotals.total) }} Token</p></div>
                            <div class="uwa-token-legend"><span><i class="is-total"></i>总 Token</span><span><i class="is-input"></i>输入</span><span><i class="is-output"></i>输出</span></div>
                        </div>
                        <div v-if="dailyTokenBuckets.length" class="uwa-daily-token-chart">
                            <svg viewBox="0 0 800 220" role="img" aria-label="每日 Token 趋势" preserveAspectRatio="none">
                                <line x1="20" y1="45" x2="780" y2="45"></line><line x1="20" y1="95" x2="780" y2="95"></line><line x1="20" y1="145" x2="780" y2="145"></line><line x1="20" y1="190" x2="780" y2="190"></line>
                                <g v-for="bar in dailyChartBars" :key="bar.key">
                                    <rect class="is-input" :x="bar.x" :y="bar.promptY" :width="bar.width" :height="bar.promptHeight"></rect>
                                    <rect class="is-output" :x="bar.x" :y="bar.responseY" :width="bar.width" :height="bar.responseHeight"></rect>
                                </g>
                                <polyline :points="dailyTotalPoints"></polyline>
                            </svg>
                            <div class="uwa-chart-axis"><span v-for="item in dailyChartLabels" :key="item.index">{{ item.label }}</span></div>
                        </div>
                        <div v-else class="uwa-analytics-empty">当前范围暂无 Token 数据</div>
                    </div>

                    <aside class="uwa-model-usage-panel">
                        <div class="uwa-panel-heading"><div><h2>模型用量占比</h2><p>{{ analyticsRangeLabel() }} · Top 8</p></div></div>
                        <div v-if="modelDonutSegments.length" class="uwa-model-usage-body">
                            <div class="uwa-model-donut">
                                <svg viewBox="0 0 160 160" role="img" aria-label="模型 Token 用量占比">
                                    <circle class="uwa-donut-track" cx="80" cy="80" r="58"></circle>
                                    <circle v-for="segment in modelDonutSegments" :key="segment.name" class="uwa-donut-segment" cx="80" cy="80" r="58" :stroke="segment.color" :stroke-dasharray="segment.dasharray" :stroke-dashoffset="segment.dashoffset"></circle>
                                </svg>
                                <div><strong>{{ formatTokenNumber(analyticsModelTotal) }}</strong><span>Token</span></div>
                            </div>
                            <div class="uwa-model-usage-list">
                                <div v-for="segment in modelDonutSegments" :key="'legend-' + segment.name">
                                    <i :style="{ background: segment.color }"></i><strong :title="segment.name">{{ segment.name }}</strong><span>{{ segment.percent }}%</span>
                                    <b><em :style="{ width: segment.percent + '%', background: segment.color }"></em></b>
                                </div>
                            </div>
                        </div>
                        <div v-else class="uwa-analytics-empty">暂无模型用量数据</div>
                    </aside>
                </section>

                <div class="uwa-monitor-kpis" aria-label="请求概览">
                    <div><span>历史样本</span><strong>{{ formatNumber(sortedRecords.length) }}</strong><small>当前 {{ formatNumber(sortedRecords.length) }} / 上限 {{ formatNumber(retentionLimit) }}</small></div>
                    <div><span>保留容量</span><strong>{{ retentionUsage }}%</strong><small>新增请求将继续保留至 {{ formatNumber(retentionLimit) }} 条</small></div>
                    <div><span>样本 Token</span><strong>{{ formatTokenNumber(sampleTokens) }}</strong><small>当前历史估算用量</small></div>
                    <div><span>样本成功率</span><strong>{{ globalSuccessRate }}%</strong><small>{{ successCount }} 成功 · {{ failureCount }} 未成功</small></div>
                    <div><span>响应耗时</span><strong>{{ formatDurationMs(avgDuration) }}</strong><small>P95 {{ formatDurationMs(p95Duration) }}</small></div>
                    <div><span>正在执行</span><strong>{{ runningCount }}</strong><small>实时请求状态</small></div>
                </div>

                <section class="uwa-monitor-overview">
                    <div class="uwa-trend-panel">
                        <div class="uwa-panel-heading">
                            <div><h2>请求趋势 <strong>{{ trendTotal }}</strong><span>次</span></h2><p>基于当前请求历史的真实时间分布</p></div>
                            <div class="uwa-segmented" aria-label="趋势时间范围">
                                <button type="button" :class="{ 'is-active': trendRange === '1h' }" @click="trendRange = '1h'">1 小时</button>
                                <button type="button" :class="{ 'is-active': trendRange === '24h' }" @click="trendRange = '24h'">24 小时</button>
                                <button type="button" :class="{ 'is-active': trendRange === '7d' }" @click="trendRange = '7d'">7 天</button>
                            </div>
                        </div>
                        <div class="uwa-trend-chart">
                            <svg viewBox="0 0 800 240" role="img" aria-label="请求数量趋势图" preserveAspectRatio="none">
                                <line x1="20" y1="60" x2="780" y2="60"></line>
                                <line x1="20" y1="115" x2="780" y2="115"></line>
                                <line x1="20" y1="170" x2="780" y2="170"></line>
                                <polygon :points="trendAreaPoints"></polygon>
                                <polyline :points="trendLinePoints"></polyline>
                                <circle v-for="(bucket, index) in trendBuckets" :key="index"
                                        :cx="20 + index * (760 / Math.max(1, trendBuckets.length - 1))"
                                        :cy="205 - (bucket.total / trendMax) * 160" r="4"></circle>
                            </svg>
                            <div class="uwa-trend-labels"><span>{{ trendLabels[0] }}</span><span>{{ trendLabels[1] }}</span><span>{{ trendLabels[2] }}</span></div>
                            <div class="uwa-trend-health-legend" aria-label="请求结果图例">
                                <span><i class="is-success"></i>成功 {{ trendSuccessTotal }}</span>
                                <span><i class="is-failed"></i>失败 {{ trendFailureTotal }}</span>
                                <span><i class="is-idle"></i>无请求时段</span>
                            </div>
                            <div class="uwa-trend-health" aria-label="分时段请求健康度">
                                <div v-for="(bucket, index) in trendBuckets" :key="'health-' + index" :title="trendBucketTitle(bucket)" :class="{ 'is-idle': !bucket.total }">
                                    <i class="is-success" :style="{ width: trendBucketSuccessWidth(bucket) }"></i><i class="is-failed"></i>
                                </div>
                            </div>
                        </div>
                    </div>

                    <aside class="uwa-domain-ranking">
                        <div class="uwa-panel-heading">
                            <div><h2>{{ rankingDimension === 'model' ? '模型用量' : '站点请求量' }}</h2><p>当前样本 · Top 10</p></div>
                            <div class="uwa-ranking-tabs" aria-label="排行维度">
                                <button type="button" :class="{ 'is-active': rankingDimension === 'domain' }" @click="rankingDimension = 'domain'">站点</button>
                                <button type="button" :class="{ 'is-active': rankingDimension === 'model' }" @click="rankingDimension = 'model'">模型</button>
                            </div>
                        </div>
                        <div v-if="rankingStats.length" class="uwa-domain-list">
                            <div v-for="item in rankingStats" :key="item.key" class="uwa-domain-row">
                                <span class="uwa-domain-avatar">{{ item.label.charAt(0).toUpperCase() }}</span>
                                <div><strong :title="item.label">{{ item.label }}</strong><small>{{ item.meta }}</small><i><b :style="{ width: (item.total / maxRankingTotal * 100) + '%' }"></b></i></div>
                                <span>{{ item.total }}</span>
                            </div>
                        </div>
                        <div v-else class="uwa-domain-empty">暂无排行数据</div>
                    </aside>
                </section>

                <div class="uwa-request-filters">
                    <label class="uwa-search-field"><span v-html="$icons.magnifyingGlass"></span><input v-model="query" type="search" autocomplete="off" placeholder="搜索模型、域名或请求 ID"></label>
                    <div class="uwa-segmented uwa-status-tabs">
                        <button type="button" :class="{ 'is-active': statusFilter === 'all' }" @click="statusFilter = 'all'">全部 {{ sortedRecords.length }}</button>
                        <button type="button" :class="{ 'is-active': statusFilter === 'success' }" @click="statusFilter = 'success'">成功 {{ successCount }}</button>
                        <button type="button" :class="{ 'is-active': statusFilter === 'failed' }" @click="statusFilter = 'failed'">失败 {{ Math.max(0, failureCount - cancelledCount) }}</button>
                        <button type="button" :class="{ 'is-active': statusFilter === 'cancelled' }" @click="statusFilter = 'cancelled'">取消 {{ cancelledCount }}</button>
                    </div>
                    <label class="uwa-filter-toggle"><input v-model="includeMultimodal" type="checkbox"><i></i><span>包含多模态</span></label>
                </div>

                <div class="uwa-request-table-wrap">
                    <div class="uwa-request-table-header">
                        <span>状态</span><span>模型 / 路由</span><span>端点</span><span>输入 / 输出</span><span>耗时</span><span>时间</span><span></span>
                    </div>
                    <button v-for="record in visibleRecords" :key="record.__historyKey" type="button" class="uwa-request-row" @click="openRecord(record)">
                        <span class="uwa-request-status" :class="statusTone(record)"><i></i><strong>{{ record.is_stream && !record.finished_at ? '流式响应' : record.__statusText }}</strong></span>
                        <span class="uwa-request-route"><strong>{{ record.model || record.preset_name || '默认模型' }}</strong><small>{{ record.__domain }}<template v-if="record.route_group"> · {{ record.route_group }}</template><template v-if="record.is_multimodal"> · 多模态</template></small></span>
                        <code>{{ record.endpoint || record.request_type || '-' }}</code>
                        <span>{{ formatTokenNumber(record.token_estimate ? record.token_estimate.prompt : 0) }} / {{ formatTokenNumber(record.token_estimate ? record.token_estimate.response : 0) }}</span>
                        <strong>{{ record.__durationText }}</strong>
                        <time>{{ formatTime(record.started_at || record.created_at) }}</time>
                        <span class="uwa-row-arrow" v-html="$icons.arrowRight"></span>
                    </button>
                    <div v-if="!visibleRecords.length" class="uwa-request-empty">{{ query || statusFilter !== 'all' ? '没有匹配的请求' : '暂无请求历史' }}</div>
                    <nav v-if="filteredRecords.length" class="uwa-pagination" aria-label="请求历史分页">
                        <span>第 {{ currentPage }} / {{ totalPages }} 页 · 共 {{ filteredRecords.length }} 条 · 保留上限 {{ formatNumber(retentionLimit) }}</span>
                        <div>
                            <button type="button" class="uwa-page-button is-prev" @click="goToPage(currentPage - 1)" :disabled="currentPage <= 1" title="上一页" aria-label="上一页"><span v-html="$icons.arrowRight"></span></button>
                            <template v-for="item in paginationItems" :key="item.key">
                                <span v-if="!item.page" class="uwa-page-ellipsis">{{ item.label }}</span>
                                <button v-else type="button" :class="['uwa-page-button', { 'is-active': item.page === currentPage }]" @click="goToPage(item.page)" :aria-current="item.page === currentPage ? 'page' : null">{{ item.label }}</button>
                            </template>
                            <button type="button" class="uwa-page-button" @click="goToPage(currentPage + 1)" :disabled="currentPage >= totalPages" title="下一页" aria-label="下一页"><span v-html="$icons.arrowRight"></span></button>
                        </div>
                    </nav>
                </div>
            </section>
            <div class="mx-auto max-w-7xl space-y-5">
                <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                        <h2 class="text-xl font-bold text-slate-950 dark:text-white">📊 请求监控</h2>
                        <p class="mt-1 text-[11px] text-slate-400 dark:text-slate-500">按保留配置加载请求历史，已自动过滤超大 Base64 图片数据并进行智能输入输出统计。</p>
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
                                            <span v-if="record.route_group" class="rounded-full bg-cyan-50 px-2 py-0.5 text-[11px] text-cyan-700 dark:bg-cyan-500/10 dark:text-cyan-200">组 {{ record.route_group }}</span>
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
                                <span v-if="selectedRecord.route_group">路由组 {{ selectedRecord.route_group }}</span>
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
