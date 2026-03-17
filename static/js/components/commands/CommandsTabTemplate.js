// ==================== CommandsTab Template ====================
window.CommandsTabTemplate = `
    <div class="p-4 space-y-4">
        <!-- 标题栏 -->
        <div class="flex flex-col gap-3 rounded-2xl border border-slate-200/80 bg-[linear-gradient(135deg,rgba(255,255,255,0.98),rgba(241,245,249,0.92))] p-4 shadow-[0_14px_36px_-32px_rgba(15,23,42,0.55)] dark:border-slate-700/70 dark:bg-[linear-gradient(145deg,rgba(15,23,42,0.98),rgba(30,41,59,0.92))] lg:flex-row lg:items-center lg:justify-between">
            <div>
                <h2 class="text-xl font-bold dark:text-white">⚡ 自动化命令</h2>
                <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
                    设置触发条件和执行动作，实现标签页自动化管理
                </p>
            </div>
            <div class="flex flex-wrap items-center gap-3">
                <button @click.stop="toggleHelp"
                        class="flex h-9 w-9 items-center justify-center rounded-xl border border-amber-300/60 bg-white/80 text-sm font-bold text-amber-600 transition hover:bg-amber-50 dark:border-amber-500/30 dark:bg-slate-900/70 dark:text-amber-300 dark:hover:bg-slate-800">
                    ?
                </button>
                <button @click="fetchCommands" :disabled="loading"
                        class="rounded-xl border border-slate-300/80 bg-white/85 px-3 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-100 dark:border-slate-600 dark:bg-slate-900/70 dark:text-white dark:hover:bg-slate-800">
                    {{ loading ? '刷新中...' : '刷新' }}
                </button>
                <button @click="openNewCommand"
                        class="rounded-xl bg-blue-500 px-3 py-2 text-sm font-semibold text-white shadow-md shadow-blue-500/20 transition hover:bg-blue-600">
                    + 新建命令
                </button>
            </div>
        </div>

        <!-- 使用说明 -->
        <div v-if="showHelpTip" class="p-4 bg-amber-50/90 dark:bg-amber-900/20 rounded-2xl border border-amber-200 dark:border-amber-800 shadow-sm">
            <h3 class="font-semibold text-amber-800 dark:text-amber-300 mb-2">💡 工作原理</h3>
            <ul class="text-sm text-amber-700 dark:text-amber-200 space-y-1">
                <li>• <strong>简单模式</strong>：选择触发条件 + 配置动作列表，零代码实现自动化</li>
                <li>• <strong>高级模式</strong>：直接编写 JavaScript 或 Python 脚本，完全自由控制</li>
                <li>• 支持“命令结果匹配”条件分支、网络状态码拦截、Webhook 外部告警</li>
                <li>• 命令在每次对话完成后自动检查触发条件，网络拦截命中时会立即执行</li>
            </ul>
        </div>

        <!-- 空状态 -->
        <div v-if="commands.length === 0 && !loading" class="text-center py-12 text-gray-500 dark:text-gray-400">
            <div class="text-4xl mb-4">⚙️</div>
            <p>还没有自动化命令</p>
            <p class="text-sm mt-2">点击「新建命令」开始配置</p>
        </div>

        <!-- 命令列表 -->
        <div v-if="commands.length > 0" class="rounded-xl border border-slate-200/80 bg-white/80 p-3 shadow-sm dark:border-slate-700/70 dark:bg-slate-900/70">
            <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                <div class="flex flex-wrap items-center gap-3 text-sm text-slate-600 dark:text-slate-300">
                    <span class="rounded-full bg-slate-900/5 px-3 py-1.5 dark:bg-white/5">总数 {{ commands.length }}</span>
                    <span class="rounded-full bg-emerald-500/10 px-3 py-1.5 text-emerald-600 dark:text-emerald-300">启用 {{ enabledCount }}</span>
                    <span class="rounded-full bg-slate-500/10 px-3 py-1.5">禁用 {{ disabledCount }}</span>
                    <span>当前显示 {{ pageStartIndex }} - {{ pageEndIndex }}</span>
                </div>
                <label class="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
                    <span>每页</span>
                    <input v-model.number="pageSize"
                           @change="applyPageSize"
                           type="number"
                           min="1"
                           max="500"
                           list="command-page-size-options"
                           class="w-24 rounded-xl border border-slate-200 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200">
                    <datalist id="command-page-size-options">
                        <option v-for="size in pageSizeOptions" :key="size" :value="size">{{ size }}</option>
                    </datalist>
                </label>
            </div>
        </div>

        <div v-if="commands.length > 0" class="rounded-xl border border-sky-200/80 bg-[linear-gradient(135deg,rgba(240,249,255,0.96),rgba(238,242,255,0.92))] p-2.5 shadow-sm dark:border-sky-800/60 dark:bg-[linear-gradient(145deg,rgba(10,25,47,0.7),rgba(30,41,59,0.75))]">
            <div class="flex flex-wrap items-center justify-between gap-2">
                <div class="flex flex-wrap items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
                    <span class="rounded-full bg-slate-900/5 px-3 py-1.5 dark:bg-white/5">命令组 {{ commandGroups.length }}</span>
                    <span class="rounded-full bg-slate-900/5 px-3 py-1.5 dark:bg-white/5">已选 {{ selectedCommands.length }}</span>
                </div>
                <button @click="showGroupTools = !showGroupTools"
                        class="rounded-xl border border-slate-200 bg-white/80 px-3 py-1.5 text-xs font-semibold text-slate-600 transition hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-200 dark:hover:bg-slate-800">
                    {{ showGroupTools ? '收起分组工具' : '展开分组工具' }}
                </button>
            </div>
            <div v-show="showGroupTools" class="mt-3 flex flex-wrap items-center gap-2">
                <button @click="toggleCurrentPageSelection"
                        class="rounded-xl border border-slate-200 bg-white/80 px-3 py-2 text-xs font-medium text-slate-600 transition hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-300 dark:hover:bg-slate-800">
                    当前可见全选/反选                </button>
                <button @click="clearSelection"
                        :disabled="!hasSelection"
                        class="rounded-xl border border-slate-200 bg-white/80 px-3 py-2 text-xs font-medium text-slate-600 transition hover:bg-slate-100 disabled:opacity-40 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-300 dark:hover:bg-slate-800">
                    清空选择
                </button>
                <input v-model.trim="pendingGroupName"
                       type="text"
                       list="existing-command-groups"
                       placeholder="命令组名称（留空自动）"
                       class="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200">
                <datalist id="existing-command-groups">
                    <option v-for="group in commandGroups" :key="'group_hint_' + group.name" :value="group.name"></option>
                </datalist>
                <button @click="assignSelectedToGroup"
                        :disabled="groupWorking || !hasSelection"
                        class="rounded-xl bg-sky-600 px-3 py-2 text-xs font-semibold text-white transition hover:bg-sky-700 disabled:opacity-40">
                    收纳为命令组
                </button>
                <select v-model="selectedExistingGroupName"
                        :disabled="groupWorking || commandGroups.length === 0"
                        class="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700 disabled:opacity-40 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200">
                    <option value="" disabled>选择已有命令组</option>
                    <option v-for="group in commandGroups" :key="'group_pick_' + group.name" :value="group.name">
                        {{ group.name }}
                    </option>
                </select>
                <button @click="assignSelectedToExistingGroup"
                        :disabled="groupWorking || !hasSelection || !selectedExistingGroupName"
                        class="rounded-xl border border-sky-300 bg-sky-50 px-3 py-2 text-xs font-semibold text-sky-700 transition hover:bg-sky-100 disabled:opacity-40 dark:border-sky-700 dark:bg-sky-900/30 dark:text-sky-300 dark:hover:bg-sky-900/40">
                    加入已有组
                </button>
                <button @click="ungroupSelectedCommands"
                        :disabled="groupWorking || !hasSelection"
                        class="rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-700 transition hover:bg-amber-100 disabled:opacity-40 dark:border-amber-700 dark:bg-amber-900/20 dark:text-amber-300 dark:hover:bg-amber-900/30">
                    解散选中分组
                </button>
                <label class="flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white/80 px-3 py-2 text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-300">
                    <input type="checkbox" v-model="includeDisabledWhenRunGroup">
                    执行组时包含禁用命令
                </label>
                <label class="flex items-center gap-2 rounded-xl border border-slate-200 bg-white/80 px-3 py-2 text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-300">
                    <span>执行组占用策略</span>
                    <select v-model="runGroupAcquirePolicy"
                            class="rounded-lg border border-slate-200 bg-white px-2 py-1 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200">
                        <option value="inherit_session">沿用当前会话</option>
                        <option value="try_acquire">尝试重新占用</option>
                        <option value="require_acquire">必须重新占用</option>
                    </select>
                </label>
                <span class="text-xs text-slate-500 dark:text-slate-400">可直接拖动命令卡片到某个组头完成收纳</span>
            </div>
        </div>

        <div class="space-y-3">
            <div v-for="row in paginatedDisplayRows" :key="row.key"
                 :class="[
                    row.isGroup ? 'rounded-xl border border-sky-200/80 bg-sky-50/50 p-2.5 dark:border-sky-800/50 dark:bg-sky-900/10' : '',
                    row.isGroup && isGroupDropTarget(row.groupName) ? 'ring-2 ring-sky-400 ring-offset-1 ring-offset-white dark:ring-offset-slate-900' : ''
                 ]">
                <div v-if="row.isGroup"
                     @dragover.prevent="onGroupDragOver(row.groupName, $event)"
                     @dragleave="onGroupDragLeave(row.groupName)"
                     @drop.prevent="onGroupDrop(row.groupName)"
                     class="flex flex-wrap items-center justify-between gap-2">
                    <div class="flex items-center gap-2">
                        <button @click="toggleGroupCollapse(row.groupName)"
                                class="rounded-lg border border-slate-200 bg-white/80 px-2 py-1 text-xs font-bold text-slate-600 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-300">
                            {{ isGroupCollapsed(row.groupName) ? '展开' : '折叠' }}
                        </button>
                        <span class="rounded-full bg-sky-100 px-3 py-1 text-xs font-semibold text-sky-700 dark:bg-sky-900/40 dark:text-sky-300">
                            {{ row.groupName }}
                        </span>
                        <span class="text-xs text-slate-500 dark:text-slate-300">
                            {{ row.commands.filter(item => item.enabled).length }}/{{ row.commands.length }}
                        </span>
                    </div>
                    <div class="flex items-center gap-2">
                        <button @click="runGroup(row.groupName)"
                                :disabled="groupWorking"
                                class="rounded-lg bg-blue-500 px-2 py-1 text-xs font-semibold text-white hover:bg-blue-600 disabled:opacity-40">
                            执行组
                        </button>
                        <button @click="disbandGroup(row.groupName)"
                                :disabled="groupWorking"
                                class="rounded-lg border border-red-300 px-2 py-1 text-xs font-semibold text-red-600 hover:bg-red-50 disabled:opacity-40 dark:border-red-700 dark:text-red-300 dark:hover:bg-red-900/30">
                            解散组
                        </button>
                    </div>
                </div>

                <div :class="row.isGroup ? 'mt-2 space-y-2' : 'space-y-2'" v-show="!row.isGroup || !isGroupCollapsed(row.groupName)">
                    <div v-for="cmd in row.commands" :key="cmd.id"
                         draggable="true"
                         @dragstart="beginGroupDrag(cmd.id, $event)"
                         @dragend="clearGroupDragState"
                         :class="['rounded-xl border p-3 transition-all shadow-sm',
                                  cmd.enabled
                                  ? 'bg-[linear-gradient(145deg,rgba(255,255,255,0.98),rgba(241,245,249,0.94))] border-slate-200/80 hover:-translate-y-0.5 hover:shadow-md dark:bg-[linear-gradient(145deg,rgba(15,23,42,0.96),rgba(30,41,59,0.92))] dark:border-slate-700/70'
                                  : 'bg-slate-100/85 dark:bg-slate-900 border-slate-200 dark:border-slate-700 opacity-70']">
                        <div class="flex items-start justify-between">
                            <div class="flex-1 min-w-0">
                                <div class="flex items-center gap-3 mb-2">
                                    <label class="inline-flex items-center">
                                        <input type="checkbox"
                                               :checked="isCommandSelected(cmd.id)"
                                               @change="toggleCommandSelection(cmd.id)"
                                               class="h-4 w-4 rounded border-slate-300 text-sky-600 focus:ring-sky-500">
                                    </label>
                                    <span class="inline-flex h-7 min-w-7 items-center justify-center rounded-xl bg-slate-900 px-2 text-[11px] font-bold text-white dark:bg-slate-100 dark:text-slate-900">
                                        {{ getCommandOrder(cmd.id) }}
                                    </span>
                                    <span class="font-semibold dark:text-white text-base">{{ cmd.name }}</span>
                                    <span v-if="cmd.group_name"
                                          class="px-2 py-0.5 rounded-full text-xs font-medium bg-sky-100 dark:bg-sky-900/40 text-sky-700 dark:text-sky-300">
                                        组: {{ cmd.group_name }}
                                    </span>
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

                                <div class="text-sm text-gray-600 dark:text-gray-300 mb-1">
                                    <span class="font-medium">触发：</span>
                                    {{ getTriggerLabel(cmd.trigger?.type) }}
                                    <span v-if="getTriggerValueDisplay(cmd.trigger)" class="text-blue-600 dark:text-blue-400 font-mono">= {{ getTriggerValueDisplay(cmd.trigger) }}</span>
                                    <span class="text-gray-400 mx-1">|</span>
                                    <span>范围：{{ getScopeLabel(cmd.trigger?.scope) }}</span>
                                    <span v-if="cmd.trigger?.scope === 'domain' && cmd.trigger?.domain" class="text-green-600 dark:text-green-400">
                                        ({{ cmd.trigger.domain }})
                                    </span>
                                    <span v-if="cmd.trigger?.scope === 'tab' && cmd.trigger?.tab_index != null" class="text-green-600 dark:text-green-400">
                                        (#{{ cmd.trigger.tab_index }})
                                    </span>
                                </div>

                                <div v-if="cmd.mode === 'simple'" class="text-sm text-gray-500 dark:text-gray-400">
                                    <span class="font-medium">动作：</span>
                                    <span v-for="(a, i) in (cmd.actions || []).slice(0, 3)" :key="i">
                                        {{ getActionLabel(a.type) }}<span v-if="i < Math.min((cmd.actions || []).length, 3) - 1">、</span>
                                    </span>
                                    <span v-if="(cmd.actions || []).length > 3"> 等{{ cmd.actions.length }} 步</span>
                                </div>
                                <div v-else class="text-sm text-gray-500 dark:text-gray-400">
                                    <span class="font-medium">脚本：</span>
                                    {{ cmd.script_lang === 'python' ? 'Python' : 'JavaScript' }}
                                    ({{ (cmd.script || '').split('\\n').length }} 行)
                                </div>

                                <div class="text-xs text-gray-400 dark:text-gray-500 mt-2">
                                    已触发{{ cmd.trigger_count || 0 }} 次
                                    <span v-if="cmd.last_triggered"> · 上次: {{ formatTime(cmd.last_triggered) }}</span>
                                </div>
                            </div>

                            <div class="flex flex-wrap items-center gap-2 ml-4">
                                <button @click="moveCommand(cmd, -1)" :disabled="reordering || getCommandOrder(cmd.id) === 1"
                                        class="rounded-lg border border-slate-200 bg-white/80 px-2.5 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-300 dark:hover:bg-slate-800">
                                    ↑ 上移
                                </button>
                                <button @click="moveCommand(cmd, 1)" :disabled="reordering || getCommandOrder(cmd.id) === commands.length"
                                        class="rounded-lg border border-slate-200 bg-white/80 px-2.5 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-300 dark:hover:bg-slate-800">
                                    ↓ 下移
                                </button>
                                <button @click="testCommand(cmd)" title="手动执行"
                                        class="rounded-lg bg-blue-500 px-2.5 py-1 text-xs font-semibold text-white transition hover:bg-blue-600">
                                    ▶️
                                </button>
                                <button @click="toggleCommand(cmd)" :title="cmd.enabled ? '禁用' : '启用'"
                                        class="rounded-lg border border-slate-200 bg-white/80 px-2.5 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-300 dark:hover:bg-slate-800">
                                    {{ cmd.enabled ? '⏸️' : '▶️' }}
                                </button>
                                <button @click="openEditCommand(cmd)" title="编辑"
                                        class="rounded-lg border border-slate-200 bg-white/80 px-2.5 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-300 dark:hover:bg-slate-800">
                                    ✏️
                                </button>
                                <button @click="deleteCommand(cmd)" title="删除"
                                        class="rounded-lg border border-red-200 bg-red-50 px-2.5 py-1 text-xs font-medium text-red-500 transition hover:bg-red-100 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300 dark:hover:bg-red-500/20">
                                    🗑️
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        <!-- ========== 编辑弹窗 ========== -->
        <div v-if="commands.length > 0" class="flex flex-col gap-2 rounded-xl border border-slate-200/80 bg-white/85 p-3 shadow-sm dark:border-slate-700/70 dark:bg-slate-900/75 sm:flex-row sm:items-center sm:justify-between">
            <div class="text-sm text-slate-500 dark:text-slate-400">
                第<span class="font-semibold text-slate-900 dark:text-white">{{ currentPage }}</span> / {{ totalPages }} 页            </div>
            <div class="flex flex-wrap items-center gap-2">
                <button @click="changePage(currentPage - 1)" :disabled="currentPage === 1"
                        class="rounded-xl border border-slate-200 bg-white/80 px-3 py-2 text-sm text-slate-600 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-300 dark:hover:bg-slate-800">
                    上一页                </button>
                <button v-for="page in visiblePageNumbers" :key="page"
                        @click="changePage(page)"
                        :class="[
                            'rounded-xl px-3 py-2 text-sm font-medium transition',
                            page === currentPage
                                ? 'bg-slate-900 text-white dark:bg-white dark:text-slate-900'
                                : 'border border-slate-200 bg-white/80 text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-300 dark:hover:bg-slate-800'
                        ]">
                    {{ page }}
                </button>
                <button @click="changePage(currentPage + 1)" :disabled="currentPage === totalPages"
                        class="rounded-xl border border-slate-200 bg-white/80 px-3 py-2 text-sm text-slate-600 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-300 dark:hover:bg-slate-800">
                    下一页                </button>
            </div>
        </div>

        <div v-if="showEditor" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
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
                        <div>
                            <label class="block text-sm font-medium dark:text-gray-300 mb-1">命令组（可选）</label>
                            <input v-model.trim="editingCommand.group_name"
                                   list="command-group-options"
                                   type="text"
                                   placeholder="例如：过盾流程组"
                                   class="w-full px-3 py-2 border dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 dark:text-white focus:ring-2 focus:ring-sky-400">
                            <datalist id="command-group-options">
                                <option v-for="group in commandGroups" :key="group.name" :value="group.name"></option>
                            </datalist>
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

                        <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
                            <div>
                                <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">类型</label>
                                <select v-model="editingCommand.trigger.type"
                                        @change="handleTriggerTypeChange"
                                        class="w-full px-3 py-2 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                    <option v-for="opt in triggerTypeOptions" :key="opt.value" :value="opt.value">
                                        {{ opt.label }}
                                    </option>
                                </select>
                            </div>
                            <div>
                                <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">
                                    {{ getTriggerTargetLabel(editingCommand.trigger) }}
                                </label>
                                <div v-if="['command_triggered', 'command_result_match', 'command_result_event'].includes(editingCommand.trigger.type)"
                                     class="relative">
                                    <button type="button"
                                            @click="toggleSourceCommandPicker"
                                            :disabled="sourceCommandOptions.length === 0"
                                            class="flex w-full items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2 text-left text-sm text-slate-700 transition hover:border-sky-300 hover:bg-sky-50/70 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-600 dark:bg-gray-700 dark:text-white dark:hover:border-sky-500 dark:hover:bg-slate-800">
                                        <div class="min-w-0">
                                            <div class="truncate font-medium">{{ getSourceCommandButtonLabel() }}</div>
                                            <div class="truncate text-xs text-slate-500 dark:text-slate-300">
                                                <span v-if="editingCommand.trigger.type === 'command_result_event'">
                                                    {{ editingCommand.trigger.listen_all_commands ? '全部命令结果' : ((selectedSourceCommandOptions || []).length + ' 条已选') }}
                                                </span>
                                                <span v-else>{{ selectedSourceCommandOption?.groupName || '未分组命令' }}</span>
                                            </div>
                                        </div>
                                        <span class="ml-3 text-xs text-slate-400">{{ sourceCommandPickerOpen ? '收起' : '展开' }}</span>
                                    </button>

                                    <div v-if="sourceCommandPickerOpen"
                                         class="absolute left-0 right-0 z-30 mt-2 overflow-hidden rounded-2xl border border-slate-200 bg-white/98 shadow-2xl shadow-slate-900/10 backdrop-blur dark:border-slate-700 dark:bg-slate-900/98">
                                        <div class="border-b border-slate-200/80 p-3 dark:border-slate-700">
                                            <div class="flex items-center gap-2">
                                                <input v-model.trim="sourceCommandSearch"
                                                       type="text"
                                                       placeholder="搜索命令名 / 命令组 / ID"
                                                       class="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700 focus:border-sky-300 focus:outline-none dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100">
                                                <button v-if="sourceCommandSearch"
                                                        type="button"
                                                        @click="sourceCommandSearch = ''"
                                                        class="rounded-lg border border-slate-200 px-2 py-2 text-xs text-slate-500 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800">
                                                    清空
                                                </button>
                                            </div>
                                             <p class="mt-2 text-xs text-slate-500 dark:text-slate-400">
                                                 优先按命令组浏览，展开组后再选择具体命令
                                             </p>
                                             <div v-if="editingCommand.trigger.type === 'command_result_event'"
                                                  class="mt-3 flex items-center justify-between rounded-xl border border-emerald-200/80 bg-emerald-50/80 px-3 py-2 text-xs text-emerald-700 dark:border-emerald-800/60 dark:bg-emerald-900/20 dark:text-emerald-200">
                                                 <div>可多选命令，或直接监听全部命令结果</div>
                                                 <button type="button"
                                                         @click="toggleListenAllCommands"
                                                         class="rounded-lg border border-emerald-300 px-2 py-1 font-semibold hover:bg-emerald-100 dark:border-emerald-700 dark:hover:bg-emerald-900/40">
                                                     {{ editingCommand.trigger.listen_all_commands ? '改为手动选择' : '监听全部命令' }}
                                                 </button>
                                             </div>
                                         </div>

                                        <div class="max-h-80 overflow-y-auto p-2">
                                            <div v-if="filteredSourceCommandSections.length === 0"
                                                 class="rounded-xl border border-dashed border-slate-200 px-3 py-6 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
                                                没有匹配的来源命令
                                            </div>

                                            <div v-for="section in filteredSourceCommandSections" :key="section.key" class="mb-2 rounded-xl border border-slate-200/80 bg-slate-50/80 dark:border-slate-700 dark:bg-slate-800/70">
                                                <button type="button"
                                                        @click="toggleSourceCommandSection(section)"
                                                        class="flex w-full items-center justify-between gap-3 px-3 py-2 text-left">
                                                    <div class="min-w-0">
                                                        <div class="truncate text-sm font-semibold text-slate-700 dark:text-slate-100">{{ section.name }}</div>
                                                        <div class="text-xs text-slate-500 dark:text-slate-400">{{ section.commands.length }} 条命令</div>
                                                    </div>
                                                    <span class="rounded-full bg-slate-900/5 px-2 py-1 text-[11px] text-slate-500 dark:bg-white/5 dark:text-slate-300">
                                                        {{ isSourceCommandSectionExpanded(section) ? '收起' : '展开' }}
                                                    </span>
                                                </button>

                                                <div v-show="isSourceCommandSectionExpanded(section)" class="border-t border-slate-200/70 p-2 dark:border-slate-700">
                                                    <button v-for="opt in section.commands"
                                                            :key="opt.value"
                                                            type="button"
                                                            @click="selectSourceCommand(opt.value)"
                                                            :class="[
                                                                'mb-1 flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2 text-left transition',
                                                                isSourceCommandSelected(opt.value)
                                                                    ? 'bg-sky-100 text-sky-800 ring-1 ring-sky-300 dark:bg-sky-900/40 dark:text-sky-200 dark:ring-sky-700'
                                                                    : 'bg-white text-slate-700 hover:bg-sky-50 dark:bg-slate-900 dark:text-slate-100 dark:hover:bg-slate-800'
                                                            ]">
                                                        <div class="min-w-0">
                                                            <div class="truncate text-sm font-medium">{{ opt.label }}</div>
                                                            <div class="truncate text-[11px] text-slate-500 dark:text-slate-400">{{ opt.value }}</div>
                                                        </div>
                                                        <span v-if="!opt.enabled"
                                                              class="rounded-full bg-slate-200 px-2 py-1 text-[11px] text-slate-500 dark:bg-slate-700 dark:text-slate-300">
                                                            已禁用
                                                        </span>
                                                    </button>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <input v-else-if="editingCommand.trigger.type === 'network_request_error'"
                                       v-model.trim="editingCommand.trigger.url_pattern"
                                       type="text"
                                       :placeholder="editingCommand.trigger.match_mode === 'regex'
                                           ? '如: .*/queue/join.* 或 .*conversation.*'
                                           : '如: /queue/join 或 /conversation'"
                                       class="w-full px-3 py-2 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm font-mono">
                                <input v-else-if="editingCommand.trigger.type === 'page_check'"
                                       v-model="editingCommand.trigger.value"
                                       type="text"
                                       placeholder="Cloudflare"
                                       class="w-full px-3 py-2 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                <input v-else v-model.number="editingCommand.trigger.value"
                                       type="number"
                                       placeholder="10"
                                       class="w-full px-3 py-2 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                            </div>
                        </div>

                        <div v-if="editingCommand.trigger.type === 'command_result_match'"
                             class="mt-3 rounded-xl border border-emerald-200/70 bg-emerald-50/70 p-3 dark:border-emerald-800/60 dark:bg-emerald-900/20">
                            <div class="grid grid-cols-1 gap-3 md:grid-cols-3">
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">目标步骤</label>
                                    <select v-model="editingCommand.trigger.action_ref"
                                            class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                        <option value="">命令最终返回值</option>
                                        <option v-for="opt in resultSourceActionOptions" :key="opt.value" :value="opt.value">
                                            {{ opt.label }}
                                        </option>
                                    </select>
                                </div>
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">匹配规则</label>
                                    <select v-model="editingCommand.trigger.match_rule"
                                            class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                        <option value="equals">等于</option>
                                        <option value="contains">包含</option>
                                        <option value="not_equals">不等于</option>
                                    </select>
                                </div>
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">期望值</label>
                                    <input v-model="editingCommand.trigger.expected_value"
                                           type="text"
                                           placeholder="如: CSS_FAILED / SUCCESS"
                                           class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                </div>
                            </div>
                        </div>

                        <div v-if="editingCommand.trigger.type === 'network_request_error'"
                             class="mt-3 rounded-xl border border-rose-200/70 bg-rose-50/70 p-3 dark:border-rose-800/60 dark:bg-rose-900/20">
                            <div class="grid grid-cols-1 gap-3 md:grid-cols-3">
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">规则类型</label>
                                    <select v-model="editingCommand.trigger.match_mode"
                                            class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                        <option value="keyword">关键词</option>
                                        <option value="regex">正则表达式</option>
                                    </select>
                                </div>
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">状态码</label>
                                    <input v-model="editingCommand.trigger.status_codes"
                                           type="text"
                                           placeholder="403,429,500"
                                           class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm font-mono">
                                </div>
                                <div class="flex items-center pt-5">
                                    <label class="flex items-center gap-2 cursor-pointer text-sm dark:text-gray-300">
                                        <input type="checkbox" v-model="editingCommand.trigger.abort_on_match" class="rounded">
                                        命中后立即中断等待                                    </label>
                                </div>
                            </div>
                            <p class="mt-2 text-xs text-rose-700 dark:text-rose-300">
                                {{ editingCommand.trigger.match_mode === 'regex'
                                    ? '正则内容在上方“正则表达式”输入框填写。例如：.*/queue/join.*'
                                    : '关键词模式同样在上方输入框填写，支持 URL 子串匹配。' }}
                            </p>
                        </div>

                        <div v-if="editingCommand.trigger.type === 'command_result_event'"
                             class="mt-3 rounded-xl border border-emerald-200/70 bg-emerald-50/70 p-3 dark:border-emerald-800/60 dark:bg-emerald-900/20">
                            <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
                                <label class="flex items-center gap-2 text-sm dark:text-gray-300">
                                    <input type="checkbox" v-model="editingCommand.trigger.listen_all_commands" class="rounded">
                                    监听全部命令返回结果
                                </label>
                                <label class="flex items-center gap-2 text-sm dark:text-gray-300">
                                    <input type="checkbox" v-model="editingCommand.trigger.informative_only" class="rounded">
                                    仅通知有信息的结果
                                </label>
                            </div>
                            <p class="mt-2 text-xs text-emerald-700 dark:text-emerald-300">
                                只监听命令最终返回值，不会按每个步骤单独触发。可用变量：<span v-pre>{{source_command_name}}</span>、<span v-pre>{{command_result_summary}}</span>、<span v-pre>{{command_result}}</span>
                            </p>
                        </div>

                        <div class="mt-3 rounded-xl border border-slate-200/70 bg-white/80 p-3 dark:border-slate-700/60 dark:bg-slate-900/40">
                            <div class="grid grid-cols-1 gap-3 md:grid-cols-4">
                                <label class="flex items-center gap-2 text-sm dark:text-gray-300 pt-5 md:pt-6">
                                    <input type="checkbox" v-model="editingCommand.trigger.periodic_enabled" class="rounded">
                                    启用该命令周期检测
                                </label>
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">命令优先级（整数）</label>
                                    <input v-model.number="editingCommand.trigger.priority"
                                           type="number"
                                           step="1"
                                           class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                </div>
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">检测间隔（秒）</label>
                                    <input v-model.number="editingCommand.trigger.periodic_interval_sec"
                                           type="number"
                                           min="1"
                                           step="0.5"
                                           class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                </div>
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">随机抖动（秒）</label>
                                    <input v-model.number="editingCommand.trigger.periodic_jitter_sec"
                                           type="number"
                                           min="0"
                                           step="0.2"
                                           class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                </div>
                            </div>
                            <p class="mt-2 text-xs text-slate-500 dark:text-slate-400">
                                仅影响“空闲标签页周期扫描”；对话完成后的即时触发检查仍会执行。
                            </p>
                            <p class="mt-1 text-xs text-slate-500 dark:text-slate-400">
                                优先级支持任意整数，数值越大越高。默认请求基准优先级为 2（可用环境变量 <code>CMD_REQUEST_PRIORITY_BASELINE</code> 调整），所以像 <code>-99</code>、<code>0</code>、<code>2</code>、<code>99</code> 都可以。
                            </p>
                        </div>

                        <div class="mt-3 rounded-xl border border-slate-200/70 bg-white/80 p-3 dark:border-slate-700/60 dark:bg-slate-900/40">
                            <div class="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
                                <div v-if="editingCommand.trigger.type === 'page_check'">
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">页面命中触发模式</label>
                                    <select v-model="editingCommand.trigger.fire_mode"
                                            class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                        <option value="edge">边沿触发</option>
                                        <option value="level">持续触发</option>
                                    </select>
                                </div>
                                <div v-if="editingCommand.trigger.type === 'page_check'">
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">冷却时间（秒）</label>
                                    <input v-model.number="editingCommand.trigger.cooldown_sec"
                                           type="number"
                                           min="0"
                                           step="0.5"
                                           class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                </div>
                                <div v-if="editingCommand.trigger.type === 'page_check'">
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">页面稳定命中（秒）</label>
                                    <input v-model.number="editingCommand.trigger.stable_for_sec"
                                           type="number"
                                           min="0"
                                           step="0.5"
                                           class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                </div>
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">工作流中断策略</label>
                                    <select v-model="editingCommand.trigger.interrupt_policy"
                                            class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                        <option value="auto">自动</option>
                                        <option value="resume">恢复后继续</option>
                                        <option value="abort">直接中止</option>
                                    </select>
                                </div>
                                <label class="flex items-center gap-2 text-sm dark:text-gray-300 pt-5 md:pt-6">
                                    <input type="checkbox" v-model="editingCommand.trigger.allow_during_workflow" class="rounded">
                                    允许在工作流中插队
                                </label>
                                <label v-if="editingCommand.trigger.type === 'page_check'" class="flex items-center gap-2 text-sm dark:text-gray-300 pt-5 md:pt-6">
                                    <input type="checkbox" v-model="editingCommand.trigger.check_while_busy_workflow" class="rounded">
                                    工作流忙碌时仍参与页面检查
                                </label>
                            </div>
                            <div class="mt-3">
                                <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">工作流中断提示（可选）</label>
                                <input v-model.trim="editingCommand.trigger.interrupt_message"
                                       type="text"
                                       placeholder="触发该命令时，后续工作流已打断，请重试"
                                       class="w-full px-3 py-2 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                            </div>
                        </div>

                        <div class="mt-3">
                            <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">作用范围</label>
                            <div class="flex items-center gap-4">
                                <label class="flex items-center gap-1.5 text-sm dark:text-gray-300">
                                    <input type="radio" v-model="editingCommand.trigger.scope" value="all" @change="handleTriggerScopeChange"> 所有标签页
                                </label>
                                <label class="flex items-center gap-1.5 text-sm dark:text-gray-300">
                                    <input type="radio" v-model="editingCommand.trigger.scope" value="domain" @change="handleTriggerScopeChange"> 指定域名
                                </label>
                                <label class="flex items-center gap-1.5 text-sm dark:text-gray-300">
                                    <input type="radio" v-model="editingCommand.trigger.scope" value="tab" @change="handleTriggerScopeChange"> 指定标签页                                </label>
                            </div>
                        </div>

                        <div v-if="editingCommand.trigger.scope === 'domain'" class="mt-2">
                            <input v-model.trim="editingCommand.trigger.domain"
                                   @change="handleTriggerTargetChange"
                                   list="command-domain-options"
                                   type="text" placeholder="例如: chatgpt.com"
                                   class="w-full px-3 py-2 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                            <datalist id="command-domain-options">
                                <option v-for="domain in availableDomains" :key="domain" :value="domain"></option>
                            </datalist>
                        </div>
                        <div v-if="editingCommand.trigger.scope === 'tab'" class="mt-2">
                            <select v-if="availableTabs.length > 0"
                                    v-model.number="editingCommand.trigger.tab_index"
                                    @change="handleTriggerTargetChange"
                                    class="w-full px-3 py-2 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                <option :value="null" disabled>选择标签页</option>
                                <option v-for="tab in availableTabs" :key="tab.persistent_index" :value="tab.persistent_index">
                                    {{ getTabLabel(tab) }}
                                </option>
                            </select>
                            <input v-else
                                   v-model.number="editingCommand.trigger.tab_index"
                                   @change="handleTriggerTargetChange"
                                   type="number" min="1" placeholder="标签页编号"
                                   class="w-full px-3 py-2 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                        </div>
                    </div>

                    <!-- 简单模式：动作列表 -->
                    <div v-if="editingCommand.mode === 'simple'" class="mb-6">
                        <div class="flex items-center justify-between mb-3">
                            <h4 class="text-sm font-semibold dark:text-gray-300">🔧 动作列表</h4>
                            <button @click="addAction" class="text-xs text-blue-500 hover:text-blue-700">+ 添加动作</button>
                        </div>

                        <label class="mb-3 flex items-center gap-2 text-sm dark:text-gray-300">
                            <input type="checkbox" v-model="editingCommand.stop_on_error" class="rounded">
                            动作失败后立即停止后续步骤
                        </label>

                        <div v-if="editingCommand.actions.length === 0" class="text-sm text-gray-400 dark:text-gray-500 text-center py-4">
                            暂无动作，点击上方添加
                        </div>

                        <div v-for="(action, i) in editingCommand.actions" :key="i"
                             class="flex flex-wrap items-start gap-2 mb-2 p-3 bg-gray-50 dark:bg-gray-900 rounded-lg">
                            <span class="text-xs text-gray-400 w-5">{{ i + 1 }}</span>

                                <select v-model="action.type"
                                     @change="handleActionTypeChange(action)"
                                     class="flex-1 min-w-[180px] px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                    <optgroup v-for="group in actionTypeGroups" :key="group.label" :label="group.label">
                                        <option v-for="opt in group.options" :key="opt.value" :value="opt.value">
                                            {{ opt.label }}
                                        </option>
                                    </optgroup>
                                </select>

                            <!-- 动作参数 -->
                            <input v-if="action.type === 'wait'" v-model.number="action.seconds" type="number" min="0" step="0.5" placeholder="秒"
                                   class="w-20 px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                            <input v-if="action.type === 'run_js'" v-model="action.code" type="text" placeholder="JavaScript 代码"
                                   class="flex-1 px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm font-mono">
                            <input v-if="action.type === 'click_element'" v-model.trim="action.selector" type="text" placeholder="CSS / XPath 选择器"
                                   class="flex-1 px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm font-mono">
                            <div v-if="action.type === 'click_coordinates'" class="flex flex-wrap items-center gap-2">
                                <input v-model.number="action.x" type="number" step="1" placeholder="X"
                                       class="w-24 px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                <input v-model.number="action.y" type="number" step="1" placeholder="Y"
                                       class="w-24 px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                            </div>
                            <div v-if="['execute_preset', 'execute_workflow'].includes(action.type)" class="flex-1 min-w-[220px]">
                                <select v-model="action.preset_name"
                                        :disabled="availablePresets.length === 0"
                                        class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                    <option value="" disabled>{{ getPresetSelectPlaceholder() }}</option>
                                    <option v-for="preset in availablePresets" :key="preset" :value="preset">
                                        {{ preset }}
                                    </option>
                                </select>
                                <input v-if="action.type === 'execute_workflow'"
                                       v-model="action.prompt"
                                       type="text"
                                       placeholder="可选测试消息"
                                       class="w-full mt-2 px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                                    {{ getPresetHint() }}
                                </p>
                            </div>
                            <div v-if="action.type === 'execute_command_group'" class="flex-1 min-w-[220px] space-y-2">
                                <select v-model="action.group_name"
                                        class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                    <option value="" disabled>请选择命令组</option>
                                    <option v-for="group in commandGroups" :key="group.name" :value="group.name">
                                        {{ group.name }}（{{ group.enabledCount }}/{{ group.count }}）
                                    </option>
                                </select>
                                <label class="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
                                    <input type="checkbox" v-model="action.include_disabled" class="rounded">
                                    包含禁用命令
                                </label>
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">占用策略</label>
                                    <select v-model="action.acquire_policy"
                                            class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                        <option value="inherit_session">沿用当前会话</option>
                                        <option value="try_acquire">尝试重新占用</option>
                                        <option value="require_acquire">必须重新占用</option>
                                    </select>
                                </div>
                            </div>
                            <input v-if="action.type === 'navigate'" v-model="action.url" type="text" placeholder="URL"
                                   class="flex-1 px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                            <span v-if="action.type === 'send_webhook'" class="text-xs text-gray-500 dark:text-gray-400 flex-1 font-mono">
                                {{ (action.method || 'POST').toUpperCase() }} · {{ action.url || '未配置 URL' }}
                            </span>
                            <span v-if="action.type === 'send_napcat'" class="text-xs text-gray-500 dark:text-gray-400 flex-1 font-mono">
                                NapCat · {{ action.target_type === 'group' ? ('群 ' + (action.group_id || '未填写')) : ('QQ ' + (action.user_id || '未填写')) }}
                            </span>
                            <span v-if="action.type === 'abort_task'" class="text-xs text-gray-500 dark:text-gray-400 flex-1">
                                触发后取消当前请求并停止后续动作
                            </span>
                            <span v-if="action.type === 'release_tab_lock'" class="text-xs text-gray-500 dark:text-gray-400 flex-1">
                                解除当前标签页占用（可强制释放并清空页面）                            </span>

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

                        <!-- 代理切换详细配置（当某个 switch_proxy 动作时显示） -->
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
                                💡 请确认 Clash 已启动并开启 External Controller（通常在 9090 端口）                            </p>
                        </div>

                        <div v-for="(action, i) in editingCommand.actions.filter(a => a.type === 'send_webhook')"
                             :key="'webhook-' + i"
                             class="mt-4 p-4 bg-emerald-50 dark:bg-emerald-900/20 rounded-lg border border-emerald-200 dark:border-emerald-800">
                            <h5 class="text-sm font-semibold text-emerald-800 dark:text-emerald-300 mb-3">📣 Webhook 配置</h5>

                            <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">请求方法</label>
                                    <select v-model="action.method"
                                            class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                        <option value="POST">POST</option>
                                        <option value="GET">GET</option>
                                    </select>
                                </div>
                                <div class="md:col-span-2">
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">请求 URL</label>
                                    <input v-model.trim="action.url" type="text"
                                           placeholder="https://oapi.dingtalk.com/robot/send?access_token=..."
                                           class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm font-mono">
                                </div>
                            </div>

                            <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Payload（支持变量）</label>
                                    <textarea v-model="action.payload"
                                              rows="3"
                                              class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm font-mono resize-y"
                                              placeholder='{"msg":"标签页#{{tab_index}} 在 {{domain}} 连续失败"}'></textarea>
                                </div>
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Headers（JSON，可选）</label>
                                    <textarea v-model="action.headers"
                                              rows="3"
                                              class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm font-mono resize-y"
                                              placeholder='{"Content-Type":"application/json"}'></textarea>
                                </div>
                            </div>

                            <div class="mt-3 flex flex-wrap items-center gap-4">
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">超时（秒）</label>
                                    <input v-model.number="action.timeout" type="number" min="1" step="1"
                                           class="w-24 px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                </div>
                                <label class="flex items-center gap-2 cursor-pointer text-sm dark:text-gray-300 pt-5">
                                    <input type="checkbox" v-model="action.raise_for_status" class="rounded">
                                    HTTP 非 2xx 视为失败
                                </label>
                            </div>

                            <p class="mt-2 text-xs text-emerald-700 dark:text-emerald-300">
                                可用变量：                                <span v-pre>{{tab_index}}</span>、                                <span v-pre>{{domain}}</span>、                                <span v-pre>{{network_status}}</span>、                                <span v-pre>{{network_url}}</span>、                                <span v-pre>{{timestamp}}</span>
                            </p>
                        </div>

                        <div v-for="(action, i) in editingCommand.actions.filter(a => a.type === 'send_napcat')"
                             :key="'napcat-' + i"
                             class="mt-4 p-4 bg-cyan-50 dark:bg-cyan-900/20 rounded-lg border border-cyan-200 dark:border-cyan-800">
                            <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
                                <h5 class="text-sm font-semibold text-cyan-800 dark:text-cyan-300">🐧 NapCat QQ 通知</h5>
                                <div class="flex gap-2">
                                    <button @click="useNapcatPreset(action, 'private')"
                                            type="button"
                                            class="rounded-lg border border-cyan-300 px-2 py-1 text-xs font-semibold text-cyan-700 hover:bg-cyan-100 dark:border-cyan-700 dark:text-cyan-300 dark:hover:bg-cyan-900/40">
                                        私聊模板
                                    </button>
                                    <button @click="useNapcatPreset(action, 'group')"
                                            type="button"
                                            class="rounded-lg border border-cyan-300 px-2 py-1 text-xs font-semibold text-cyan-700 hover:bg-cyan-100 dark:border-cyan-700 dark:text-cyan-300 dark:hover:bg-cyan-900/40">
                                        群聊模板
                                    </button>
                                </div>
                            </div>

                            <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                                <div class="md:col-span-2">
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">NapCat HTTP 地址</label>
                                    <input v-model.trim="action.base_url" type="text"
                                           placeholder="http://127.0.0.1:3000"
                                           class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm font-mono">
                                </div>
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">发送目标</label>
                                    <select v-model="action.target_type"
                                            class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                        <option value="private">私聊</option>
                                        <option value="group">群聊</option>
                                    </select>
                                </div>
                            </div>

                            <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
                                <div v-if="action.target_type !== 'group'">
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">QQ 号</label>
                                    <input v-model.trim="action.user_id" type="text"
                                           placeholder="接收通知的 QQ 号"
                                           class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm font-mono">
                                </div>
                                <div v-else>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">群号</label>
                                    <input v-model.trim="action.group_id" type="text"
                                           placeholder="接收通知的群号"
                                           class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm font-mono">
                                </div>
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Access Token（可选）</label>
                                    <input v-model.trim="action.access_token" type="text"
                                           placeholder="留空表示不带鉴权头"
                                           class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm font-mono">
                                </div>
                            </div>

                            <div class="mt-3">
                                <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">消息内容（支持变量）</label>
                                <textarea v-model="action.message"
                                          rows="4"
                                          class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm font-mono resize-y"
                                          placeholder="命令通知：{{source_command_name}}&#10;{{command_result_summary}}"></textarea>
                            </div>

                            <div class="mt-3 flex flex-wrap items-center gap-4">
                                <div>
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">超时（秒）</label>
                                    <input v-model.number="action.timeout" type="number" min="1" step="1"
                                           class="w-24 px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm">
                                </div>
                                <label class="flex items-center gap-2 cursor-pointer text-sm dark:text-gray-300 pt-5">
                                    <input type="checkbox" v-model="action.raise_for_status" class="rounded">
                                    HTTP 非 2xx 视为失败
                                </label>
                            </div>

                            <p class="mt-2 text-xs text-cyan-700 dark:text-cyan-300">
                                常用变量：<span v-pre>{{source_command_name}}</span>、<span v-pre>{{command_result_summary}}</span>、<span v-pre>{{command_result}}</span>、<span v-pre>{{domain}}</span>、<span v-pre>{{network_url}}</span>
                            </p>
                        </div>

                        <div v-for="(action, i) in editingCommand.actions.filter(a => a.type === 'release_tab_lock')"
                             :key="'unlock-' + i"
                             class="mt-4 p-4 bg-amber-50 dark:bg-amber-900/20 rounded-lg border border-amber-200 dark:border-amber-800">
                            <h5 class="text-sm font-semibold text-amber-800 dark:text-amber-300 mb-3">🔓 解锁配置</h5>

                            <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                                <div class="md:col-span-2">
                                    <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">原因标记</label>
                                    <input v-model.trim="action.reason" type="text"
                                           placeholder="release_tab_lock_action"
                                           class="w-full px-2 py-1.5 border dark:border-gray-600 rounded bg-white dark:bg-gray-700 dark:text-white text-sm font-mono">
                                </div>
                                <div class="flex items-center pt-5">
                                    <label class="flex items-center gap-2 cursor-pointer text-sm dark:text-gray-300">
                                        <input type="checkbox" v-model="action.clear_page" class="rounded">
                                        释放后重置为空白页                                    </label>
                                </div>
                            </div>

                            <div class="mt-3">
                                <label class="flex items-center gap-2 cursor-pointer text-sm dark:text-gray-300">
                                    <input type="checkbox" v-model="action.stop_actions" class="rounded">
                                    执行后中断后续动作                                </label>
                            </div>
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
`;
