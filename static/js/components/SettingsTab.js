// ==================== 设置 Tab 组件 (修复版) ====================
window.SettingsTab = {
    name: 'SettingsTab',
    props: {
        envConfig: { type: Object, required: true },
        envSchema: { type: Object, required: true },
        envCollapsed: { type: Object, required: true },
        envChanged: { type: Boolean, default: false },
        savingEnv: { type: Boolean, default: false },
        
        browserConstants: { type: Object, required: true },
        browserSchema: { type: Object, required: true },
        browserCollapsed: { type: Object, required: true },
        browserChanged: { type: Boolean, default: false },
        savingBrowser: { type: Boolean, default: false },

        updatePreserveOptions: { type: Array, required: true },
        updatePreserveSelected: { type: Array, required: true },
        updatePreserveChanged: { type: Boolean, default: false },
        savingUpdatePreserve: { type: Boolean, default: false },
        
        selectorDefinitions: { type: Array, required: true },
        definitionsChanged: { type: Boolean, default: false },
        savingDefinitions: { type: Boolean, default: false },

        releases: { type: Array, required: true },
        releasesLoading: { type: Boolean, default: false },
        releasesError: { type: String, default: '' },
        releasesCurrentVersion: { type: String, default: '' },
        switchingTag: { type: String, default: null }
    },
    emits: [
        'save-env', 'reset-env', 'toggle-env-group',
        'save-browser', 'reset-browser', 'toggle-browser-group',
        'save-update-preserve', 'reset-update-preserve', 'toggle-update-preserve',
        'save-definitions', 'reset-definitions',
        'add-definition', 'edit-definition', 'remove-definition', 
        'toggle-definition', 'move-definition',
        'load-releases', 'switch-to-version', 'show-changelog'
    ],
    data() {
        return {
            selectorDefsCollapsed: true,
            updatePreserveCollapsed: true,
            versionCollapsed: false
        };
    },
    computed: {
        updatePreserveGroups() {
            const groups = {}
            for (const item of this.updatePreserveOptions || []) {
                const key = item.category || '其他'
                if (!groups[key]) groups[key] = []
                groups[key].push(item)
            }
            return groups
        }
    },
    methods: {
        getEnvApplyScope(field, group) {
            return field.apply || group.apply || 'service'
        },
        getEnvApplyLabel(field, group) {
            const scope = this.getEnvApplyScope(field, group)
            if (scope === 'launcher') return '需重启 start.bat'
            return '保存后服务重启生效'
        },
        getEnvApplyClass(field, group) {
            const scope = this.getEnvApplyScope(field, group)
            if (scope === 'launcher') {
                return 'bg-amber-50 text-amber-700 border border-amber-200 dark:bg-amber-900/20 dark:text-amber-300 dark:border-amber-800'
            }
            return 'bg-emerald-50 text-emerald-700 border border-emerald-200 dark:bg-emerald-900/20 dark:text-emerald-300 dark:border-emerald-800'
        },
        formatReleaseDate(isoStr) {
            if (!isoStr) return '—';
            try {
                var d = new Date(isoStr);
                var pad = function(n) { return String(n).padStart(2, '0'); };
                return d.getFullYear() + '/' + pad(d.getMonth()+1) + '/' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
            } catch (e) {
                return isoStr;
            }
        }
    },
    template: `
        <div class="settings-workspace h-full overflow-auto p-4 md:p-6 bg-gray-50 dark:bg-gray-900">
            <div class="max-w-7xl mx-auto space-y-6">
                
                <!-- ========== AI 元素识别 - 放在最上面 ========== -->
                <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
                    <div class="p-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center cursor-pointer"
                         @click="selectorDefsCollapsed = !selectorDefsCollapsed">
                        <div>
                            <h3 class="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                <span class="text-xl">🎯</span> AI 元素识别
                            </h3>
                            <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">配置 AI 分析页面时需要查找的目标元素</p>
                        </div>
                        <div class="flex gap-2 items-center">
                            <span class="text-xs px-2 py-1 bg-gray-100 dark:bg-gray-700 rounded-full text-gray-600 dark:text-gray-300">{{ selectorDefinitions.length }} 个定义</span>
                            <div class="h-6 w-px bg-gray-200 dark:bg-gray-600 mx-2"></div>
                            <button @click.stop="$emit('reset-definitions')" title="重置"
                                    class="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors">
                                <span v-html="$icons.arrowPath"></span>
                            </button>
                            <button @click.stop="$emit('save-definitions')"
                                    :disabled="savingDefinitions || !definitionsChanged"
                                    :class="['px-3 py-1.5 text-sm font-medium text-white rounded-lg transition-colors flex items-center gap-1 shadow-sm',
                                             savingDefinitions || !definitionsChanged
                                             ? 'bg-blue-400 cursor-not-allowed opacity-60'
                                             : 'bg-blue-600 hover:bg-blue-700']">
                                <span v-if="!savingDefinitions" v-html="$icons.arrowDownTray" class="w-4 h-4"></span>
                                {{ savingDefinitions ? '...' : '保存' }}
                            </button>
                            <button class="p-1.5 ml-2 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded transition-colors"
                                    v-html="selectorDefsCollapsed ? $icons.chevronDown : $icons.chevronUp">
                            </button>
                        </div>
                    </div>

                    <div v-show="!selectorDefsCollapsed" class="p-0">
                        <!-- 表头 -->
                        <div class="grid grid-cols-12 gap-4 px-6 py-3 bg-gray-50 dark:bg-gray-900/50 text-xs font-semibold text-gray-600 dark:text-gray-300 border-b border-gray-200 dark:border-gray-700">
                            <div class="col-span-1 text-center">排序</div>
                            <div class="col-span-3 md:col-span-2">关键词 (Key)</div>
                            <div class="col-span-6 md:col-span-7">描述 (Description)</div>
                            <div class="col-span-1 text-center">启用</div>
                            <div class="col-span-1 text-center">操作</div>
                        </div>

                        <!-- 列表 -->
                        <div class="divide-y divide-gray-100 dark:divide-gray-700">
                            <div v-for="(def, index) in selectorDefinitions" :key="def.key" 
                                 class="grid grid-cols-12 gap-4 px-6 py-3 items-center hover:bg-gray-50 dark:hover:bg-gray-700/30 transition-colors">
                                
                                <!-- 排序按钮 - 优化日夜模式显示 -->
                                <div class="col-span-1 flex flex-col items-center gap-0.5">
                                    <button @click.stop="$emit('move-definition', index, -1)" 
                                            :disabled="index === 0" 
                                            :class="['p-1 rounded-md transition-all duration-150', 
                                                     index === 0 
                                                     ? 'text-gray-300 dark:text-gray-600 cursor-not-allowed' 
                                                     : 'text-gray-600 dark:text-gray-300 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-900/40 active:scale-95']"
                                            title="上移">
                                        <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5"/>
                                        </svg>
                                    </button>
                                    <button @click.stop="$emit('move-definition', index, 1)" 
                                            :disabled="index === selectorDefinitions.length - 1" 
                                            :class="['p-1 rounded-md transition-all duration-150', 
                                                     index === selectorDefinitions.length - 1 
                                                     ? 'text-gray-300 dark:text-gray-600 cursor-not-allowed' 
                                                     : 'text-gray-600 dark:text-gray-300 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-900/40 active:scale-95']"
                                            title="下移">
                                        <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5"/>
                                        </svg>
                                    </button>
                                </div>

                                <!-- Key -->
                                <div class="col-span-3 md:col-span-2 flex items-center gap-2 flex-wrap">
                                    <code class="px-2 py-1 bg-blue-50 dark:bg-blue-900/30 border border-blue-200 dark:border-blue-700 rounded text-xs font-mono text-blue-700 dark:text-blue-300">
                                        {{ def.key }}
                                    </code>
                                    <span v-if="def.required" class="text-[10px] text-red-600 dark:text-red-400 border border-red-300 dark:border-red-700 px-1.5 py-0.5 rounded bg-red-50 dark:bg-red-900/30 font-medium">必需</span>
                                </div>

                                <!-- Description -->
                                <div class="col-span-6 md:col-span-7 text-sm text-gray-700 dark:text-gray-200 truncate" :title="def.description">
                                    {{ def.description }}
                                </div>

                                <!-- 启用开关 -->
                                <div class="col-span-1 flex justify-center">
                                    <label class="toggle-label scale-90">
                                        <input type="checkbox" :checked="def.enabled" 
                                               @change="$emit('toggle-definition', index)" 
                                               :disabled="def.required" class="sr-only peer">
                                        <div class="toggle-bg" :class="{'opacity-50 cursor-not-allowed': def.required}"></div>
                                    </label>
                                </div>

                                <!-- 操作按钮 - 优化删除按钮日夜模式显示 -->
                                <div class="col-span-1 flex justify-center gap-1">
                                    <button @click.stop="$emit('edit-definition', index)" 
                                            class="p-1.5 rounded-md transition-all duration-150 text-gray-600 dark:text-gray-300 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-900/40 active:scale-95" 
                                            title="编辑">
                                        <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125"/>
                                        </svg>
                                    </button>
                                    <button @click.stop="$emit('remove-definition', index)" 
                                            :disabled="def.required" 
                                            :class="['p-1.5 rounded-md transition-all duration-150', 
                                                     def.required 
                                                     ? 'text-gray-300 dark:text-gray-600 cursor-not-allowed' 
                                                     : 'text-gray-600 dark:text-gray-300 hover:text-red-600 dark:hover:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/40 active:scale-95']" 
                                            title="删除">
                                        <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0"/>
                                        </svg>
                                    </button>
                                </div>
                            </div>
                        </div>

                        <!-- 添加按钮 -->
                        <div class="p-4 bg-gray-50 dark:bg-gray-800/80 border-t border-gray-200 dark:border-gray-700 text-center">
                            <button @click="$emit('add-definition')"
                                    class="text-sm text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 font-medium py-2 px-5 border-2 border-dashed border-blue-300 dark:border-blue-600 rounded-lg hover:bg-blue-50 dark:hover:bg-blue-900/30 transition-all duration-150 inline-flex items-center gap-2 active:scale-95">
                                <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v6m3-3H9m12 0a9 9 0 11-18 0 9 9 0 0118 0z"/>
                                </svg>
                                添加新定义
                            </button>
                        </div>
                    </div>
                </div>
                

                <!-- ========== 版本管理 ========== -->
                <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
                    <div class="p-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center cursor-pointer"
                         @click="versionCollapsed = !versionCollapsed">
                        <div>
                            <h3 class="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                <span class="text-xl">🔄</span> 版本管理
                            </h3>
                            <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">手动选择 GitHub 发布的版本进行切换/更新</p>
                        </div>
                        <div class="flex gap-2 items-center">
                            <span class="text-xs px-2 py-1 bg-blue-50 dark:bg-blue-900/30 border border-blue-200 dark:border-blue-700 rounded-full text-blue-700 dark:text-blue-300 font-mono font-medium">当前版本: v{{ releasesCurrentVersion || '加载中...' }}</span>
                            <div class="h-6 w-px bg-gray-200 dark:bg-gray-600 mx-2"></div>
                            <button @click.stop="$emit('load-releases')" title="刷新版本列表"
                                    :disabled="releasesLoading"
                                    class="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors flex items-center justify-center">
                                <svg :class="['w-4 h-4', releasesLoading ? 'animate-spin' : '']" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"/>
                                </svg>
                            </button>
                            <button class="p-1.5 ml-2 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded transition-colors"
                                    v-html="versionCollapsed ? $icons.chevronDown : $icons.chevronUp">
                            </button>
                        </div>
                    </div>

                    <div v-show="!versionCollapsed" class="p-0">
                        <div v-if="releasesLoading && !releases.length" class="p-8 text-center text-gray-500 dark:text-gray-400">
                            <div class="animate-spin inline-block w-6 h-6 border-[3px] border-current border-t-transparent text-blue-600 rounded-full mb-2" role="status" aria-label="loading"></div>
                            <div>正在加载版本列表...</div>
                        </div>
                        <div v-else-if="releasesError" class="p-8 text-center text-red-500">
                            {{ releasesError }}
                            <button @click="$emit('load-releases')" class="mt-2 block mx-auto px-3 py-1 bg-red-100 hover:bg-red-200 text-red-700 rounded text-xs">重试</button>
                        </div>
                        <div v-else-if="!releases.length" class="p-8 text-center text-gray-500 dark:text-gray-400">
                            暂无版本发布记录
                        </div>
                        <div v-else>
                            <!-- 表头 -->
                            <div class="grid grid-cols-12 gap-4 px-6 py-3 bg-gray-50 dark:bg-gray-900/50 text-xs font-semibold text-gray-600 dark:text-gray-300 border-b border-gray-200 dark:border-gray-700">
                                <div class="col-span-3">版本 (Tag)</div>
                                <div class="col-span-4">发布时间</div>
                                <div class="col-span-2 text-center">更新说明</div>
                                <div class="col-span-3 text-center">操作</div>
                            </div>

                            <!-- 列表 -->
                            <div class="divide-y divide-gray-100 dark:divide-gray-700">
                                <div v-for="rel in releases" :key="rel.tag" 
                                     class="grid grid-cols-12 gap-4 px-6 py-3.5 items-center hover:bg-gray-50 dark:hover:bg-gray-700/30 transition-colors">
                                    <div class="col-span-3 flex items-center gap-2">
                                        <span class="font-mono font-bold text-gray-900 dark:text-white">{{ rel.tag }}</span>
                                        <span v-if="rel.tag === 'v' + releasesCurrentVersion || rel.tag === releasesCurrentVersion || rel.is_current" class="px-2 py-0.5 text-[10px] text-green-700 bg-green-50 border border-green-200 rounded dark:bg-green-900/30 dark:text-green-300 dark:border-green-800">当前</span>
                                    </div>
                                    <div class="col-span-4 text-sm text-gray-500 dark:text-gray-400">
                                        {{ formatReleaseDate(rel.published_at) }}
                                    </div>
                                    <div class="col-span-2 text-center">
                                        <button @click="$emit('show-changelog', rel.tag, rel.body)"
                                                class="text-xs text-blue-600 dark:text-blue-400 hover:underline">
                                            查看说明
                                        </button>
                                    </div>
                                    <div class="col-span-3 text-center">
                                        <span v-if="rel.tag === 'v' + releasesCurrentVersion || rel.tag === releasesCurrentVersion || rel.is_current" 
                                              class="inline-block px-3 py-1 text-xs text-gray-400 bg-gray-100 dark:bg-gray-800 dark:text-gray-600 rounded-lg font-medium">
                                            已是当前版本
                                        </span>
                                        <button v-else-if="switchingTag === rel.tag" disabled
                                                class="px-3 py-1 text-xs text-white bg-blue-400 rounded-lg cursor-wait animate-pulse">
                                            切换中...
                                        </button>
                                        <button v-else @click="$emit('switch-to-version', rel.tag)"
                                                :disabled="!!switchingTag"
                                                :class="['px-3 py-1 text-xs font-medium rounded-lg transition-colors',
                                                         switchingTag ? 'bg-gray-100 text-gray-400 dark:bg-gray-800 dark:text-gray-600 cursor-not-allowed' : 'bg-blue-600 text-white hover:bg-blue-700 shadow-sm']">
                                            切换到此版本
                                        </button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- ========== 环境配置 和 浏览器常量 ========== -->
                <div class="grid grid-cols-1 xl:grid-cols-2 gap-6">
                    
                    <!-- 环境配置 -->
                    <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 flex flex-col">
                        <div class="p-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-start">
                            <div>
                                <h3 class="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                    <span class="text-xl" v-html="$icons.folderOpen"></span> 环境配置
                                </h3>
                                <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">每个字段都会标明是服务重启生效，还是需要重新运行 start.bat</p>
                            </div>
                            <div class="flex gap-2">
                                <button @click="$emit('reset-env')" title="重置"
                                        class="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors">
                                    <span v-html="$icons.arrowPath"></span>
                                </button>
                                <button @click="$emit('save-env')"
                                        :disabled="savingEnv || !envChanged"
                                        :class="['px-3 py-1.5 text-sm font-medium text-white rounded-lg transition-colors flex items-center gap-1 shadow-sm',
                                                 savingEnv || !envChanged
                                                 ? 'bg-blue-400 cursor-not-allowed opacity-60'
                                                 : 'bg-blue-600 hover:bg-blue-700']">
                                    <span v-if="!savingEnv" v-html="$icons.arrowDownTray" class="w-4 h-4"></span>
                                    {{ savingEnv ? '...' : '保存' }}
                                </button>
                            </div>
                        </div>

                        <div class="p-2 space-y-2">
                            <div v-for="(group, groupKey) in envSchema" :key="groupKey" class="rounded-lg border border-gray-100 dark:border-gray-700/50 overflow-hidden">
                                <div class="px-4 py-2 bg-gray-50/50 dark:bg-gray-800/50 flex justify-between items-center cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700/50 transition-colors"
                                     @click="$emit('toggle-env-group', groupKey)">
                                    <div class="flex items-center gap-2 text-sm font-medium text-gray-700 dark:text-gray-200">
                                        <span class="opacity-70">{{ group.icon }}</span>
                                        <span>{{ group.label }}</span>
                                    </div>
                                    <span v-html="envCollapsed[groupKey] ? $icons.chevronDown : $icons.chevronUp" class="text-gray-400 w-4 h-4"></span>
                                </div>

                                <div v-show="!envCollapsed[groupKey]" class="px-4 py-4 space-y-5 bg-white dark:bg-gray-800">
                                    <div v-for="(field, fieldKey) in group.items" :key="fieldKey" class="grid grid-cols-1 gap-1">
                                        <div class="flex justify-between items-start gap-3">
                                            <div class="min-w-0 space-y-1">
                                                <label class="text-sm font-medium text-gray-700 dark:text-gray-300">
                                                    {{ field.label }}
                                                </label>
                                                <span :class="['inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium', getEnvApplyClass(field, group)]">
                                                    {{ getEnvApplyLabel(field, group) }}
                                                </span>
                                            </div>
                                            <span v-if="field.unit" class="text-xs text-gray-400 dark:text-gray-500 font-mono">
                                                {{ field.unit }}
                                            </span>
                                        </div>

                                        <div>
                                            <div v-if="field.type === 'switch'" class="flex items-center h-9">
                                                <label class="toggle-label">
                                                    <input type="checkbox" v-model="envConfig[fieldKey]" class="sr-only peer">
                                                    <div class="toggle-bg"></div>
                                                </label>
                                            </div>
                                            <select v-else-if="field.type === 'select'" v-model="envConfig[fieldKey]" 
                                                    class="settings-input w-full">
                                                <option v-for="opt in field.options"
                                                        :key="typeof opt === 'object' ? opt.value : opt"
                                                        :value="typeof opt === 'object' ? opt.value : opt">
                                                    {{ typeof opt === 'object' ? opt.label : opt }}
                                                </option>
                                            </select>
                                            <input v-else-if="field.type === 'number'" type="number"
                                                   v-model.number="envConfig[fieldKey]"
                                                   :min="field.min" :max="field.max" :step="field.step || 1"
                                                   class="settings-input w-full">
                                            <input v-else :type="field.type === 'password' ? 'password' : 'text'"
                                                   v-model="envConfig[fieldKey]"
                                                   :placeholder="field.default"
                                                   class="settings-input w-full">
                                        </div>

                                        <div v-if="field.desc" class="text-xs text-gray-400 dark:text-gray-500 flex items-start gap-1 mt-0.5">
                                            <span class="mt-0.5 opacity-70">ℹ️</span> 
                                            <span>{{ field.desc }}</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- 浏览器常量 -->
                    <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 flex flex-col">
                        <div class="p-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-start">
                            <div>
                                <h3 class="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                    <span class="text-xl">🌐</span> 浏览器常量
                                </h3>
                                <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">即时生效</p>
                            </div>
                            <div class="flex gap-2">
                                <button @click="$emit('reset-browser')" title="重置"
                                        class="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors">
                                    <span v-html="$icons.arrowPath"></span>
                                </button>
                                <button @click="$emit('save-browser')"
                                        :disabled="savingBrowser || !browserChanged"
                                        :class="['px-3 py-1.5 text-sm font-medium text-white rounded-lg transition-colors flex items-center gap-1 shadow-sm',
                                                 savingBrowser || !browserChanged
                                                 ? 'bg-blue-400 cursor-not-allowed opacity-60'
                                                 : 'bg-blue-600 hover:bg-blue-700']">
                                    <span v-if="!savingBrowser" v-html="$icons.arrowDownTray" class="w-4 h-4"></span>
                                    {{ savingBrowser ? '...' : '保存' }}
                                </button>
                            </div>
                        </div>

                        <div class="p-2 space-y-2">
                            <div v-for="(group, groupKey) in browserSchema" :key="groupKey" class="rounded-lg border border-gray-100 dark:border-gray-700/50 overflow-hidden">
                                <div class="px-4 py-2 bg-gray-50/50 dark:bg-gray-800/50 flex justify-between items-center cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700/50 transition-colors"
                                     @click="$emit('toggle-browser-group', groupKey)">
                                    <div class="flex items-center gap-2 text-sm font-medium text-gray-700 dark:text-gray-200">
                                        <span class="opacity-70">{{ group.icon }}</span>
                                        <span>{{ group.label }}</span>
                                    </div>
                                    <span v-html="browserCollapsed[groupKey] ? $icons.chevronDown : $icons.chevronUp" class="text-gray-400 w-4 h-4"></span>
                                </div>

                                <div v-show="!browserCollapsed[groupKey]" class="px-4 py-4 space-y-5 bg-white dark:bg-gray-800">
                                    <div v-for="(field, fieldKey) in group.items" :key="fieldKey" class="grid grid-cols-1 gap-1">
                                        <div class="flex justify-between">
                                            <label class="text-sm font-medium text-gray-700 dark:text-gray-300">
                                                {{ field.label }}
                                            </label>
                                            <span v-if="field.unit" class="text-xs text-gray-400 dark:text-gray-500 font-mono">
                                                {{ field.unit }}
                                            </span>
                                        </div>
                                        
                                        <div>
                                            <div v-if="field.type === 'switch'" class="flex items-center h-9">
                                                <label class="toggle-label">
                                                    <input type="checkbox" v-model="browserConstants[fieldKey]" class="sr-only peer">
                                                    <div class="toggle-bg"></div>
                                                </label>
                                            </div>
                                            <select v-else-if="field.type === 'select'" v-model="browserConstants[fieldKey]"
                                                    class="settings-input w-full">
                                                <option v-for="opt in field.options" :key="opt" :value="opt">{{ opt }}</option>
                                            </select>
                                            <input v-else-if="field.type === 'number'" type="number"
                                                   v-model.number="browserConstants[fieldKey]"
                                                   :min="field.min" :max="field.max" :step="field.step || 1"
                                                   class="settings-input w-full">
                                            <input v-else :type="field.type === 'password' ? 'password' : 'text'"
                                                   v-model="browserConstants[fieldKey]"
                                                   :placeholder="field.default"
                                                   class="settings-input w-full">
                                        </div>

                                        <div v-if="field.desc" class="text-xs text-gray-400 dark:text-gray-500 flex items-start gap-1 mt-0.5">
                                            <span class="mt-0.5 opacity-70">ℹ️</span>
                                            <span>{{ field.desc }}</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
                    <div class="p-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center cursor-pointer"
                         @click="updatePreserveCollapsed = !updatePreserveCollapsed">
                        <div>
                            <h3 class="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                <span class="text-xl">🛠️</span> 更新白名单
                            </h3>
                            <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">勾选后，下次自动更新会原样保留对应文件或目录</p>
                        </div>
                        <div class="flex gap-2 items-center">
                            <button @click.stop="$emit('reset-update-preserve')" title="重置"
                                    class="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors">
                                <span v-html="$icons.arrowPath"></span>
                            </button>
                            <button @click.stop="$emit('save-update-preserve')"
                                    :disabled="savingUpdatePreserve || !updatePreserveChanged"
                                    :class="['px-3 py-1.5 text-sm font-medium text-white rounded-lg transition-colors flex items-center gap-1 shadow-sm',
                                             savingUpdatePreserve || !updatePreserveChanged
                                             ? 'bg-blue-400 cursor-not-allowed opacity-60'
                                             : 'bg-blue-600 hover:bg-blue-700']">
                                <span v-if="!savingUpdatePreserve" v-html="$icons.arrowDownTray" class="w-4 h-4"></span>
                                {{ savingUpdatePreserve ? '...' : '保存' }}
                            </button>
                            <button class="p-1.5 ml-2 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded transition-colors"
                                    v-html="updatePreserveCollapsed ? $icons.chevronDown : $icons.chevronUp">
                            </button>
                        </div>
                    </div>

                    <div v-show="!updatePreserveCollapsed" class="p-4 space-y-4">
                        <div class="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-900/20 dark:text-amber-300">
                            这里控制的是“更新时是否保留不变”，不是运行时是否启用。目录项会保留整个目录；未勾选的项目会在更新时按新版本内容覆盖或合并。
                        </div>

                        <div class="space-y-4">
                            <div v-for="(items, category) in updatePreserveGroups" :key="category" class="space-y-2">
                                <div class="text-xs font-semibold uppercase tracking-[0.14em] text-gray-400 dark:text-gray-500">{{ category }}</div>
                                <div class="grid gap-2 md:grid-cols-2">
                                    <label v-for="item in items" :key="item.id"
                                           class="flex items-start gap-3 rounded-xl border border-gray-200 bg-gray-50/70 px-3 py-3 text-sm text-gray-700 transition hover:bg-gray-100 dark:border-gray-700 dark:bg-gray-900/40 dark:text-gray-200 dark:hover:bg-gray-900/70">
                                        <input type="checkbox"
                                               :checked="updatePreserveSelected.includes(item.pattern)"
                                               @change="$emit('toggle-update-preserve', item.pattern)"
                                               class="mt-0.5 h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500">
                                        <span class="min-w-0">
                                            <span class="block font-medium">{{ item.label }}</span>
                                            <span class="mt-0.5 block text-xs text-gray-500 dark:text-gray-400">{{ item.description }}</span>
                                        </span>
                                    </label>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

            </div>
        </div>
    `
};
