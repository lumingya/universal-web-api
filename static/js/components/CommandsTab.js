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
            },
            automationWriteDefaults: {
                selector: '',
                write_mode: 'replace',
                clear_first: true,
                value_source: 'literal',
                text: '',
                template: '',
                variable_name: '',
                random_kind: 'alnum',
                random_length: 8,
                prefix: '',
                suffix: '',
                preset_name: 'name_cn',
                date_format: 'YYYY-MM-DD',
                min_age: 18,
                max_age: 35,
                save_as: '',
                timeout_sec: 6
            },
            automationReadDefaults: {
                selector: '',
                read_mode: 'auto',
                attr_name: '',
                trim: true,
                save_as: '',
                timeout_sec: 6
            },
            httpRequestDefaults: {
                method: 'GET',
                url: '',
                headers: '{"Accept":"application/json"}',
                body: '',
                body_mode: 'json',
                response_mode: 'text',
                credentials: 'include',
                timeout_sec: 15,
                fail_on_http_error: true,
                save_as: ''
            },
            appendFileDefaults: {
                file_path: '',
                content: '',
                append_newline: true,
                create_dirs: true,
                encoding: 'utf-8'
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
