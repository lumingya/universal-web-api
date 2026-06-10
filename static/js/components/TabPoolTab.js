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
                { value: 'fixed_tab', label: '固定标签页路由' },
                { value: 'exact_url', label: '标签页 URL 路由' },
                { value: 'exact_url_preset', label: 'URL 绑定预设路由' }
            ],
            enabledRouteMethods: ['domain', 'fixed_tab', 'exact_url', 'exact_url_preset'],
            routeMethodUpdating: false,
            showRouteSettings: false,
            fetchInFlight: false,
            fetchRequestSeq: 0,
            fetchAbortController: null,
            visibilityChangeHandler: null,
            tabsResponseSignature: ''
        };
    },
    computed: {
        statusColor() {
            return (status) => {
                switch (status) {
                    case 'idle': return 'bg-green-500';
                    case 'busy': return 'bg-yellow-500';
                    case 'error': return 'bg-red-500';
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
                    default: return status;
                }
            };
        },
        routeMethodSet() {
            return new Set(this.enabledRouteMethods || []);
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

        makeTabsResponseSignature(data) {
            try {
                return JSON.stringify({
                    tabs: Array.isArray(data && data.tabs) ? data.tabs : [],
                    allocation_mode: data && data.allocation_mode || '',
                    allocation_mode_options: data && data.allocation_mode_options || [],
                    enabled_route_methods: data && data.enabled_route_methods || [],
                    route_method_options: data && data.route_method_options || []
                });
            } catch (e) {
                return '';
            }
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
                const token = localStorage.getItem('api_token');
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
                const signature = this.makeTabsResponseSignature(data);
                const shouldApplyResponse = force || !signature || signature !== this.tabsResponseSignature;
                if (shouldApplyResponse) {
                    this.tabs = data.tabs || [];
                    this.allocationMode = data.allocation_mode || 'first_idle';
                    this.allocationModeOptions = data.allocation_mode_options || this.allocationModeOptions;
                    this.enabledRouteMethods = data.enabled_route_methods || this.enabledRouteMethods;
                    this.routeMethodOptions = data.route_method_options || this.routeMethodOptions;
                    this.tabsResponseSignature = signature;
                    this.lastUpdate = new Date().toLocaleTimeString();
                }
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

            this.allocationModeUpdating = true;
            try {
                const token = localStorage.getItem('api_token');
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = `Bearer ${token}`;

                const response = await fetch('/api/tab-pool/config', {
                    method: 'PUT',
                    headers,
                    body: JSON.stringify({
                        allocation_mode: nextMode,
                        enabled_route_methods: this.enabledRouteMethods
                    })
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);

                const data = await response.json();
                this.allocationMode = data.allocation_mode || nextMode;
                this.allocationModeOptions = data.allocation_mode_options || this.allocationModeOptions;
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

        async saveRouteMethodSettings() {
            this.routeMethodUpdating = true;
            try {
                const token = localStorage.getItem('api_token');
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = `Bearer ${token}`;

                const response = await fetch('/api/tab-pool/config', {
                    method: 'PUT',
                    headers,
                    body: JSON.stringify({
                        allocation_mode: this.allocationMode,
                        enabled_route_methods: this.enabledRouteMethods
                    })
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);

                const data = await response.json();
                this.enabledRouteMethods = data.enabled_route_methods || this.enabledRouteMethods;
                this.routeMethodOptions = data.route_method_options || this.routeMethodOptions;
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

        async changePreset(tab, newPresetName) {
            const tabIndex = tab.persistent_index;
            this.presetUpdating = { ...this.presetUpdating, [tabIndex]: true };

            try {
                const token = localStorage.getItem('api_token');
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

        async terminateTask(tab) {
            const tabIndex = tab.persistent_index;
            const task = tab.current_task || '(无 task_id)';
            if (!confirm(`确定终止标签页 #${tabIndex} 的当前任务吗？\n当前任务: ${task}`)) return;

            try {
                const token = localStorage.getItem('api_token');
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = 'Bearer ' + token;

                const response = await fetch('/api/tab-pool/tabs/' + tabIndex + '/terminate', {
                    method: 'POST',
                    headers,
                    body: JSON.stringify({
                        reason: 'manual_terminate_from_tab_pool',
                        clear_page: true
                    })
                });

                if (!response.ok) throw new Error('HTTP ' + response.status);
                const data = await response.json();
                const msg = data.cancelled
                    ? `标签页 #${tabIndex} 已终止并解除占用`
                    : `标签页 #${tabIndex} 已解除占用（无可取消请求）`;
                this.$emit('notify', { type: 'success', message: msg });
                await this.fetchTabs({ force: true });
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '终止任务失败: ' + e.message });
            }
        },

        getCurrentPreset(tab) {
            return tab.is_using_default_preset
                ? this.getDefaultPresetOptionValue()
                : (tab.preset_name || '主预设');
        }
    },
    mounted() {
        this.baseUrl = window.location.origin;
        this.fetchTabs();
        this.startAutoRefresh();
        document.addEventListener('click', this.handleDocumentClick);
    },
    beforeUnmount() {
        this.stopAutoRefresh();
        this.fetchRequestSeq += 1;
        if (this.fetchAbortController) {
            this.fetchAbortController.abort();
            this.fetchAbortController = null;
        }
        document.removeEventListener('click', this.handleDocumentClick);
    },
    template: `
        <div class="p-6">
            <!-- 标题栏 -->
            <div class="flex items-center justify-between mb-6">
                <div>
                    <div class="flex items-center gap-3 flex-wrap">
                        <h2 class="text-xl font-bold dark:text-white">🗂️ 标签页池</h2>
                        <div class="flex items-center gap-2">
                            <span class="text-xs text-gray-500 dark:text-gray-400">分配模式</span>
                            <select
                                :value="allocationMode"
                                @change="updateAllocationMode($event.target.value)"
                                :disabled="allocationModeUpdating"
                                class="text-xs border dark:border-gray-600 px-2 py-1 rounded bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:ring-2 focus:ring-blue-400 focus:border-transparent disabled:opacity-50"
                            >
                                <option v-for="mode in allocationModeOptions" :key="mode.value" :value="mode.value">
                                    {{ mode.label }}
                                </option>
                            </select>
                        </div>
                    </div>
                    <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
                        管理浏览器中的标签页，每个标签页有独立的路由前缀
                    </p>
                </div>
                <div class="flex items-center gap-4">
                    <div class="relative">
                        <button
                            @click="showRouteSettings = !showRouteSettings"
                            data-route-settings-trigger
                            class="w-9 h-9 rounded-full border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:text-blue-600 dark:hover:text-blue-400 hover:border-blue-300 dark:hover:border-blue-600 transition-colors"
                            title="路由显示设置"
                        >
                            ⚙
                        </button>
                        <div
                            v-if="showRouteSettings"
                            data-route-settings-panel
                            class="absolute right-0 top-11 z-20 w-72 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-xl p-4"
                        >
                            <div class="flex items-start justify-between gap-3 mb-3">
                                <div>
                                    <div class="text-sm font-semibold text-gray-900 dark:text-white">路由显示设置</div>
                                    <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">关闭后前端会隐藏对应路由；URL 路由只会匹配已打开标签页，相同 URL 会自动轮询。</p>
                                </div>
                                <button @click="showRouteSettings = false" class="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-200">关闭</button>
                            </div>
                            <div class="space-y-2">
                                <label
                                    v-for="method in routeMethodOptions"
                                    :key="method.value"
                                    class="flex items-center justify-between gap-3 text-sm text-gray-700 dark:text-gray-200"
                                >
                                    <span>{{ method.label }}</span>
                                    <input
                                        type="checkbox"
                                        :checked="isRouteMethodEnabled(method.value)"
                                        :disabled="routeMethodUpdating"
                                        @change="toggleRouteMethod(method.value)"
                                        class="rounded"
                                    >
                                </label>
                            </div>
                        </div>
                    </div>
                    <label class="flex items-center gap-2 text-sm dark:text-gray-300">
                        <input type="checkbox" v-model="autoRefresh" class="rounded">
                        自动刷新
                    </label>
                    <button @click="fetchTabs" 
                            :disabled="loading"
                            class="px-3 py-1 bg-blue-500 text-white rounded text-sm hover:bg-blue-600 disabled:opacity-50">
                        {{ loading ? '刷新中...' : '立即刷新' }}
                    </button>
                </div>
            </div>
            
            <!-- 状态信息 -->
            <div class="mb-4 flex items-center gap-4 text-sm">
                <span class="dark:text-gray-300">
                    共 <strong class="text-blue-600 dark:text-blue-400">{{ tabs.length }}</strong> 个标签页
                </span>
                <span v-if="lastUpdate" class="text-gray-500 dark:text-gray-400">
                    上次更新: {{ lastUpdate }}
                </span>
                <span v-if="error" class="text-red-500">
                    ⚠️ {{ error }}
                </span>
            </div>
            
            <!-- 使用说明 -->
            <div class="mb-6 p-4 bg-blue-50 dark:bg-blue-900/30 rounded-lg border border-blue-200 dark:border-blue-800">
                <h3 class="font-semibold text-blue-800 dark:text-blue-300 mb-2">💡 使用方式</h3>
                <ul class="text-sm text-blue-700 dark:text-blue-200 space-y-1">
                    <li>• <strong>默认路由</strong>：<code class="bg-blue-100 dark:bg-blue-800 px-1 rounded">/v1/chat/completions</code> - 自动选择空闲标签页</li>
                    <li v-if="isRouteMethodEnabled('domain')">• <strong>指定站点域名</strong>：<code class="bg-blue-100 dark:bg-blue-800 px-1 rounded">/url/gemini.com/v1/chat/completions</code> - 自动匹配该站点的标签页</li>
                    <li v-if="isRouteMethodEnabled('fixed_tab')">• <strong>指定标签页</strong>：<code class="bg-blue-100 dark:bg-blue-800 px-1 rounded">/tab/{编号}/v1/chat/completions</code> - 使用特定标签页</li>
                    <li v-if="isRouteMethodEnabled('exact_url')">• <strong>标签页 URL 路由</strong>：<code class="bg-blue-100 dark:bg-blue-800 px-1 rounded">/tab-url/{token}/v1/chat/completions</code> - 只匹配当前已打开的 URL，相同 URL 会轮询，不会回退到别的 URL</li>
                    <li v-if="isRouteMethodEnabled('exact_url_preset')">• <strong>URL 绑定预设</strong>：<code class="bg-blue-100 dark:bg-blue-800 px-1 rounded">/tab-url/{token}/{预设}/v1/chat/completions</code> - 先严格匹配已打开 URL，再严格使用对应站点预设</li>
                    <li>• 标签页编号在脚本运行期间保持不变，关闭标签页不会影响其他编号</li>
                </ul>
            </div>
            
            <!-- 标签页列表 -->
            <div v-if="tabs.length === 0 && !loading" 
                 class="text-center py-12 text-gray-500 dark:text-gray-400">
                <div class="text-4xl mb-4">📭</div>
                <p>暂无可用标签页</p>
                <p class="text-sm mt-2">请在浏览器中打开 AI 网站</p>
            </div>
            
            <div v-else class="space-y-3">
                <div v-for="tab in tabs" :key="tab.persistent_index"
                     class="p-4 rounded-lg border dark:border-gray-700 bg-white dark:bg-gray-800 hover:shadow-md transition-shadow">
                    <div class="flex items-start justify-between">
                        <!-- 左侧信息 -->
                        <div class="flex-1 min-w-0">
                            <div class="flex items-center gap-3 mb-2">
                                <!-- 编号徽章 -->
                                <span class="inline-flex items-center justify-center w-8 h-8 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 font-bold text-lg">
                                    {{ tab.persistent_index }}
                                </span>
                                
                                <!-- 状态指示器 -->
                                <span class="flex items-center gap-1.5">
                                    <span :class="['w-2.5 h-2.5 rounded-full', statusColor(tab.status)]"></span>
                                    <span class="text-sm font-medium dark:text-white">{{ statusText(tab.status) }}</span>
                                </span>
                                
                                <!-- 会话 ID -->
                                <span class="text-xs text-gray-500 dark:text-gray-400 font-mono">
                                    {{ tab.id }}
                                </span>
                                <span v-if="tab.is_isolated_context"
                                      class="inline-flex items-center px-2 py-0.5 rounded-full bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800 text-xs">
                                    独立 Cookie
                                </span>
                            </div>
                            
                            <div class="flex flex-wrap items-center gap-2 mb-1 text-sm">
                                <span class="text-gray-500 dark:text-gray-400">🏷️</span>
                                <span class="font-medium text-gray-800 dark:text-gray-100">{{ getDomainLabel(tab) }}</span>
                                <a v-if="tab.domain_url"
                                   :href="tab.domain_url"
                                   target="_blank"
                                   rel="noreferrer"
                                   class="text-xs text-blue-500 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300 font-mono">
                                    {{ tab.domain_url }}
                                </a>
                            </div>

                            <!-- URL -->
                            <div class="text-sm text-gray-600 dark:text-gray-300 truncate mb-2" :title="tab.url">
                                🌐 {{ truncateUrl(tab.url, 72) }}
                            </div>
                            
                            <!-- 路由端点 -->
                            <div class="space-y-2">
                                <div v-if="isRouteMethodEnabled('domain') && getDomainRoutePrefix(tab)" class="flex flex-wrap items-center gap-2">
                                    <span class="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">站点域名路由</span>
                                    <code class="text-xs bg-gray-100 dark:bg-gray-700 px-2 py-1 rounded text-gray-700 dark:text-gray-300">
                                        {{ getDomainRoutePrefix(tab) }}/v1/chat/completions
                                    </code>
                                    <button @click="copyEndpoint(getDomainRoutePrefix(tab), '已复制站点域名路由')"
                                            class="text-xs text-blue-500 hover:text-blue-700 dark:text-blue-400">
                                        📋 复制
                                    </button>
                                </div>
                                <div v-if="isRouteMethodEnabled('fixed_tab')" class="flex flex-wrap items-center gap-2">
                                    <span class="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">固定标签页路由</span>
                                    <code class="text-xs bg-gray-100 dark:bg-gray-700 px-2 py-1 rounded text-gray-700 dark:text-gray-300">
                                        {{ getFixedTabRoutePrefix(tab) }}/v1/chat/completions
                                    </code>
                                    <button @click="copyEndpoint(getFixedTabRoutePrefix(tab), '已复制固定标签页路由')"
                                            class="text-xs text-blue-500 hover:text-blue-700 dark:text-blue-400">
                                        📋 复制
                                    </button>
                                </div>
                                <div v-if="isRouteMethodEnabled('exact_url') && getExactUrlRoutePrefix(tab)" class="flex flex-wrap items-center gap-2">
                                    <span class="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">标签页 URL 路由</span>
                                    <code class="text-xs bg-gray-100 dark:bg-gray-700 px-2 py-1 rounded text-gray-700 dark:text-gray-300">
                                        {{ getExactUrlRoutePrefix(tab) }}/v1/chat/completions
                                    </code>
                                    <button @click="copyEndpoint(getExactUrlRoutePrefix(tab), '已复制标签页 URL 路由')"
                                            class="text-xs text-blue-500 hover:text-blue-700 dark:text-blue-400">
                                        📋 复制
                                    </button>
                                </div>
                                
                                <!-- 预设专属路由 -->
                                <template v-if="tab.available_presets && tab.available_presets.length > 0">
                                    <div v-if="isRouteMethodEnabled('domain') && getPresetDomainRoutePrefix(tab)" class="flex flex-wrap items-center gap-2">
                                        <span class="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">预设域名路由</span>
                                        <code class="text-xs bg-gray-100 dark:bg-gray-700 px-2 py-1 rounded text-gray-700 dark:text-gray-300">
                                            {{ buildPresetEndpointPath(getPresetDomainRoutePrefix(tab), getDisplayedPreset(tab)) }}
                                        </code>
                                        <button @click="copyPresetEndpoint(getPresetDomainRoutePrefix(tab), getDisplayedPreset(tab), '已复制预设域名路由')"
                                                class="text-xs text-blue-500 hover:text-blue-700 dark:text-blue-400">
                                            📋 复制
                                        </button>
                                    </div>
                                    <div v-if="isRouteMethodEnabled('exact_url_preset') && getExactUrlRoutePrefix(tab)" class="flex flex-wrap items-center gap-2">
                                        <span class="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">URL 绑定预设路由</span>
                                        <code class="text-xs bg-gray-100 dark:bg-gray-700 px-2 py-1 rounded text-gray-700 dark:text-gray-300">
                                            {{ buildPresetEndpointPath(getExactUrlRoutePrefix(tab), getDisplayedPreset(tab)) }}
                                        </code>
                                        <button @click="copyPresetEndpoint(getExactUrlRoutePrefix(tab), getDisplayedPreset(tab), '已复制 URL 绑定预设路由')"
                                                class="text-xs text-blue-500 hover:text-blue-700 dark:text-blue-400">
                                            📋 复制
                                        </button>
                                    </div>
                                </template>
                            </div>

                            <!-- 🆕 预设选择器 -->
                            <div v-if="tab.available_presets && tab.available_presets.length > 0"
                                 class="flex items-center gap-2 mt-2">
                                <span class="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">🎛️ 预设:</span>
                                <select :value="getCurrentPreset(tab)"
                                        @change="changePreset(tab, $event.target.value)"
                                        :disabled="presetUpdating[tab.persistent_index]"
                                        class="text-xs border dark:border-gray-600 px-2 py-1 rounded bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:ring-2 focus:ring-blue-400 focus:border-transparent disabled:opacity-50 min-w-[100px]">
                                    <option :value="getDefaultPresetOptionValue()">
                                        {{ getDefaultPresetLabel(tab) }}
                                    </option>
                                    <option v-for="preset in tab.available_presets" :key="preset" :value="preset">
                                        {{ preset }}
                                    </option>
                                </select>
                                <span v-if="presetUpdating[tab.persistent_index]" class="text-xs text-blue-500 dark:text-blue-400">
                                    切换中...
                                </span>
                            </div>
                            <div v-if="tab.available_presets && tab.available_presets.length > 0" class="mt-1">
                                <span class="text-xs text-gray-400 dark:text-gray-500">{{ getPresetStatusText(tab) }}</span>
                            </div>
                            <div v-else-if="tab.current_domain" class="mt-2">
                                <span class="text-xs text-gray-400 dark:text-gray-500">🎛️ 预设: {{ getDisplayedPreset(tab) }}（仅有一个）</span>
                            </div>
                        </div>

                        <!-- 右侧统计 -->
                        <div class="text-right text-xs text-gray-500 dark:text-gray-400 ml-4">
                            <div>请求数: {{ tab.request_count }}</div>
                            <div v-if="tab.busy_duration" class="text-yellow-600 dark:text-yellow-400">
                                已忙碌: {{ tab.busy_duration }}s
                            </div>
                            <div v-if="tab.current_task" class="text-blue-600 dark:text-blue-400 truncate max-w-32">
                                任务: {{ tab.current_task }}
                            </div>
                            <button v-if="tab.status === 'busy' || tab.current_task"
                                    @click="terminateTask(tab)"
                                    class="mt-2 px-2 py-1 rounded bg-red-600 text-white hover:bg-red-700 text-xs">
                                终止并解锁
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `
};
