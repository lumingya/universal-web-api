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
        
        selectorDefinitions: { type: Array, required: true },
        definitionsChanged: { type: Boolean, default: false },
        savingDefinitions: { type: Boolean, default: false }
    },
    emits: [
        'save-env', 'reset-env', 'toggle-env-group',
        'save-browser', 'reset-browser', 'toggle-browser-group',
        'save-definitions', 'reset-definitions',
        'add-definition', 'edit-definition', 'remove-definition', 
        'toggle-definition', 'move-definition'
    ],
    data() {
        return {
            selectorDefsCollapsed: false
        };
    },
    template: `
        <div class="h-full overflow-auto p-4 md:p-6 bg-gray-50 dark:bg-gray-900">
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

                <!-- ========== 环境配置 和 浏览器常量 ========== -->
                <div class="grid grid-cols-1 xl:grid-cols-2 gap-6">
                    
                    <!-- 环境配置 -->
                    <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 flex flex-col">
                        <div class="p-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-start">
                            <div>
                                <h3 class="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                    <span class="text-xl" v-html="$icons.folderOpen"></span> 环境配置
                                </h3>
                                <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">需重启服务生效</p>
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
                                                    <input type="checkbox" v-model="envConfig[fieldKey]" class="sr-only peer">
                                                    <div class="toggle-bg"></div>
                                                </label>
                                            </div>
                                            <select v-else-if="field.type === 'select'" v-model="envConfig[fieldKey]" 
                                                    class="settings-input w-full">
                                                <option v-for="opt in field.options" :key="opt" :value="opt">{{ opt }}</option>
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

            </div>
        </div>
    `
};