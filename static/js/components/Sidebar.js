// ==================== 侧边栏组件 ====================
window.getDashboardAuthToken = window.getDashboardAuthToken || function () {
    try {
        return String(
            localStorage.getItem('dashboard_token')
            || localStorage.getItem('api_token')
            || ''
        ).trim();
    } catch (e) {
        return '';
    }
};

window.SidebarComponent = {
    name: 'SidebarComponent',
    props: {
        sites: { type: Object, required: true },
        currentDomain: { type: String, default: '' },
        browserStatus: { type: Object, required: true },
        authEnabled: { type: Boolean, default: false },
        hasToken: { type: Boolean, default: false },
        darkMode: { type: Boolean, default: false },
        activeTab: { type: String, default: 'home' },
        updateAvailable: { type: Boolean, default: false },
        systemStats: { type: Object, default: () => ({ memory_mb: 0, disk_status: '未知', total_requests: 0 }) }
    },
    emits: [
        'select-site', 
        'add-site', 
        'delete-site', 
        'export-site', 
        'toggle-dark', 
        'refresh-status', 
        'trigger-import', 
        'export-all',
        'show-token-dialog',
        'open-guide',
        'primary-action',
        'collapse-change',
        'change-tab'  // ✨ 新增
    ],
    data() {
        return {
            navCollapsed: false,
            mobileOpen: false,
            tourNavigationSnapshot: null
        };
    },
    computed: {
        primaryActionLabel() {
            return this.activeTab === 'commands' ? '新建命令' : '新增站点';
        }
    },
    mounted() {
        try {
            this.navCollapsed = localStorage.getItem('dashboard_nav_collapsed') === '1';
        } catch (e) {
            this.navCollapsed = false;
        }
        this.$emit('collapse-change', this.navCollapsed);
    },
    methods: {
        toggleNavCollapsed() {
            this.navCollapsed = !this.navCollapsed;
            this.$emit('collapse-change', this.navCollapsed);
            try {
                localStorage.setItem('dashboard_nav_collapsed', this.navCollapsed ? '1' : '0');
            } catch (e) {
                // Layout persistence is optional.
            }
        },
        openMobileNav() {
            this.mobileOpen = true;
        },
        closeMobileNav() {
            this.mobileOpen = false;
        },
        beginTourNavigation() {
            if (!this.tourNavigationSnapshot) {
                this.tourNavigationSnapshot = {
                    navCollapsed: this.navCollapsed,
                    mobileOpen: this.mobileOpen
                };
            }
            this.navCollapsed = false;
            this.mobileOpen = window.innerWidth <= 900;
            this.$emit('collapse-change', false);
        },
        endTourNavigation() {
            const snapshot = this.tourNavigationSnapshot;
            this.mobileOpen = false;
            if (snapshot) {
                this.navCollapsed = snapshot.navCollapsed;
                this.$emit('collapse-change', this.navCollapsed);
            }
            this.tourNavigationSnapshot = null;
        },
        selectTab(tab) {
            this.mobileOpen = false;
            this.$emit('change-tab', tab);
        },
        selectSite(domain) {
            this.mobileOpen = false;
            this.$emit('select-site', domain);
        },
        runPrimaryAction() {
            this.mobileOpen = false;
            this.$emit('primary-action', this.activeTab);
        }
    },
    template: `
        <div :class="['app-sidebar-layer', { 'is-mobile-open': mobileOpen }]">
        <button type="button" class="app-sidebar-scrim" @click="closeMobileNav" aria-label="关闭导航"></button>
        <aside :class="['app-sidebar', { 'is-collapsed': navCollapsed }]" aria-label="主导航">
            <div class="app-brand">
                <img src="/static/images/logo.svg" alt="Universal Web API logo" class="app-brand-logo">
                <div class="app-brand-copy">
                    <strong>Universal API</strong>
                    <span>Local AI bridge</span>
                </div>
                <button type="button" class="app-nav-collapse" @click="toggleNavCollapsed"
                        :title="navCollapsed ? '展开导航' : '折叠导航'"
                        :aria-label="navCollapsed ? '展开导航' : '折叠导航'">
                    <span v-if="navCollapsed" class="app-collapse-lines" aria-hidden="true"></span>
                    <span v-else v-html="$icons.xMark" aria-hidden="true"></span>
                </button>
                <button type="button" class="app-nav-mobile-close" @click="closeMobileNav" title="关闭导航" aria-label="关闭导航" v-html="$icons.xMark"></button>
                <button type="button" class="app-collapsed-status" @click="$emit('refresh-status')" title="刷新状态" aria-label="刷新服务状态">
                    <i :class="['app-service-dot', { 'is-online': browserStatus.connected }]"></i>
                    <span>{{ browserStatus.connected ? '在线' : '离线' }}</span>
                </button>
            </div>

            <nav class="app-primary-nav" aria-label="功能区">
                <button type="button" @click="selectTab('home')"
                        :class="['app-nav-item', { 'is-active': activeTab === 'home' }]"
                        title="首页" aria-label="首页"
                        :aria-current="activeTab === 'home' ? 'page' : null">
                    <span class="app-nav-icon" v-html="$icons.home"></span>
                    <span>首页</span>
                </button>
                <button type="button" @click="selectTab('config')"
                        data-tour-target="config"
                        :class="['app-nav-item', { 'is-active': activeTab === 'config' }]"
                        title="站点配置" aria-label="站点配置"
                        :aria-current="activeTab === 'config' ? 'page' : null">
                    <span class="app-nav-icon" v-html="$icons.clipboardList"></span>
                    <span>站点配置</span>
                    <span class="app-nav-count">{{ Object.keys(sites).length }}</span>
                </button>
                <button type="button" @click="selectTab('tabpool')"
                        data-tour-target="tabpool"
                        :class="['app-nav-item', { 'is-active': activeTab === 'tabpool' }]"
                        title="标签页池" aria-label="标签页池"
                        :aria-current="activeTab === 'tabpool' ? 'page' : null">
                    <span class="app-nav-icon" v-html="$icons.folderOpen"></span>
                    <span>标签页池</span>
                </button>
                <button type="button" @click="selectTab('monitor')"
                        data-tour-target="monitor"
                        :class="['app-nav-item', { 'is-active': activeTab === 'monitor' }]"
                        title="请求监控" aria-label="请求监控"
                        :aria-current="activeTab === 'monitor' ? 'page' : null">
                    <span class="app-nav-icon" v-html="$icons.chartBar"></span>
                    <span>请求监控</span>
                </button>
                <button type="button" @click="selectTab('commands')"
                        data-tour-target="commands"
                        :class="['app-nav-item', { 'is-active': activeTab === 'commands' }]"
                        title="命令系统" aria-label="命令系统"
                        :aria-current="activeTab === 'commands' ? 'page' : null">
                    <span class="app-nav-icon" v-html="$icons.arrowPathRoundedSquare"></span>
                    <span>命令系统</span>
                </button>
                <button type="button" @click="selectTab('logs')"
                        data-tour-target="logs"
                        :class="['app-nav-item', { 'is-active': activeTab === 'logs' }]"
                        title="运行日志" aria-label="运行日志"
                        :aria-current="activeTab === 'logs' ? 'page' : null">
                    <span class="app-nav-icon" v-html="$icons.documentArrowDown"></span>
                    <span>运行日志</span>
                </button>
                <button type="button" @click="selectTab('settings')"
                        data-tour-target="settings"
                        :class="['app-nav-item', { 'is-active': activeTab === 'settings' }]"
                        title="设置" aria-label="设置"
                        :aria-current="activeTab === 'settings' ? 'page' : null">
                    <span class="app-nav-icon" v-html="$icons.cog"></span>
                    <span>设置</span>
                    <span v-if="updateAvailable" class="app-update-dot" title="发现新版本"></span>
                </button>
            </nav>
            <div class="app-sidebar-spacer"></div>

            <div class="app-sidebar-footer">
                <button type="button" class="app-guide-link" @click="$emit('open-guide')" title="重新打开新手指南">
                    <span class="app-footer-icon" v-html="$icons.bookOpen" aria-hidden="true"></span>
                    <span>新手指南</span>
                </button>
                <button type="button" class="app-theme-toggle"
                        @click.prevent.stop="$emit('toggle-dark')"
                        :aria-pressed="darkMode"
                        :title="darkMode ? '切换到日间模式' : '切换到夜间模式'">
                    <span class="app-footer-icon" v-html="darkMode ? $icons.sun : $icons.moon" aria-hidden="true"></span>
                    <span>{{ darkMode ? '日间模式' : '夜间模式' }}</span>
                    <i aria-hidden="true"></i>
                </button>
                <div class="app-service-row">
                    <span :class="['app-service-dot', { 'is-online': browserStatus.connected }]"></span>
                    <span>{{ browserStatus.connected ? '浏览器已连接' : '浏览器未连接' }}</span>
                    <button type="button" @click="$emit('refresh-status')" title="刷新状态" aria-label="刷新状态" v-html="$icons.arrowPath"></button>
                </div>
                <button v-if="authEnabled" type="button" class="app-auth-row" @click.stop="$emit('show-token-dialog')">
                    <span>面板认证</span>
                    <strong>{{ hasToken ? '已配置' : '待配置' }}</strong>
                </button>
                <button type="button" class="app-sidebar-primary" @click="runPrimaryAction" :title="primaryActionLabel" :aria-label="primaryActionLabel">
                    <span v-html="$icons.plusCircle"></span>
                    <span>{{ primaryActionLabel }}</span>
                </button>
                <div class="app-sidebar-version"><span>本地模式</span><strong>v2.9.5</strong></div>
            </div>
        </aside>
        </div>
    `
};
