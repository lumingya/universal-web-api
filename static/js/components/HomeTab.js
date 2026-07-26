window.HomeTab = {
    name: 'HomeTab',
    props: {
        sites: { type: Object, default: () => ({}) },
        browserStatus: { type: Object, default: () => ({ connected: false }) },
        systemStats: { type: Object, default: () => ({}) },
        records: { type: Array, default: () => [] }
    },
    emits: ['change-tab', 'add-site', 'notify'],
    computed: {
        siteCount() {
            return Object.keys(this.sites || {}).length;
        },
        sortedRecords() {
            return (Array.isArray(this.records) ? this.records : []).slice().sort((a, b) => {
                return this.recordTimestamp(b) - this.recordTimestamp(a);
            });
        },
        recentRecords() {
            return this.sortedRecords.slice(0, 3);
        },
        totalRequests() {
            const reported = Number(this.systemStats && this.systemStats.total_requests || 0);
            return reported > 0 ? reported : this.sortedRecords.length;
        },
        averageDurationMs() {
            const completed = this.sortedRecords.filter(record => {
                return record && record.success && Number(record.duration_ms || 0) > 0;
            });
            if (!completed.length) return 0;
            return Math.round(completed.reduce((total, record) => total + Number(record.duration_ms || 0), 0) / completed.length);
        },
        baseUrl() {
            const origin = typeof window !== 'undefined' ? window.location.origin : 'http://127.0.0.1:8199';
            return origin + '/v1';
        },
        heroTitle() {
            return this.browserStatus && this.browserStatus.connected
                ? '本地 API 已准备就绪'
                : '本地 API 正在等待浏览器';
        },
        browserLabel() {
            if (!(this.browserStatus && this.browserStatus.connected)) return '等待浏览器连接';
            return this.browserStatus.tab_title || this.browserStatus.tab_url || '受控浏览器已连接';
        },
        latestRecord() {
            return this.sortedRecords[0] || null;
        }
    },
    methods: {
        recordTimestamp(record) {
            return Number(record && (record.finished_at || record.started_at || record.created_at || record.timestamp) || 0);
        },
        recordDomain(record) {
            return String(record && (record.domain || record.site_domain || record.target_domain || record.model || record.route_group) || '未知站点');
        },
        recordStatus(record) {
            if (record && record.success) return '成功';
            if (record && record.status === 'cancelled') return '已取消';
            return '失败';
        },
        recordStatusClass(record) {
            if (record && record.success) return 'is-success';
            if (record && record.status === 'cancelled') return 'is-cancelled';
            return 'is-failed';
        },
        recordDuration(record) {
            const value = Number(record && record.duration_ms || 0);
            if (!value) return '-';
            return value >= 1000 ? (value / 1000).toFixed(value >= 10000 ? 1 : 2) + 's' : Math.round(value) + 'ms';
        },
        recordTime(record) {
            const timestamp = this.recordTimestamp(record);
            if (!timestamp) return '-';
            return new Date(timestamp * 1000).toLocaleTimeString('zh-CN', {
                hour: '2-digit', minute: '2-digit', second: '2-digit'
            });
        },
        averageDurationText() {
            if (!this.averageDurationMs) return '-';
            return this.recordDuration({ duration_ms: this.averageDurationMs });
        },
        async copyBaseUrl() {
            try {
                await navigator.clipboard.writeText(this.baseUrl);
                this.$emit('notify', { message: 'API 地址已复制', type: 'success' });
            } catch (error) {
                this.$emit('notify', { message: '复制失败，请手动复制地址', type: 'error' });
            }
        }
    },
    template: `
        <section class="uwa-home-view" aria-labelledby="home-title">
            <div class="uwa-hero-band">
                <div class="uwa-hero-copy">
                    <div class="uwa-ready-label"><span v-html="$icons.activity"></span> Web to API, ready</div>
                    <h1 id="home-title">{{ heroTitle }}</h1>
                    <p>连接已登录的 AI 网页，把常用模型作为兼容 OpenAI / Anthropic 的本地接口使用。</p>
                </div>
                <div class="uwa-hero-art" aria-hidden="true">
                    <i class="shape-a"></i><i class="shape-b"></i><i class="shape-c"></i>
                    <i class="line-a"></i><i class="line-b"></i>
                    <i class="node-a"></i><i class="node-b"></i><i class="node-c"></i>
                </div>
            </div>

            <div class="uwa-health-strip" aria-label="服务概览">
                <div class="uwa-health-item">
                    <span class="uwa-health-icon is-service" v-html="$icons.activity"></span>
                    <div><span>服务状态</span><strong>{{ browserStatus.connected ? '运行正常' : '等待浏览器' }}</strong></div>
                </div>
                <div class="uwa-health-item">
                    <span class="uwa-health-icon is-sites" v-html="$icons.globe"></span>
                    <div><span>可用站点</span><strong>{{ siteCount }} 个站点</strong></div>
                </div>
                <div class="uwa-health-item">
                    <span class="uwa-health-icon is-requests" v-html="$icons.arrowPathRoundedSquare"></span>
                    <div><span>累计请求</span><strong>{{ totalRequests.toLocaleString('zh-CN') }} 次</strong></div>
                </div>
                <div class="uwa-health-item">
                    <span class="uwa-health-icon is-latency" v-html="$icons.timer"></span>
                    <div><span>样本平均响应</span><strong>{{ averageDurationText() }}</strong></div>
                </div>
            </div>

            <div class="uwa-section-heading">
                <div><h2>开始使用</h2><p>选择当前最需要处理的任务。</p></div>
                <button type="button" class="uwa-text-button" @click="$emit('change-tab', 'monitor')">查看运行详情 <span v-html="$icons.arrowRight"></span></button>
            </div>

            <div class="uwa-action-grid">
                <article class="uwa-action-card is-api">
                    <div class="uwa-card-topline"><span class="uwa-feature-icon is-mint" v-html="$icons.arrowPathRoundedSquare"></span><span class="uwa-soft-tag">本机</span></div>
                    <h3>连接 API 客户端</h3>
                    <p>复制当前服务 Base URL，在兼容 OpenAI 的客户端中直接使用。</p>
                    <div class="uwa-endpoint-box"><code>{{ baseUrl }}</code><button type="button" @click="copyBaseUrl" title="复制 API 地址" aria-label="复制 API 地址" v-html="$icons.copy"></button></div>
                    <button type="button" class="uwa-primary-button" @click="copyBaseUrl">复制 API 地址 <span v-html="$icons.arrowRight"></span></button>
                </article>

                <article class="uwa-action-card is-browser">
                    <div class="uwa-card-topline"><span class="uwa-feature-icon is-olive" v-html="$icons.folderOpen"></span><span class="uwa-soft-tag">{{ browserStatus.connected ? '已连接' : '未连接' }}</span></div>
                    <h3>管理受控浏览器</h3>
                    <p>检查当前浏览器连接与标签页占用情况，快速定位失效会话。</p>
                    <div class="uwa-browser-preview">
                        <div><i></i><i></i><i></i><span>{{ browserStatus.connected ? '浏览器在线' : '浏览器离线' }}</span></div>
                        <strong :title="browserLabel">{{ browserLabel }}</strong>
                    </div>
                    <button type="button" class="uwa-primary-button" @click="$emit('change-tab', 'tabpool')">查看标签页池 <span v-html="$icons.arrowRight"></span></button>
                </article>

                <article class="uwa-action-card is-monitor">
                    <div class="uwa-card-topline"><span class="uwa-feature-icon is-ochre" v-html="$icons.activity"></span><span class="uwa-soft-tag">实时</span></div>
                    <h3>检查最近请求</h3>
                    <p>查看模型路由、响应耗时与异常详情，定位客户端接入问题。</p>
                    <div class="uwa-request-preview" v-if="recentRecords.length">
                        <div v-for="record in recentRecords" :key="record.id || record.history_key || recordTimestamp(record)">
                            <i :class="recordStatusClass(record)"></i><strong>{{ recordDomain(record) }}</strong><span>{{ recordStatus(record) }}</span><time>{{ recordDuration(record) }}</time>
                        </div>
                    </div>
                    <div class="uwa-empty-preview" v-else>暂无请求记录</div>
                    <button type="button" class="uwa-primary-button" @click="$emit('change-tab', 'monitor')">打开请求监控 <span v-html="$icons.arrowRight"></span></button>
                </article>
            </div>

            <div class="uwa-activity-band" v-if="latestRecord">
                <div><span v-html="$icons.arrowPath"></span><div><strong>最近活动</strong><span>{{ recordDomain(latestRecord) }} · {{ recordStatus(latestRecord) }}</span></div></div>
                <div><span>{{ latestRecord.endpoint || latestRecord.request_type || 'API 请求' }}</span><strong>{{ recordDuration(latestRecord) }}</strong><time>{{ recordTime(latestRecord) }}</time></div>
            </div>
        </section>
    `
};
