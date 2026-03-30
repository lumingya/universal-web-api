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
            pageSize: 16,
            pageSizeOptions: [8, 16, 24, 32, 48, 64],
            reordering: false,
            selectedCommandIds: [],
            pendingGroupName: '',
            selectedExistingGroupName: '',
            groupWorking: false,
            includeDisabledWhenRunGroup: false,
            runGroupAcquirePolicy: 'inherit_session',
            showGroupTools: false,
            collapsedGroups: {},
            bulkActionMenuOpen: false,
            groupActionMenuOpen: '',
            draggingCommandId: '',
            dragOverGroupName: '',
            triggerTypePickerOpen: false,
            triggerTypeTooltipType: '',
            sourceCommandPickerOpen: false,
            sourceCommandSearch: '',
            sourcePickerExpandedGroups: {},
            sourcePickerShowUngrouped: false,
            pageProbeExpanded: false,

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
            napcatDefaults: {
                base_url: 'http://127.0.0.1:3000',
                target_type: 'private',
                user_id: '',
                group_id: '',
                message: '命令通知：{{source_command_name}}\\n{{command_result_summary}}',
                access_token: '',
                timeout: 8,
                raise_for_status: true
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
