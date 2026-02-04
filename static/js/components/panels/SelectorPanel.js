// ==================== 选择器管理面板 ====================

window.SelectorPanel = {
    name: 'SelectorPanel',
    props: {
        selectors: { type: Object, required: true },
        collapsed: { type: Boolean, default: false }
    },
    emits: [
        'update:collapsed',
        'add-selector',
        'remove-selector',
        'update-selector-key',
        'update-selector-value',
        'test-selector'
    ],
    data() {
        return {
            showMenu: false
        };
    },
    computed: {
        count() {
            return Object.keys(this.selectors).length;
        }
    },
    methods: {
        toggle() {
            this.$emit('update:collapsed', !this.collapsed);
        },

        toggleMenu(e) {
            e.stopPropagation();
            this.showMenu = !this.showMenu;
        },

        addSelector(type) {
            this.showMenu = false;
            this.$emit('add-selector', type);
        },

        closeMenu() {
            this.showMenu = false;
        }
    },
    template: `
        <div class="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-sm" @click="closeMenu">
            <!-- 标题栏 -->
            <div class="px-4 py-3 border-b dark:border-gray-700 flex justify-between items-center cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                 @click="toggle">
                <div class="flex items-center gap-2">
                    <span class="text-gray-500 dark:text-gray-400" v-html="collapsed ? $icons.chevronDown : $icons.chevronUp"></span>
                    <h3 class="font-semibold text-gray-900 dark:text-white">🏷️ 选择器</h3>
                    <span class="text-sm text-gray-500 dark:text-gray-400">({{ count }})</span>
                </div>
                
                <div class="relative" @click.stop>
                    <button @click="toggleMenu"
                            class="border rounded-md transition-colors bg-blue-500 text-white hover:bg-blue-600 border-blue-500 px-3 py-1 text-sm font-medium flex items-center gap-1">
                        <span v-html="$icons.plusCircle"></span> 新增 <span v-html="$icons.chevronDown"></span>
                    </button>
                    
                    <div v-if="showMenu"
                         class="absolute right-0 mt-1 w-56 bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-lg z-10 overflow-hidden">
                        <button @click="addSelector('custom')"
                                class="w-full text-left px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-700 text-gray-900 dark:text-white text-sm border-b dark:border-gray-700 transition-colors">
                            自定义字段
                        </button>
                        <div class="px-3 py-1.5 text-xs text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/50 font-medium">
                            辅助字段
                        </div>
                        <button @click="addSelector('message_wrapper')"
                                class="w-full text-left px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-700 text-xs transition-colors">
                            <div class="font-semibold text-gray-900 dark:text-white">message_wrapper</div>
                            <div class="text-gray-500 dark:text-gray-400">消息完整容器（用于锚点定位）</div>
                        </button>
                        <button @click="addSelector('generating_indicator')"
                                class="w-full text-left px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-700 text-xs transition-colors">
                            <div class="font-semibold text-gray-900 dark:text-white">generating_indicator</div>
                            <div class="text-gray-500 dark:text-gray-400">生成中指示器（检测是否结束）</div>
                        </button>
                    </div>
                </div>
            </div>

            <!-- 内容 -->
            <div v-show="!collapsed" class="p-4 space-y-3 max-h-96 overflow-auto">
                <div v-for="(val, key) in selectors"
                     :key="key"
                     class="p-3 border dark:border-gray-700 rounded-lg hover:border-blue-300 dark:hover:border-blue-600 transition-colors bg-gray-50/50 dark:bg-gray-900/30">
                    <input :value="key"
                           @blur="$emit('update-selector-key', key, $event.target.value)"
                           class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent font-semibold w-full mb-2 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                           placeholder="键名">
                    <input :value="selectors[key]"
                           @input="$emit('update-selector-value', key, $event.target.value)"
                           class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-full bg-white dark:bg-gray-800 text-sm font-mono text-gray-700 dark:text-gray-300"
                           placeholder="CSS 选择器">
                    <div class="mt-2 flex justify-between">
                        <button @click="$emit('test-selector', key, val)"
                                class="px-3 py-1 rounded-md text-xs font-medium transition-all duration-150 text-blue-600 dark:text-blue-400 border border-blue-300 dark:border-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/30 active:scale-95">
                            测试
                        </button>
                        <button @click="$emit('remove-selector', key)"
                                class="p-1.5 rounded-md transition-all duration-150 text-gray-500 dark:text-gray-400 hover:text-red-600 dark:hover:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/40 active:scale-95"
                                title="删除选择器">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0"/>
                            </svg>
                        </button>
                    </div>
                </div>
                
                <div v-if="count === 0" class="text-center text-gray-400 dark:text-gray-500 text-sm py-8">
                    暂无选择器，点击新增添加
                </div>
            </div>
        </div>
    `
};