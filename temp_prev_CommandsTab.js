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
            }
        };
    },
    computed: {
        triggerTypeOptions() {
            return Object.entries(this.meta.trigger_types || {}).map(([k, v]) => ({ value: k, label: v }));
        },
        actionTypeOptions() {
            return Object.entries(this.meta.action_types || {}).map(([k, v]) => ({ value: k, label: v }));
        },
        // 🆕 将复杂的 placeholder 移到 computed 中
        scriptPlaceholder() {
            if (!this.editingCommand) return '';
            if (this.editingCommand.script_lang === 'javascript') {
                return '// 在页面中执行的 JavaScript\n' +
                    '// 清除 cookies 并刷新页面\n' +
                    'document.cookie.split(";").forEach(c => {\n' +
                    '  document.cookie = c.trim().split("=")[0] + "=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/";\n' +
                    '});\n' +
                    'location.reload();';
            } else {
                return '# Python 脚本\n' +
                    '# 可用变量: tab, session, browser, config_engine, logger, time, json\n\n' +
                    'logger.info(f"当前 URL: {tab.url}")\n' +
                    'logger.info(f"请求次数: {session.request_count}")\n\n' +
                    '# 清除 cookies 并刷新\n' +
                    'tab.run_js("document.cookie.split(\\";\\").forEach(c => document.cookie = c.trim().split(\\"=\\")[0] + \\"=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/\\");")\n' +
                    'time.sleep(0.5)\n' +
                    'tab.refresh()';
            }
        }
    },
    methods: {
        async apiRequest(url, options) {
            const token = localStorage.getItem('api_token');
            const headers = { 'Content-Type': 'application/json', ...(options || {}).headers };
            if (token) headers['Authorization'] = 'Bearer ' + token;
            const response = await fetch(url, { ...options, headers });
            if (!response.ok) {
                const err = await response.json().catch(() => ({}));
                throw new Error(err.detail || 'HTTP ' + response.status);
            }
            return response.json();
        },

        async fetchCommands() {
            this.loading = true;
            try {
                const data = await this.apiRequest('/api/commands');
                this.commands = data.commands || [];
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '加载命令失败: ' + e.message });
            } finally {
                this.loading = false;
            }
        },

        async fetchMeta() {
            try {
                this.meta = await this.apiRequest('/api/commands/meta');
            } catch (e) {
                console.error('加载元信息失败:', e);
            }
        },

        openNewCommand() {
            this.editingCommand = {
                name: '新命令',
                enabled: true,
                mode: 'simple',
                trigger: { type: 'request_count', value: 10, scope: 'all', domain: '', tab_index: null },
                actions: [{ type: 'clear_cookies' }, { type: 'refresh_page' }],
                script: '',
                script_lang: 'javascript'
            };
            this.isNew = true;
            this.showEditor = true;
        },

        openEditCommand(cmd) {
            this.editingCommand = JSON.parse(JSON.stringify(cmd));
            this.isNew = false;
            this.showEditor = true;
        },

        addAction() {
            if (!this.editingCommand) return;
            this.editingCommand.actions.push({ type: 'wait', seconds: 1 });
        },

        // 当动作类型变为 switch_proxy 时，初始化默认值
        initProxyAction(action) {
            if (action.type === 'switch_proxy') {
                // 设置所有缺失的默认值
                action.clash_api = action.clash_api || this.proxyDefaults.clash_api;
                action.clash_secret = action.clash_secret || '';
                action.selector = action.selector || this.proxyDefaults.selector;
                action.mode = action.mode || 'random';
                action.node_name = action.node_name || '';
                action.exclude_keywords = action.exclude_keywords || this.proxyDefaults.exclude_keywords;
                if (action.refresh_after === undefined) {
                    action.refresh_after = true;
                }
            }
        },

        removeAction(index) {
            if (!this.editingCommand) return;
            this.editingCommand.actions.splice(index, 1);
        },

        moveAction(index, direction) {
            if (!this.editingCommand) return;
            const arr = this.editingCommand.actions;
            const newIndex = index + direction;
            if (newIndex < 0 || newIndex >= arr.length) return;
            const temp = arr[index];
            arr[index] = arr[newIndex];
            arr[newIndex] = temp;
        },

        async saveCommand() {
            if (!this.editingCommand) return;
            try {
                if (this.isNew) {
                    await this.apiRequest('/api/commands', {
                        method: 'POST',
                        body: JSON.stringify(this.editingCommand)
                    });
                    this.$emit('notify', { type: 'success', message: '命令已创建' });
                } else {
                    await this.apiRequest('/api/commands/' + this.editingCommand.id, {
                        method: 'PUT',
                        body: JSON.stringify(this.editingCommand)
                    });
                    this.$emit('notify', { type: 'success', message: '命令已更新' });
                }
                this.showEditor = false;
                await this.fetchCommands();
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '保存失败: ' + e.message });
            }
        },

        async deleteCommand(cmd) {
            if (!confirm('确定删除命令「' + cmd.name + '」吗？')) return;
            try {
                await this.apiRequest('/api/commands/' + cmd.id, { method: 'DELETE' });
                this.$emit('notify', { type: 'success', message: '命令已删除' });
                await this.fetchCommands();
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '删除失败: ' + e.message });
            }
        },

        async toggleCommand(cmd) {
            try {
                await this.apiRequest('/api/commands/' + cmd.id, {
                    method: 'PUT',
                    body: JSON.stringify({ enabled: !cmd.enabled })
                });
                await this.fetchCommands();
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '切换失败: ' + e.message });
            }
        },

        async testCommand(cmd) {
            try {
                const result = await this.apiRequest('/api/commands/' + cmd.id + '/test', { method: 'POST' });
                this.$emit('notify', { type: 'success', message: result.message || '命令已执行' });
            } catch (e) {
                this.$emit('notify', { type: 'error', message: '执行失败: ' + e.message });
            }
        },

        getTriggerLabel(type) {
            return (this.meta.trigger_types || {})[type] || type;
        },

        getActionLabel(type) {
            return (this.meta.action_types || {})[type] || type;
        },

        getScopeLabel(scope) {
            const map = { all: '所有标签页', domain: '指定域名', tab: '指定标签页' };
            return map[scope] || scope;
        },

        formatTime(ts) {
            if (!ts) return '从未';
            return new Date(ts * 1000).toLocaleString();
        }
    },

    mounted() {
        this.fetchMeta();
        this.fetchCommands();
    },

    template: `
    <div class="p-6">
        <!-- 标题栏 -->
        <div class="flex items-center justify-between mb-6">
            <div>
                <h2 class="text-xl font-bold dark:text-white">⚡ 自动化命令</h2>
                <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
                    设置触发条件和执行动作，实现标签页自动化管理
                </p>
            </div>
            <div class="flex items-center gap-3">
                <button @click="fetchCommands" :disabled="loading"
                        class="px-3 py-1.5 border dark:border-gray-600 rounded text-sm hover:bg-gray-100 dark:hover:bg-gray-700 dark:text-white">
                    {{ loading ? '刷新中...' : '刷新' }}
                </button>
                <button @click="openNewCommand"
                        class="px-3 py-1.5 bg-blue-500 text-white rounded text-sm hover:bg-blue-600">
                    + 新建命令
                </button>
            </div>
        </div>

        <!-- 使用说明 -->
        <div class="mb-6 p-4 bg-amber-50 dark:bg-amber-900/20 rounded-lg border border-amber-200 dark:border-amber-800">
            <h3 class="font-semibold text-amber-800 dark:text-amber-300 mb-2">💡 工作原理</h3>
            <ul class="text-sm text-amber-700 dark:text-amber-200 space-y-1">
                <li>• <strong>简单模式</strong>：选择触发条件 + 配置动作列表，零代码实现自动化</li>
                <li>• <strong>高级模式</strong>：直接编写 JavaScript 或 Python 脚本，完全自由控制</li>
                <li>• 命令在每次对话完成后自动检查触发条件，满足时在后台异步执行</li>
            </ul>
        </div>

        <!-- 空状态 -->
        <div v-if="commands.length === 0 && !loading" class="text-center py-12 text-gray-500 dark:text-gray-400">
            <div class="text-4xl mb-4">⚡</div>
            <p>还没有自动化命令</p>
            <p class="text-sm mt-2">点击「新建命令」开始配置</p>
        </div>

        <!-- 命令列表 -->
        <div class="space-y-3">
            <div v-for="cmd in commands" :key="cmd.id"
                 :class="['p-4 rounded-lg border transition-shadow hover:shadow-md',
                          cmd.enabled
                          ? 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700'
                          : 'bg-gray-50 dark:bg-gray-900 border-gray-200 dark:border-gray-700 opacity-60']">
                <div class="flex items-start justify-between">
                    <div class="flex-1 min-w-0">
                        <!-- 名称和状态 -->
                        <div class="flex items-center gap-3 mb-2">
                            <span class="font-semibold dark:text-white text-lg">{{ cmd.name }}</span>
                            <span :class="['px-2 py-0.5 rounded-full text-xs font-medium',
                                           cmd.mode === 'advanced'
                                           ? 'bg-purple-100 dark:bg-purple-900/40 text-purple-700 dark:text-purple-300'
                                           : 'bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300']">
                                {{ cmd.mode === 'advanced' ? '高级' : '简单' }}
                            </span>
                            <span v-if="!cmd.enabled" class="px-2 py-0.5 rounded-full text-xs bg-gray-200 dark:bg-gray-700 text-gray-500 dark:text-gray-400">
                                已禁用
                            </span>
                        </div>

                        <!-- 触发条件摘要 -->
                        <div class="text-sm text-gray-600 dark:text-gray-300 mb-1">
                            <span class="font-medium">触发：</span>
                            {{ getTriggerLabel(cmd.trigger?.type) }}
                            <span v-if="cmd.trigger?.value" class="text-blue-600 dark:text-blue-400 font-mono">= {{ cmd.trigger.value }}</span>
                            <span class="text-gray-400 mx-1">|</span>
                            <span>范围：{{ getScopeLabel(cmd.trigger?.scope) }}</span>
                            <span v-if="cmd.trigger?.scope === 'domain' && cmd.trigger?.domain" class="text-green-600 dark:text-green-400">
                                ({{ cmd.trigger.domain }})
                            </span>
                            <span v-if="cmd.trigger?.scope === 'tab' && cmd.trigger?.tab_index != null" class="text-green-600 dark:text-green-400">
                                (#{{ cmd.trigger.tab_index }})
                            </span>
                        </div>

                        <!-- 动作摘要 -->
                        <div v-if="cmd.mode === 'simple'" class="text-sm text-gray-500 dark:text-gray-400">
                            <span class="font-medium">动作：</span>
                            <span v-for="(a, i) in (cmd.actions || []).slice(0, 3)" :key="i">
                                {{ getActionLabel(a.type) }}<span v-if="i < Math.min((cmd.actions || []).length, 3) - 1">、</span>
                            </span>
                            <span v-if="(cmd.actions || []).length > 3"> 等 {{ cmd.actions.length }} 步</span>
                        </div>
                        <div v-else class="text-sm text-gray-500 dark:text-gray-400">
                            <span class="font-medium">脚本：</span>
                            {{ cmd.script_lang === 'python' ? 'Python' : 'JavaScript' }}
                            ({{ (cmd.script || '').split('\\n').length }} 行)
                        </div>

                        <!-- 统计 -->
                        <div class="text-xs text-gray-400 dark:text-gray-500 mt-2">
                            已触发 {{ cmd.trigger_count || 0 }} 次
                            <span v-if="cmd.last_triggered"> · 上次: {{ formatTime(cmd.last_triggered) }}</span>
                        </div>
                    </div>

                    <!-- 操作按钮 -->
                    <div class="flex items-center gap-2 ml-4">
                        <button @click="testCommand(cmd)" title="手动执行"
                                class="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500 dark:text-gray-400">
                            ▶️
                        </button>
                        <button @click="toggleCommand(cmd)" :title="cmd.enabled ? '禁用' : '启用'"
                                class="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500 dark:text-gray-400">
                            {{ cmd.enabled ? '⏸️' : '▶️' }}
                        </button>
                        <button @click="openEditCommand(cmd)" title="编辑"
                                class="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500 dark:text-gray-400">
                            ✏️
                        </button>
                        <button @click="deleteCommand(cmd)" title="删除"
                                class="p-1.5 rounded hover:bg-red-50 dark:hover:bg-red-900/30 text-red-400">
                            🗑️
                        </button>
                    </div>
                </div>
            </div>
        </div>

        <!-- ========== 编辑弹窗 ========== -->
        <div v-if="showEditor" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50" @click.self="showEditor = false">
            <div class="bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-full max-w-2xl max-h-[85vh] overflow-y-auto m-4">
                <div class="p-6">
                    <!-- 弹窗标题 -->
                    <div class="flex justify-between items-center mb-6">
                        <h3 class="text-lg font-bold dark:text-white">
                            {{ isNew ? '新建命令' : '编辑命令' }}
                        </h3>
                        <button @click="showEditor = false" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-xl">✕</button>
                    </div>

                    <!-- 基本信息 -->
                    <div class="space-y-4 mb-6">
                        <div>
                            <label class="block text-sm font-medium dark:text-gray-300 mb-1">命令名称</label>
                            <input v-model="editingCommand.name" type="text"
                                   class="w-full px-3 py-2 border dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 dark:text-white focus:ring-2 focus:ring-blue-400">
                        </div>

                        <div class="flex items-center gap-4">
                            <label class="flex items-center gap-2 cursor-pointer">
                                <input type="radio" v-model="editingCommand.mode" value="simple" class="text-blue-500">
                                <span class="text-sm dark:text-gray-300">简单模式</span>
                            </label>
                            <label class="flex items-center gap-2 cursor-pointer">
                                <input type="radio" v-model="editingCommand.mode" value="advanced" class="text-purple-500">
                                <span class="text-sm dark:text-gray-300">高级模式</span>
                            </label>
                        </div>
                    </div>

                    <!-- 触发条件 -->
                    <div class="mb-6 p-4 bg-gray-50 dark:bg-gray-900 rounded-lg">
                        <h4 class="text-sm font-semibold dark:text-gray-300 mb-3">🎯 触发条件</h4>

                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">类型</label>
                                <select v-model="editingCommand.trigger.type"
                                        class="w-full px-3 py-2 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                    <option v-for="opt in triggerTypeOptions" :key="opt.value" :value="opt.value">
                                        {{ opt.label }}
                                    </option>
                                </select>
                            </div>
                            <div>
                                <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">
                                    {{ editingCommand.trigger.type === 'page_check' ? '检查文本' : '阈值' }}
                                </label>
                                <input v-model="editingCommand.trigger.value"
                                       :type="editingCommand.trigger.type === 'page_check' ? 'text' : 'number'"
                                       :placeholder="editingCommand.trigger.type === 'page_check' ? 'Cloudflare' : '10'"
                                       class="w-full px-3 py-2 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                            </div>
                        </div>

                        <div class="mt-3">
                            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">作用范围</label>
                            <div class="flex items-center gap-4">
                                <label class="flex items-center gap-1.5 text-sm dark:text-gray-300">
                                    <input type="radio" v-model="editingCommand.trigger.scope" value="all"> 所有标签页
                                </label>
                                <label class="flex items-center gap-1.5 text-sm dark:text-gray-300">
                                    <input type="radio" v-model="editingCommand.trigger.scope" value="domain"> 指定域名
                                </label>
                                <label class="flex items-center gap-1.5 text-sm dark:text-gray-300">
                                    <input type="radio" v-model="editingCommand.trigger.scope" value="tab"> 指定标签页
                                </label>
                            </div>
                        </div>

                        <div v-if="editingCommand.trigger.scope === 'domain'" class="mt-2">
                            <input v-model="editingCommand.trigger.domain" type="text" placeholder="例如: chatgpt.com"
                                   class="w-full px-3 py-2 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                        </div>
                        <div v-if="editingCommand.trigger.scope === 'tab'" class="mt-2">
                            <input v-model.number="editingCommand.trigger.tab_index" type="number" min="1" placeholder="标签页编号"
                                   class="w-full px-3 py-2 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                        </div>
                    </div>

                    <!-- 简单模式：动作列表 -->
                    <div v-if="editingCommand.mode === 'simple'" class="mb-6">
                        <div class="flex items-center justify-between mb-3">
                            <h4 class="text-sm font-semibold dark:text-gray-300">🔧 动作列表</h4>
                            <button @click="addAction" class="text-xs text-blue-500 hover:text-blue-700">+ 添加动作</button>
                        </div>

                        <div v-if="editingCommand.actions.length === 0" class="text-sm text-gray-400 dark:text-gray-500 text-center py-4">
                            暂无动作，点击上方添加
                        </div>

                        <div v-for="(action, i) in editingCommand.actions" :key="i"
                             class="flex items-center gap-2 mb-2 p-3 bg-gray-50 dark:bg-gray-900 rounded-lg">
                            <span class="text-xs text-gray-400 w-5">{{ i + 1 }}</span>

                            <select v-model="action.type"
                                    @change="initProxyAction(action)"
                                    class="flex-1 px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                <option v-for="opt in actionTypeOptions" :key="opt.value" :value="opt.value">
                                    {{ opt.label }}
                                </option>
                            </select>

                            <!-- 动作参数 -->
                            <input v-if="action.type === 'wait'" v-model.number="action.seconds" type="number" min="0" step="0.5" placeholder="秒"
                                   class="w-20 px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                            <input v-if="action.type === 'run_js'" v-model="action.code" type="text" placeholder="JavaScript 代码"
                                   class="flex-1 px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm font-mono">
                            <input v-if="action.type === 'switch_preset'" v-model="action.preset_name" type="text" placeholder="预设名称"
                                   class="flex-1 px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                            <input v-if="action.type === 'navigate'" v-model="action.url" type="text" placeholder="URL"
                                   class="flex-1 px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">

                            <!-- 代理切换 - 简略显示 -->
                            <span v-if="action.type === 'switch_proxy'" class="text-xs text-gray-500 dark:text-gray-400 flex-1">
                                {{ action.mode === 'random' ? '随机' : action.mode === 'round_robin' ? '轮询' : action.node_name || '指定' }}
                                @ {{ action.selector || 'Proxy' }}
                            </span>

                            <!-- 排序 & 删除 -->
                            <button @click="moveAction(i, -1)" :disabled="i === 0" class="text-gray-400 hover:text-gray-600 disabled:opacity-30 text-sm">↑</button>
                            <button @click="moveAction(i, 1)" :disabled="i === editingCommand.actions.length - 1" class="text-gray-400 hover:text-gray-600 disabled:opacity-30 text-sm">↓</button>
                            <button @click="removeAction(i)" class="text-red-400 hover:text-red-600 text-sm">✕</button>
                        </div>

                        <!-- 代理切换详细配置（当有 switch_proxy 动作时显示） -->
                        <div v-for="(action, i) in editingCommand.actions.filter(a => a.type === 'switch_proxy')"
                             :key="'proxy-' + i"
                             class="mt-4 p-4 bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-200 dark:border-blue-800">
                            <h5 class="text-sm font-semibold text-blue-800 dark:text-blue-300 mb-3">🔀 代理切换配置</h5>

                            <div class="grid grid-cols-2 gap-3">
                                <!-- Clash API 地址 -->
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Clash API 地址</label>
                                    <input v-model="action.clash_api" type="text"
                                           :placeholder="proxyDefaults.clash_api"
                                           class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm font-mono">
                                </div>

                                <!-- 代理组名称 -->
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">代理组名称</label>
                                    <input v-model="action.selector" type="text"
                                           :placeholder="proxyDefaults.selector"
                                           class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                </div>

                                <!-- 切换模式 -->
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">切换模式</label>
                                    <select v-model="action.mode"
                                            class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                        <option value="random">随机</option>
                                        <option value="round_robin">轮询</option>
                                        <option value="specific">指定节点</option>
                                    </select>
                                </div>

                                <!-- 指定节点名称 -->
                                <div v-if="action.mode === 'specific'">
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">节点名称</label>
                                    <input v-model="action.node_name" type="text" placeholder="输入节点名称"
                                           class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                </div>

                                <!-- Clash Secret -->
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Clash Secret（可选）</label>
                                    <input v-model="action.clash_secret" type="password" placeholder="如未设置可留空"
                                           class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                </div>

                                <!-- 刷新页面 -->
                                <div class="flex items-center">
                                    <label class="flex items-center gap-2 cursor-pointer text-sm dark:text-gray-300">
                                        <input type="checkbox" v-model="action.refresh_after" class="rounded">
                                        切换后刷新页面
                                    </label>
                                </div>
                            </div>

                            <!-- 排除关键词 -->
                            <div class="mt-3">
                                <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">排除节点关键词（逗号分隔）</label>
                                <input v-model="action.exclude_keywords" type="text"
                                       :placeholder="proxyDefaults.exclude_keywords"
                                       class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                            </div>

                            <p class="mt-2 text-xs text-blue-600 dark:text-blue-400">
                                💡 请确保 Clash 已启动并开启 External Controller（通常在 9090 端口）
                            </p>
                        </div>
                    </div>

                    <!-- 高级模式：脚本编辑器 -->
                    <div v-if="editingCommand.mode === 'advanced'" class="mb-6">
                        <div class="flex items-center justify-between mb-3">
                            <h4 class="text-sm font-semibold dark:text-gray-300">📝 脚本</h4>
                            <select v-model="editingCommand.script_lang"
                                    class="px-2 py-1 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-xs">
                                <option value="javascript">JavaScript</option>
                                <option value="python">Python</option>
                            </select>
                        </div>

                        <div class="mb-2 p-3 bg-gray-50 dark:bg-gray-900 rounded text-xs text-gray-500 dark:text-gray-400">
                            <div v-if="editingCommand.script_lang === 'javascript'">
                                💡 脚本将在浏览器页面中执行（等同于 DevTools Console）
                            </div>
                            <div v-else>
                                💡 可用变量：<code>tab</code>（标签页）、<code>session</code>（会话）、
                                <code>browser</code>、<code>config_engine</code>、<code>logger</code>、
                                <code>time</code>、<code>json</code>
                            </div>
                        </div>

                        <textarea v-model="editingCommand.script"
                                  :style="{ height: scriptEditorHeight }"
                                  :placeholder="scriptPlaceholder"
                                  class="w-full px-3 py-2 border dark:border-gray-600 rounded-lg bg-white dark:bg-gray-900 dark:text-green-400 text-sm font-mono resize-y focus:ring-2 focus:ring-purple-400"
                                  spellcheck="false">
                        </textarea>
                    </div>

                    <!-- 底部按钮 -->
                    <div class="flex justify-end gap-3 pt-4 border-t dark:border-gray-700">
                        <button @click="showEditor = false"
                                class="px-4 py-2 border dark:border-gray-600 rounded-lg text-sm dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700">
                            取消
                        </button>
                        <button @click="saveCommand"
                                class="px-4 py-2 bg-blue-500 text-white rounded-lg text-sm hover:bg-blue-600">
                            {{ isNew ? '创建' : '保存' }}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    </div>
    `
};
