// R2-8：站点适配器维护面板——健康巡检（R1-6）、选择器脆弱度（R1-7）、适配器在线更新（R1-3）。
// 由 ConfigTab 以 <adapter-panel> 使用；request 为带鉴权与超时的请求函数（返回解析后的 JSON，出错抛异常）。
window.AdapterPanel = {
    name: 'AdapterPanel',
    props: {
        domain: { type: String, default: '' },
        presetName: { type: String, default: '' },
        request: { type: Function, required: true }
    },
    data() {
        return {
            health: null, healthLoading: false, healthError: '',
            lint: null, lintLoading: false, lintError: '',
            updates: null, updatesLoading: false, updatesError: '',
            applyResult: null, applying: false
        };
    },
    computed: {
        healthTab() {
            return (this.health && Array.isArray(this.health.tabs) && this.health.tabs[0]) || null;
        },
        lintRows() {
            const presets = (this.lint && this.lint.presets) || {};
            const rows = presets[this.presetName] || Object.values(presets)[0] || [];
            return rows.filter(row => row.score < 100 || (row.issues || []).length);
        },
        updateRows() {
            return ((this.updates && this.updates.sites) || []).filter(row => row.status !== 'up_to_date');
        },
        safeUpdateCount() {
            return this.updateRows.filter(row => row.status === 'update_available' || row.status === 'new_site').length;
        }
    },
    watch: {
        domain() {
            this.health = null; this.healthError = '';
            this.lint = null; this.lintError = '';
        }
    },
    methods: {
        statusLabel(status) {
            return ({
                healthy: '健康', degraded: '部分缺失', broken: '异常', unreachable: '无可用页面',
                ok: '命中', missing: '未命中', invalid: '语法无效',
                up_to_date: '已是最新', update_available: '可安全更新', conflict: '本地已修改',
                new_site: '新站点', local_only: '仅本地', requires_newer_app: '需要更新程序'
            })[status] || status;
        },
        statusClass(status) {
            if (['healthy', 'ok', 'up_to_date'].includes(status)) return 'adapter-badge is-good';
            if (['degraded', 'missing', 'update_available', 'new_site', 'conflict'].includes(status)) return 'adapter-badge is-warn';
            if (['broken', 'invalid', 'requires_newer_app'].includes(status)) return 'adapter-badge is-bad';
            return 'adapter-badge';
        },
        async runHealth() {
            if (!this.domain) return;
            this.healthLoading = true; this.healthError = '';
            try {
                const query = new URLSearchParams({ site: this.domain });
                if (this.presetName) query.set('preset', this.presetName);
                this.health = await this.request('/api/adapters/health?' + query.toString());
            } catch (error) {
                this.healthError = error.message || String(error);
            } finally {
                this.healthLoading = false;
            }
        },
        async runLint() {
            if (!this.domain) return;
            this.lintLoading = true; this.lintError = '';
            try {
                this.lint = await this.request('/api/adapters/lint?' + new URLSearchParams({ site: this.domain }).toString());
            } catch (error) {
                this.lintError = error.message || String(error);
            } finally {
                this.lintLoading = false;
            }
        },
        async checkUpdates() {
            this.updatesLoading = true; this.updatesError = ''; this.applyResult = null;
            try {
                this.updates = await this.request('/api/adapters/updates');
            } catch (error) {
                this.updatesError = error.message || String(error);
            } finally {
                this.updatesLoading = false;
            }
        },
        async applyUpdates(sites = null, includeConflicts = false) {
            if (includeConflicts && !window.confirm('该站点的本地配置改过：将按「本地优先」与官方新版合并（保留你的修改，补入新增字段）。继续？')) {
                return;
            }
            this.applying = true; this.updatesError = '';
            try {
                const body = { include_conflicts: includeConflicts };
                if (sites) body.sites = sites;
                this.applyResult = await this.request('/api/adapters/updates/apply', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
                });
                this.$emit('updated', this.applyResult);
                await this.checkUpdates();
            } catch (error) {
                this.updatesError = error.message || String(error);
            } finally {
                this.applying = false;
            }
        }
    },
    template: `
    <div class="adapter-panel">
        <section class="cap-card adapter-card" aria-label="健康巡检">
            <div class="adapter-card-header">
                <div>
                    <h4>健康巡检</h4>
                    <p>在浏览器里已打开的 {{ domain || '当前站点' }} 页面上检查各选择器能否命中。只读取页面，不会输入、点击或发送消息。</p>
                </div>
                <button type="button" class="adapter-btn" :disabled="!domain || healthLoading" @click="runHealth">
                    {{ healthLoading ? '巡检中…' : '开始巡检' }}
                </button>
            </div>
            <p v-if="healthError" class="adapter-error">{{ healthError }}</p>
            <div v-if="health" class="adapter-result">
                <p>结果：<span :class="statusClass(health.status)">{{ statusLabel(health.status) }}</span>
                    <span v-if="health.preset" class="adapter-muted">预设：{{ health.preset }}</span></p>
                <ul v-if="health.problems && health.problems.length" class="adapter-problems">
                    <li v-for="problem in health.problems" :key="problem">{{ problem }}</li>
                </ul>
                <table v-if="healthTab" class="adapter-table">
                    <thead><tr><th>元素</th><th>状态</th><th>命中数</th><th>稳健度</th><th>说明</th></tr></thead>
                    <tbody>
                        <tr v-for="row in healthTab.selectors" :key="row.key">
                            <td><code>{{ row.key }}</code></td>
                            <td><span :class="statusClass(row.status)">{{ statusLabel(row.status) }}</span></td>
                            <td>{{ row.count }}</td>
                            <td>{{ row.quality ? row.quality.score : '-' }}</td>
                            <td>
                                <span v-if="row.hint">{{ row.hint }}</span>
                                <span v-if="row.suggestions && row.suggestions.length">可尝试：<code>{{ row.suggestions[0].selector }}</code></span>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </section>

        <section class="cap-card adapter-card" aria-label="选择器稳健度">
            <div class="adapter-card-header">
                <div>
                    <h4>选择器稳健度</h4>
                    <p>静态检查当前预设的选择器：生成的哈希类名、依赖元素顺序或界面文字的写法，在站点改版时容易失效。</p>
                </div>
                <button type="button" class="adapter-btn" :disabled="!domain || lintLoading" @click="runLint">
                    {{ lintLoading ? '检查中…' : '检查' }}
                </button>
            </div>
            <p v-if="lintError" class="adapter-error">{{ lintError }}</p>
            <div v-if="lint" class="adapter-result">
                <p v-if="!lintRows.length" class="adapter-good">当前预设的选择器没有发现明显问题。</p>
                <table v-else class="adapter-table">
                    <thead><tr><th>元素</th><th>得分</th><th>问题</th></tr></thead>
                    <tbody>
                        <tr v-for="row in lintRows" :key="row.key">
                            <td><code>{{ row.key }}</code></td>
                            <td>{{ row.score }}</td>
                            <td>{{ (row.issues || []).map(issue => issue.message).join('；') || '—' }}</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </section>

        <section class="cap-card adapter-card" aria-label="适配器更新">
            <div class="adapter-card-header">
                <div>
                    <h4>适配器更新（全部站点）</h4>
                    <p>与官方仓库的站点配置比较。没改过的站点可以一键更新；改过的站点会单独标出，需要你确认后按「本地优先」合并。</p>
                </div>
                <button type="button" class="adapter-btn" :disabled="updatesLoading || applying" @click="checkUpdates">
                    {{ updatesLoading ? '检查中…' : '检查更新' }}
                </button>
            </div>
            <p v-if="updatesError" class="adapter-error">{{ updatesError }}</p>
            <div v-if="updates" class="adapter-result">
                <p v-if="!updateRows.length" class="adapter-good">所有站点配置都是最新的。</p>
                <template v-else>
                    <table class="adapter-table">
                        <thead><tr><th>站点</th><th>状态</th><th>本地版本</th><th>官方版本</th><th></th></tr></thead>
                        <tbody>
                            <tr v-for="row in updateRows" :key="row.site">
                                <td><code>{{ row.site }}</code></td>
                                <td><span :class="statusClass(row.status)">{{ statusLabel(row.status) }}</span></td>
                                <td>{{ row.local_version || '—' }}</td>
                                <td>{{ row.official_version || '—' }}</td>
                                <td>
                                    <button v-if="row.status === 'conflict'" type="button" class="adapter-btn is-small" :disabled="applying"
                                            @click="applyUpdates([row.site], true)">合并</button>
                                    <span v-else-if="row.status === 'requires_newer_app'" class="adapter-muted">需要 {{ row.min_app_version }}+</span>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                    <button type="button" class="adapter-btn" :disabled="applying || !safeUpdateCount" @click="applyUpdates()">
                        {{ applying ? '更新中…' : ('应用 ' + safeUpdateCount + ' 个安全更新') }}
                    </button>
                </template>
                <p v-if="applyResult" class="adapter-good">已更新 {{ (applyResult.applied || []).length }} 个站点<span v-if="(applyResult.skipped || []).length">，跳过 {{ applyResult.skipped.length }} 个</span>。</p>
            </div>
        </section>
    </div>`
};
