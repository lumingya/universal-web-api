// ==================== 工作流管理面板 ====================

window.WorkflowPanel = {
    name: 'WorkflowPanel',
    props: {
        workflow: { type: Array, required: true },
        selectors: { type: Object, required: true },
        currentDomain: { type: String, default: null },
        collapsed: { type: Boolean, default: false }
    },
    emits: ['update:collapsed', 'add-step', 'remove-step', 'move-step', 'action-change', 'show-templates'],
    data() {
        return {
            editorInjecting: false
        };
    },
    methods: {
        toggle() {
            this.$emit('update:collapsed', !this.collapsed);
        },

        async launchVisualEditor() {
            if (this.editorInjecting) return;
            this.editorInjecting = true;
            try {
                const response = await fetch('/api/workflow-editor/inject', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ target_domain: this.currentDomain })
                });
                const result = await response.json();
                if (result.success) {
                    alert(result.already_existed
                        ? '✅ 编辑器已激活，请切换到浏览器窗口查看'
                        : '✅ 编辑器已注入！请切换到浏览器窗口，使用右下角工具栏添加步骤');
                } else {
                    alert('❌ ' + (result.message || '注入失败'));
                }
            } catch (e) {
                alert('❌ 网络错误: ' + e.message);
            } finally {
                this.editorInjecting = false;
            }
        }
    },
    template: `
        <div class="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-sm">
            <!-- 标题栏 -->
            <div class="px-4 py-3 border-b dark:border-gray-700 flex justify-between items-center cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                 @click="toggle">
                <div class="flex items-center gap-2">
                    <span class="text-gray-500 dark:text-gray-400" v-html="collapsed ? $icons.chevronDown : $icons.chevronUp"></span>
                    <h3 class="font-semibold text-gray-900 dark:text-white">⚙️ 工作流</h3>
                    <span class="text-sm text-gray-500 dark:text-gray-400">({{ workflow.length }} 步)</span>
                </div>
                
                <div class="flex gap-2" @click.stop>
                    <button @click="launchVisualEditor" :disabled="editorInjecting"
                            :class="['px-3 py-1 rounded-md text-sm font-medium transition-colors flex items-center gap-1',
                                     editorInjecting ? 'bg-gray-300 dark:bg-gray-600 text-gray-500 dark:text-gray-400 cursor-wait'
                                     : 'text-purple-700 dark:text-purple-300 border border-purple-400 dark:border-purple-500 hover:bg-purple-50 dark:hover:bg-purple-900/30']">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                            <circle cx="12" cy="12" r="3"/><path d="M12 2v4m0 12v4m10-10h-4M6 12H2m15.364-6.364l-2.828 2.828M9.464 14.536l-2.828 2.828m12.728 0l-2.828-2.828M9.464 9.464L6.636 6.636"/>
                        </svg>
                        {{ editorInjecting ? '注入中...' : '可视化' }}
                    </button>
                    <button @click="$emit('show-templates')"
                            class="px-3 py-1 rounded-md text-sm font-medium transition-colors text-gray-700 dark:text-gray-200 border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-1">
                        <span v-html="$icons.clipboardList"></span> 模板
                    </button>
                    <button @click="$emit('add-step')"
                            class="px-3 py-1 rounded-md text-sm font-medium transition-colors bg-blue-500 text-white hover:bg-blue-600 border border-blue-500 flex items-center gap-1">
                        <span v-html="$icons.plusCircle"></span> 新增步骤
                    </button>
                </div>
            </div>

            <!-- 内容 -->
            <div v-show="!collapsed" class="p-4 space-y-3 max-h-96 overflow-auto">
                <div v-for="(step, index) in workflow" :key="index"
                     class="border dark:border-gray-700 rounded-lg p-3 hover:border-blue-300 dark:hover:border-blue-600 transition-colors bg-gray-50/50 dark:bg-gray-900/30">
                    <div class="flex gap-3 items-start">
                        <!-- 序号和移动按钮 -->
                        <div class="flex flex-col items-center gap-0.5 pt-1">
                            <span class="text-xs font-bold text-gray-600 dark:text-gray-300 w-6 h-6 flex items-center justify-center bg-gray-200 dark:bg-gray-700 rounded-full">{{ index + 1 }}</span>
                            <div class="flex flex-col mt-1">
                                <button @click="$emit('move-step', index, -1)" :disabled="index === 0"
                                        :class="['p-1 rounded-md transition-all duration-150', index === 0 ? 'text-gray-300 dark:text-gray-600 cursor-not-allowed' : 'text-gray-600 dark:text-gray-300 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-900/40 active:scale-95']">
                                    <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5"/></svg>
                                </button>
                                <button @click="$emit('move-step', index, 1)" :disabled="index === workflow.length - 1"
                                        :class="['p-1 rounded-md transition-all duration-150', index === workflow.length - 1 ? 'text-gray-300 dark:text-gray-600 cursor-not-allowed' : 'text-gray-600 dark:text-gray-300 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-900/40 active:scale-95']">
                                    <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5"/></svg>
                                </button>
                            </div>
                        </div>

                        <!-- 动作选择 -->
                        <div class="w-32">
                            <label class="text-xs font-medium text-gray-500 dark:text-gray-400">动作</label>
                            <select v-model="step.action" @change="$emit('action-change', step)"
                                    class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-full text-sm mt-1 bg-white dark:bg-gray-700 text-gray-900 dark:text-white">
                                <option value="FILL_INPUT">填入内容</option>
                                <option value="CLICK">点击元素</option>
                                <option value="STREAM_WAIT">流式等待</option>
                                <option value="WAIT">等待</option>
                                <option value="KEY_PRESS">按键</option>
                            </select>
                        </div>

                        <!-- 目标/参数 -->
                        <div class="flex-1">
                            <label class="text-xs font-medium text-gray-500 dark:text-gray-400">
                                {{ ['FILL_INPUT', 'CLICK', 'STREAM_WAIT'].includes(step.action) ? '目标选择器' : '参数' }}
                            </label>
                            <select v-if="['FILL_INPUT', 'CLICK', 'STREAM_WAIT'].includes(step.action)" v-model="step.target"
                                    class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-full mt-1 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white">
                                <option value="" disabled>选择选择器...</option>
                                <option v-for="(v, k) in selectors" :key="k" :value="k">{{ k }} ({{ v || '未设置' }})</option>
                            </select>
                            <input v-else-if="step.action === 'KEY_PRESS'" v-model="step.target" placeholder="例如: Enter"
                                   class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-full mt-1 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white">
                            <div v-else-if="step.action === 'WAIT'" class="flex items-center gap-2 mt-1">
                                <input v-model.number="step.value" type="number" step="0.1" min="0"
                                       class="border dark:border-gray-600 px-2 py-1.5 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-24 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white">
                                <span class="text-sm text-gray-500 dark:text-gray-400">秒</span>
                            </div>
                        </div>

                        <!-- 可选标记 -->
                        <div class="pt-5">
                            <label class="flex items-center text-xs cursor-pointer whitespace-nowrap text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white transition-colors">
                                <input type="checkbox" v-model="step.optional" class="mr-1.5 rounded">
                                <span>可选</span>
                            </label>
                        </div>

                        <!-- 删除按钮 -->
                        <div class="pt-4">
                            <button @click="$emit('remove-step', index)"
                                    class="p-1.5 rounded-md transition-all duration-150 text-gray-500 dark:text-gray-400 hover:text-red-600 dark:hover:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/40 active:scale-95">
                                <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
                                </svg>
                            </button>
                        </div>
                    </div>
                </div>

                <div v-if="workflow.length === 0" class="text-center text-gray-400 dark:text-gray-500 text-sm py-8">
                    暂无工作流步骤，点击新增步骤或使用模板
                </div>
            </div>
        </div>
    `
};