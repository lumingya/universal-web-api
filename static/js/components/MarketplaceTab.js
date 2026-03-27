window.MarketplaceTab = {
    name: 'MarketplaceTab',
    props: {
        catalog: {
            type: Object,
            default: () => ({
                items: [],
                count: 0,
                total_downloads: 0,
                default_sort: 'downloads',
                source_mode: 'local',
                source_name: '配置市场',
                source_url: '',
                warning: ''
            })
        },
        loading: { type: Boolean, default: false },
        error: { type: String, default: '' },
        importingId: { type: String, default: null }
    },
    emits: ['refresh', 'import-item', 'preview-item', 'open-submit', 'open-link'],
    data() {
        return {
            searchQuery: '',
            selectedType: 'all',
            selectedSite: 'all',
            sortBy: 'downloads'
        };
    },
    computed: {
        iconSet() {
            return window.$icons || window.icons || {};
        },
        typeOptions() {
            return [
                { value: 'all', label: '全部类型' },
                { value: 'site_config', label: '站点配置' },
                { value: 'command_bundle', label: '命令系统' }
            ];
        },
        siteOptions() {
            const sites = new Set(['all']);
            const items = Array.isArray(this.catalog.items) ? this.catalog.items : [];
            items.forEach(item => {
                if (item.item_type === 'site_config' && item.site_domain) {
                    sites.add(item.site_domain);
                }
            });
            return Array.from(sites);
        },
        sourceBadge() {
            if (this.catalog.source_mode === 'hybrid') return 'GitHub + 本地投稿';
            if (this.catalog.source_mode === 'remote') return 'GitHub 实时索引';
            return '本地市场';
        },
        filteredItems() {
            const query = this.searchQuery.trim().toLowerCase();
            let items = Array.isArray(this.catalog.items) ? [...this.catalog.items] : [];

            if (this.selectedType !== 'all') {
                items = items.filter(item => item.item_type === this.selectedType);
            }

            if (this.selectedSite !== 'all') {
                items = items.filter(item => item.site_domain === this.selectedSite);
            }

            if (query) {
                items = items.filter(item => {
                    const parts = [
                        item.name,
                        item.summary,
                        item.author,
                        item.category,
                        item.site_domain,
                        ...(Array.isArray(item.tags) ? item.tags : [])
                    ];
                    return parts.some(part => String(part || '').toLowerCase().includes(query));
                });
            }

            const sorters = {
                downloads: (a, b) => (Number(b.downloads) || 0) - (Number(a.downloads) || 0),
                updated: (a, b) => new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime(),
                stars: (a, b) => (Number(b.stars) || 0) - (Number(a.stars) || 0),
                name: (a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'zh-CN')
            };

            const sorter = sorters[this.sortBy] || sorters.downloads;
            items.sort((a, b) => {
                const primary = sorter(a, b);
                if (primary !== 0) {
                    return primary;
                }
                return String(a.name || '').localeCompare(String(b.name || ''), 'zh-CN');
            });
            return items;
        }
    },
    watch: {
        catalog: {
            deep: true,
            handler(nextValue) {
                const defaultSort = String(nextValue?.default_sort || '').trim();
                if (defaultSort) {
                    this.sortBy = defaultSort;
                }
                if (!this.siteOptions.includes(this.selectedSite)) {
                    this.selectedSite = 'all';
                }
            }
        }
    },
    methods: {
        formatNumber(value) {
            return (Number(value) || 0).toLocaleString('zh-CN');
        },
        formatDate(value) {
            if (!value) return '未知';
            const date = new Date(value);
            if (Number.isNaN(date.getTime())) {
                return String(value);
            }
            return date.toLocaleDateString('zh-CN', {
                year: 'numeric',
                month: '2-digit',
                day: '2-digit'
            });
        },
        typeLabel(itemType) {
            return itemType === 'command_bundle' ? '命令系统' : '站点配置';
        },
        isImporting(itemId) {
            return this.importingId === itemId;
        }
    },
    template: `
        <section class="marketplace-shell">
            <div class="marketplace-hero">
                <div class="marketplace-hero__glow"></div>
                <div class="marketplace-hero__top">
                    <div class="marketplace-hero__copy">
                        <div class="marketplace-eyebrow">
                            <span v-html="iconSet.shoppingBag"></span>
                            插件市场
                        </div>
                        <h2 class="marketplace-title">站点配置与命令系统都能在这里浏览、预览、投稿和导入</h2>
                        <p class="marketplace-subtitle">
                            站点配置默认按下载量排序，并支持按站点分类。命令系统也可以作为命令包投稿和分发。
                        </p>
                        <div class="marketplace-source-row">
                            <span class="marketplace-source-badge">{{ sourceBadge }}</span>
                            <span class="marketplace-source-text">{{ catalog.source_name || '配置市场' }}</span>
                            <button v-if="catalog.source_url"
                                    type="button"
                                    class="marketplace-inline-link"
                                    @click="$emit('open-link', catalog.source_url)">
                                查看源
                            </button>
                        </div>
                        <div v-if="catalog.warning" class="marketplace-warning">{{ catalog.warning }}</div>
                    </div>

                    <div class="marketplace-stats">
                        <div class="marketplace-stat-card">
                            <span class="marketplace-stat-label">市场项目</span>
                            <strong class="marketplace-stat-value">{{ formatNumber(catalog.count || 0) }}</strong>
                        </div>
                        <div class="marketplace-stat-card">
                            <span class="marketplace-stat-label">累计下载</span>
                            <strong class="marketplace-stat-value">{{ formatNumber(catalog.total_downloads || 0) }}</strong>
                        </div>
                        <div class="marketplace-stat-card">
                            <span class="marketplace-stat-label">默认排序</span>
                            <strong class="marketplace-stat-value">按下载量</strong>
                        </div>
                    </div>
                </div>

                <div class="marketplace-toolbar">
                    <div class="marketplace-search">
                        <input v-model.trim="searchQuery"
                               type="search"
                               class="marketplace-input"
                               placeholder="搜索标题、站点、标签、作者或简介">
                    </div>
                    <div class="marketplace-toolbar__actions">
                        <select v-model="sortBy" class="marketplace-select">
                            <option value="downloads">按下载量</option>
                            <option value="updated">按最近更新</option>
                            <option value="stars">按 Star</option>
                            <option value="name">按名称</option>
                        </select>
                        <button type="button" class="marketplace-btn marketplace-btn--secondary" @click="$emit('refresh')">
                            <span v-html="iconSet.arrowPath"></span>
                            刷新市场
                        </button>
                        <button type="button" class="marketplace-btn marketplace-btn--primary" @click="$emit('open-submit')">
                            <span v-html="iconSet.arrowUpTray"></span>
                            投稿上传
                        </button>
                    </div>
                </div>

                <div class="marketplace-filter-group">
                    <div class="marketplace-filter-title">类型</div>
                    <div class="marketplace-categories">
                        <button v-for="option in typeOptions"
                                :key="option.value"
                                type="button"
                                @click="selectedType = option.value"
                                :class="['marketplace-category-chip', { 'is-active': selectedType === option.value }]">
                            {{ option.label }}
                        </button>
                    </div>
                </div>

                <div class="marketplace-filter-group" v-if="siteOptions.length > 1">
                    <div class="marketplace-filter-title">站点分类</div>
                    <div class="marketplace-categories">
                        <button v-for="site in siteOptions"
                                :key="site"
                                type="button"
                                @click="selectedSite = site"
                                :class="['marketplace-category-chip', { 'is-active': selectedSite === site }]">
                            {{ site === 'all' ? '全部站点' : site }}
                        </button>
                    </div>
                </div>
            </div>

            <div v-if="error && !loading" class="marketplace-empty">
                <h3>市场加载失败</h3>
                <p>{{ error }}</p>
                <button type="button" class="marketplace-btn marketplace-btn--primary" @click="$emit('refresh')">重新加载</button>
            </div>

            <div v-else-if="loading" class="marketplace-grid">
                <article v-for="index in 6" :key="'skeleton-' + index" class="marketplace-card marketplace-card--skeleton">
                    <div class="marketplace-skeleton marketplace-skeleton--pill"></div>
                    <div class="marketplace-skeleton marketplace-skeleton--title"></div>
                    <div class="marketplace-skeleton marketplace-skeleton--line"></div>
                    <div class="marketplace-skeleton marketplace-skeleton--line short"></div>
                    <div class="marketplace-skeleton marketplace-skeleton--meta"></div>
                    <div class="marketplace-skeleton marketplace-skeleton--meta short"></div>
                </article>
            </div>

            <div v-else-if="filteredItems.length === 0" class="marketplace-empty">
                <h3>没有找到匹配项目</h3>
                <p>可以试试切换类型、站点分类，或者换个关键词搜索。</p>
            </div>

            <div v-else class="marketplace-grid">
                <article v-for="item in filteredItems" :key="item.id" class="marketplace-card">
                    <div class="marketplace-card__header">
                        <span class="marketplace-badge">{{ typeLabel(item.item_type) }}</span>
                        <span v-if="item.site_domain" class="marketplace-badge marketplace-badge--muted">{{ item.site_domain }}</span>
                    </div>

                    <div class="marketplace-card__body">
                        <h3 class="marketplace-card__title">{{ item.name }}</h3>
                        <p class="marketplace-card__summary">{{ item.summary || '暂无简介。' }}</p>

                        <dl class="marketplace-meta-grid">
                            <div>
                                <dt>作者</dt>
                                <dd>{{ item.author || '社区贡献' }}</dd>
                            </div>
                            <div>
                                <dt>版本</dt>
                                <dd>{{ item.version || '未标记' }}</dd>
                            </div>
                            <div>
                                <dt>分类</dt>
                                <dd>{{ item.category || '未分类' }}</dd>
                            </div>
                            <div>
                                <dt>兼容</dt>
                                <dd>{{ item.compatibility || '通用' }}</dd>
                            </div>
                        </dl>

                        <div v-if="Array.isArray(item.tags) && item.tags.length" class="marketplace-tags">
                            <span v-for="tag in item.tags" :key="item.id + '-' + tag" class="marketplace-tag">{{ tag }}</span>
                        </div>
                    </div>

                    <div class="marketplace-card__footer">
                        <div class="marketplace-stat-row">
                            <span>下载 {{ formatNumber(item.downloads) }}</span>
                            <span v-if="item.stars">Star {{ formatNumber(item.stars) }}</span>
                            <span>更新 {{ formatDate(item.updated_at) }}</span>
                        </div>
                        <div class="marketplace-actions">
                            <button type="button"
                                    class="marketplace-btn marketplace-btn--ghost"
                                    @click="$emit('preview-item', item)">
                                <span v-html="iconSet.folderOpen"></span>
                                预览
                            </button>
                            <button type="button"
                                    class="marketplace-btn marketplace-btn--primary"
                                    :disabled="isImporting(item.id)"
                                    @click="$emit('import-item', item)">
                                <span v-if="!isImporting(item.id)" v-html="iconSet.arrowDownTray"></span>
                                {{ isImporting(item.id) ? '处理中...' : '导入' }}
                            </button>
                        </div>
                    </div>
                </article>
            </div>
        </section>
    `
};
