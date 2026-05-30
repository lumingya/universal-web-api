// ==================== 多模态提取配置面板 ====================

window.ImageConfigPanel = {
    name: 'ImageConfigPanel',
    props: {
        imageConfig: { type: Object, required: true },
        currentDomain: { type: String, default: null },
        collapsed: { type: Boolean, default: true }
    },
    emits: ['update:collapsed', 'update-image-config', 'reload-config'],
    data() {
        return {
            showPresetMenu: false,
            availablePresets: [],
            currentPreset: null,
            loadingPresets: false
        };
    },
    computed: {
        modalityPolicies() {
            return {
                image: this.normalizeModalityPolicy('image', (this.imageConfig.modalities || {}).image),
                audio: this.normalizeModalityPolicy('audio', (this.imageConfig.modalities || {}).audio),
                video: this.normalizeModalityPolicy('video', (this.imageConfig.modalities || {}).video)
            };
        },
        modalities() {
            return {
                image: !!this.modalityPolicies.image.enabled,
                audio: !!this.modalityPolicies.audio.enabled,
                video: !!this.modalityPolicies.video.enabled
            };
        },
        isEnabled() {
            return ['image', 'audio', 'video'].some(key => !!this.modalities[key]);
        },
        enabledLabels() {
            const labels = [];
            if (this.modalities.image) labels.push('图片');
            if (this.modalities.audio) labels.push('音频');
            if (this.modalities.video) labels.push('视频');
            return labels;
        },
        srcAllowPatternsText() {
            const patterns = Array.isArray(this.imageConfig.src_allow_patterns)
                ? this.imageConfig.src_allow_patterns
                : [];
            return patterns.join('\n');
        },
        audioNetworkCapture() {
            return {
                enabled: false,
                timeout_seconds: 2.5,
                transport: 'page_websocket_probe',
                url_patterns: ['voicegenie', 'speech', 'audio', 'tts'],
                extractor: 'voicegenie_ogg_pages',
                settle_seconds: 0.35,
                max_payload_bytes: 10 * 1024 * 1024,
                ...((this.imageConfig && this.imageConfig.audio_network_capture) || {})
            };
        },
        audioNetworkMaxPayloadMb() {
            const raw = Number(this.audioNetworkCapture.max_payload_bytes || 0);
            const fallback = Number(this.imageConfig.max_size_mb || 10);
            if (!Number.isFinite(raw) || raw <= 0) return fallback;
            return Math.round((raw / 1024 / 1024) * 10) / 10;
        },
        audioNetworkUrlPatternsText() {
            const patterns = Array.isArray(this.audioNetworkCapture.url_patterns)
                ? this.audioNetworkCapture.url_patterns
                : [];
            return patterns.join('\n');
        },
        audioCaptureEnabled() {
            return this.imageConfig.audio_capture_enabled !== false;
        }
    },
    watch: {
        currentDomain(newVal) {
            if (newVal) this.checkCurrentPreset();
        }
    },
    mounted() {
        this.loadPresets();
        if (this.currentDomain) this.checkCurrentPreset();
    },
    methods: {
        buildAuthHeaders(extraHeaders = {}) {
            const token = String(localStorage.getItem('api_token') || '').trim();
            const headers = { ...extraHeaders };
            if (token) {
                headers['Authorization'] = 'Bearer ' + token;
            }
            return headers;
        },

        toggle() {
            this.$emit('update:collapsed', !this.collapsed);
        },

        buildNextConfig(patch = {}) {
            const nextModalities = {
                ...this.modalityPolicies
            };
            if (patch && patch.modalities && typeof patch.modalities === 'object') {
                for (const type of ['image', 'audio', 'video']) {
                    if (Object.prototype.hasOwnProperty.call(patch.modalities, type)) {
                        nextModalities[type] = this.normalizeModalityPolicy(
                            type,
                            patch.modalities[type],
                            nextModalities[type]
                        );
                    }
                }
            }
            const next = {
                ...this.imageConfig,
                ...patch,
                modalities: nextModalities
            };
            next.enabled = ['image', 'audio', 'video'].some(key => !!(next.modalities[key] && next.modalities[key].enabled));
            return next;
        },

        updateField(field, value) {
            const newConfig = this.buildNextConfig({ [field]: value });
            this.$emit('update-image-config', newConfig);
        },

        numberOrFallback(value, fallback) {
            const parsed = Number(value);
            return Number.isFinite(parsed) ? parsed : fallback;
        },

        defaultModalityPolicy(type, enabled = false) {
            const base = {
                enabled: !!enabled,
                run_policy: enabled ? 'on_signal' : 'disabled',
                quick_probe_timeout_seconds: 1.0
            };
            if (type === 'audio') {
                return {
                    ...base,
                    run_policy: enabled ? 'probe_if_trigger_found' : 'disabled',
                    capture_timeout_seconds: 12.0
                };
            }
            if (type === 'video') {
                return {
                    ...base,
                    late_wait_timeout_seconds: 90.0
                };
            }
            return {
                ...base,
                late_wait_timeout_seconds: 45.0,
                blind_wait_timeout_seconds: 1.0
            };
        },

        normalizeModalityPolicy(type, value, fallback = null) {
            const current = fallback || this.defaultModalityPolicy(type, false);
            if (value && typeof value === 'object' && !Array.isArray(value)) {
                const enabled = !!value.enabled;
                const allowedPolicies = ['disabled', 'generic_only', 'on_signal', 'probe_if_trigger_found', 'always_probe'];
                let runPolicy = String(value.run_policy || current.run_policy || '').trim();
                if (!allowedPolicies.includes(runPolicy)) {
                    runPolicy = enabled ? this.defaultModalityPolicy(type, true).run_policy : 'disabled';
                }
                if (!enabled) {
                    runPolicy = 'disabled';
                }
                return {
                    ...this.defaultModalityPolicy(type, enabled),
                    ...current,
                    ...value,
                    enabled,
                    run_policy: runPolicy
                };
            }
            return this.defaultModalityPolicy(type, !!value);
        },

        updateModalityPolicy(type, patch = {}) {
            const current = this.modalityPolicies[type] || this.defaultModalityPolicy(type, false);
            const nextPolicy = this.normalizeModalityPolicy(type, { ...current, ...(patch || {}) }, current);
            this.updateField('modalities', {
                ...this.modalityPolicies,
                [type]: nextPolicy
            });
        },

        updateSrcAllowPatterns(text) {
            const patterns = String(text || '')
                .split(/\r?\n/)
                .map(line => line.trim())
                .filter(Boolean);
            this.updateField('src_allow_patterns', patterns);
        },

        updateAudioNetworkCapture(patch = {}) {
            const nextConfig = {
                ...this.audioNetworkCapture,
                ...(patch || {})
            };
            this.updateField('audio_network_capture', nextConfig);
        },

        updateAudioNetworkUrlPatterns(text) {
            const patterns = String(text || '')
                .split(/\r?\n/)
                .map(line => line.trim())
                .filter(Boolean);
            this.updateAudioNetworkCapture({ url_patterns: patterns });
        },

        toggleModality(type) {
            const enabled = !this.modalities[type];
            this.updateModalityPolicy(type, {
                enabled,
                run_policy: enabled ? this.defaultModalityPolicy(type, true).run_policy : 'disabled'
            });
        },

        modalityCardClass(type) {
            return this.modalities[type]
                ? 'border-blue-400 bg-blue-50 dark:bg-blue-900/20 dark:border-blue-500/50'
                : 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40';
        },

        inputWrapClass(enabled) {
            return enabled ? '' : 'opacity-50';
        },

        async loadPresets() {
            if (this.loadingPresets) return;
            this.loadingPresets = true;
            try {
                const response = await fetch('/api/image-presets', {
                    headers: this.buildAuthHeaders()
                });
                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || ('HTTP ' + response.status));
                }
                const data = await response.json();
                this.availablePresets = data.presets || [];
            } catch (e) {
                console.error('加载预设失败:', e);
            } finally {
                this.loadingPresets = false;
            }
        },

        async checkCurrentPreset() {
            if (!this.currentDomain) return;
            try {
                const response = await fetch('/api/sites/' + encodeURIComponent(this.currentDomain) + '/image-preset', {
                    headers: this.buildAuthHeaders()
                });
                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || ('HTTP ' + response.status));
                }
                const data = await response.json();
                this.currentPreset = data.available ? data : null;
            } catch (e) {
                console.error('读取当前图片预设失败:', e);
                this.currentPreset = null;
            }
        },

        async applyPreset(presetDomain) {
            if (!this.currentDomain) return;
            try {
                const response = await fetch('/api/sites/' + encodeURIComponent(this.currentDomain) + '/apply-image-preset', {
                    method: 'POST',
                    headers: this.buildAuthHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ preset_domain: presetDomain })
                });

                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || ('HTTP ' + response.status));
                }

                this.showPresetMenu = false;
                this.$emit('reload-config');
                await this.checkCurrentPreset();
            } catch (e) {
                alert('应用预设失败: ' + e.message);
            }
        },

        togglePresetMenu(e) {
            e.stopPropagation();
            this.showPresetMenu = !this.showPresetMenu;
            if (this.showPresetMenu && this.availablePresets.length === 0) {
                this.loadPresets();
            }
        },

        closeMenu() {
            this.showPresetMenu = false;
        }
    },
    template: `
        <div class="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-sm" @click="closeMenu">
            <div class="px-4 py-3 border-b dark:border-gray-700 flex justify-between items-center cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                 @click="toggle">
                <div class="flex items-center gap-2 flex-wrap">
                    <span class="w-4 inline-flex justify-center text-gray-500 dark:text-gray-400" v-html="collapsed ? $icons.chevronDown : $icons.chevronUp"></span>
                    <h3 class="font-semibold text-gray-900 dark:text-white">🎞️ 多模态提取</h3>
                    <span v-if="isEnabled" class="px-2 py-0.5 text-xs bg-green-100 dark:bg-green-900/50 text-green-700 dark:text-green-300 rounded font-medium">已启用</span>
                    <span v-else class="px-2 py-0.5 text-xs bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 rounded">未启用</span>
                    <span v-for="label in enabledLabels" :key="label"
                          class="px-2 py-0.5 text-xs bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 rounded font-medium">
                        {{ label }}
                    </span>
                    <span v-if="currentPreset && currentPreset.available"
                          class="px-2 py-0.5 text-xs bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 rounded font-medium flex items-center gap-1">
                        <svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><path d="M10 2a6 6 0 00-6 6v3.586l-.707.707A1 1 0 004 14h12a1 1 0 00.707-1.707L16 11.586V8a6 6 0 00-6-6zM10 18a3 3 0 01-3-3h6a3 3 0 01-3 3z"/></svg>
                        预设
                    </span>
                </div>

                <div class="flex items-center gap-2" @click.stop>
                    <div class="relative">
                        <button @click="togglePresetMenu"
                                class="px-3 py-1.5 text-sm font-medium text-gray-700 dark:text-gray-200 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors flex items-center gap-1">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
                            </svg>
                            预设
                        </button>

                        <div v-if="showPresetMenu" class="absolute right-0 mt-1 w-80 max-h-96 overflow-y-auto bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-lg z-10">
                            <div v-if="currentPreset && currentPreset.available" class="px-3 py-2 bg-blue-50 dark:bg-blue-900/30 border-b dark:border-gray-700 text-xs text-blue-700 dark:text-blue-300">
                                <div class="font-medium">当前使用预设</div>
                                <div class="mt-0.5">{{ currentPreset.name }}</div>
                            </div>
                            <div v-if="loadingPresets" class="px-3 py-6 text-center text-sm text-gray-400">加载中...</div>
                            <div v-else-if="availablePresets.length > 0" class="divide-y dark:divide-gray-700">
                                <div class="px-3 py-1.5 bg-gray-50 dark:bg-gray-900/50 text-xs font-medium text-gray-500">站点预设</div>
                                <button v-for="preset in availablePresets" :key="preset.domain"
                                        @click="applyPreset(preset.domain)"
                                        class="w-full text-left px-3 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors">
                                    <div class="font-medium text-sm text-gray-900 dark:text-white truncate">{{ preset.name }}</div>
                                    <div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{{ preset.domain }}</div>
                                </button>
                            </div>
                            <div v-else class="px-3 py-6 text-center text-sm text-gray-400">暂无可用预设</div>
                        </div>
                    </div>
                </div>
            </div>

            <div v-show="!collapsed" class="p-4 space-y-4">
                <div>
                    <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">提取类型</div>
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                        <div :class="['border rounded-xl p-4 transition-colors', modalityCardClass('image')]">
                            <div class="flex items-start justify-between gap-3">
                                <div>
                                    <div class="text-sm font-medium text-gray-900 dark:text-white">图片提取</div>
                                    <div class="mt-1 text-xs text-gray-500 dark:text-gray-400">提取回复中的图片资源，并以图片形式返回。</div>
                                </div>
                                <label class="toggle-label scale-90">
                                    <input type="checkbox" :checked="modalities.image" @change="toggleModality('image')" class="sr-only peer">
                                    <div class="toggle-bg"></div>
                                </label>
                            </div>
                            <div class="mt-4 grid grid-cols-3 gap-2">
                                <div>
                                    <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">运行策略</label>
                                    <select :value="modalityPolicies.image.run_policy"
                                            @change="updateModalityPolicy('image', { run_policy: $event.target.value, enabled: $event.target.value !== 'disabled' })"
                                            class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-xs bg-white dark:bg-gray-700 text-gray-900 dark:text-white">
                                        <option value="disabled">disabled</option>
                                        <option value="generic_only">generic_only</option>
                                        <option value="on_signal">on_signal</option>
                                        <option value="always_probe">always_probe</option>
                                    </select>
                                </div>
                                <div>
                                    <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">快速探测</label>
                                    <input type="number"
                                           :value="modalityPolicies.image.quick_probe_timeout_seconds"
                                           @input="updateModalityPolicy('image', { quick_probe_timeout_seconds: numberOrFallback($event.target.value, 1) })"
                                           min="0.1" max="10" step="0.1"
                                           :disabled="!modalities.image"
                                           class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-xs bg-white dark:bg-gray-700 text-gray-900 dark:text-white disabled:opacity-50">
                                </div>
                                <div>
                                    <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">长等上限</label>
                                    <input type="number"
                                           :value="modalityPolicies.image.late_wait_timeout_seconds"
                                           @input="updateModalityPolicy('image', { late_wait_timeout_seconds: numberOrFallback($event.target.value, 45) })"
                                           min="0.2" max="300" step="1"
                                           :disabled="!modalities.image || modalityPolicies.image.run_policy === 'generic_only'"
                                           class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-xs bg-white dark:bg-gray-700 text-gray-900 dark:text-white disabled:opacity-50">
                                </div>
                            </div>
                            <div class="mt-2">
                                <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">无占位盲等</label>
                                <input type="number"
                                       :value="modalityPolicies.image.blind_wait_timeout_seconds"
                                       @input="updateModalityPolicy('image', { blind_wait_timeout_seconds: numberOrFallback($event.target.value, 1) })"
                                       min="0" max="300" step="0.5"
                                       :disabled="!modalities.image || modalityPolicies.image.run_policy === 'generic_only'"
                                       class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-xs bg-white dark:bg-gray-700 text-gray-900 dark:text-white disabled:opacity-50">
                            </div>
                        </div>

                        <div :class="['border rounded-xl p-4 transition-colors', modalityCardClass('audio')]">
                            <div class="flex items-start justify-between gap-3">
                                <div>
                                    <div class="text-sm font-medium text-gray-900 dark:text-white">音频文件提取</div>
                                    <div class="mt-1 text-xs text-gray-500 dark:text-gray-400">提取回复里的音频节点或音频源链接。</div>
                                </div>
                                <label class="toggle-label scale-90">
                                    <input type="checkbox" :checked="modalities.audio" @change="toggleModality('audio')" class="sr-only peer">
                                    <div class="toggle-bg"></div>
                                </label>
                            </div>
                            <div class="mt-4 grid grid-cols-3 gap-2">
                                <div>
                                    <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">运行策略</label>
                                    <select :value="modalityPolicies.audio.run_policy"
                                            @change="updateModalityPolicy('audio', { run_policy: $event.target.value, enabled: $event.target.value !== 'disabled' })"
                                            class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-xs bg-white dark:bg-gray-700 text-gray-900 dark:text-white">
                                        <option value="disabled">disabled</option>
                                        <option value="generic_only">generic_only</option>
                                        <option value="on_signal">on_signal</option>
                                        <option value="probe_if_trigger_found">probe_if_trigger_found</option>
                                        <option value="always_probe">always_probe</option>
                                    </select>
                                </div>
                                <div>
                                    <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">快速探测</label>
                                    <input type="number"
                                           :value="modalityPolicies.audio.quick_probe_timeout_seconds"
                                           @input="updateModalityPolicy('audio', { quick_probe_timeout_seconds: numberOrFallback($event.target.value, 1) })"
                                           min="0.1" max="10" step="0.1"
                                           :disabled="!modalities.audio"
                                           class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-xs bg-white dark:bg-gray-700 text-gray-900 dark:text-white disabled:opacity-50">
                                </div>
                                <div>
                                    <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">录制上限</label>
                                    <input type="number"
                                           :value="modalityPolicies.audio.capture_timeout_seconds"
                                           @input="updateModalityPolicy('audio', { capture_timeout_seconds: numberOrFallback($event.target.value, 12) })"
                                           min="0.2" max="180" step="0.5"
                                           :disabled="!modalities.audio || !['probe_if_trigger_found', 'always_probe', 'on_signal'].includes(modalityPolicies.audio.run_policy)"
                                           class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-xs bg-white dark:bg-gray-700 text-gray-900 dark:text-white disabled:opacity-50">
                                </div>
                            </div>
                        </div>

                        <div :class="['border rounded-xl p-4 transition-colors', modalityCardClass('video')]">
                            <div class="flex items-start justify-between gap-3">
                                <div>
                                    <div class="text-sm font-medium text-gray-900 dark:text-white">视频提取</div>
                                    <div class="mt-1 text-xs text-gray-500 dark:text-gray-400">提取回复里的视频节点或视频源链接。</div>
                                </div>
                                <label class="toggle-label scale-90">
                                    <input type="checkbox" :checked="modalities.video" @change="toggleModality('video')" class="sr-only peer">
                                    <div class="toggle-bg"></div>
                                </label>
                            </div>
                            <div class="mt-4 grid grid-cols-3 gap-2">
                                <div>
                                    <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">运行策略</label>
                                    <select :value="modalityPolicies.video.run_policy"
                                            @change="updateModalityPolicy('video', { run_policy: $event.target.value, enabled: $event.target.value !== 'disabled' })"
                                            class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-xs bg-white dark:bg-gray-700 text-gray-900 dark:text-white">
                                        <option value="disabled">disabled</option>
                                        <option value="generic_only">generic_only</option>
                                        <option value="on_signal">on_signal</option>
                                        <option value="always_probe">always_probe</option>
                                    </select>
                                </div>
                                <div>
                                    <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">快速探测</label>
                                    <input type="number"
                                           :value="modalityPolicies.video.quick_probe_timeout_seconds"
                                           @input="updateModalityPolicy('video', { quick_probe_timeout_seconds: numberOrFallback($event.target.value, 1) })"
                                           min="0.1" max="10" step="0.1"
                                           :disabled="!modalities.video"
                                           class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-xs bg-white dark:bg-gray-700 text-gray-900 dark:text-white disabled:opacity-50">
                                </div>
                                <div>
                                    <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">长等上限</label>
                                    <input type="number"
                                           :value="modalityPolicies.video.late_wait_timeout_seconds"
                                           @input="updateModalityPolicy('video', { late_wait_timeout_seconds: numberOrFallback($event.target.value, 90) })"
                                           min="0.2" max="300" step="1"
                                           :disabled="!modalities.video || modalityPolicies.video.run_policy === 'generic_only'"
                                           class="w-full border dark:border-gray-600 px-2 py-1.5 rounded-md text-xs bg-white dark:bg-gray-700 text-gray-900 dark:text-white disabled:opacity-50">
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <div v-if="!isEnabled" class="bg-gray-50 dark:bg-gray-900/50 border border-gray-200 dark:border-gray-700 rounded-lg p-3 text-center">
                    <div class="text-gray-500 dark:text-gray-400 text-sm">当前未启用任何提取类型。请至少开启一种媒体提取能力。</div>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div :class="inputWrapClass(modalities.image)">
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">图片选择器</label>
                        <input type="text" :value="imageConfig.selector" @input="updateField('selector', $event.target.value)" placeholder="img"
                               :disabled="!modalities.image"
                               class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent disabled:cursor-not-allowed">
                        <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">默认 <code>img</code></p>
                    </div>
                    <div :class="inputWrapClass(modalities.audio)">
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">音频选择器</label>
                        <input type="text" :value="imageConfig.audio_selector" @input="updateField('audio_selector', $event.target.value)" placeholder="audio, audio source"
                               :disabled="!modalities.audio"
                               class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent disabled:cursor-not-allowed">
                        <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">默认 <code>audio, audio source</code></p>
                    </div>
                    <div :class="inputWrapClass(modalities.video)">
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">视频选择器</label>
                        <input type="text" :value="imageConfig.video_selector" @input="updateField('video_selector', $event.target.value)" placeholder="video, video source"
                               :disabled="!modalities.video"
                               class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent disabled:cursor-not-allowed">
                        <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">默认 <code>video, video source</code></p>
                    </div>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">容器选择器 <span class="text-gray-400 font-normal">(可选)</span></label>
                        <input type="text" :value="imageConfig.container_selector || ''" @input="updateField('container_selector', $event.target.value || null)" placeholder="留空则使用响应容器"
                               class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                        <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">统一限定媒体查找范围</p>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">提取模式</label>
                        <select :value="imageConfig.mode" @change="updateField('mode', $event.target.value)"
                                class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                            <option value="all">全部提取</option>
                            <option value="first">仅第一项</option>
                            <option value="last">仅最后一项</option>
                        </select>
                    </div>
                </div>

                <div :class="inputWrapClass(modalities.image)">
                    <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">图片来源白名单 <span class="text-gray-400 font-normal">(可选)</span></label>
                    <textarea :value="srcAllowPatternsText" @input="updateSrcAllowPatterns($event.target.value)" rows="5"
                              placeholder="^data:image/\n^blob:\n^https?://[^/]*oaiusercontent\\.com/\n^https?://[^/]*oaistatic\\.com/\n^https?://cdn\\.openai\\.com/"
                              :disabled="!modalities.image"
                              class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent disabled:cursor-not-allowed"></textarea>
                    <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">每行一个正则，仅提取匹配这些 <code>src</code> 的图片。留空表示不过滤。</p>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">最大大小 (MB)</label>
                        <select :value="imageConfig.max_size_mb" @change="updateField('max_size_mb', parseInt($event.target.value))"
                                class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                            <option :value="5">5 MB</option>
                            <option :value="10">10 MB</option>
                            <option :value="20">20 MB</option>
                            <option :value="50">50 MB</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">防抖延迟 (秒)</label>
                        <input type="number" :value="imageConfig.debounce_seconds" @input="updateField('debounce_seconds', parseFloat($event.target.value) || 2)" min="0" max="30" step="0.5"
                               class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">加载超时时间 (秒)</label>
                        <input type="number" :value="imageConfig.load_timeout_seconds" @input="updateField('load_timeout_seconds', parseFloat($event.target.value) || 5)"
                               min="1" max="60" step="1" :disabled="!imageConfig.wait_for_load"
                               :class="['w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent', !imageConfig.wait_for_load ? 'opacity-50 cursor-not-allowed' : '']">
                    </div>
                </div>

                <div :class="inputWrapClass(modalities.image)" class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Canvas 导出格式</label>
                        <select :value="imageConfig.canvas_export_mime || 'image/jpeg'"
                                @change="updateField('canvas_export_mime', $event.target.value)"
                                :disabled="!modalities.image"
                                class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent disabled:cursor-not-allowed">
                            <option value="image/jpeg">JPEG</option>
                            <option value="image/webp">WebP</option>
                            <option value="image/png">PNG</option>
                        </select>
                        <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">用于 blob 图片 Canvas 兜底导出。JPEG/WebP 体积更小。</p>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Canvas 导出质量</label>
                        <input type="number"
                               :value="imageConfig.canvas_export_quality === undefined ? 0.88 : imageConfig.canvas_export_quality"
                               @input="updateField('canvas_export_quality', numberOrFallback($event.target.value, 0.88))"
                               min="0.1" max="1" step="0.01"
                               :disabled="!modalities.image || (imageConfig.canvas_export_mime || 'image/jpeg') === 'image/png'"
                               class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent disabled:opacity-50 disabled:cursor-not-allowed">
                        <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">仅 JPEG/WebP 生效，建议 0.80-0.92。</p>
                    </div>
                </div>

                <div class="border-t dark:border-gray-700 pt-4">
                    <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">高级选项</div>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div class="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg">
                            <div>
                                <div class="text-sm font-medium text-gray-700 dark:text-gray-300">等待媒体加载</div>
                                <div class="text-xs text-gray-500 dark:text-gray-400">等待音视频或图片完成加载后再提取</div>
                            </div>
                            <label class="toggle-label scale-90">
                                <input type="checkbox" :checked="imageConfig.wait_for_load" @change="updateField('wait_for_load', $event.target.checked)" class="sr-only peer">
                                <div class="toggle-bg"></div>
                            </label>
                        </div>
                        <div class="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg">
                            <div>
                                <div class="text-sm font-medium text-gray-700 dark:text-gray-300">转换 Blob 媒体</div>
                                <div class="text-xs text-gray-500 dark:text-gray-400">将 blob: 资源转为可返回的数据 URI 或本地文件</div>
                            </div>
                            <label class="toggle-label scale-90">
                                <input type="checkbox" :checked="imageConfig.download_blobs" @change="updateField('download_blobs', $event.target.checked)" class="sr-only peer">
                                <div class="toggle-bg"></div>
                            </label>
                        </div>
                    </div>
                </div>

                <div v-if="modalities.audio" class="border-t dark:border-gray-700 pt-4">
                    <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">音频捕获</div>

                    <div class="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg mb-3">
                        <div>
                            <div class="text-sm font-medium text-gray-700 dark:text-gray-300">页面播放音频捕获回退</div>
                            <div class="text-xs text-gray-500 dark:text-gray-400">直接提取不到音频时，允许触发页面播放并录制音频。Gemini 直连提取预设应保持关闭。</div>
                        </div>
                        <label class="toggle-label scale-90">
                            <input type="checkbox" :checked="audioCaptureEnabled" @change="updateField('audio_capture_enabled', $event.target.checked)" class="sr-only peer">
                            <div class="toggle-bg"></div>
                        </label>
                    </div>

                    <div class="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg">
                        <div>
                            <div class="text-sm font-medium text-gray-700 dark:text-gray-300">启用网络音频捕获</div>
                            <div class="text-xs text-gray-500 dark:text-gray-400">优先尝试从页面内 WebSocket / 网络流直接提取音频；是否继续回退到页面录音由上方开关控制。</div>
                        </div>
                        <label class="toggle-label scale-90">
                            <input type="checkbox" :checked="audioNetworkCapture.enabled" @change="updateAudioNetworkCapture({ enabled: $event.target.checked })" class="sr-only peer">
                            <div class="toggle-bg"></div>
                        </label>
                    </div>

                    <div :class="['mt-4 space-y-4', audioNetworkCapture.enabled ? '' : 'opacity-50']">
                        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">捕获超时 (秒)</label>
                                <input type="number"
                                       :value="audioNetworkCapture.timeout_seconds"
                                       @input="updateAudioNetworkCapture({ timeout_seconds: numberOrFallback($event.target.value, 2.5) })"
                                       min="0.1" max="15" step="0.1"
                                       :disabled="!audioNetworkCapture.enabled"
                                       class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent disabled:cursor-not-allowed">
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">传输类型</label>
                                <select :value="audioNetworkCapture.transport"
                                        @change="updateAudioNetworkCapture({ transport: $event.target.value })"
                                        :disabled="!audioNetworkCapture.enabled"
                                        class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent disabled:cursor-not-allowed">
                                    <option value="page_websocket_probe">页面 WebSocket Probe</option>
                                </select>
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">提取器</label>
                                <select :value="audioNetworkCapture.extractor"
                                        @change="updateAudioNetworkCapture({ extractor: $event.target.value })"
                                        :disabled="!audioNetworkCapture.enabled"
                                        class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent disabled:cursor-not-allowed">
                                    <option value="voicegenie_ogg_pages">voicegenie_ogg_pages</option>
                                </select>
                            </div>
                        </div>

                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">稳定等待窗口 (秒)</label>
                                <input type="number"
                                       :value="audioNetworkCapture.settle_seconds"
                                       @input="updateAudioNetworkCapture({ settle_seconds: numberOrFallback($event.target.value, 0.35) })"
                                       min="0.05" max="5" step="0.05"
                                       :disabled="!audioNetworkCapture.enabled"
                                       class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent disabled:cursor-not-allowed">
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">单条载荷上限 (MB)</label>
                                <input type="number"
                                       :value="audioNetworkMaxPayloadMb"
                                       @input="updateAudioNetworkCapture({ max_payload_bytes: Math.round(numberOrFallback($event.target.value, 10) * 1024 * 1024) })"
                                       min="0.1" max="100" step="0.5"
                                       :disabled="!audioNetworkCapture.enabled"
                                       class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent disabled:cursor-not-allowed">
                                <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">超过上限的二进制帧只记录摘要，不回传 Base64。</p>
                            </div>
                        </div>

                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">URL 匹配关键字</label>
                            <textarea :value="audioNetworkUrlPatternsText"
                                      @input="updateAudioNetworkUrlPatterns($event.target.value)"
                                      rows="4"
                                      :disabled="!audioNetworkCapture.enabled"
                                      placeholder="voicegenie&#10;speech&#10;audio&#10;tts"
                                      class="w-full border dark:border-gray-600 px-3 py-2 rounded-md text-sm font-mono bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-400 focus:border-transparent disabled:cursor-not-allowed"></textarea>
                            <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">每行一个关键字，命中对应 WebSocket / 网络请求 URL 时才会参与音频聚合。</p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `
};
