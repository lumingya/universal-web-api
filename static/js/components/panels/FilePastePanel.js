// ==================== 文件粘贴 / 附件发送配置面板 ====================

window.FilePastePanel = {
    name: 'FilePastePanel',
    props: {
        filePasteConfig: { type: Object, required: true },
        currentDomain: { type: String, default: null },
        selectedPreset: { type: String, default: null },
        collapsed: { type: Boolean, default: true }
    },
    emits: ['update:collapsed'],
    data() {
        return {
            // 内部分区折叠状态：高级附件规则默认收起，其余默认展开
            sectionCollapsed: {
                attachments: false,
                pasteMode: false,
                sendConfirm: false,
                advancedRules: true
            },
            defaultAttachments: {
                enabled: true, max_count: 8, max_file_mb: 20, max_total_mb: 50,
                allowed_types: [], error_selectors: [], ready_timeout: 30,
                transport_order: ['file_input', 'cdp_drop', 'js_drop', 'file_clipboard']
            },
            transportOptions: [
                { value: 'file_input', label: '文件输入框', description: '优先上传原文件；使用 file_input 选择器或自动查找。' },
                { value: 'cdp_drop', label: '浏览器原生拖拽', description: '需要 drop_zone；不通过 base64 传输大文件。' },
                { value: 'js_drop', label: '网页拖拽事件', description: '需要 drop_zone；仅用于不超过 4 MB 的文件。' },
                { value: 'file_clipboard', label: '原生文件剪贴板', description: '需要系统支持与已确认的输入框焦点。' },
                { value: 'image_clipboard', label: '图片像素剪贴板（有损）', description: '仅用于图片兼容；可能丢失透明度、动画帧和元数据。' }
            ],
            defaultFilePaste: {
                enabled: false,
                threshold: 50000,
                temp_file_type: 'txt',
                hint_text: '完全专注于文件内容',
                txt_hint_text: '完全专注于文件内容',
                pdf_hint_text: '完全专注于文件内容',
                error_hint_text: '输入文本长度超过限制，已中止发送',
                reacquire_input_after_upload: false,
                post_upload_input_selector: '',
                post_upload_settle: 0.0,
                upload_signal_timeout: 2.5,
                upload_signal_grace: 3.0,
                state_probe: {
                    enabled: false,
                    code: ''
                }
            },
            tempFileTypeOptions: [
                { value: 'txt', label: 'TXT' },
                { value: 'pdf', label: 'PDF' },
                { value: 'chunk', label: '分块' },
                { value: 'error', label: 'ERROR' }
            ],
            // 修复：补齐后端 get_default_send_confirmation_config() 的 7 个缺失键，
            // 否则未配过这些项的预设打开面板时对应输入框为空（如 pre_retry_probe_window）
            defaultSendConfirmation: {
                attachment_sensitivity: 'medium',
                post_click_observe_window: 1.8,
                pre_retry_probe_window: 0.12,
                retry_observe_window: 0.9,
                attachment_observe_window: 6.0,
                max_retry_count: 2,
                retry_interval: 0.6,
                retry_cooldown_window: 1.5,
                retry_action: 'click_send_btn',
                retry_key_combo: 'Enter',
                retry_on_unconfirmed_send: true,
                accept_attachment_change: false,
                accept_attachment_disappear: false,
                accept_probe_confirmation: true,
                retry_block_on_stop_button: true,
                retry_block_if_generating: true,
                trust_network_activity: true,
                trust_generating_indicator: true,
                trust_send_disabled_with_input_shrink: true
            },
            defaultAttachmentMonitor: {
                root_selectors: [],
                attachment_selectors: [],
                pending_selectors: [],
                busy_text_markers: [],
                ignored_busy_text_markers: [],
                send_button_disabled_markers: [],
                require_attachment_present: false,
                require_upload_signal_before_ready: false,
                continue_once_on_unconfirmed_send: true,
                idle_timeout: 8.0,
                hard_max_wait: 90.0
            },
            attachmentSensitivityOptions: [
                {
                    value: 'low',
                    label: '低',
                    description: '只认更强的发送信号，适合按钮状态经常乱跳的站点。'
                },
                {
                    value: 'medium',
                    label: '中',
                    description: '平衡等待时间和识别速度，适合作为大多数站点的默认值。'
                },
                {
                    value: 'high',
                    label: '高',
                    description: '更早接受附件发送成功信号，适合附件反馈慢、预览挂得晚的站点。'
                }
            ],
            attachmentMonitorDrafts: {}
        };
    },
    watch: {
        filePasteConfig: {
            handler() {
                this.syncAttachmentMonitorDrafts();
            },
            deep: true,
            immediate: true
        },
        currentDomain() {
            this.syncAttachmentMonitorDrafts(true);
        },
        selectedPreset() {
            this.syncAttachmentMonitorDrafts(true);
        }
    },
    mounted() {
        this.syncAttachmentMonitorDrafts(true);
    },
    computed: {
        resolvedAttachments() {
            return { ...this.defaultAttachments, ...((this.filePasteConfig || {}).attachments || {}) };
        },
        orderedTransports() {
            return (this.resolvedAttachments.transport_order || [])
                .map(value => this.transportOptions.find(option => option.value === value)).filter(Boolean);
        },
        longTextStrategy() {
            return ['chunk', 'error'].includes(this.resolvedFilePaste.temp_file_type)
                ? this.resolvedFilePaste.temp_file_type : 'attachment';
        },
        resolvedFilePaste() {
            const raw = this.filePasteConfig || {};
            return {
                ...this.defaultFilePaste,
                ...raw,
                send_confirmation: {
                    ...this.defaultSendConfirmation,
                    ...((raw && raw.send_confirmation) || {})
                },
                attachment_monitor: {
                    ...this.defaultAttachmentMonitor,
                    ...((raw && raw.attachment_monitor) || {})
                },
                state_probe: {
                    ...(this.defaultFilePaste.state_probe || {}),
                    ...((raw && raw.state_probe) || {})
                },
            };
        },
        currentPresetLabel() {
            return String(this.selectedPreset || '').trim() || '主预设';
        },
        statusText() {
            return (this.resolvedAttachments.enabled ? '附件允许' : '附件禁止') + ' · ' + (this.resolvedFilePaste.enabled ? '长文本处理开启' : '长文本处理关闭');
        },
        attachmentSensitivityMeta() {
            const value = this.resolvedFilePaste.send_confirmation.attachment_sensitivity;
            return this.attachmentSensitivityOptions.find(option => option.value === value) || this.attachmentSensitivityOptions[1];
        }
    },
    methods: {
        updateAttachmentField(key, value) {
            const config = this.getMutableFilePaste();
            config.attachments = { ...(config.attachments || {}), [key]: value };
        },
        updateAttachmentLimit(key, value, ceiling) {
            const number = Number(value);
            if (!Number.isFinite(number) || value === '') return;
            this.updateAttachmentField(key, Math.max(1, Math.min(ceiling, Math.trunc(number))));
        },
        updateAllowedTypes(value) {
            this.updateAttachmentField('allowed_types', [...new Set(String(value || '').toLowerCase().split(/[\s,;]+/).filter(Boolean))]);
        },
        toggleTransport(value) {
            const current = [...(this.resolvedAttachments.transport_order || [])];
            const next = current.includes(value) ? current.filter(item => item !== value) : [...current, value];
            this.updateAttachmentField('transport_order', next);
        },
        moveTransport(index, direction) {
            const current = [...(this.resolvedAttachments.transport_order || [])];
            const next = index + direction;
            if (next < 0 || next >= current.length) return;
            [current[index], current[next]] = [current[next], current[index]];
            this.updateAttachmentField('transport_order', current);
        },
        updateLongTextStrategy(value) {
            this.updateTempFileType(value === 'attachment' ? 'txt' : value);
        },
        toggle() {
            this.$emit('update:collapsed', !this.collapsed);
        },

        toggleSection(key) {
            this.sectionCollapsed[key] = !this.sectionCollapsed[key];
        },

        getMutableFilePaste() {
            return this.filePasteConfig || {};
        },

        ensureSendConfirmation() {
            const fp = this.getMutableFilePaste();
            if (!fp.send_confirmation || typeof fp.send_confirmation !== 'object') {
                fp.send_confirmation = {};
            }
            return fp.send_confirmation;
        },

        ensureAttachmentMonitor() {
            const fp = this.getMutableFilePaste();
            if (!fp.attachment_monitor || typeof fp.attachment_monitor !== 'object') {
                fp.attachment_monitor = {};
            }
            return fp.attachment_monitor;
        },

        ensureStateProbe() {
            const fp = this.getMutableFilePaste();
            if (!fp.state_probe || typeof fp.state_probe !== 'object') {
                fp.state_probe = {};
            }
            return fp.state_probe;
        },

        toggleEnabled() {
            const fp = this.getMutableFilePaste();
            fp.enabled = !this.resolvedFilePaste.enabled;
        },

        updateThreshold(value) {
            // 修复：此前 <1000 直接丢弃，DOM 与模型脱节；改为与后端一致的夹取
            const num = parseInt(value);
            this.getMutableFilePaste().threshold = Number.isFinite(num)
                ? Math.max(1000, Math.min(num, 10000000))
                : 50000;
        },

        updateTempFileType(value) {
            const normalized = String(value || '').trim().toLowerCase();
            this.getMutableFilePaste().temp_file_type = ['txt', 'pdf', 'chunk', 'error'].includes(normalized) ? normalized : 'txt';
        },

        updateHintText(value) {
            const fp = this.getMutableFilePaste();
            const type = this.resolvedFilePaste.temp_file_type;
            if (type === 'txt') {
                fp.txt_hint_text = value;
            } else if (type === 'pdf') {
                fp.pdf_hint_text = value;
            } else if (type === 'error') {
                fp.error_hint_text = value;
            }
            fp.hint_text = value;
        },

        updateNumberField(field, value, fallback) {
            const parsed = parseFloat(value);
            this.getMutableFilePaste()[field] = Number.isFinite(parsed) ? parsed : fallback;
        },

        updateBooleanField(field, value) {
            this.getMutableFilePaste()[field] = !!value;
        },

        updateTextField(field, value) {
            this.getMutableFilePaste()[field] = value;
        },

        updateSendConfirmationField(field, value) {
            this.ensureSendConfirmation()[field] = value;
        },

        updateAttachmentMonitorField(field, value) {
            this.ensureAttachmentMonitor()[field] = value;
        },

        updateStateProbeField(field, value) {
            this.ensureStateProbe()[field] = value;
        },

        normalizeRuleList(value) {
            const lines = String(value || '')
                .split(/\r?\n/)
                .map(item => item.trim())
                .filter(Boolean);
            return [...new Set(lines)];
        },

        syncAttachmentMonitorDrafts(force = false) {
            const monitor = this.resolvedFilePaste?.attachment_monitor || {};
            const listFields = [
                'attachment_selectors',
                'pending_selectors',
                'busy_text_markers',
                'send_button_disabled_markers',
                'ignored_busy_text_markers',
                'root_selectors'
            ];
            if (!this.attachmentMonitorDrafts) {
                this.attachmentMonitorDrafts = {};
            }
            const nextDrafts = { ...this.attachmentMonitorDrafts };
            let hasChange = false;
            for (const field of listFields) {
                const propArr = Array.isArray(monitor[field]) ? monitor[field] : [];
                const curDraftArr = this.normalizeRuleList(nextDrafts[field]);
                if (force || JSON.stringify(propArr) !== JSON.stringify(curDraftArr) || nextDrafts[field] === undefined || nextDrafts[field] === null) {
                    nextDrafts[field] = propArr.join('\n');
                    hasChange = true;
                }
            }
            if (hasChange) {
                this.attachmentMonitorDrafts = nextDrafts;
            }
        },

        updateAttachmentMonitorListField(field, value) {
            if (!this.attachmentMonitorDrafts) {
                this.attachmentMonitorDrafts = {};
            }
            this.attachmentMonitorDrafts[field] = value;
            this.updateAttachmentMonitorField(field, this.normalizeRuleList(value));
        },

        formatRuleList(value) {
            return Array.isArray(value) ? value.join('\n') : '';
        }
    },
    template: `
        <div class="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-sm">
            <div class="px-4 py-3 border-b dark:border-gray-700 flex justify-between items-center cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                 @click="toggle">
                <div class="flex items-center gap-2">
                    <span class="w-4 inline-flex justify-center text-gray-500 dark:text-gray-400" v-html="collapsed ? $icons.chevronDown : $icons.chevronUp"></span>
                    <h3 class="font-semibold text-gray-900 dark:text-white">通用附件 / 超长输入</h3>
                    <span class="text-sm text-gray-500 dark:text-gray-400">({{ statusText }})</span>
                </div>
            </div>

            <div v-show="!collapsed" class="p-4 space-y-4">
                <div class="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg px-4 py-3">
                    <div class="text-sm text-blue-700 dark:text-blue-300">
                        当前预设：{{ currentPresetLabel }}
                    </div>
                </div>

                <section class="rounded-xl border border-blue-200 dark:border-blue-800 bg-blue-50/40 dark:bg-blue-900/10 p-4 space-y-4" aria-label="通用附件设置">
                    <div class="flex items-center justify-between gap-4">
                        <div>
                            <h4 class="text-sm font-semibold text-gray-900 dark:text-white">通用附件管线</h4>
                            <p class="mt-1 text-xs text-gray-600 dark:text-gray-400">图片、文档、音频、视频与长文本生成文件，共用上传限制与确认规则。与下方超长文本开关独立。</p>
                        </div>
                        <label class="toggle-label flex-shrink-0" title="允许当前预设上传附件">
                            <input type="checkbox" aria-label="允许附件上传" :checked="resolvedAttachments.enabled" @change="updateAttachmentField('enabled', $event.target.checked)" class="sr-only peer">
                            <div class="toggle-bg"></div>
                        </label>
                    </div>
                    <div v-if="!resolvedAttachments.enabled" class="text-sm text-amber-700 dark:text-amber-300">当前预设会拒绝附件请求；超长文本转文件也不可用。纯文本、分块和直接报错策略不受此开关影响。</div>
                    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
                        <label class="text-xs text-gray-700 dark:text-gray-300">最多附件数
                            <input type="number" min="1" max="32" :value="resolvedAttachments.max_count" @change="updateAttachmentLimit('max_count', $event.target.value, 32)" class="mt-1 w-full border dark:border-gray-600 rounded-md px-3 py-2 bg-white dark:bg-gray-700">
                        </label>
                        <label class="text-xs text-gray-700 dark:text-gray-300">单文件上限（MB）
                            <input type="number" min="1" max="100" :value="resolvedAttachments.max_file_mb" @change="updateAttachmentLimit('max_file_mb', $event.target.value, 100)" class="mt-1 w-full border dark:border-gray-600 rounded-md px-3 py-2 bg-white dark:bg-gray-700">
                        </label>
                        <label class="text-xs text-gray-700 dark:text-gray-300">附件总上限（MB）
                            <input type="number" min="1" max="200" :value="resolvedAttachments.max_total_mb" @change="updateAttachmentLimit('max_total_mb', $event.target.value, 200)" class="mt-1 w-full border dark:border-gray-600 rounded-md px-3 py-2 bg-white dark:bg-gray-700">
                        </label>
                        <label class="text-xs text-gray-700 dark:text-gray-300">单附件确认预算（秒）
                            <input type="number" min="1" max="180" :value="resolvedAttachments.ready_timeout" @change="updateAttachmentLimit('ready_timeout', $event.target.value, 180)" class="mt-1 w-full border dark:border-gray-600 rounded-md px-3 py-2 bg-white dark:bg-gray-700">
                        </label>
                    </div>
                    <label class="block text-sm text-gray-700 dark:text-gray-300">允许的 MIME / 扩展名
                        <input type="text" :value="(resolvedAttachments.allowed_types || []).join(', ')" @change="updateAllowedTypes($event.target.value)" placeholder="例如 image/*, application/pdf, .docx, .txt, audio/*" class="mt-1 w-full border dark:border-gray-600 rounded-md px-3 py-2 text-sm font-mono bg-white dark:bg-gray-700">
                        <span class="block mt-1 text-xs text-gray-500 dark:text-gray-400">留空表示不额外限制类型，不代表网站或模型支持所有文件。实际上传仍需网站就绪信号；默认不转换、不截断、不忽略失败。</span>
                    </label>
                    <label class="block text-sm text-gray-700 dark:text-gray-300">上传错误 CSS 选择器（可选，一行一个）
                        <textarea :value="(resolvedAttachments.error_selectors || []).join('\\n')" @change="updateAttachmentField('error_selectors', $event.target.value.split(/\\n/).map(v => v.trim()).filter(Boolean))" placeholder=".upload-card[data-state=error]" rows="2" class="mt-1 w-full border dark:border-gray-600 rounded-md px-3 py-2 text-sm font-mono bg-white dark:bg-gray-700"></textarea>
                        <span class="block text-xs text-gray-500 dark:text-gray-400">只匹配编辑区内可见的错误节点；命中后立即停止，不把错误预览视为上传成功。</span>
                    </label>
                    <div class="space-y-2">
                        <div class="text-sm font-medium text-gray-800 dark:text-gray-200">上传优先级 <span class="text-xs font-normal text-gray-500">仅在确认尚未投递时回退</span></div>
                        <div v-for="(transport, index) in orderedTransports" :key="transport.value" class="flex items-center gap-3 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-3">
                            <span class="text-xs text-blue-600 dark:text-blue-400">{{ index + 1 }}</span>
                            <div class="flex-1 min-w-0"><div class="text-sm text-gray-800 dark:text-gray-200">{{ transport.label }}</div><p class="text-xs text-gray-500 dark:text-gray-400 mt-1">{{ transport.description }}</p></div>
                            <button type="button" :disabled="index === 0" @click="moveTransport(index, -1)" :aria-label="transport.label + '上移'" class="px-2 py-1 border rounded disabled:opacity-30">↑</button>
                            <button type="button" :disabled="index === orderedTransports.length - 1" @click="moveTransport(index, 1)" :aria-label="transport.label + '下移'" class="px-2 py-1 border rounded disabled:opacity-30">↓</button>
                            <button type="button" @click="toggleTransport(transport.value)" :aria-label="'停用' + transport.label" class="text-xs text-red-600 dark:text-red-400 px-2 py-1">停用</button>
                        </div>
                        <div v-if="!orderedTransports.length" class="text-xs text-red-600 dark:text-red-400">没有启用上传方式，附件将被明确拒绝，不会自动启用其他方式。</div>
                        <div class="flex flex-wrap gap-2">
                            <template v-for="option in transportOptions" :key="option.value">
                                <button v-if="!resolvedAttachments.transport_order.includes(option.value)" type="button" @click="toggleTransport(option.value)" class="border border-dashed border-gray-400 rounded-md text-xs px-3 py-2 text-gray-600 dark:text-gray-300">＋ {{ option.label }}</button>
                            </template>
                        </div>
                        <p v-if="resolvedAttachments.transport_order.includes('image_clipboard')" class="text-xs text-amber-700 dark:text-amber-300">已启用有损图片剪贴板兼容。需要原文件保真时，请停用此方式。</p>
                    </div>
                    <details class="text-xs text-gray-600 dark:text-gray-400">
                        <summary class="cursor-pointer font-medium">API 附件格式与兼容说明</summary>
                        <div class="mt-2 space-y-2 leading-5">
                            <p>支持 image_url、OpenAI file / Responses input_file、Anthropic document、input_audio 与音视频 URL。文件 ID、本地路径和 file:// 暂不支持；原始附件内容不会降级为提示词。</p>
                            <pre class="overflow-x-auto p-3 rounded bg-gray-900 text-gray-100">{"type":"file","file":{"filename":"notes.txt","file_data":"data:text/plain;base64,aGVsbG8="}}</pre>
                            <p>配置存储在当前预设的 file_paste.attachments 中，旧配置无需手动迁移。选择器仍在「元素选择器」，就绪探针在下方「高级附件规则」。MB 按 1024² 字节计算。</p>
                        </div>
                    </details>
                </section>

                <div class="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50/60 dark:bg-gray-900/30 p-4"
                     :class="sectionCollapsed.pasteMode ? '' : 'space-y-4'">
                    <div class="flex items-center justify-between gap-4 cursor-pointer select-none -m-2 p-2 rounded-md hover:bg-gray-100/70 dark:hover:bg-gray-800/60 transition-colors"
                         @click="toggleSection('pasteMode')">
                        <div>
                            <div class="flex items-center gap-2 text-sm font-medium text-gray-800 dark:text-gray-100">
                                <span class="w-4 inline-flex justify-center text-gray-400 dark:text-gray-500" v-html="sectionCollapsed.pasteMode ? $icons.chevronDown : $icons.chevronUp"></span>
                                <span>超长输入处理</span>
                            </div>
                            <p v-show="!sectionCollapsed.pasteMode" class="mt-1 text-xs text-gray-500 dark:text-gray-400 leading-5">
                                超过阈值后可转为附件、分块连续发送或直接报错。分块会等待每一轮完成，但只把最后一轮回复返回客户端。
                            </p>
                        </div>
                        <label class="toggle-label scale-90 flex-shrink-0" @click.stop>
                            <input type="checkbox" :checked="resolvedFilePaste.enabled" @change="toggleEnabled" class="sr-only peer">
                            <div class="toggle-bg"></div>
                        </label>
                    </div>

                    <div v-show="!sectionCollapsed.pasteMode" class="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">阈值</label>
                            <div class="flex items-center gap-2">
                                <input type="number"
                                       :value="resolvedFilePaste.threshold"
                                       @input="updateThreshold($event.target.value)"
                                       min="1000"
                                       max="10000000"
                                       step="1000"
                                       class="flex-1 border dark:border-gray-600 px-3 py-2 rounded-md text-sm text-right bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                <span class="text-sm text-gray-500 dark:text-gray-400">字符</span>
                            </div>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">超长处理方式</label>
                            <select :value="longTextStrategy"
                                    @change="updateLongTextStrategy($event.target.value)"
                                    class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                <option value="attachment">转为附件（共用上传管线）</option>
                                <option value="chunk">分块连续发送</option>
                                <option value="error">直接返回错误</option>
                            </select>
                            <label v-if="longTextStrategy === 'attachment'" class="mt-2 block text-xs text-gray-600 dark:text-gray-400">生成格式
                                <select :value="resolvedFilePaste.temp_file_type" @change="updateTempFileType($event.target.value)" class="ml-2 border rounded px-2 py-1 bg-white dark:bg-gray-700">
                                    <option value="txt">TXT（推荐，保留原文）</option><option value="pdf">PDF</option>
                                </select>
                            </label>
                        </div>
                        <div>
                            <label v-if="resolvedFilePaste.temp_file_type !== 'chunk'" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                                {{ resolvedFilePaste.temp_file_type === 'error' ? '错误信息' : '引导文本' }}
                            </label>
                            <input v-if="resolvedFilePaste.temp_file_type !== 'chunk'"
                                   type="text"
                                   :value="resolvedFilePaste.temp_file_type === 'txt' ? resolvedFilePaste.txt_hint_text : (resolvedFilePaste.temp_file_type === 'pdf' ? resolvedFilePaste.pdf_hint_text : resolvedFilePaste.error_hint_text)"
                                   @input="updateHintText($event.target.value)"
                                   :placeholder="resolvedFilePaste.temp_file_type === 'error' ? '超过阈值时返回给客户端的错误信息' : '粘贴文件后追加的文字，留空则不追加'"
                                   class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                            <div v-else class="min-h-[38px] flex items-center rounded-md border border-blue-200 dark:border-blue-800 bg-blue-50/70 dark:bg-blue-900/20 px-3 py-2 text-xs leading-5 text-blue-700 dark:text-blue-300">
                                自动使用最少块数，并把分块说明计入阈值。
                            </div>
                        </div>
                    </div>

                    <div v-show="!sectionCollapsed.pasteMode" class="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">上传后稳定等待</label>
                            <div class="flex items-center gap-2">
                                <input type="number"
                                       :value="resolvedFilePaste.post_upload_settle"
                                       @input="updateNumberField('post_upload_settle', $event.target.value, 0)"
                                       min="0"
                                       max="30"
                                       step="0.5"
                                       class="flex-1 border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                <span class="text-sm text-gray-500 dark:text-gray-400">秒</span>
                            </div>
                        </div>
                    </div>

                    <label v-show="!sectionCollapsed.pasteMode" class="flex items-center gap-3 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
                        <input type="checkbox"
                               class="rounded"
                               :checked="resolvedFilePaste.reacquire_input_after_upload"
                               @change="updateBooleanField('reacquire_input_after_upload', $event.target.checked)">
                        <span>上传完成后重新定位输入框</span>
                    </label>

                    <div v-show="!sectionCollapsed.pasteMode">
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">上传后专用输入框 selector</label>
                        <input type="text"
                               :value="resolvedFilePaste.post_upload_input_selector"
                               @input="updateTextField('post_upload_input_selector', $event.target.value)"
                               placeholder=".composer textarea"
                               class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                    </div>
                </div>

                <div class="rounded-xl border border-blue-200/80 dark:border-blue-800/70 bg-blue-50/70 dark:bg-blue-900/20 p-4">
                    <div class="flex items-start justify-between gap-3 cursor-pointer select-none -m-2 p-2 rounded-md hover:bg-blue-100/50 dark:hover:bg-blue-900/30 transition-colors"
                         @click="toggleSection('sendConfirm')">
                        <div>
                            <div class="flex items-center gap-2 text-sm font-medium text-gray-800 dark:text-gray-100">
                                <span class="w-4 inline-flex justify-center text-gray-400 dark:text-gray-500" v-html="sectionCollapsed.sendConfirm ? $icons.chevronDown : $icons.chevronUp"></span>
                                <span>附件发送判定</span>
                            </div>
                            <p v-show="!sectionCollapsed.sendConfirm" class="mt-1 text-xs leading-5 text-gray-600 dark:text-gray-300">
                                这里会同时作用于图片、文档、音视频与长文本附件。点击发送后，系统会先观察附件预览、上传中状态、发送按钮灰态和页面进入生成态的信号，再决定这次附件是否真的发出去了。
                            </p>
                        </div>
                        <span class="px-2 py-0.5 text-xs rounded-full bg-white/80 dark:bg-gray-800/80 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-700 flex-shrink-0">
                            当前：{{ attachmentSensitivityMeta.label }}
                        </span>
                    </div>

                    <div v-show="!sectionCollapsed.sendConfirm" class="mt-3 grid grid-cols-1 md:grid-cols-3 gap-3 items-start">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">敏感度</label>
                            <select :value="resolvedFilePaste.send_confirmation.attachment_sensitivity"
                                    @change="updateSendConfirmationField('attachment_sensitivity', $event.target.value)"
                                    class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                <option v-for="option in attachmentSensitivityOptions"
                                        :key="option.value"
                                        :value="option.value">
                                    {{ option.label }}
                                </option>
                            </select>
                        </div>
                        <div class="md:col-span-2 text-xs leading-6 text-gray-600 dark:text-gray-300 bg-white/70 dark:bg-gray-900/40 rounded-lg border border-blue-100 dark:border-blue-900/60 px-3 py-2">
                            {{ attachmentSensitivityMeta.description }}
                        </div>
                    </div>

                    <div v-show="!sectionCollapsed.sendConfirm" class="mt-4 grid grid-cols-1 md:grid-cols-4 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">最大重试次数</label>
                            <div class="flex items-center gap-2">
                                <input type="number"
                                       :value="resolvedFilePaste.send_confirmation.max_retry_count"
                                       @input="updateSendConfirmationField('max_retry_count', parseInt($event.target.value) || 0)"
                                       min="0"
                                       max="10"
                                       step="1"
                                       class="flex-1 border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                <span class="text-sm text-gray-500 dark:text-gray-400">次</span>
                            </div>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">重试间隔</label>
                            <div class="flex items-center gap-2">
                                <input type="number"
                                       :value="resolvedFilePaste.send_confirmation.retry_interval"
                                       @input="updateSendConfirmationField('retry_interval', parseFloat($event.target.value) || 0)"
                                       min="0"
                                       max="30"
                                       step="0.1"
                                       class="flex-1 border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                <span class="text-sm text-gray-500 dark:text-gray-400">秒</span>
                            </div>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">最小冷却窗</label>
                            <div class="flex items-center gap-2">
                                <input type="number"
                                       :value="resolvedFilePaste.send_confirmation.retry_cooldown_window"
                                       @input="updateSendConfirmationField('retry_cooldown_window', parseFloat($event.target.value) || 0)"
                                       min="0"
                                       max="30"
                                       step="0.1"
                                       class="flex-1 border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                <span class="text-sm text-gray-500 dark:text-gray-400">秒</span>
                            </div>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">重试前短探测</label>
                            <div class="flex items-center gap-2">
                                <input type="number"
                                       :value="resolvedFilePaste.send_confirmation.pre_retry_probe_window"
                                       @input="updateSendConfirmationField('pre_retry_probe_window', parseFloat($event.target.value) || 0)"
                                       min="0"
                                       max="5"
                                       step="0.05"
                                       class="flex-1 border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                <span class="text-sm text-gray-500 dark:text-gray-400">秒</span>
                            </div>
                        </div>
                    </div>

                    <div v-show="!sectionCollapsed.sendConfirm" class="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">自动重试动作</label>
                            <select :value="resolvedFilePaste.send_confirmation.retry_action"
                                    @change="updateSendConfirmationField('retry_action', $event.target.value)"
                                    class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                <option value="click_send_btn">点击发送按钮</option>
                                <option value="key_press">按键发送</option>
                            </select>
                            <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                                某些站点二次点击会把“发送”变成“停止”，这时更适合改成按键发送。
                            </p>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">重试按键组合</label>
                            <input type="text"
                                   :value="resolvedFilePaste.send_confirmation.retry_key_combo"
                                   @input="updateSendConfirmationField('retry_key_combo', $event.target.value)"
                                   placeholder="Enter / Ctrl+Enter"
                                   class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                            <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                                仅在“按键发送”时生效，支持 Enter、Ctrl+Enter、Shift+Enter 等组合。
                            </p>
                        </div>
                    </div>

                    <div v-show="!sectionCollapsed.sendConfirm" class="mt-4 border-t border-blue-100 dark:border-blue-900/60 pt-4"
                         :class="sectionCollapsed.advancedRules ? '' : 'space-y-4'">
                        <div class="cursor-pointer select-none -m-2 p-2 rounded-md hover:bg-blue-100/50 dark:hover:bg-blue-900/30 transition-colors"
                             @click="toggleSection('advancedRules')">
                            <div class="flex items-center gap-2 text-sm font-medium text-gray-800 dark:text-gray-100">
                                <span class="w-4 inline-flex justify-center text-gray-400 dark:text-gray-500" v-html="sectionCollapsed.advancedRules ? $icons.chevronDown : $icons.chevronUp"></span>
                                <span>高级附件规则</span>
                                <span v-show="sectionCollapsed.advancedRules" class="text-xs font-normal text-gray-400 dark:text-gray-500">（点击展开）</span>
                            </div>
                            <p v-show="!sectionCollapsed.advancedRules" class="mt-1 text-xs leading-5 text-gray-600 dark:text-gray-300">
                                像 Gemini 这类站点，可以在这里补发送按钮灰态 token、附件预览 selector 和 pending 文案。即使文件粘贴没开，这块也会继续影响图片上传和发送前的附件 gate。
                            </p>
                        </div>

                        <div v-show="!sectionCollapsed.advancedRules" class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <label class="flex items-center gap-3 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
                                <input type="checkbox"
                                       class="rounded"
                                       :checked="resolvedFilePaste.attachment_monitor.require_attachment_present"
                                       @change="updateAttachmentMonitorField('require_attachment_present', $event.target.checked)">
                                <span>发送前必须看到附件已挂上页面</span>
                            </label>
                            <label class="flex items-center gap-3 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
                                <input type="checkbox"
                                       class="rounded"
                                       :checked="resolvedFilePaste.attachment_monitor.require_upload_signal_before_ready"
                                       @change="updateAttachmentMonitorField('require_upload_signal_before_ready', $event.target.checked)">
                                <span>没有观察到上传启动前，不允许判定 ready</span>
                            </label>
                            <label class="flex items-center gap-3 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
                                <input type="checkbox"
                                       class="rounded"
                                       :checked="resolvedFilePaste.attachment_monitor.continue_once_on_unconfirmed_send"
                                       @change="updateAttachmentMonitorField('continue_once_on_unconfirmed_send', $event.target.checked)">
                                <span>未确认时仍允许继续点一次发送</span>
                            </label>
                            <label class="flex items-center gap-3 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
                                <input type="checkbox"
                                       class="rounded"
                                       :checked="resolvedFilePaste.send_confirmation.retry_on_unconfirmed_send"
                                       @change="updateSendConfirmationField('retry_on_unconfirmed_send', $event.target.checked)">
                                <span>发送未确认时允许自动重试</span>
                            </label>
                            <label class="flex items-center gap-3 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
                                <input type="checkbox"
                                       class="rounded"
                                       :checked="resolvedFilePaste.send_confirmation.retry_block_on_stop_button"
                                       @change="updateSendConfirmationField('retry_block_on_stop_button', $event.target.checked)">
                                <span>发送按钮变成 stop 时禁止重试</span>
                            </label>
                            <label class="flex items-center gap-3 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
                                <input type="checkbox"
                                       class="rounded"
                                       :checked="resolvedFilePaste.send_confirmation.retry_block_if_generating"
                                       @change="updateSendConfirmationField('retry_block_if_generating', $event.target.checked)">
                                <span>页面进入生成态时禁止重试</span>
                            </label>
                            <label class="flex items-center gap-3 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
                                <input type="checkbox"
                                       class="rounded"
                                       :checked="resolvedFilePaste.send_confirmation.accept_attachment_change"
                                       @change="updateSendConfirmationField('accept_attachment_change', $event.target.checked)">
                                <span>发送后附件区发生变化时可视为已接受</span>
                            </label>
                            <label class="flex items-center gap-3 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
                                <input type="checkbox"
                                       class="rounded"
                                       :checked="resolvedFilePaste.send_confirmation.accept_attachment_disappear"
                                       @change="updateSendConfirmationField('accept_attachment_disappear', $event.target.checked)">
                                <span>发送后附件区消失时可视为已接受</span>
                            </label>
                            <label class="flex items-center gap-3 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
                                <input type="checkbox"
                                       class="rounded"
                                       :checked="resolvedFilePaste.send_confirmation.accept_probe_confirmation"
                                       @change="updateSendConfirmationField('accept_probe_confirmation', $event.target.checked)">
                                <span>允许 JS probe 直接确认发送成功</span>
                            </label>
                        </div>

                        <div v-show="!sectionCollapsed.advancedRules" class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">空闲超时</label>
                                <div class="flex items-center gap-2">
                                    <input type="number"
                                           :value="resolvedFilePaste.attachment_monitor.idle_timeout"
                                           @input="updateAttachmentMonitorField('idle_timeout', parseFloat($event.target.value) || 8)"
                                           min="0.5"
                                           max="60"
                                           step="0.5"
                                           class="flex-1 border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                    <span class="text-sm text-gray-500 dark:text-gray-400">秒</span>
                                </div>
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">附件最长等待</label>
                                <div class="flex items-center gap-2">
                                    <input type="number"
                                           :value="resolvedFilePaste.attachment_monitor.hard_max_wait"
                                           @input="updateAttachmentMonitorField('hard_max_wait', parseFloat($event.target.value) || 90)"
                                           min="1"
                                           max="300"
                                           step="1"
                                           class="flex-1 border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                    <span class="text-sm text-gray-500 dark:text-gray-400">秒</span>
                                </div>
                            </div>
                        </div>

                        <div v-show="!sectionCollapsed.advancedRules" class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">附件预览 selector</label>
                                <textarea
                                    :value="attachmentMonitorDrafts ? (attachmentMonitorDrafts.attachment_selectors ?? '') : ''"
                                    @input="updateAttachmentMonitorListField('attachment_selectors', $event.target.value)"
                                    rows="5"
                                    placeholder="[class*='attachment']&#10;.upload-preview"
                                    class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent"></textarea>
                                <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">每行一个 selector，命中后会被视为“附件已挂上页面”。</p>
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">上传中 selector</label>
                                <textarea
                                    :value="attachmentMonitorDrafts ? (attachmentMonitorDrafts.pending_selectors ?? '') : ''"
                                    @input="updateAttachmentMonitorListField('pending_selectors', $event.target.value)"
                                    rows="5"
                                    placeholder="[aria-busy='true']&#10;.uploading"
                                    class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent"></textarea>
                                <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">每行一个 selector，命中后会继续等待，不会急着发送。</p>
                            </div>
                        </div>

                        <div v-show="!sectionCollapsed.advancedRules" class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">忙碌文本 / token</label>
                                <textarea
                                    :value="attachmentMonitorDrafts ? (attachmentMonitorDrafts.busy_text_markers ?? '') : ''"
                                    @input="updateAttachmentMonitorListField('busy_text_markers', $event.target.value)"
                                    rows="5"
                                    placeholder="uploading&#10;处理中&#10;解析中"
                                    class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent"></textarea>
                                <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">会同时用于附件区域文本和发送按钮 busy 文案匹配。</p>
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">发送按钮灰态 token</label>
                                <textarea
                                    :value="attachmentMonitorDrafts ? (attachmentMonitorDrafts.send_button_disabled_markers ?? '') : ''"
                                    @input="updateAttachmentMonitorListField('send_button_disabled_markers', $event.target.value)"
                                    rows="5"
                                    placeholder="is-disabled&#10;cursor-not-allowed&#10;upload failed"
                                    class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent"></textarea>
                                <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">填发送按钮 class / title / aria-label 里会出现的关键字，命中后视为按钮不可发。</p>
                            </div>
                        </div>

                        <div v-show="!sectionCollapsed.advancedRules">
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">忽略忙碌文本 / token</label>
                            <textarea
                                :value="attachmentMonitorDrafts ? (attachmentMonitorDrafts.ignored_busy_text_markers ?? '') : ''"
                                @input="updateAttachmentMonitorListField('ignored_busy_text_markers', $event.target.value)"
                                rows="3"
                                placeholder="thinking"
                                class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent"></textarea>
                            <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">用于排除输入区固定开关或标签文案，避免被误判成附件仍在处理。</p>
                        </div>

                        <div v-show="!sectionCollapsed.advancedRules">
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">根容器 selector</label>
                            <textarea
                                :value="attachmentMonitorDrafts ? (attachmentMonitorDrafts.root_selectors ?? '') : ''"
                                @input="updateAttachmentMonitorListField('root_selectors', $event.target.value)"
                                rows="4"
                                placeholder=".composer-shell&#10;.input-area"
                                class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent"></textarea>
                            <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">当通用根容器猜错时再填。这里会限制附件节点和 pending 节点的查找范围。</p>
                        </div>

                        <div v-show="!sectionCollapsed.advancedRules" class="border-t border-blue-100 dark:border-blue-900/60 pt-4 space-y-3">
                            <div class="flex items-center justify-between gap-3">
                                <div>
                                    <div class="text-sm font-medium text-gray-800 dark:text-gray-100">JS 状态探针</div>
                                    <p class="mt-1 text-xs leading-5 text-gray-600 dark:text-gray-300">
                                        探针会收到一个参数对象，包含 stage 和 monitorState。返回结构建议包含 uploading、ready、accepted、confirmed、retry、shouldRetry、summary 这些字段。
                                    </p>
                                </div>
                                <label class="flex items-center gap-3 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
                                    <input type="checkbox"
                                           class="rounded"
                                           :checked="resolvedFilePaste.state_probe.enabled"
                                           @change="updateStateProbeField('enabled', $event.target.checked)">
                                    <span>启用</span>
                                </label>
                            </div>

                            <div>
                                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">探针代码</label>
                                <textarea
                                    :value="resolvedFilePaste.state_probe.code"
                                    @input="updateStateProbeField('code', $event.target.value)"
                                    rows="10"
                                    placeholder="return (() => { const { stage, monitorState } = arguments[0] || {}; return { accepted: false, shouldRetry: false, summary: stage }; })();"
                                    class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent"></textarea>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `
};
