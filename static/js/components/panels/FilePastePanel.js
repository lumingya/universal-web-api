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
            defaultFilePaste: {
                enabled: false,
                threshold: 50000,
                hint_text: '完全专注于文件内容',
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
            defaultSendConfirmation: {
                attachment_sensitivity: 'medium',
                max_retry_count: 2,
                retry_interval: 0.6,
                retry_action: 'click_send_btn',
                retry_key_combo: 'Enter',
                retry_on_unconfirmed_send: true,
                accept_attachment_change: false,
                accept_attachment_disappear: false,
                accept_probe_confirmation: true,
                retry_block_on_stop_button: true,
                retry_block_if_generating: true
            },
            defaultAttachmentMonitor: {
                root_selectors: [],
                attachment_selectors: [],
                pending_selectors: [],
                busy_text_markers: [],
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
            ]
        };
    },
    computed: {
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
            return this.resolvedFilePaste.enabled ? '已启用' : '未启用';
        },
        attachmentSensitivityMeta() {
            const value = this.resolvedFilePaste.send_confirmation.attachment_sensitivity;
            return this.attachmentSensitivityOptions.find(option => option.value === value) || this.attachmentSensitivityOptions[1];
        }
    },
    methods: {
        toggle() {
            this.$emit('update:collapsed', !this.collapsed);
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
            const num = parseInt(value);
            if (!isNaN(num) && num >= 1000) {
                this.getMutableFilePaste().threshold = num;
            }
        },

        updateHintText(value) {
            this.getMutableFilePaste().hint_text = value;
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

        updateAttachmentMonitorListField(field, value) {
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
                    <h3 class="font-semibold text-gray-900 dark:text-white">📄 文件粘贴 / 附件发送</h3>
                    <span class="text-sm text-gray-500 dark:text-gray-400">({{ statusText }})</span>
                </div>
            </div>

            <div v-show="!collapsed" class="p-4 space-y-4">
                <div class="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg px-4 py-3">
                    <div class="text-sm text-blue-700 dark:text-blue-300">
                        当前预设：{{ currentPresetLabel }}
                    </div>
                </div>

                <div class="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50/60 dark:bg-gray-900/30 p-4 space-y-4">
                    <div class="flex items-center justify-between gap-4">
                        <div>
                            <div class="text-sm font-medium text-gray-800 dark:text-gray-100">文件粘贴模式</div>
                            <p class="mt-1 text-xs text-gray-500 dark:text-gray-400 leading-5">
                                当文本长度超过阈值时，将文本写入临时 txt 文件并走附件上传。下面这些上传后处理项只在文件粘贴模式生效。
                            </p>
                        </div>
                        <label class="toggle-label scale-90 flex-shrink-0">
                            <input type="checkbox" :checked="resolvedFilePaste.enabled" @change="toggleEnabled" class="sr-only peer">
                            <div class="toggle-bg"></div>
                        </label>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">阈值</label>
                            <div class="flex items-center gap-2">
                                <input type="number"
                                       :value="resolvedFilePaste.threshold"
                                       @input="updateThreshold($event.target.value)"
                                       min="1000"
                                       step="1000"
                                       class="flex-1 border dark:border-gray-600 px-3 py-2 rounded-md text-sm text-right bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                <span class="text-sm text-gray-500 dark:text-gray-400">字符</span>
                            </div>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">引导文本</label>
                            <input type="text"
                                   :value="resolvedFilePaste.hint_text"
                                   @input="updateHintText($event.target.value)"
                                   placeholder="粘贴文件后追加的文字，留空则不追加"
                                   class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                        </div>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">上传信号超时</label>
                            <div class="flex items-center gap-2">
                                <input type="number"
                                       :value="resolvedFilePaste.upload_signal_timeout"
                                       @input="updateNumberField('upload_signal_timeout', $event.target.value, 2.5)"
                                       min="0.5"
                                       max="120"
                                       step="0.5"
                                       class="flex-1 border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                <span class="text-sm text-gray-500 dark:text-gray-400">秒</span>
                            </div>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">弱信号宽限</label>
                            <div class="flex items-center gap-2">
                                <input type="number"
                                       :value="resolvedFilePaste.upload_signal_grace"
                                       @input="updateNumberField('upload_signal_grace', $event.target.value, 3)"
                                       min="0"
                                       max="120"
                                       step="0.5"
                                       class="flex-1 border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                                <span class="text-sm text-gray-500 dark:text-gray-400">秒</span>
                            </div>
                        </div>
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

                    <label class="flex items-center gap-3 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
                        <input type="checkbox"
                               class="rounded"
                               :checked="resolvedFilePaste.reacquire_input_after_upload"
                               @change="updateBooleanField('reacquire_input_after_upload', $event.target.checked)">
                        <span>上传完成后重新定位输入框</span>
                    </label>

                    <div>
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">上传后专用输入框 selector</label>
                        <input type="text"
                               :value="resolvedFilePaste.post_upload_input_selector"
                               @input="updateTextField('post_upload_input_selector', $event.target.value)"
                               placeholder=".composer textarea"
                               class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                    </div>
                </div>

                <div class="rounded-xl border border-blue-200/80 dark:border-blue-800/70 bg-blue-50/70 dark:bg-blue-900/20 p-4">
                    <div class="flex items-start justify-between gap-3">
                        <div>
                            <div class="text-sm font-medium text-gray-800 dark:text-gray-100">附件发送判定</div>
                            <p class="mt-1 text-xs leading-5 text-gray-600 dark:text-gray-300">
                                这里会同时作用于文件粘贴和图片粘贴。点击发送后，系统会先观察附件预览、上传中状态、发送按钮灰态和页面进入生成态的信号，再决定这次附件是否真的发出去了。
                            </p>
                        </div>
                        <span class="px-2 py-0.5 text-xs rounded-full bg-white/80 dark:bg-gray-800/80 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-700">
                            当前：{{ attachmentSensitivityMeta.label }}
                        </span>
                    </div>

                    <div class="mt-3 grid grid-cols-1 md:grid-cols-3 gap-3 items-start">
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

                    <div class="mt-4 grid grid-cols-1 md:grid-cols-3 gap-4">
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

                    <div class="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
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

                    <div class="mt-4 border-t border-blue-100 dark:border-blue-900/60 pt-4 space-y-4">
                        <div>
                            <div class="text-sm font-medium text-gray-800 dark:text-gray-100">高级附件规则</div>
                            <p class="mt-1 text-xs leading-5 text-gray-600 dark:text-gray-300">
                                像 Gemini 这类站点，可以在这里补发送按钮灰态 token、附件预览 selector 和 pending 文案。即使文件粘贴没开，这块也会继续影响图片上传和发送前的附件 gate。
                            </p>
                        </div>

                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
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

                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
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

                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">附件预览 selector</label>
                                <textarea
                                    :value="formatRuleList(resolvedFilePaste.attachment_monitor.attachment_selectors)"
                                    @input="updateAttachmentMonitorListField('attachment_selectors', $event.target.value)"
                                    rows="5"
                                    placeholder="[class*='attachment']&#10;.upload-preview"
                                    class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent"></textarea>
                                <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">每行一个 selector，命中后会被视为“附件已挂上页面”。</p>
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">上传中 selector</label>
                                <textarea
                                    :value="formatRuleList(resolvedFilePaste.attachment_monitor.pending_selectors)"
                                    @input="updateAttachmentMonitorListField('pending_selectors', $event.target.value)"
                                    rows="5"
                                    placeholder="[aria-busy='true']&#10;.uploading"
                                    class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent"></textarea>
                                <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">每行一个 selector，命中后会继续等待，不会急着发送。</p>
                            </div>
                        </div>

                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">忙碌文本 / token</label>
                                <textarea
                                    :value="formatRuleList(resolvedFilePaste.attachment_monitor.busy_text_markers)"
                                    @input="updateAttachmentMonitorListField('busy_text_markers', $event.target.value)"
                                    rows="5"
                                    placeholder="uploading&#10;处理中&#10;解析中"
                                    class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent"></textarea>
                                <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">会同时用于附件区域文本和发送按钮 busy 文案匹配。</p>
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">发送按钮灰态 token</label>
                                <textarea
                                    :value="formatRuleList(resolvedFilePaste.attachment_monitor.send_button_disabled_markers)"
                                    @input="updateAttachmentMonitorListField('send_button_disabled_markers', $event.target.value)"
                                    rows="5"
                                    placeholder="is-disabled&#10;cursor-not-allowed&#10;upload failed"
                                    class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent"></textarea>
                                <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">填发送按钮 class / title / aria-label 里会出现的关键字，命中后视为按钮不可发。</p>
                            </div>
                        </div>

                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">根容器 selector</label>
                            <textarea
                                :value="formatRuleList(resolvedFilePaste.attachment_monitor.root_selectors)"
                                @input="updateAttachmentMonitorListField('root_selectors', $event.target.value)"
                                rows="4"
                                placeholder=".composer-shell&#10;.input-area"
                                class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent"></textarea>
                            <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">当通用根容器猜错时再填。这里会限制附件节点和 pending 节点的查找范围。</p>
                        </div>

                        <div class="border-t border-blue-100 dark:border-blue-900/60 pt-4 space-y-3">
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
