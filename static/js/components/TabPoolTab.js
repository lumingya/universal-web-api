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
            baseUrl: ''
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
        }
    },
    methods: {
        async fetchTabs() {
            this.loading = true;
            try {
                const token = localStorage.getItem('auth_token');
                const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
                
                const response = await fetch('/api/tab-pool/tabs', { headers });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                
                const data = await response.json();
                this.tabs = data.tabs || [];
                this.lastUpdate = new Date().toLocaleTimeString();
                this.error = null;
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },
        
        startAutoRefresh() {
            if (this.refreshInterval) return;
            this.refreshInterval = setInterval(() => {
                if (this.autoRefresh) {
                    this.fetchTabs();
                }
            }, 1000);
        },
        
        stopAutoRefresh() {
            if (this.refreshInterval) {
                clearInterval(this.refreshInterval);
                this.refreshInterval = null;
            }
        },
        
        copyEndpoint(tab) {
            const endpoint = `${this.baseUrl}/tab/${tab.persistent_index}/v1/chat/completions`;
            navigator.clipboard.writeText(endpoint).then(() => {
                this.$emit('notify', { type: 'success', message: '已复制端点地址' });
            });
        },
        
        truncateUrl(url, maxLen = 50) {
            if (!url) return '(空)';
            return url.length > maxLen ? url.substring(0, maxLen) + '...' : url;
        }
    },
    mounted() {
        this.baseUrl = window.location.origin;
        this.fetchTabs();
        this.startAutoRefresh();
    },
    beforeUnmount() {
        this.stopAutoRefresh();
    },
    template: `
        <div class="p-6">
            <!-- 标题栏 -->
            <div class="flex items-center justify-between mb-6">
                <div>
                    <h2 class="text-xl font-bold dark:text-white">🗂️ 标签页池</h2>
                    <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
                        管理浏览器中的标签页，每个标签页有独立的路由前缀
                    </p>
                </div>
                <div class="flex items-center gap-4">
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
                    <li>• <strong>指定标签页</strong>：<code class="bg-blue-100 dark:bg-blue-800 px-1 rounded">/tab/{编号}/v1/chat/completions</code> - 使用特定标签页</li>
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
                            </div>
                            
                            <!-- URL -->
                            <div class="text-sm text-gray-600 dark:text-gray-300 truncate mb-2" :title="tab.url">
                                🌐 {{ truncateUrl(tab.url, 60) }}
                            </div>
                            
                            <!-- 路由端点 -->
                            <div class="flex items-center gap-2">
                                <code class="text-xs bg-gray-100 dark:bg-gray-700 px-2 py-1 rounded text-gray-700 dark:text-gray-300">
                                    {{ tab.route_prefix }}/v1/chat/completions
                                </code>
                                <button @click="copyEndpoint(tab)"
                                        class="text-xs text-blue-500 hover:text-blue-700 dark:text-blue-400">
                                    📋 复制
                                </button>
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
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `
};