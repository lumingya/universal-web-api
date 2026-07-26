// ==================== 标签页池组件 ====================
window.TabPoolTabComponent = {
    name: 'TabPoolTabComponent',
    props: {
        darkMode: { type: Boolean, default: false }
    },
    data() {
        return {
            tabs: [],
            loading: false,
            error: null,
            autoRefresh: true,
            refreshInterval: null,
            lastUpdate: null,
            baseUrl: '',
            presetUpdating: {},  // { tabIndex: true } 正在切换预设的标签页
            allocationMode: 'first_idle',
            allocationModeOptions: [
                { value: 'first_idle', label: '优先空闲' },
                { value: 'round_robin', label: '轮询' },
                { value: 'random', label: '随机' }
            ],
            allocationModeUpdating: false,
            routeMethodOptions: [
                { value: 'domain', label: '站点域名路由' },
                { value: 'route_group', label: '标签页路由组' },
                { value: 'fixed_tab', label: '固定标签页路由' },
                { value: 'exact_url', label: '标签页 URL 路由' },
                { value: 'exact_url_preset', label: 'URL 绑定预设路由' }
            ],
            enabledRouteMethods: ['domain', 'route_group', 'fixed_tab', 'exact_url', 'exact_url_preset'],
            routeMethodUpdating: false,
            routeGroups: [],
            routeGroupsExpanded: false,
            selectedRouteGroupId: 'all',
            tabSearchQuery: '',
            helpExpanded: false,
            mobileGroupNavOpen: false,
            groupNavWidth: 268,
            groupNavCollapsed: true,
            groupNavResizing: false,
            groupNavResizeStartX: 0,
            groupNavResizeStartWidth: 268,
            editorCollapsed: true,
            routeGroupModal: {
                visible: false,
                saving: false,
                editIndex: -1,
                id: '',
                name: '',
                route_domain: '',
                preset_name: '',
                allocation_mode: 'round_robin',
                members: []
            },
            excludedUrls: [],
            excludedUrlsDraft: '',
            excludedUrlsDraftDirty: false,
            excludedUrlsUpdating: false,
            preserveErrorTabs: false,
            preserveErrorTabsUpdating: false,
            showRouteSettings: false,
            // 修复：标记持久化配置是否真正加载成功，未成功时禁止任何写回操作
            configLoaded: false,
            fetchInFlight: false,
            fetchRequestSeq: 0,
            fetchAbortController: null,
            visibilityChangeHandler: null,
            tabsResponseSignature: '',
            modelNameModal: {
                visible: false,
                saving: false,
                showSaveOptions: false,
                tab: null,
                draft: ''
            },
            modelNameTooltip: {
                visible: false,
                text: '',
                left: 0,
                top: 0
            },
            terminateModal: {
                visible: false,
                submitting: false,
                tab: null
            },
            modelNameTooltipText: '修改该标签页的模型显示名称后，前端选中这个模型时，只会在该标签页或同名模型的标签页中轮询，不会调度到其他模型标签页。'
        };
    },
    computed: {
        statusColor() {
            return (status) => {
                switch (status) {
                    case 'idle': return 'bg-green-500';
                    case 'busy': return 'bg-yellow-500';
                    case 'error': return 'bg-red-500';
                    // 修复：补齐后端 TabStatus.CLOSED，用浅灰与错误态/未知态区分
                    case 'closed': return 'bg-gray-400';
                    default: return 'bg-gray-500';
                }
            };
        },
        statusText() {
            return (status) => {
                switch (status) {
                    case 'idle': return '空闲';
                    case 'busy': return '忙碌';
                    case 'error': return '错误';
                    // 修复：补齐后端 TabStatus.CLOSED，避免直接展示英文原值
                    case 'closed': return '已关闭';
                    default: return status;
                }
            };
        },
        routeMethodSet() {
            return new Set(this.enabledRouteMethods || []);
        },
        routeGroupLiveCount() {
            return (this.routeGroups || []).reduce((sum, group) => sum + Number(group.live_member_count || 0), 0);
        },
        routeGroupIdleCount() {
            return (this.routeGroups || []).reduce((sum, group) => sum + Number(group.idle_member_count || 0), 0);
        },
        idleTabCount() {
            return (this.tabs || []).filter(tab => tab.status === 'idle').length;
        },
        selectedRouteGroup() {
            if (this.selectedRouteGroupId === 'all') return null;
            return (this.routeGroups || []).find(group => String(group.id || '') === this.selectedRouteGroupId) || null;
        },
        selectedRouteGroupIndex() {
            if (!this.selectedRouteGroup) return -1;
            return (this.routeGroups || []).findIndex(group => String(group.id || '') === this.selectedRouteGroupId);
        },
        visibleTabs() {
            const group = this.selectedRouteGroup;
            if (!group) return this.tabs || [];
            return (this.tabs || []).filter(tab => (group.members || []).some(
                member => this.routeGroupMemberMatchesTab(member, tab)
            ));
        },
        displayedTabs() {
            const query = String(this.tabSearchQuery || '').trim().toLowerCase();
            if (!query) return this.visibleTabs;
            return (this.visibleTabs || []).filter(tab => {
                const searchable = [
                    tab.persistent_index,
                    tab.current_domain,
                    tab.url,
                    tab.id,
                    tab.session_id,
                    tab.status,
                    this.getExposedModelName(tab),
                    this.getDisplayedPreset(tab),
                    ...this.getTabRouteGroupIds(tab)
                ].join(' ').toLowerCase();
                return searchable.includes(query);
            });
        },
        visibleIdleCount() {
            return (this.visibleTabs || []).filter(tab => tab.status === 'idle').length;
        },
        displayedIdleCount() {
            return (this.displayedTabs || []).filter(tab => tab.status === 'idle').length;
        },
        selectedViewTitle() {
            return this.selectedRouteGroup
                ? (this.selectedRouteGroup.name || this.selectedRouteGroup.id)
                : '全部标签页';
        },
        groupNavStyle() {
            const width = this.groupNavCollapsed ? 64 : this.groupNavWidth;
            return { '--tp-group-nav-width': width + 'px' };
        },
        groupNavCompact() {
            return !this.groupNavCollapsed && this.groupNavWidth < 244;
        },
        modelNameTooltipStyle() {
            return {
                left: this.modelNameTooltip.left + 'px',
                top: this.modelNameTooltip.top + 'px'
            };
        }
    },
    watch: {
        autoRefresh(enabled) {
            if (enabled) {
                this.startAutoRefresh();
                if (!this.isDocumentHidden()) {
                    this.fetchTabs({ silent: true });
                }
            } else {
                this.stopAutoRefreshTimer();
            }
        }
    },
    methods: {
        loadTabPoolLayout() {
            try {
                const storedWidth = Number(localStorage.getItem('tab_pool_group_nav_width'));
                if (Number.isFinite(storedWidth) && storedWidth >= 220 && storedWidth <= 340) {
                    this.groupNavWidth = storedWidth;
                }
                const storedCollapsedState = localStorage.getItem('tab_pool_group_nav_collapsed');
                this.groupNavCollapsed = storedCollapsedState === null
                    ? true
                    : storedCollapsedState === '1';
                const storedEditorState = localStorage.getItem('tab_pool_group_editor_collapsed');
                this.editorCollapsed = storedEditorState === null ? true : storedEditorState === '1';
            } catch (e) {
                // Storage is optional; keep the default layout when unavailable.
            }
        },

        persistTabPoolLayout() {
            try {
                localStorage.setItem('tab_pool_group_nav_width', String(Math.round(this.groupNavWidth)));
                localStorage.setItem('tab_pool_group_nav_collapsed', this.groupNavCollapsed ? '1' : '0');
                localStorage.setItem('tab_pool_group_editor_collapsed', this.editorCollapsed ? '1' : '0');
            } catch (e) {
                // Ignore storage failures without blocking the controls.
            }
        },

        toggleGroupNavCollapsed() {
            this.groupNavCollapsed = !this.groupNavCollapsed;
            this.persistTabPoolLayout();
        },

        startGroupNavResize(event) {
            if (this.groupNavCollapsed || !event) return;
            this.groupNavResizing = true;
            this.groupNavResizeStartX = Number(event.clientX || 0);
            this.groupNavResizeStartWidth = this.groupNavWidth;
            document.body.classList.add('tab-pool-is-resizing');
            document.addEventListener('pointermove', this.handleGroupNavResize);
            document.addEventListener('pointerup', this.stopGroupNavResize);
            if (event.preventDefault) event.preventDefault();
        },

        handleGroupNavResize(event) {
            if (!this.groupNavResizing || !event) return;
            const delta = Number(event.clientX || 0) - this.groupNavResizeStartX;
            this.groupNavWidth = Math.min(340, Math.max(220, this.groupNavResizeStartWidth + delta));
        },

        stopGroupNavResize() {
            if (!this.groupNavResizing) return;
            this.groupNavResizing = false;
            document.body.classList.remove('tab-pool-is-resizing');
            document.removeEventListener('pointermove', this.handleGroupNavResize);
            document.removeEventListener('pointerup', this.stopGroupNavResize);
            this.persistTabPoolLayout();
        },

        handleGroupNavResizeKeydown(event) {
            if (this.groupNavCollapsed || !event) return;
            const step = event.shiftKey ? 24 : 8;
            if (event.key === 'ArrowLeft') {
                this.groupNavWidth = Math.max(220, this.groupNavWidth - step);
            } else if (event.key === 'ArrowRight') {
                this.groupNavWidth = Math.min(340, this.groupNavWidth + step);
            } else if (event.key === 'Home') {
                this.groupNavWidth = 220;
            } else if (event.key === 'End') {
                this.groupNavWidth = 340;
            } else {
                return;
            }
            event.preventDefault();
            this.persistTabPoolLayout();
        },

        resetGroupNavWidth() {
            this.groupNavWidth = 268;
            this.persistTabPoolLayout();
        },

        toggleGroupEditor() {
            this.editorCollapsed = !this.editorCollapsed;
            this.persistTabPoolLayout();
        },

        handleDocumentClick(event) {
            if (!this.showRouteSettings) return;
            const panel = this.$el && this.$el.querySelector('[data-route-settings-panel]');
            const trigger = this.$el && this.$el.querySelector('[data-route-settings-trigger]');
            const target = event && event.target;
            if ((panel && panel.contains(target)) || (trigger && trigger.contains(target))) {
                return;
            }
            this.showRouteSettings = false;
        },

        positionModelNameTooltip(event) {
            if (!event) return;
            const tooltipWidth = 360;
            const tooltipHeight = 92;
            const margin = 12;
            const viewportWidth = window.innerWidth || document.documentElement.clientWidth || tooltipWidth;
            const viewportHeight = window.innerHeight || document.documentElement.clientHeight || tooltipHeight;
            let left = Number(event.clientX || 0) + 14;
            let top = Number(event.clientY || 0) + 14;
            if (left + tooltipWidth > viewportWidth - margin) {
                left = Number(event.clientX || 0) - tooltipWidth - 14;
            }
            if (top + tooltipHeight > viewportHeight - margin) {
                top = Number(event.clientY || 0) - tooltipHeight - 14;
            }
            this.modelNameTooltip.left = Math.max(margin, Math.min(left, viewportWidth - tooltipWidth - margin));
            this.modelNameTooltip.top = Math.max(margin, Math.min(top, viewportHeight - tooltipHeight - margin));
        },

        showModelNameTooltip(event, text = null) {
            this.modelNameTooltip.text = text || this.modelNameTooltipText;
            this.modelNameTooltip.visible = true;
            this.positionModelNameTooltip(event);
        },

        hideModelNameTooltip() {
            this.modelNameTooltip.visible = false;
        },

        makeTabsResponseSignature(data) {
            try {
                return JSON.stringify({
                    tabs: Array.isArray(data && data.tabs) ? data.tabs : [],
                    allocation_mode: data && data.allocation_mode || '',
                    allocation_mode_options: data && data.allocation_mode_options || [],
                    enabled_route_methods: data && data.enabled_route_methods || [],
                    route_method_options: data && data.route_method_options || [],
                    route_groups: data && data.route_groups || [],
                    excluded_urls: data && data.excluded_urls || [],
                    preserve_error_tabs: !!(data && data.preserve_error_tabs)
                });
            } catch (e) {
                return '';
            }
        },

        sanitizeExcludedUrls(value) {
            const source = Array.isArray(value)
                ? value
                : String(value || '').replace(/\r\n/g, '\n').replace(/;/g, '\n').split('\n');
            const seen = new Set();
            const result = [];
            source.forEach(item => {
                const text = String(item || '').trim();
                if (!text || seen.has(text)) return;
                seen.add(text);
                result.push(text);
            });
            return result;
        },

        formatExcludedUrls(value) {
            return this.sanitizeExcludedUrls(value).join('\n');
        },

        applyExcludedUrlsFromServer(value) {
            const next = this.sanitizeExcludedUrls(value);
            this.excludedUrls = next;
            if (!this.excludedUrlsDraftDirty) {
                this.excludedUrlsDraft = this.formatExcludedUrls(next);
            }
        },

        handleExcludedUrlsDraftInput(event) {
            this.excludedUrlsDraft = event && event.target ? event.target.value : '';
            this.excludedUrlsDraftDirty = true;
        },

        async fetchTabs(options = {}) {
            const force = !!(options && options.force);
            const silent = !!(options && options.silent);
            const shouldShowLoading = !silent;
            if (this.fetchInFlight) {
                if (!force) return;
                if (this.fetchAbortController) {
                    this.fetchAbortController.abort();
                }
            }

            const requestSeq = this.fetchRequestSeq + 1;
            this.fetchRequestSeq = requestSeq;
            const controller = window.AbortController ? new AbortController() : null;
            this.fetchAbortController = controller;
            this.fetchInFlight = true;
            if (shouldShowLoading) {
                this.loading = true;
            }
            let timeoutId = null;
            try {
                const token = window.getDashboardAuthToken ? window.getDashboardAuthToken() : '';
                const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
                const fetchOptions = { headers };
                if (controller) {
                    fetchOptions.signal = controller.signal;
                    timeoutId = setTimeout(() => controller.abort(), 8000);
                }

                const response = await fetch('/api/tab-pool/tabs', fetchOptions);
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                
                const data = await response.json();
                if (requestSeq !== this.fetchRequestSeq) return;
                // 修复：后端异常时只返回 tabs/count/error 和静态选项表，不含持久化配置字段。
                // 此时若继续按 `data.x || 兜底值` 赋值，会把兜底值当成真实配置展示并在下次保存时写回磁盘
                // （尤其 route_groups 会被空数组抹掉），因此这里只更新列表和静态选项后直接返回。
                if (data && data.error) {
                    this.error = data.error;
                    this.tabs = Array.isArray(data.tabs) ? data.tabs : [];
                    this.allocationModeOptions = data.allocation_mode_options || this.allocationModeOptions;
                    this.routeMethodOptions = data.route_method_options || this.routeMethodOptions;
                    this.lastUpdate = new Date().toLocaleTimeString();
                    return;
                }
                const signature = this.makeTabsResponseSignature(data);
                const shouldApplyResponse = force || !signature || signature !== this.tabsResponseSignature;
                if (shouldApplyResponse) {
                    this.tabs = data.tabs || [];
                    this.allocationMode = data.allocation_mode || 'first_idle';
                    this.allocationModeOptions = data.allocation_mode_options || this.allocationModeOptions;
                    this.enabledRouteMethods = data.enabled_route_methods || this.enabledRouteMethods;
                    this.routeMethodOptions = data.route_method_options || this.routeMethodOptions;
                    this.routeGroups = Array.isArray(data.route_groups) ? data.route_groups : [];
                    this.applyExcludedUrlsFromServer(data.excluded_urls || []);
                    this.preserveErrorTabs = !!data.preserve_error_tabs;
                    this.tabsResponseSignature = signature;
                    this.lastUpdate = new Date().toLocaleTimeString();
                }
                // 正常路径：配置字段确实来自后端，允许写回
                this.configLoaded = true;
                if (this.error) {
                    this.error = null;
                }
            } catch (e) {
                if (requestSeq !== this.fetchRequestSeq) return;
                this.error = e && e.name === 'AbortError' ? '请求超时，请稍后重试' : e.message;
            } finally {
                if (timeoutId) clearTimeout(timeoutId);
                if (requestSeq === this.fetchRequestSeq) {
                    if (shouldShowLoading) {
                        this.loading = false;
                    }
                    this.fetchInFlight = false;
                    this.fetchAbortController = null;
                }
            }
        },

        async updateAllocationMode(newMode) {
            const nextMode = String(newMode || '').trim();
            if (!nextMode || nextMode === this.allocationMode) return;
            if (!this.ensureConfigLoaded()) return;

            this.allocationModeUpdating = true;
            try {
                const token = window.getDashboardAuthToken ? window.getDashboardAuthToken() : '';
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = `Bearer ${token}`;

                const response = await fetch('/api/tab-pool/config', {
                    method: 'PUT',
                    headers,
                    body: JSON.stringify({
                        allocation_mode: nextMode,
                        enabled_route_methods: this.enabledRouteMethods,
                        preserve_error_tabs: this.preserveErrorTabs
                    })
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);

                const data = await response.json();
                this.allocationMode = data.allocation_mode || nextMode;
                this.allocationModeOptions = data.allocation_mode_options || this.allocationModeOptions;
                this.applyExcludedUrlsFromServer(data.excluded_urls || this.excludedUrls);
                this.preserveErrorTabs = !!data.preserve_error_tabs;
                this.$emit('notify', { type: 'success', message: '标签页池分配模式已切换' });
                await this.fetchTabs({ force: true });
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '切换分配模式失败: ' + e.message });
            } finally {
                this.allocationModeUpdating = false;
            }
        },

        isRouteMethodEnabled(method) {
            return this.routeMethodSet.has(method);
        },

        // 修复：配置未加载成功时禁止一切写回，避免把前端兜底值覆盖到磁盘上的真实配置
        ensureConfigLoaded() {
            if (this.configLoaded) return true;
            this.$emit('notify', { type: 'error', message: '标签页池配置尚未加载成功，暂时无法保存' });
            return false;
        },

        async saveRouteMethodSettings() {
            if (!this.ensureConfigLoaded()) return;
            this.routeMethodUpdating = true;
            try {
                const token = window.getDashboardAuthToken ? window.getDashboardAuthToken() : '';
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = `Bearer ${token}`;

                const response = await fetch('/api/tab-pool/config', {
                    method: 'PUT',
                    headers,
                    body: JSON.stringify({
                        allocation_mode: this.allocationMode,
                        enabled_route_methods: this.enabledRouteMethods,
                        preserve_error_tabs: this.preserveErrorTabs
                    })
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);

                const data = await response.json();
                this.enabledRouteMethods = data.enabled_route_methods || this.enabledRouteMethods;
                this.routeMethodOptions = data.route_method_options || this.routeMethodOptions;
                this.applyExcludedUrlsFromServer(data.excluded_urls || this.excludedUrls);
                this.preserveErrorTabs = !!data.preserve_error_tabs;
                this.$emit('notify', { type: 'success', message: '路由显示设置已保存' });
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '保存路由显示设置失败: ' + e.message });
            } finally {
                this.routeMethodUpdating = false;
            }
        },

        async toggleRouteMethod(method) {
            const value = String(method || '').trim();
            if (!value) return;

            const next = new Set(this.enabledRouteMethods || []);
            if (next.has(value)) {
                next.delete(value);
            } else {
                next.add(value);
            }

            if (next.size === 0) {
                this.$emit('notify', { type: 'error', message: '至少保留一种路由方式' });
                return;
            }

            this.enabledRouteMethods = this.routeMethodOptions
                .map(item => item.value)
                .filter(item => next.has(item));
            await this.saveRouteMethodSettings();
        },

        async saveExcludedUrls(nextValue = null) {
            if (!this.ensureConfigLoaded()) return;
            const nextUrls = this.sanitizeExcludedUrls(
                nextValue === null ? this.excludedUrlsDraft : nextValue
            );
            this.excludedUrlsUpdating = true;
            try {
                const token = window.getDashboardAuthToken ? window.getDashboardAuthToken() : '';
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = `Bearer ${token}`;

                const response = await fetch('/api/tab-pool/config', {
                    method: 'PUT',
                    headers,
                    body: JSON.stringify({
                        allocation_mode: this.allocationMode,
                        enabled_route_methods: this.enabledRouteMethods,
                        excluded_urls: nextUrls,
                        preserve_error_tabs: this.preserveErrorTabs
                    })
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);

                const data = await response.json();
                this.allocationMode = data.allocation_mode || this.allocationMode;
                this.enabledRouteMethods = data.enabled_route_methods || this.enabledRouteMethods;
                this.preserveErrorTabs = !!data.preserve_error_tabs;
                this.excludedUrlsDraftDirty = false;
                this.applyExcludedUrlsFromServer(data.excluded_urls || nextUrls);
                this.$emit('notify', { type: 'success', message: 'URL 排除列表已保存' });
                await this.fetchTabs({ force: true });
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '保存 URL 排除列表失败: ' + e.message });
            } finally {
                this.excludedUrlsUpdating = false;
            }
        },

        async updatePreserveErrorTabs(enabled) {
            if (!this.ensureConfigLoaded()) return;
            const previous = !!this.preserveErrorTabs;
            const nextValue = !!enabled;
            this.preserveErrorTabs = nextValue;
            this.preserveErrorTabsUpdating = true;
            try {
                const token = window.getDashboardAuthToken ? window.getDashboardAuthToken() : '';
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = `Bearer ${token}`;

                const response = await fetch('/api/tab-pool/config', {
                    method: 'PUT',
                    headers,
                    body: JSON.stringify({
                        allocation_mode: this.allocationMode,
                        enabled_route_methods: this.enabledRouteMethods,
                        excluded_urls: this.excludedUrls,
                        preserve_error_tabs: nextValue
                    })
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);

                const data = await response.json();
                this.allocationMode = data.allocation_mode || this.allocationMode;
                this.enabledRouteMethods = data.enabled_route_methods || this.enabledRouteMethods;
                this.applyExcludedUrlsFromServer(data.excluded_urls || this.excludedUrls);
                this.preserveErrorTabs = Object.prototype.hasOwnProperty.call(data, 'preserve_error_tabs')
                    ? !!data.preserve_error_tabs
                    : nextValue;
                this.$emit('notify', {
                    type: 'success',
                    message: this.preserveErrorTabs ? '错误/超时标签页将保留' : '错误/超时标签页将按原策略清理'
                });
            } catch (e) {
                this.preserveErrorTabs = previous;
                this.$emit('notify', { type: 'error', message: '保存错误处理设置失败: ' + e.message });
            } finally {
                this.preserveErrorTabsUpdating = false;
            }
        },

        async toggleTabExcluded(tab) {
            const url = String(tab && tab.url || '').trim();
            if (!url) return;

            const current = this.sanitizeExcludedUrls(this.excludedUrls);
            const isExcluded = !!(tab && tab.route_excluded);
            const exclusionUrl = String(tab && tab.route_exclusion_url || url).trim();
            const next = isExcluded
                ? current.filter(item => item !== exclusionUrl)
                : this.sanitizeExcludedUrls([...current, url]);
            await this.saveExcludedUrls(next);
        },

        resetExcludedUrlsDraft() {
            this.excludedUrlsDraftDirty = false;
            this.excludedUrlsDraft = this.formatExcludedUrls(this.excludedUrls);
        },

        isDocumentHidden() {
            return typeof document !== 'undefined' && document.visibilityState === 'hidden';
        },

        startAutoRefresh() {
            this.ensureVisibilityChangeHandler();
            if (this.refreshInterval || !this.autoRefresh || this.isDocumentHidden()) return;
            this.refreshInterval = setInterval(() => {
                if (this.autoRefresh && !this.isDocumentHidden()) {
                    this.fetchTabs({ silent: true });
                }
            }, 1000);
        },

        stopAutoRefresh() {
            this.stopAutoRefreshTimer();
            if (typeof document !== 'undefined' && this.visibilityChangeHandler) {
                document.removeEventListener('visibilitychange', this.visibilityChangeHandler);
                this.visibilityChangeHandler = null;
            }
        },

        stopAutoRefreshTimer() {
            if (!this.refreshInterval) return;
            clearInterval(this.refreshInterval);
            this.refreshInterval = null;
        },

        ensureVisibilityChangeHandler() {
            if (
                this.visibilityChangeHandler
                || typeof document === 'undefined'
                || typeof document.addEventListener !== 'function'
            ) {
                return;
            }

            this.visibilityChangeHandler = () => {
                if (this.isDocumentHidden()) {
                    this.stopAutoRefreshTimer();
                    return;
                }
                if (this.autoRefresh) {
                    this.startAutoRefresh();
                    this.fetchTabs({ silent: true });
                }
            };
            document.addEventListener('visibilitychange', this.visibilityChangeHandler);
        },

        routeGroupMemberKey(value) {
            const token = String(value && value.url_token || '').trim().toLowerCase();
            const url = String(value && value.url || '').trim();
            const tabIndex = Number(value && (value.tab_index || value.persistent_index) || 0);
            return `${token || url}::#${tabIndex > 0 ? tabIndex : '*'}`;
        },

        routeGroupMemberMatchesTab(member, tab) {
            const memberToken = String(member && member.url_token || '').trim().toLowerCase();
            const tabToken = String(tab && tab.url_route_token || '').trim().toLowerCase();
            if (memberToken && tabToken && memberToken === tabToken) return true;
            const memberUrl = String(member && member.url || '').trim();
            const tabUrl = String(tab && tab.url || '').trim();
            return !!memberUrl && memberUrl === tabUrl;
        },

        getRouteGroupStats(group) {
            const memberTabs = (this.tabs || []).filter(tab => (group && group.members || []).some(
                member => this.routeGroupMemberMatchesTab(member, tab)
            ));
            const liveCount = Number(group && group.live_member_count);
            const idleCount = Number(group && group.idle_member_count);
            return {
                total: Number.isFinite(liveCount) ? liveCount : memberTabs.length,
                idle: Number.isFinite(idleCount)
                    ? idleCount
                    : memberTabs.filter(tab => tab.status === 'idle').length
            };
        },

        getAllocationModeLabel(value) {
            const option = (this.allocationModeOptions || []).find(item => item.value === value);
            return option ? option.label : (value || '未设置');
        },

        getTabRouteGroupIds(tab) {
            const direct = Array.isArray(tab && tab.route_groups) ? tab.route_groups : [];
            if (direct.length) return direct;
            return (this.routeGroups || [])
                .filter(group => (group.members || []).some(member => this.routeGroupMemberMatchesTab(member, tab)))
                .map(group => group.id);
        },

        selectAllTabs() {
            this.selectedRouteGroupId = 'all';
            this.mobileGroupNavOpen = false;
            this.closeRouteGroupModal(true);
        },

        selectRouteGroup(group, editIndex) {
            if (!group) return;
            this.selectedRouteGroupId = String(group.id || '');
            this.mobileGroupNavOpen = false;
            this.openRouteGroupModal(group, editIndex);
        },

        createRouteGroup() {
            this.selectedRouteGroupId = '__new__';
            this.mobileGroupNavOpen = false;
            this.editorCollapsed = false;
            this.persistTabPoolLayout();
            this.openRouteGroupModal();
        },

        getRouteGroupPrefix(group) {
            const groupId = String(group && group.id || '').trim().toLowerCase();
            return groupId ? `/group/${groupId}` : '';
        },

        getRouteGroupPresetOptions(group) {
            const names = new Set();
            const configured = String(group && group.preset_name || '').trim();
            if (configured) names.add(configured);
            const routeDomain = String(group && group.route_domain || '').trim().toLowerCase();
            (this.tabs || []).forEach(tab => {
                const isMember = (group && group.members || []).some(
                    member => this.routeGroupMemberMatchesTab(member, tab)
                );
                const tabDomains = [tab.preset_route_domain, tab.route_domain, tab.current_domain]
                    .map(value => String(value || '').trim().toLowerCase());
                if (!isMember && (!routeDomain || !tabDomains.includes(routeDomain))) return;
                (tab.available_presets || []).forEach(name => names.add(String(name || '').trim()));
            });
            return Array.from(names).filter(Boolean);
        },

        getRouteGroupOfflineMembers(group) {
            return (group && group.members || []).filter(member => !(this.tabs || []).some(
                tab => this.routeGroupMemberMatchesTab(member, tab)
            ));
        },

        getRouteGroupOnlineMemberCount(group) {
            return (this.tabs || []).filter(tab => (group && group.members || []).some(
                member => this.routeGroupMemberMatchesTab(member, tab)
            )).length;
        },

        openRouteGroupModal(group = null, editIndex = -1) {
            const source = group || {};
            this.routeGroupsExpanded = true;
            this.routeGroupModal = {
                visible: true,
                saving: false,
                editIndex,
                id: String(source.id || ''),
                name: String(source.name || ''),
                route_domain: String(source.route_domain || ''),
                preset_name: String(source.preset_name || ''),
                allocation_mode: String(source.allocation_mode || 'round_robin'),
                members: (source.members || []).map(member => ({ ...member }))
            };
        },

        closeRouteGroupModal(force = false) {
            if (this.routeGroupModal.saving && !force) return;
            this.routeGroupModal.visible = false;
        },

        isRouteGroupMemberSelected(tab) {
            return (this.routeGroupModal.members || []).some(
                member => this.routeGroupMemberMatchesTab(member, tab)
            );
        },

        toggleRouteGroupMember(tab) {
            const members = (this.routeGroupModal.members || []).filter(
                member => !this.routeGroupMemberMatchesTab(member, tab)
            );
            if (members.length === (this.routeGroupModal.members || []).length) {
                members.push({
                    url: String(tab.url || ''),
                    url_token: String(tab.url_route_token || ''),
                    tab_index: Number(tab.persistent_index || 0)
                });
                if (!this.routeGroupModal.route_domain) {
                    this.routeGroupModal.route_domain = String(tab.route_domain || tab.current_domain || '');
                }
            }
            this.routeGroupModal.members = members;
        },

        removeRouteGroupMember(member) {
            const memberKey = this.routeGroupMemberKey(member);
            this.routeGroupModal.members = (this.routeGroupModal.members || []).filter(
                item => this.routeGroupMemberKey(item) !== memberKey
            );
        },

        removeRouteGroupTab(tab) {
            this.routeGroupModal.members = (this.routeGroupModal.members || []).filter(
                member => !this.routeGroupMemberMatchesTab(member, tab)
            );
        },

        removeOfflineRouteGroupMembers() {
            const offlineKeys = new Set(
                this.getRouteGroupOfflineMembers(this.routeGroupModal).map(member => this.routeGroupMemberKey(member))
            );
            this.routeGroupModal.members = (this.routeGroupModal.members || []).filter(
                member => !offlineKeys.has(this.routeGroupMemberKey(member))
            );
        },

        async persistRouteGroups(nextGroups, successMessage) {
            const token = window.getDashboardAuthToken ? window.getDashboardAuthToken() : '';
            const headers = { 'Content-Type': 'application/json' };
            if (token) headers.Authorization = `Bearer ${token}`;
            const response = await fetch('/api/tab-pool/config', {
                method: 'PUT',
                headers,
                body: JSON.stringify({
                    allocation_mode: this.allocationMode,
                    enabled_route_methods: this.enabledRouteMethods,
                    preserve_error_tabs: this.preserveErrorTabs,
                    route_groups: nextGroups
                })
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            this.routeGroups = Array.isArray(data.route_groups) ? data.route_groups : nextGroups;
            this.$emit('notify', { type: 'success', message: successMessage });
            await this.fetchTabs({ force: true });
        },

        async saveRouteGroup() {
            if (!this.ensureConfigLoaded()) return;
            const modal = this.routeGroupModal;
            const groupId = String(modal.id || '').trim().toLowerCase();
            if (!/^[a-z0-9][a-z0-9._-]{0,63}$/.test(groupId)) {
                this.$emit('notify', { type: 'error', message: '组 ID 只能使用小写字母、数字、点、下划线和连字符' });
                return;
            }
            if (modal.preset_name && !modal.route_domain) {
                this.$emit('notify', { type: 'error', message: '固定预设前需要设置站点域名' });
                return;
            }
            const duplicateIndex = (this.routeGroups || []).findIndex(
                (item, index) => String(item.id || '').toLowerCase() === groupId && index !== modal.editIndex
            );
            if (duplicateIndex >= 0) {
                this.$emit('notify', { type: 'error', message: '路由组 ID 已存在' });
                return;
            }

            const nextGroup = {
                id: groupId,
                name: String(modal.name || groupId).trim() || groupId,
                route_domain: String(modal.route_domain || '').trim(),
                preset_name: String(modal.preset_name || '').trim(),
                allocation_mode: String(modal.allocation_mode || 'round_robin'),
                members: (modal.members || []).map(member => ({ ...member }))
            };
            const nextGroups = (this.routeGroups || []).map(group => ({ ...group }));
            if (modal.editIndex >= 0) nextGroups.splice(modal.editIndex, 1, nextGroup);
            else nextGroups.push(nextGroup);

            this.routeGroupModal.saving = true;
            try {
                await this.persistRouteGroups(nextGroups, '标签页路由组已保存');
                this.selectedRouteGroupId = groupId;
                const savedIndex = (this.routeGroups || []).findIndex(group => String(group.id || '') === groupId);
                if (savedIndex >= 0) {
                    this.openRouteGroupModal(this.routeGroups[savedIndex], savedIndex);
                } else {
                    this.closeRouteGroupModal(true);
                }
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '保存路由组失败: ' + e.message });
            } finally {
                this.routeGroupModal.saving = false;
            }
        },

        async deleteRouteGroup(group, index) {
            if (!this.ensureConfigLoaded()) return;
            if (!window.confirm(`删除路由组 ${group.name || group.id}？`)) return;
            try {
                const nextGroups = (this.routeGroups || []).filter((_, itemIndex) => itemIndex !== index);
                await this.persistRouteGroups(nextGroups, '标签页路由组已删除');
                this.selectedRouteGroupId = 'all';
                if (this.routeGroupModal.editIndex === index) {
                    this.closeRouteGroupModal(true);
                }
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '删除路由组失败: ' + e.message });
            }
        },

        copyEndpoint(routePrefix, successMessage = '已复制端点地址') {
            const endpoint = `${this.baseUrl}${routePrefix}/v1/chat/completions`;
            navigator.clipboard.writeText(endpoint).then(() => {
                this.$emit('notify', { type: 'success', message: successMessage });
            });
        },
        
        copyPresetEndpoint(routePrefix, presetName, successMessage = '已复制端点地址') {
            const endpoint = `${this.baseUrl}${this.buildPresetEndpointPath(routePrefix, presetName, { encoded: true })}`;
            navigator.clipboard.writeText(endpoint).then(() => {
                this.$emit('notify', { type: 'success', message: successMessage });
            });
        },

        buildPresetEndpointPath(routePrefix, presetName, options = {}) {
            const rawPreset = String(presetName || '');
            const displayPreset = options.encoded ? encodeURIComponent(rawPreset) : rawPreset;
            return `${routePrefix}/${displayPreset}/v1/chat/completions`;
        },

        getDomainRoutePrefix(tab) {
            return tab.domain_route_prefix || '';
        },

        getPresetDomainRoutePrefix(tab) {
            return tab.preset_domain_route_prefix || this.getDomainRoutePrefix(tab);
        },

        getFixedTabRoutePrefix(tab) {
            return tab.tab_route_prefix || `/tab/${tab.persistent_index}`;
        },

        getExactUrlRoutePrefix(tab) {
            return tab.exact_url_route_prefix || '';
        },

        getExposedModelName(tab) {
            return (tab && (tab.exposed_model_name || tab.default_model_name || tab.route_domain || tab.current_domain || tab.id)) || 'web-browser';
        },

        getModelNameSourceText(tab) {
            const source = String(tab && tab.model_name_override_source || '').trim();
            if (source === 'tab') return '临时';
            if (source === 'site') return '站点保存';
            if (source === 'url') return 'URL 保存';
            return '默认';
        },

        truncateModelName(value, maxLen = 26) {
            const text = String(value || '').trim();
            if (!text) return 'web-browser';
            return text.length > maxLen ? text.substring(0, maxLen) + '...' : text;
        },

        openModelNameModal(tab) {
            this.hideModelNameTooltip();
            this.modelNameModal.visible = true;
            this.modelNameModal.saving = false;
            this.modelNameModal.showSaveOptions = false;
            this.modelNameModal.tab = tab;
            this.modelNameModal.draft = this.getExposedModelName(tab);
        },

        closeModelNameModal(force = false) {
            if (this.modelNameModal.saving && !force) return;
            this.modelNameModal.visible = false;
            this.modelNameModal.showSaveOptions = false;
            this.modelNameModal.tab = null;
            this.modelNameModal.draft = '';
        },

        openModelNameSaveOptions() {
            const name = String(this.modelNameModal.draft || '').trim();
            if (!name) {
                this.$emit('notify', { type: 'error', message: '模型显示名称不能为空' });
                return;
            }
            this.modelNameModal.showSaveOptions = true;
        },

        async submitModelName(scope = 'tab', reset = false) {
            this.hideModelNameTooltip();
            const tab = this.modelNameModal.tab;
            const tabIndex = tab && tab.persistent_index;
            if (!tabIndex) return;

            const modelName = String(this.modelNameModal.draft || '').trim();
            if (!reset && !modelName) {
                this.$emit('notify', { type: 'error', message: '模型显示名称不能为空' });
                return;
            }

            this.modelNameModal.saving = true;
            try {
                const token = window.getDashboardAuthToken ? window.getDashboardAuthToken() : '';
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = 'Bearer ' + token;

                const response = await fetch('/api/tab-pool/tabs/' + tabIndex + '/model-name', {
                    method: 'PUT',
                    headers,
                    body: JSON.stringify({
                        model_name: reset ? null : modelName,
                        persist_scope: scope,
                        reset
                    })
                });
                if (!response.ok) throw new Error('HTTP ' + response.status);

                const scopeText = reset
                    ? '模型显示名称已恢复默认'
                    : (scope === 'site'
                        ? '模型显示名称已按站点保存'
                        : (scope === 'url' ? '模型显示名称已按网页 URL 保存' : '模型显示名称已临时应用'));
                this.$emit('notify', { type: 'success', message: scopeText });
                this.closeModelNameModal(true);
                await this.fetchTabs({ force: true });
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '保存模型显示名称失败: ' + e.message });
            } finally {
                this.modelNameModal.saving = false;
            }
        },
        
        truncateUrl(url, maxLen = 50) {
            if (!url) return '(空)';
            return url.length > maxLen ? url.substring(0, maxLen) + '...' : url;
        },

        getDomainLabel(tab) {
            return tab.current_domain || '未识别域名';
        },

        getDefaultPresetOptionValue() {
            return '__DEFAULT__';
        },

        getDefaultPresetLabel(tab) {
            const fallback = tab.default_preset || tab.effective_preset_name || '主预设';
            return `跟随站点默认（${fallback}）`;
        },

        getDisplayedPreset(tab) {
            return tab.effective_preset_name || tab.preset_name || tab.default_preset || '主预设';
        },

        getPresetStatusText(tab) {
            if (tab.is_using_default_preset) {
                return '当前生效: ' + this.getDisplayedPreset(tab) + '（跟随站点默认）';
            }
            return '当前生效: ' + this.getDisplayedPreset(tab) + '（手动指定）';
        },

        getCommandLoopText(tab) {
            const loop = tab && tab.command_loop ? tab.command_loop : null;
            if (!loop || !loop.active) return '';
            const parts = [];
            if (loop.label) parts.push(loop.label);
            if (loop.iteration) {
                parts.push(loop.total ? `#${loop.iteration}/${loop.total}` : `#${loop.iteration}`);
            }
            const elapsed = Number(loop.elapsed_sec || 0);
            parts.push(`本轮 ${elapsed.toFixed(1)}s`);
            return parts.join(' · ');
        },

        formatExecutionDuration(tab) {
            if (!tab || tab.status !== 'busy') return '未在执行';
            const totalSeconds = Math.max(0, Math.floor(Number(tab.busy_duration) || 0));
            const hours = Math.floor(totalSeconds / 3600);
            const minutes = Math.floor((totalSeconds % 3600) / 60);
            const seconds = totalSeconds % 60;
            return [hours, minutes, seconds]
                .map(value => String(value).padStart(2, '0'))
                .join(':');
        },

        openTerminateModal(tab) {
            this.terminateModal = {
                visible: true,
                submitting: false,
                tab
            };
        },

        closeTerminateModal() {
            if (this.terminateModal.submitting) return;
            this.terminateModal = {
                visible: false,
                submitting: false,
                tab: null
            };
        },

        async changePreset(tab, newPresetName) {
            const tabIndex = tab.persistent_index;
            this.presetUpdating = { ...this.presetUpdating, [tabIndex]: true };

            try {
                const token = window.getDashboardAuthToken ? window.getDashboardAuthToken() : '';
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = 'Bearer ' + token;

                const response = await fetch('/api/tab-pool/tabs/' + tabIndex + '/preset', {
                    method: 'PUT',
                    headers,
                    body: JSON.stringify({ preset_name: newPresetName })
                });

                if (!response.ok) throw new Error('HTTP ' + response.status);

                const presetLabel = newPresetName === this.getDefaultPresetOptionValue()
                    ? this.getDefaultPresetLabel(tab)
                    : newPresetName;
                this.$emit('notify', { type: 'success', message: '预设已切换: ' + presetLabel });
                await this.fetchTabs({ force: true });
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '切换预设失败: ' + e.message });
            } finally {
                const updated = { ...this.presetUpdating };
                delete updated[tabIndex];
                this.presetUpdating = updated;
            }
        },

        async terminateTask(tab, scope = 'task') {
            if (!tab) return;
            const tabIndex = tab.persistent_index;
            const normalizedScope = scope === 'loop' ? 'loop' : 'task';
            this.terminateModal = { ...this.terminateModal, submitting: true };

            try {
                const token = window.getDashboardAuthToken ? window.getDashboardAuthToken() : '';
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = 'Bearer ' + token;

                const response = await fetch('/api/tab-pool/tabs/' + tabIndex + '/terminate', {
                    method: 'POST',
                    headers,
                    body: JSON.stringify({
                        reason: normalizedScope === 'loop'
                            ? 'manual_cancel_command_loop_from_tab_pool'
                            : 'manual_terminate_from_tab_pool',
                        clear_page: normalizedScope !== 'loop',
                        scope: normalizedScope,
                        expected_session_id: String(tab.id || tab.session_id || ''),
                        expected_task_id: String(tab.current_task || tab.command_task || '')
                    })
                });

                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(data.detail || ('HTTP ' + response.status));
                }
                const msg = normalizedScope === 'loop'
                    ? `标签页 #${tabIndex} 已请求终止本次循环`
                    : (data.pending
                        ? `标签页 #${tabIndex} 正在终止，旧任务退出前不会重新分配`
                    : (data.cancelled
                        ? `标签页 #${tabIndex} 已终止并解除占用`
                        : `标签页 #${tabIndex} 已解除占用（无可取消请求）`));
                this.$emit('notify', { type: 'success', message: msg });
                this.terminateModal = {
                    visible: false,
                    submitting: false,
                    tab: null
                };
                await this.fetchTabs({ force: true });
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '终止任务失败: ' + e.message });
                this.terminateModal = { ...this.terminateModal, submitting: false };
            }
        },

        getCurrentPreset(tab) {
            if (tab.is_using_default_preset) return this.getDefaultPresetOptionValue();
            // 修复：预设被删除后 session 仍指向旧名，而 available_presets 已不含它，
            // 会导致 <select> 渲染为空白且用户无法脱困；此时回落到默认预设选项。
            const current = tab.preset_name || '主预设';
            const options = Array.isArray(tab.available_presets) ? tab.available_presets : [];
            return options.includes(current) ? current : this.getDefaultPresetOptionValue();
        }
    },
    mounted() {
        this.baseUrl = window.location.origin;
        this.loadTabPoolLayout();
        this.fetchTabs();
        this.startAutoRefresh();
        document.addEventListener('click', this.handleDocumentClick);
    },
    beforeUnmount() {
        this.stopAutoRefresh();
        this.stopGroupNavResize();
        this.fetchRequestSeq += 1;
        if (this.fetchAbortController) {
            this.fetchAbortController.abort();
            this.fetchAbortController = null;
        }
        document.body.classList.remove('tab-pool-is-resizing');
        document.removeEventListener('pointermove', this.handleGroupNavResize);
        document.removeEventListener('pointerup', this.stopGroupNavResize);
        document.removeEventListener('click', this.handleDocumentClick);
    },
    template: `
        <div class="tab-pool-console">
            <div v-if="mobileGroupNavOpen" class="tab-pool-mobile-backdrop" @click="mobileGroupNavOpen = false"></div>

            <aside
                :class="['tab-pool-group-nav', { 'is-mobile-open': mobileGroupNavOpen, 'is-collapsed': groupNavCollapsed, 'is-compact': groupNavCompact, 'is-resizing': groupNavResizing }]"
                :style="groupNavStyle"
            >
                <div class="tab-pool-group-brand">
                    <span class="tab-pool-brand-icon" aria-hidden="true">▱</span>
                    <span class="tab-pool-brand-copy">
                        <strong>标签页池</strong>
                        <small>Tab Pool Console</small>
                    </span>
                    <button type="button" class="tab-pool-nav-collapse" @click="toggleGroupNavCollapsed" :title="groupNavCollapsed ? '展开路由组栏' : '折叠路由组栏'">
                        <span v-html="$icons.chevronDown"></span>
                    </button>
                    <button type="button" class="tab-pool-mobile-close" @click="mobileGroupNavOpen = false" title="关闭路由组导航" v-html="$icons.xMark"></button>
                </div>

                <nav class="tab-pool-group-list" aria-label="标签页路由组">
                    <button
                        type="button"
                        :class="['tab-pool-group-item', { active: selectedRouteGroupId === 'all' }]"
                        @click="selectAllTabs"
                    >
                        <span class="tab-pool-group-symbol" aria-hidden="true">⌗</span>
                        <span class="tab-pool-group-copy">
                            <strong>全部标签页</strong>
                            <small>{{ idleTabCount }}/{{ tabs.length }} 空闲</small>
                        </span>
                        <span class="tab-pool-count-badge">{{ idleTabCount }}/{{ tabs.length }}</span>
                    </button>

                    <div class="tab-pool-group-heading">
                        <span>路由组 · {{ routeGroups.length }}</span>
                        <!-- 修复：配置未加载成功时禁用写操作入口 -->
                        <button type="button" @click="createRouteGroup" :disabled="!configLoaded" title="新建路由组" v-html="$icons.plusCircle"></button>
                    </div>

                    <button
                        v-for="(group, groupIndex) in routeGroups"
                        :key="group.id"
                        type="button"
                        :class="['tab-pool-group-item', { active: selectedRouteGroupId === group.id }]"
                        @click="selectRouteGroup(group, groupIndex)"
                    >
                        <span class="tab-pool-group-symbol" aria-hidden="true">◇</span>
                        <span class="tab-pool-group-copy">
                            <strong>{{ group.name || group.id }}</strong>
                            <code>{{ group.id }}</code>
                            <small>
                                <i :class="getRouteGroupStats(group).idle > 0 ? 'is-idle' : 'is-waiting'"></i>
                                {{ getRouteGroupStats(group).idle }}/{{ getRouteGroupStats(group).total }} 空闲 · {{ getAllocationModeLabel(group.allocation_mode) }}
                            </small>
                        </span>
                    </button>

                    <div v-if="routeGroups.length === 0" class="tab-pool-group-empty">
                        尚未创建路由组
                    </div>
                </nav>

                <div class="tab-pool-global-controls">
                    <label>
                        <span>全局分配模式</span>
                        <select
                            :value="allocationMode"
                            @change="updateAllocationMode($event.target.value)"
                            :disabled="allocationModeUpdating || !configLoaded"
                        >
                            <option v-for="mode in allocationModeOptions" :key="mode.value" :value="mode.value">{{ mode.label }}</option>
                        </select>
                    </label>
                    <label class="tab-pool-switch-row">
                        <span>自动刷新</span>
                        <input type="checkbox" v-model="autoRefresh">
                        <i aria-hidden="true"></i>
                    </label>
                </div>
                <div
                    class="tab-pool-group-resizer"
                    role="separator"
                    aria-orientation="vertical"
                    aria-label="调整路由组栏宽度"
                    :aria-valuenow="Math.round(groupNavWidth)"
                    aria-valuemin="220"
                    aria-valuemax="340"
                    tabindex="0"
                    @pointerdown="startGroupNavResize"
                    @keydown="handleGroupNavResizeKeydown"
                    @dblclick="resetGroupNavWidth"
                ></div>
            </aside>

            <main class="tab-pool-main">
                <header class="tab-pool-topbar">
                    <button type="button" class="tab-pool-mobile-menu" @click="mobileGroupNavOpen = true" aria-label="打开路由组导航" title="打开路由组导航">☰</button>
                    <div class="tab-pool-view-title">
                        <small class="tab-pool-breadcrumb">Console / Pool / Live</small>
                        <span class="tab-pool-title-line">
                            <h2>{{ selectedViewTitle }}</h2>
                            <span v-if="selectedRouteGroup" class="tab-pool-pill tab-pool-pill-violet">/group/{{ selectedRouteGroup.id }}</span>
                            <span v-else class="tab-pool-pill tab-pool-pill-green">{{ idleTabCount }}/{{ tabs.length }} 空闲</span>
                        </span>
                    </div>
                    <div class="tab-pool-top-actions">
                        <label class="tab-pool-search">
                            <input v-model="tabSearchQuery" type="search" aria-label="搜索标签页、模型或会话" placeholder="搜索标签页、模型或会话" autocomplete="off">
                            <span v-if="tabSearchQuery">{{ displayedTabs.length }}</span>
                        </label>
                        <button type="button" @click="helpExpanded = !helpExpanded" :class="{ active: helpExpanded }" title="使用说明">使用说明</button>
                        <div class="tab-pool-settings-wrap">
                            <button
                                type="button"
                                data-route-settings-trigger
                                @click="showRouteSettings = !showRouteSettings"
                                :class="{ active: showRouteSettings }"
                                title="标签页池设置"
                                v-html="$icons.cog"
                            ></button>
                            <div v-if="showRouteSettings" data-route-settings-panel class="tab-pool-settings-popover">
                                <div class="tab-pool-popover-title">
                                    <span><strong>标签页池设置</strong><small>控制路由显示与异常处理策略</small></span>
                                    <button type="button" @click="showRouteSettings = false" title="关闭" v-html="$icons.xMark"></button>
                                </div>
                                <div class="tab-pool-setting-list">
                                    <label v-for="method in routeMethodOptions" :key="method.value">
                                        <span>{{ method.label }}</span>
                                        <input type="checkbox" :checked="isRouteMethodEnabled(method.value)" :disabled="routeMethodUpdating || !configLoaded" @change="toggleRouteMethod(method.value)">
                                    </label>
                                </div>
                                <div class="tab-pool-setting-block">
                                    <div><strong>域名路由排除 URL</strong><small>{{ excludedUrls.length }} 条</small></div>
                                    <textarea :value="excludedUrlsDraft" @input="handleExcludedUrlsDraftInput" :disabled="excludedUrlsUpdating" rows="5" placeholder="https://chatgpt.com/c/..."></textarea>
                                    <div class="tab-pool-setting-actions">
                                        <button type="button" @click="resetExcludedUrlsDraft" :disabled="excludedUrlsUpdating || !excludedUrlsDraftDirty">重置</button>
                                        <button type="button" class="primary" @click="saveExcludedUrls()" :disabled="excludedUrlsUpdating || !configLoaded">{{ excludedUrlsUpdating ? '保存中...' : '保存排除列表' }}</button>
                                    </div>
                                </div>
                                <label class="tab-pool-preserve-setting">
                                    <span><strong>错误/超时保留标签页</strong><small>异常时只记录错误，不自动关闭浏览器标签页。</small></span>
                                    <input type="checkbox" :checked="preserveErrorTabs" :disabled="preserveErrorTabsUpdating || !configLoaded" @change="updatePreserveErrorTabs($event.target.checked)">
                                </label>
                            </div>
                        </div>
                        <button type="button" class="tab-pool-refresh" @click="fetchTabs" :disabled="loading" title="立即刷新">
                            <span :class="{ spinning: loading }" v-html="$icons.arrowPath"></span>
                            <span>{{ loading ? '刷新中...' : '立即刷新' }}</span>
                        </button>
                    </div>
                </header>

                <div class="tab-pool-scroll">
                    <div class="tab-pool-content">
                        <div v-if="error" class="tab-pool-error">{{ error }}</div>

                        <section v-show="helpExpanded" class="tab-pool-help">
                            <button type="button" @click="helpExpanded = !helpExpanded" :aria-expanded="helpExpanded">
                                <span><strong>使用方式</strong><small>查看全部可用路由格式</small></span>
                                <span :class="['help-chevron', { open: helpExpanded }]" v-html="$icons.chevronDown"></span>
                            </button>
                            <ul v-show="helpExpanded">
                                <li><strong>默认路由</strong><code>/v1/chat/completions</code><span>从全局标签页池自动选择。</span></li>
                                <li v-if="isRouteMethodEnabled('domain')"><strong>域名路由</strong><code>/url/{domain}/v1/chat/completions</code><span>匹配指定站点域名。</span></li>
                                <li v-if="isRouteMethodEnabled('route_group')"><strong>标签页路由组</strong><code>/group/{组ID}/v1/chat/completions</code><span>只在组内原子选择空闲标签页。</span></li>
                                <li v-if="isRouteMethodEnabled('fixed_tab')"><strong>固定标签页</strong><code>/tab/{编号}/v1/chat/completions</code><span>精确使用指定标签页。</span></li>
                                <li v-if="isRouteMethodEnabled('exact_url')"><strong>精确 URL</strong><code>/tab-url/{token}/v1/chat/completions</code><span>只匹配当前已打开的 URL。</span></li>
                                <!-- 修复：补齐缺失的第 5 种路由方式说明 -->
                                <li v-if="isRouteMethodEnabled('exact_url_preset')"><strong>URL 绑定预设</strong><code>/tab-url/{token}/{预设名}/v1/chat/completions</code><span>在精确 URL 路由基础上强制指定预设。</span></li>
                            </ul>
                        </section>

                        <section v-if="routeGroupModal.visible" :class="['tab-pool-group-editor', { 'is-collapsed': editorCollapsed }]">
                            <div class="group-editor-head">
                                <button type="button" class="group-editor-disclosure" @click="toggleGroupEditor" :aria-expanded="!editorCollapsed">
                                    <span>
                                        <strong>{{ routeGroupModal.editIndex >= 0 ? (routeGroupModal.name || routeGroupModal.id) : '新建路由组' }}</strong>
                                        <code v-if="routeGroupModal.id">{{ routeGroupModal.id }}</code>
                                        <small>固定预设会覆盖请求体中的 preset_name；保存后立即热更新调度器。</small>
                                    </span>
                                    <span :class="['group-editor-chevron', { open: !editorCollapsed }]" v-html="$icons.chevronDown"></span>
                                </button>
                                <button v-if="routeGroupModal.editIndex >= 0" type="button" class="danger" @click="deleteRouteGroup(routeGroups[routeGroupModal.editIndex], routeGroupModal.editIndex)" :disabled="!configLoaded" v-html="$icons.trash" title="删除路由组"></button>
                            </div>
                            <div v-show="!editorCollapsed" class="group-editor-grid">
                                <div class="group-editor-fields">
                                    <label><span>组 ID</span><input v-model.trim="routeGroupModal.id" :disabled="routeGroupModal.editIndex >= 0" placeholder="arena-image"></label>
                                    <label><span>显示名称</span><input v-model.trim="routeGroupModal.name" placeholder="Arena 生图"></label>
                                    <label><span>站点域名</span><input v-model.trim="routeGroupModal.route_domain" placeholder="arena.ai"></label>
                                    <label><span>固定预设</span><select v-model="routeGroupModal.preset_name"><option value="">不固定预设</option><option v-for="preset in getRouteGroupPresetOptions(routeGroupModal)" :key="preset" :value="preset">{{ preset }}</option></select></label>
                                    <label class="wide"><span>组内分配模式</span><select v-model="routeGroupModal.allocation_mode"><option v-for="mode in allocationModeOptions" :key="mode.value" :value="mode.value">{{ mode.label }}</option></select></label>
                                    <div v-if="routeGroupModal.id" class="group-editor-endpoint wide">
                                        <code>/group/{{ routeGroupModal.id }}/v1/chat/completions</code>
                                        <button type="button" @click="copyEndpoint('/group/' + routeGroupModal.id, '已复制路由组端点')" title="复制端点" v-html="$icons.copy"></button>
                                    </div>
                                </div>
                                <div class="group-editor-members">
                                    <div class="member-list-heading">
                                        <strong>标签页成员</strong>
                                        <span>
                                            <small>{{ routeGroupModal.members.length }} 个成员 · {{ getRouteGroupOnlineMemberCount(routeGroupModal) }} 个在线</small>
                                            <button v-if="getRouteGroupOfflineMembers(routeGroupModal).length" type="button" @click="removeOfflineRouteGroupMembers">清理未在线</button>
                                        </span>
                                    </div>
                                    <div class="member-list">
                                        <div v-for="tab in tabs" :key="tab.persistent_index" :class="['member-list-item', { selected: isRouteGroupMemberSelected(tab) }]">
                                            <label>
                                                <input type="checkbox" :checked="isRouteGroupMemberSelected(tab)" @change="toggleRouteGroupMember(tab)">
                                                <i :class="statusColor(tab.status)"></i>
                                                <span><strong>#{{ tab.persistent_index }} · {{ getDomainLabel(tab) }}</strong><small>{{ tab.url }}</small></span>
                                                <em>{{ statusText(tab.status) }}</em>
                                            </label>
                                            <button v-if="isRouteGroupMemberSelected(tab)" type="button" class="member-remove" @click="removeRouteGroupTab(tab)" title="移出路由组" v-html="$icons.xMark"></button>
                                        </div>
                                        <div v-for="(member, memberIndex) in getRouteGroupOfflineMembers(routeGroupModal)" :key="'offline-' + routeGroupMemberKey(member) + '-' + memberIndex" class="member-list-item selected offline">
                                            <span class="member-offline-dot"></span>
                                            <span><strong>#{{ member.tab_index || '?' }} · 未在线</strong><small>{{ member.url || member.url_token }}</small></span>
                                            <em>未在线</em>
                                            <button type="button" class="member-remove" @click="removeRouteGroupMember(member)" title="移出路由组" v-html="$icons.xMark"></button>
                                        </div>
                                    </div>
                                    <div class="group-editor-actions">
                                        <button type="button" @click="selectAllTabs">取消</button>
                                        <button type="button" class="primary" @click="saveRouteGroup" :disabled="routeGroupModal.saving || !configLoaded">{{ routeGroupModal.saving ? '保存中...' : '保存路由组' }}</button>
                                    </div>
                                </div>
                            </div>
                        </section>

                        <div class="tab-pool-list-heading">
                            <h3>{{ selectedRouteGroup ? '组内标签页' : '全部标签页' }}</h3>
                            <span>{{ displayedTabs.length }} 个标签页 · {{ displayedIdleCount }} 个空闲</span>
                        </div>

                        <div v-if="loading && tabs.length === 0" class="tab-pool-empty">正在加载标签页...</div>
                        <div v-else-if="displayedTabs.length === 0" class="tab-pool-empty">
                            <strong>{{ tabSearchQuery ? '没有匹配的标签页' : (selectedRouteGroup ? '该路由组暂无成员' : '当前没有标签页') }}</strong>
                            <span>{{ tabSearchQuery ? '请尝试其他域名、模型名或会话 ID' : (selectedRouteGroup ? '在上方勾选标签页并保存即可加入' : '请先在浏览器中打开受支持的站点') }}</span>
                        </div>

                        <div v-else class="tab-pool-resource-board">
                            <div class="tab-pool-board-labels"><span>序号</span><span>连接与路由</span><span>运行配置</span></div>
                            <div class="tab-pool-card-list">
                            <article v-for="tab in displayedTabs" :key="tab.persistent_index" class="tab-pool-card">
                                <div :class="['tab-number', tab.status]">{{ tab.persistent_index }}</div>
                                <div class="tab-card-main">
                                    <div class="tab-card-status">
                                        <i :class="['status-dot', tab.status]"></i>
                                        <strong>{{ statusText(tab.status) }} · {{ getDomainLabel(tab) }}</strong>
                                        <span v-if="tab.route_excluded" class="tab-pool-pill tab-pool-pill-amber">域名路由已排除</span>
                                        <span v-for="groupId in getTabRouteGroupIds(tab)" :key="groupId" class="tab-pool-pill tab-pool-pill-blue">组: {{ groupId }}</span>
                                        <span v-if="tab.is_isolated_context" class="tab-pool-pill tab-pool-pill-green">独立 Cookie</span>
                                    </div>
                                    <p class="tab-card-url" :title="tab.url">{{ tab.url || '(空)' }}</p>
                                    <div class="tab-route-list">
                                        <!-- 修复：补上 route_group 的显示守卫（Vue3 不允许同元素 v-if + v-for，故外层包 template） -->
                                        <template v-if="isRouteMethodEnabled('route_group')">
                                            <div v-for="groupId in getTabRouteGroupIds(tab)" :key="'route-' + groupId" class="tab-route-row">
                                                <span>路由组</span><code>/group/{{ groupId }}/v1/chat/completions</code><button type="button" @click="copyEndpoint('/group/' + groupId, '已复制路由组端点')" title="复制路由组端点" v-html="$icons.copy"></button>
                                            </div>
                                        </template>
                                        <div v-if="isRouteMethodEnabled('domain') && getDomainRoutePrefix(tab)" class="tab-route-row">
                                            <span>站点域名</span><code>{{ getDomainRoutePrefix(tab) }}/v1/chat/completions</code><button type="button" @click="copyEndpoint(getDomainRoutePrefix(tab), '已复制站点域名路由')" title="复制站点域名路由" v-html="$icons.copy"></button>
                                        </div>
                                        <div v-if="tab.available_presets && tab.available_presets.length > 0 && isRouteMethodEnabled('domain') && getPresetDomainRoutePrefix(tab)" class="tab-route-row">
                                            <span>预设域名</span><code>{{ buildPresetEndpointPath(getPresetDomainRoutePrefix(tab), getDisplayedPreset(tab)) }}</code><button type="button" @click="copyPresetEndpoint(getPresetDomainRoutePrefix(tab), getDisplayedPreset(tab), '已复制预设域名路由')" title="复制预设域名路由" v-html="$icons.copy"></button>
                                        </div>
                                        <div v-if="isRouteMethodEnabled('fixed_tab')" class="tab-route-row">
                                            <span>固定标签页</span><code>{{ getFixedTabRoutePrefix(tab) }}/v1/chat/completions</code><button type="button" @click="copyEndpoint(getFixedTabRoutePrefix(tab), '已复制固定标签页路由')" title="复制固定标签页路由" v-html="$icons.copy"></button>
                                        </div>
                                        <div v-if="isRouteMethodEnabled('exact_url') && getExactUrlRoutePrefix(tab)" class="tab-route-row">
                                            <span>精确 URL</span><code>{{ getExactUrlRoutePrefix(tab) }}/v1/chat/completions</code><button type="button" @click="copyEndpoint(getExactUrlRoutePrefix(tab), '已复制标签页 URL 路由')" title="复制精确 URL 路由" v-html="$icons.copy"></button>
                                        </div>
                                        <div v-if="tab.available_presets && tab.available_presets.length > 0 && isRouteMethodEnabled('exact_url_preset') && getExactUrlRoutePrefix(tab)" class="tab-route-row">
                                            <span>URL 绑定预设</span><code>{{ buildPresetEndpointPath(getExactUrlRoutePrefix(tab), getDisplayedPreset(tab)) }}</code><button type="button" @click="copyPresetEndpoint(getExactUrlRoutePrefix(tab), getDisplayedPreset(tab), '已复制 URL 绑定预设路由')" title="复制 URL 绑定预设路由" v-html="$icons.copy"></button>
                                        </div>
                                    </div>
                                </div>
                                <aside class="tab-card-side">
                                    <div><span>请求次数</span><strong>{{ tab.request_count || 0 }}</strong></div>
                                    <div class="tab-execution-time" :class="{ active: tab.status === 'busy' }"><span>执行时长</span><strong>{{ formatExecutionDuration(tab) }}</strong></div>
                                    <div><span>会话 ID</span><code>{{ tab.id || tab.session_id || '-' }}</code></div>
                                    <button type="button" class="model-name-control" @click="openModelNameModal(tab)" @mouseenter="showModelNameTooltip($event)" @mousemove="positionModelNameTooltip($event)" @mouseleave="hideModelNameTooltip" @focus="showModelNameTooltip($event)" @blur="hideModelNameTooltip">
                                        <span>模型名称</span><strong>{{ truncateModelName(getExposedModelName(tab)) }}</strong><small>{{ getModelNameSourceText(tab) }}</small>
                                    </button>
                                    <label v-if="tab.available_presets && tab.available_presets.length > 0" class="tab-preset-control">
                                        <span>标签页预设</span>
                                        <select :value="getCurrentPreset(tab)" @change="changePreset(tab, $event.target.value)" :disabled="presetUpdating[tab.persistent_index]">
                                            <option :value="getDefaultPresetOptionValue()">{{ getDefaultPresetLabel(tab) }}</option>
                                            <option v-for="preset in tab.available_presets" :key="preset" :value="preset">{{ preset }}</option>
                                        </select>
                                    </label>
                                    <div v-else class="tab-single-preset"><span>标签页预设</span><strong>{{ getDisplayedPreset(tab) }}</strong></div>
                                    <p v-if="getCommandLoopText(tab)" class="tab-task-line">{{ getCommandLoopText(tab) }}</p>
                                    <p v-if="tab.current_task" class="tab-task-line">任务: {{ tab.current_task }}</p>
                                    <div class="tab-card-actions">
                                        <button v-if="tab.url" type="button" @click="toggleTabExcluded(tab)" :disabled="excludedUrlsUpdating || !configLoaded">{{ tab.route_excluded ? '解除排除' : '排除域名路由' }}</button>
                                        <button v-if="tab.status === 'busy' || tab.current_task || tab.command_task || tab.current_command" type="button" class="danger" @click="openTerminateModal(tab)">终止并解锁</button>
                                    </div>
                                </aside>
                            </article>
                            </div>
                        </div>
                    </div>
                </div>
            </main>

            <div v-if="modelNameTooltip.visible" :style="modelNameTooltipStyle" class="tab-pool-tooltip">{{ modelNameTooltip.text }}</div>

            <div v-if="terminateModal.visible" class="tab-pool-modal-backdrop">
                <div class="tab-pool-modal">
                    <div class="tab-pool-modal-head"><span><strong>终止标签页任务</strong><small>标签页 #{{ terminateModal.tab && terminateModal.tab.persistent_index }}</small></span><button type="button" @click="closeTerminateModal()" :disabled="terminateModal.submitting" v-html="$icons.xMark" title="关闭"></button></div>
                    <div class="tab-pool-modal-body"><p>当前任务: {{ (terminateModal.tab && (terminateModal.tab.current_task || terminateModal.tab.command_task)) || '无 task_id' }}</p><small>终止本次循环只发送单轮取消信号；终止整个任务会取消请求并释放标签页。</small></div>
                    <div class="tab-pool-modal-actions"><button type="button" @click="closeTerminateModal()" :disabled="terminateModal.submitting">取消</button><button type="button" class="warning" @click="terminateTask(terminateModal.tab, 'loop')" :disabled="terminateModal.submitting || !(terminateModal.tab && terminateModal.tab.command_loop && terminateModal.tab.command_loop.active)">终止本次循环</button><button type="button" class="danger" @click="terminateTask(terminateModal.tab, 'task')" :disabled="terminateModal.submitting">终止整个任务</button></div>
                </div>
            </div>

            <div v-if="modelNameModal.visible" class="tab-pool-modal-backdrop">
                <div class="tab-pool-modal">
                    <div class="tab-pool-modal-head"><span><strong>修改模型显示名称</strong><small>标签页 #{{ modelNameModal.tab && modelNameModal.tab.persistent_index }}</small></span><button type="button" @click="closeModelNameModal()" :disabled="modelNameModal.saving" v-html="$icons.xMark" title="关闭"></button></div>
                    <div v-if="!modelNameModal.showSaveOptions" class="tab-pool-modal-body">
                        <label><span>当前暴露的模型名称</span><input v-model="modelNameModal.draft" type="text" maxlength="200" placeholder="例如 lmarena-creative" @keydown.enter.prevent="submitModelName('tab')"></label>
                        <small>临时应用只绑定当前标签页；关闭或销毁该标签页后，需要重新命名。</small>
                    </div>
                    <div v-else class="tab-pool-modal-body tab-pool-save-options">
                        <button type="button" @click="submitModelName('site')" :disabled="modelNameModal.saving"><strong>按站点保存</strong><small>之后同一站点的标签页默认使用这个模型名。</small></button>
                        <button type="button" @click="submitModelName('url')" :disabled="modelNameModal.saving"><strong>按网页 URL 保存</strong><small>仅当前完整网页地址持久使用这个模型名。</small></button>
                    </div>
                    <div class="tab-pool-modal-actions split">
                        <button type="button" class="danger-text" @click="submitModelName('tab', true)" :disabled="modelNameModal.saving">恢复默认</button>
                        <span><button v-if="modelNameModal.showSaveOptions" type="button" @click="modelNameModal.showSaveOptions = false" :disabled="modelNameModal.saving">返回</button><button v-else type="button" @click="openModelNameSaveOptions" :disabled="modelNameModal.saving">保存...</button><button v-if="!modelNameModal.showSaveOptions" type="button" class="primary" @click="submitModelName('tab')" :disabled="modelNameModal.saving">{{ modelNameModal.saving ? '应用中...' : '临时应用' }}</button></span>
                    </div>
                </div>
            </div>
        </div>
    `
};
