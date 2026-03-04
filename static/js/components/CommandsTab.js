// ==================== 命令管理组件 ====================
window.CommandsTabComponent = {
    name: 'CommandsTabComponent',
    props: {
        darkMode: { type: Boolean, default: false }
    },
    data() {
        return {
            commands: [],
            loading: false,
            meta: { trigger_types: {}, action_types: {} },
            availableDomains: [],
            availableTabs: [],
            availablePresets: [],
            presetLoading: false,
            showHelpTip: false,
            currentPage: 1,
            pageSize: 6,
            pageSizeOptions: [4, 6, 10, 16],
            reordering: false,
            selectedCommandIds: [],
            pendingGroupName: '',
            groupWorking: false,
            includeDisabledWhenRunGroup: false,
            showGroupTools: false,
            collapsedGroups: {},

            // 编辑弹窗
            showEditor: false,
            editingCommand: null,
            isNew: false,

            // 高级模式编辑器高度
            scriptEditorHeight: '300px',

            // 代理切换默认配置
            proxyDefaults: {
                clash_api: 'http://127.0.0.1:9090',
                clash_secret: '',
                selector: 'Proxy',
                mode: 'random',
                node_name: '',
                exclude_keywords: 'DIRECT,REJECT,GLOBAL,自动选择,故障转移',
                refresh_after: true
            },
            webhookDefaults: {
                method: 'POST',
                url: '',
                payload: '{"msg":"标签页#{{tab_index}} 在 {{domain}} 命中异常状态码 {{network_status}}"}',
                headers: '{"Content-Type":"application/json"}',
                timeout: 8,
                raise_for_status: false
            },
            releaseLockDefaults: {
                reason: 'release_tab_lock_action',
                clear_page: true,
                stop_actions: true
            }
        };
    },
    computed: window.CommandsTabComputed,

    methods: window.CommandsTabMethods,

    mounted() {
        this.fetchMeta();
        this.fetchCommands();
        this.fetchBindingMeta();
    },
    template: window.CommandsTabTemplate
};
