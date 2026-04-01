// ==================== 选择器管理面板 ====================

window.SelectorPanel = {
    name: 'SelectorPanel',
    props: {
        selectors: { type: Object, required: true },
        collapsed: { type: Boolean, default: true }
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
            showMenu: false,
            guideExpanded: false,
            openRenameKey: '',
            renameDrafts: {},
            coreSelectorKeys: ['input_box', 'send_btn', 'result_container']
        };
    },
    computed: {
        count() {
            return Object.keys(this.selectors || {}).length;
        },

        selectorEntries() {
            return Object.entries(this.selectors || {});
        },

        coreChecklist() {
            return this.coreSelectorKeys.map(key => ({
                key,
                ...this.getSelectorMeta(key),
                ready: this.isSelectorFilled(key)
            }));
        },

        coreReadyCount() {
            return this.coreChecklist.filter(item => item.ready).length;
        },

        missingQuickAdds() {
            return this.coreChecklist.filter(item => !Object.prototype.hasOwnProperty.call(this.selectors || {}, item.key));
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
        },

        openInNewTab(url) {
            const target = String(url || '').trim();
            if (!target) {
                return;
            }
            window.open(target, '_blank', 'noopener,noreferrer');
        },

        openTutorial(anchor = 'selector-basics') {
            this.openInNewTab('/static/tutorial.html#' + encodeURIComponent(anchor));
        },

        openPracticeLab() {
            this.openInNewTab('/static/selector-practice.html');
        },

        isSelectorFilled(key) {
            return String((this.selectors || {})[key] || '').trim().length > 0;
        },

        isBuiltInSelectorKey(key) {
            return [
                'input_box',
                'send_btn',
                'result_container',
                'new_chat_btn',
                'message_wrapper',
                'generating_indicator',
                'upload_btn',
                'file_input',
                'drop_zone'
            ].includes(String(key || '').trim());
        },

        canRenameSelectorKey(key) {
            return !this.isBuiltInSelectorKey(key);
        },

        openRenameEditor(key) {
            this.openRenameKey = key;
            this.renameDrafts = {
                ...this.renameDrafts,
                [key]: key
            };

            this.$nextTick(() => {
                const ref = this.$refs['renameInput-' + key];
                const input = Array.isArray(ref) ? ref[0] : ref;
                if (input && typeof input.focus === 'function') {
                    input.focus();
                    if (typeof input.select === 'function') {
                        input.select();
                    }
                }
            });
        },

        closeRenameEditor() {
            this.openRenameKey = '';
        },

        submitRenameKey(key) {
            const nextKey = String((this.renameDrafts || {})[key] || '').trim();
            this.openRenameKey = '';
            if (!nextKey || nextKey === key) {
                return;
            }
            this.$emit('update-selector-key', key, nextKey);
        },

        getSelectorMeta(key) {
            const metaMap = {
                input_box: {
                    title: '输入框',
                    description: '用户真正打字的地方。常见是 textarea、contenteditable 或 role=textbox。',
                    hint: '先右键输入区域点“检查”。看到 textarea，或者看到可编辑的 div，通常就离答案很近了。',
                    required: true,
                    chip: '必填',
                    placeholder: '例如：textarea, div[contenteditable="true"]'
                },
                send_btn: {
                    title: '发送按钮',
                    description: '点一下就会把消息发出去的按钮，常见是纸飞机、箭头或“发送”字样。',
                    hint: '优先找 button 上比较稳的属性，比如 type、aria-label、data-testid；只靠一串随机 class 往往不稳。',
                    required: true,
                    chip: '必填',
                    placeholder: '例如：button[type="submit"], button[data-testid="send"]'
                },
                result_container: {
                    title: '回复容器',
                    description: '包住 AI 回复正文的区域。尽量选整段回复的外层，不要只选某个 p 或 span。',
                    hint: '看到整条 AI 回复后，先选能完整包住这条内容的外层块。范围太小，后面很容易漏字、漏代码块。',
                    required: true,
                    chip: '必填',
                    placeholder: '例如：main article .markdown-body'
                },
                new_chat_btn: {
                    title: '新建对话按钮',
                    description: '每次开始新会话前需要点的按钮，没有也可以先留空。',
                    hint: '如果页面左上角有“新聊天”“新建对话”“清空上下文”之类的入口，这个字段通常就该填它。',
                    required: false,
                    chip: '选填',
                    placeholder: '例如：button[aria-label*="新"], button.new-chat'
                },
                message_wrapper: {
                    title: '消息外层容器',
                    description: '一条消息最外层的盒子。长回复、分段节点、复杂样式场景里很有帮助。',
                    hint: '如果一条回复里有很多段落、代码块、图片，给它补上最外层容器，提取结果通常会稳很多。',
                    required: false,
                    chip: '辅助',
                    placeholder: '例如：article[data-role="assistant-message"]'
                },
                generating_indicator: {
                    title: '生成中指示器',
                    description: '能表明“模型还在继续输出”的元素，比如停止按钮、转圈、typing 提示。',
                    hint: '有“停止生成”按钮时，这个字段通常很好找。它出现时代表还在生成，消失后通常代表结束。',
                    required: false,
                    chip: '辅助',
                    placeholder: '例如：button[aria-label*="停止"], .typing-indicator'
                },
                upload_btn: {
                    title: '上传按钮',
                    description: '点一下会弹出上传面板或系统文件选择窗口的按钮。',
                    hint: '页面里有加号、回形针、图片图标时，先看那个按钮。很多站点把上传入口藏在工具栏里。',
                    required: false,
                    chip: '上传',
                    placeholder: '例如：button[aria-label*="上传"], .toolbar-upload'
                },
                file_input: {
                    title: '原生文件输入框',
                    description: '真正的 input[type=file] 元素，适合直接写入文件。',
                    hint: '很多站点会把它藏起来。开发者工具里搜索 type="file" 往往最快。',
                    required: false,
                    chip: '上传',
                    placeholder: '例如：input[type="file"]'
                },
                drop_zone: {
                    title: '拖拽上传区域',
                    description: '支持把文件拖到页面里的区域，适合不吃粘贴的网站。',
                    hint: '拖一张图到页面上时，哪个区域会高亮、变色、出现提示，那个区域通常就是它。',
                    required: false,
                    chip: '上传',
                    placeholder: '例如：div[data-dropzone="true"]'
                }
            };

            const presetMeta = metaMap[key];
            if (presetMeta) {
                return presetMeta;
            }

            return {
                title: '自定义字段',
                description: '你自己补的额外选择器，常用于临时按钮、模式切换、弹窗确认之类的操作。',
                hint: '自定义字段也建议优先用稳定属性。页面每次刷新都会变的随机 class，后面维护起来会很痛苦。',
                required: false,
                chip: '自定义',
                placeholder: '例如：button[data-role="mode-switch"]'
            };
        },

        getChipClass(key) {
            const chip = this.getSelectorMeta(key).chip;
            if (chip === '必填') {
                return 'dashboard-field-chip';
            }
            if (chip === '自定义') {
                return 'dashboard-field-chip dashboard-field-chip--custom';
            }
            return 'dashboard-field-chip dashboard-field-chip--optional';
        }
    },
    template: `
        <div class="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-sm" @click="closeMenu">
            <!-- 标题栏 -->
            <div class="px-4 py-3 border-b dark:border-gray-700 flex justify-between items-center cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                 @click="toggle">
                <div class="flex items-center gap-2">
                    <span class="w-4 inline-flex justify-center text-gray-500 dark:text-gray-400" v-html="collapsed ? $icons.chevronDown : $icons.chevronUp"></span>
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
                            <div class="text-gray-500 dark:text-gray-400">消息完整容器，适合多节点回复</div>
                        </button>
                        <button @click="addSelector('generating_indicator')"
                                class="w-full text-left px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-700 text-xs transition-colors">
                            <div class="font-semibold text-gray-900 dark:text-white">generating_indicator</div>
                            <div class="text-gray-500 dark:text-gray-400">生成中提示，适合辅助判断结束</div>
                        </button>
                        <button @click="addSelector('upload_btn')"
                                class="w-full text-left px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-700 text-xs transition-colors">
                            <div class="font-semibold text-gray-900 dark:text-white">upload_btn</div>
                            <div class="text-gray-500 dark:text-gray-400">打开上传入口的按钮</div>
                        </button>
                        <button @click="addSelector('file_input')"
                                class="w-full text-left px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-700 text-xs transition-colors">
                            <div class="font-semibold text-gray-900 dark:text-white">file_input</div>
                            <div class="text-gray-500 dark:text-gray-400">原生 input[type=file]</div>
                        </button>
                        <button @click="addSelector('drop_zone')"
                                class="w-full text-left px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-700 text-xs transition-colors">
                            <div class="font-semibold text-gray-900 dark:text-white">drop_zone</div>
                            <div class="text-gray-500 dark:text-gray-400">支持拖拽上传的区域</div>
                        </button>
                    </div>
                </div>
            </div>

            <!-- 内容 -->
            <div v-show="!collapsed" class="p-4 space-y-4 max-h-[44rem] overflow-auto">
                <div v-if="!guideExpanded">
                    <button @click="guideExpanded = true" type="button" class="dashboard-guide-toggle">
                        <span>新手引导</span>
                        <span v-html="$icons.chevronDown"></span>
                    </button>
                </div>

                <div v-else class="dashboard-guide-card dashboard-guide-card--blue">
                    <div class="flex items-center justify-between gap-3">
                        <span class="dashboard-guide-badge">零基础也能开始</span>
                        <button @click="guideExpanded = false" type="button" class="dashboard-guide-toggle">
                            <span>收起</span>
                            <span v-html="$icons.chevronUp"></span>
                        </button>
                    </div>
                    <div class="mt-3">
                        <div class="text-base font-semibold text-slate-900 dark:text-slate-50">
                            先把 3 个核心字段补齐，页面就能有基础可用性
                        </div>
                        <p class="mt-1.5 text-sm leading-6 text-slate-600 dark:text-slate-300">
                            先找输入框、发送按钮、AI 回复容器。上传按钮、消息外层、生成中提示这些字段可以后面再慢慢补。第一轮目标很简单：先让页面能发、能收、能测。
                        </p>
                    </div>

                    <div class="dashboard-guide-steps">
                        <div class="dashboard-guide-step">
                            <strong>第 1 步</strong>
                            <span>打开教程章节，先看字段各自负责什么。</span>
                        </div>
                        <div class="dashboard-guide-step">
                            <strong>第 2 步</strong>
                            <span>打开本地练习站，对着假页面练一遍，熟悉输入框、发送按钮、回复容器各长什么样。</span>
                        </div>
                        <div class="dashboard-guide-step">
                            <strong>第 3 步</strong>
                            <span>回到真实站点，填一个测一个。只要“测试”能稳定命中，后面工作流就会轻松很多。</span>
                        </div>
                    </div>

                    <div class="dashboard-checklist">
                        <div v-for="item in coreChecklist"
                             :key="item.key"
                             :class="['dashboard-checklist-item', item.ready ? 'is-ready' : 'is-missing']">
                            <span>{{ item.ready ? '✓' : '•' }}</span>
                            <span>{{ item.key }}</span>
                        </div>
                    </div>

                    <p class="mt-3 text-xs text-slate-500 dark:text-slate-400">
                        当前核心字段进度：{{ coreReadyCount }}/3
                    </p>

                    <div v-if="missingQuickAdds.length" class="mt-3 flex flex-wrap gap-2">
                        <button v-for="item in missingQuickAdds"
                                :key="item.key"
                                @click="addSelector(item.key)"
                                class="px-3 py-1.5 rounded-full text-xs font-medium border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-white/80 dark:hover:bg-slate-800/80 transition">
                            新增 {{ item.key }}
                        </button>
                    </div>

                    <div class="dashboard-guide-actions">
                        <button @click="openTutorial('selector-basics')" class="dashboard-guide-btn">
                            <span v-html="$icons.arrowTopRightOnSquare"></span>
                            打开选择器教程
                        </button>
                        <button @click="openPracticeLab()" class="dashboard-guide-btn dashboard-guide-btn--secondary">
                            <span v-html="$icons.folderOpen"></span>
                            打开本地练习站
                        </button>
                    </div>
                </div>

                <div v-if="count === 0" class="dashboard-empty-state text-center text-sm text-slate-500 dark:text-slate-400">
                    <div class="text-3xl mb-3">🧭</div>
                    <p class="leading-6">
                        现在还是空白很正常。先把 <code>input_box</code>、<code>send_btn</code>、<code>result_container</code> 加出来，再开始一项一项测试。
                    </p>
                </div>

                <div v-for="([key, val]) in selectorEntries"
                     :key="key"
                     class="p-3 border dark:border-gray-700 rounded-xl hover:border-blue-300 dark:hover:border-blue-500 transition-colors bg-gray-50/70 dark:bg-gray-900/30">
                    <div class="flex items-start justify-between gap-3 mb-3">
                        <div class="dashboard-field-summary min-w-0">
                            <div class="flex flex-wrap items-center gap-2">
                                <div class="text-sm font-semibold text-gray-900 dark:text-white">{{ key }}</div>
                                <span :class="getChipClass(key)">{{ getSelectorMeta(key).chip }}</span>
                                <button v-if="canRenameSelectorKey(key)"
                                        @click="openRenameEditor(key)"
                                        type="button"
                                        class="inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-medium text-slate-500 dark:text-slate-400 border border-slate-200/80 dark:border-slate-700/80 hover:text-blue-600 dark:hover:text-blue-300 hover:border-blue-300 dark:hover:border-blue-700 transition-colors">
                                    <span v-html="$icons.pencil"></span>
                                    <span>改名</span>
                                </button>
                                <div class="dashboard-field-help">
                                    <button type="button"
                                            class="dashboard-field-help-trigger"
                                            :aria-label="key + ' 字段说明'">
                                        i
                                    </button>
                                    <div class="dashboard-field-tooltip" role="tooltip">
                                        <div class="dashboard-field-tooltip-title">{{ getSelectorMeta(key).title }}</div>
                                        <div class="dashboard-field-tooltip-copy">{{ getSelectorMeta(key).description }}</div>
                                        <div class="dashboard-field-tooltip-copy dashboard-field-tooltip-copy--muted">{{ getSelectorMeta(key).hint }}</div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="text-[11px] font-medium text-slate-400 dark:text-slate-500 shrink-0">
                            {{ isSelectorFilled(key) ? '已填写' : '待填写' }}
                        </div>
                    </div>

                    <div v-if="openRenameKey === key"
                         class="mb-2 flex flex-col md:flex-row gap-2 rounded-xl border border-blue-200/80 dark:border-blue-800/70 bg-blue-50/70 dark:bg-blue-900/20 p-2.5">
                        <input :ref="'renameInput-' + key"
                               v-model="renameDrafts[key]"
                               @keyup.enter="submitRenameKey(key)"
                               @keyup.esc="closeRenameEditor()"
                               class="flex-1 border dark:border-gray-600 px-2.5 py-2 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent font-semibold text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
                               placeholder="新的字段键名">
                        <div class="flex items-center gap-2">
                            <button @click="submitRenameKey(key)"
                                    type="button"
                                    class="px-3 py-1.5 rounded-md text-xs font-medium text-blue-600 dark:text-blue-300 border border-blue-300 dark:border-blue-700 hover:bg-blue-100/80 dark:hover:bg-blue-900/40 transition-colors">
                                保存
                            </button>
                            <button @click="closeRenameEditor()"
                                    type="button"
                                    class="px-3 py-1.5 rounded-md text-xs font-medium text-slate-500 dark:text-slate-300 border border-slate-200 dark:border-slate-700 hover:bg-white/80 dark:hover:bg-slate-800/70 transition-colors">
                                取消
                            </button>
                        </div>
                    </div>

                    <input :value="selectors[key]"
                           @input="$emit('update-selector-value', key, $event.target.value)"
                           class="border dark:border-gray-600 px-2.5 py-2 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent w-full bg-white dark:bg-gray-800 text-sm font-mono text-gray-700 dark:text-gray-300"
                           :placeholder="getSelectorMeta(key).placeholder || 'CSS 选择器'">

                    <div class="mt-3 flex justify-between items-center gap-3">
                        <button @click="$emit('test-selector', key, val)"
                                class="px-3 py-1.5 rounded-md text-xs font-medium transition-all duration-150 text-blue-600 dark:text-blue-400 border border-blue-300 dark:border-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/30 active:scale-95">
                            测试这个字段
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
            </div>
        </div>
    `
};
