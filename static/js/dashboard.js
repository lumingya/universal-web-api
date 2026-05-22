const { createApp } = Vue
const dashboardState = window.DashboardState || {}

// ========== Vue App ==========

const app = createApp({
    data: dashboardState.data || function () { return {} },
    computed: dashboardState.computed || {},
    watch: dashboardState.watch || {},
    mounted: dashboardState.mounted || function () {},
    beforeUnmount: dashboardState.beforeUnmount || function () {},

    methods: window.DashboardMethods || {}
});

// ========== 组件注册 ==========
app.component('sidebar-component', window.SidebarComponent);
app.component('config-tab', window.ConfigTab);
app.component('request-monitor-tab', window.RequestMonitorTab);
app.component('tabpool-tab', window.TabPoolTabComponent);
app.component('commands-tab', window.CommandsTabComponent);  // 🆕 命令系统
app.component('logs-tab', window.LogsTab);
app.component('settings-tab', window.SettingsTab);
app.component('json-preview-dialog', window.JsonPreviewDialog);
app.component('token-dialog', window.TokenDialog);
app.component('step-templates-dialog', window.StepTemplatesDialog);
app.component('test-dialog', window.TestDialog);
app.component('import-dialog', window.ImportDialog);
app.component('definition-dialog', window.DefinitionDialog);

// ========== 全局 Mixin (修复图标访问问题) ==========
app.mixin({
    computed: {
        $icons() {
            return window.$icons || window.icons || {};
        }
    }
});

// ========== 启动应用 ==========
app.mount('#app');
document.body.classList.add('app-mounted');
const appShell = document.getElementById('app-shell');
if (appShell) {
    appShell.style.display = 'none';
}
