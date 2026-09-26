// R2-8：由 dashboard-methods.js 拆分——数据、选择器与工作流操作。方法原样迁出，由 dashboard-methods.js 统一组装。
(() => {
    const {
        DEFAULT_SELECTOR_DEFINITIONS,
        BROWSER_CONSTANTS_SCHEMA,
        ENV_CONFIG_SCHEMA,
        DASHBOARD_TOKEN_STORAGE_KEY,
        LEGACY_API_TOKEN_STORAGE_KEY,
        SITE_CONFIG_IMPORT_MAX_BYTES,
        SETTINGS_BACKUP_IMPORT_MAX_BYTES,
        importFileSizeError,
        formatGitCompareErrorText,
        getStoredDashboardToken,
        setStoredDashboardToken,
        SITE_FRIENDLY_NAMES,
        GENERIC_DOMAIN_LABELS,
        siteDisplayName,
    } = window.DashboardShared;
    (window.DashboardMethodParts = window.DashboardMethodParts || []).push({
        // ========== 数据操作 ==========

        normalizeConfig(raw) {
            const norm = {}
            // 预设内的字段列表（用于清理顶层残留）
            const PRESET_FIELDS = [
                'selectors', 'workflow', 'stealth', 'stream_config',
                'image_extraction', 'file_paste', 'prompt_padding',
                'extractor_id', 'extractor_verified', 'model_catalog'
            ]
            for (const [k, v] of Object.entries(raw || {})) {
                if (v.presets) {
                    // 新格式：保留 presets 结构，确保每个预设有基本字段
                    const normalizedPresets = {}
                    for (const [presetName, presetData] of Object.entries(v.presets)) {
                        normalizedPresets[presetName] = {
                            ...presetData,
                            selectors: presetData.selectors || {},
                            workflow: presetData.workflow || [],
                            stealth: !!presetData.stealth
                        }
                    }
                    const presetKeys = Object.keys(normalizedPresets)
                    const configuredDefault = typeof v.default_preset === 'string'
                        ? v.default_preset
                        : null
                    const resolvedDefault = (configuredDefault && normalizedPresets[configuredDefault])
                        ? configuredDefault
                        : (normalizedPresets['主预设'] ? '主预设' : (presetKeys[0] || '主预设'))
                    // 构建站点对象，只保留 presets，清理预设外的残留字段
                    const siteObj = {
                        presets: normalizedPresets,
                        default_preset: resolvedDefault
                    }
                    // 保留非预设字段（如未来可能的站点级元数据）
                    for (const [field, value] of Object.entries(v)) {
                        if (field !== 'presets' && field !== 'default_preset' && !PRESET_FIELDS.includes(field)) {
                            siteObj[field] = value
                        }
                    }
                    if (siteObj.domain) {
                        siteObj.domain = k
                    }
                    norm[k] = siteObj
                } else {
                    // 旧格式兼容：包装为预设（后端迁移后不应再出现，但做兜底）
                    norm[k] = {
                        default_preset: '主预设',
                        presets: {
                            '主预设': {
                                ...v,
                                selectors: v.selectors || {},
                                workflow: v.workflow || [],
                                stealth: !!v.stealth
                            }
                        }
                    }
                }
            }
            return norm
        },

        validateConfig() {
            if (!this.currentDomain || !this.currentConfig) {
                this.notify('请选择站点', 'warning')
                return false
            }

            // 获取当前活跃预设的配置
            const presetConfig = this.getActivePresetConfig()
            if (!presetConfig) {
                this.notify('无法获取预设配置', 'error')
                return false
            }

            const selectors = presetConfig.selectors || {}
            const workflow = presetConfig.workflow || []
            const hasSelectorActions = workflow.some(step => ['FILL_INPUT', 'SELECT_MODEL', 'CLICK', 'STREAM_WAIT'].includes(step.action))
            if (hasSelectorActions && Object.keys(selectors).length === 0) {
                this.notify('至少需要一个选择器', 'warning')
                return false
            }

            for (let i = 0; i < workflow.length; i++) {
                const step = workflow[i]

                if (!step.action) {
                    this.notify('步骤 ' + (i + 1) + ': 缺少动作类型', 'error')
                    return false
                }

                if (['FILL_INPUT', 'SELECT_MODEL', 'CLICK', 'STREAM_WAIT'].includes(step.action)) {
                    if (!step.target) {
                        this.notify('步骤 ' + (i + 1) + ': 请选择目标选择器', 'error')
                        return false
                    }
                }

                if (step.action === 'COORD_CLICK') {
                    const x = Number(step.value?.x)
                    const y = Number(step.value?.y)
                    if (!Number.isFinite(x) || !Number.isFinite(y)) {
                        this.notify('步骤 ' + (i + 1) + ': 请输入有效的 X/Y 坐标', 'error')
                        return false
                    }
                }

                if (step.action === 'COORD_SCROLL') {
                    const startX = Number(step.value?.start_x)
                    const startY = Number(step.value?.start_y)
                    const endX = Number(step.value?.end_x)
                    const endY = Number(step.value?.end_y)
                    if (![startX, startY, endX, endY].every(Number.isFinite)) {
                        this.notify('步骤 ' + (i + 1) + ': 请输入完整的起点/终点坐标', 'error')
                        return false
                    }
                }

                if (step.action === 'KEY_PRESS' && !step.target) {
                    this.notify('步骤 ' + (i + 1) + ': 请输入按键名称', 'error')
                    return false
                }

                if (step.action === 'WAIT' && (!step.value || step.value <= 0)) {
                    this.notify('步骤 ' + (i + 1) + ': 等待时间必须大于 0', 'error')
                    return false
                }
            }

            for (let i = 0; i < workflow.length; i++) {
                const step = workflow[i]
                if (step.action === 'JS_EXEC' && !String(step.value || '').trim()) {
                    this.notify('步骤 ' + (i + 1) + ': 请输入 JavaScript 代码', 'error')
                    return false
                }
            }

            return true
        },

        selectSite(domain) {
            if (!domain || !this.sites[domain]) return
            this.flushConfigTabDrafts()
            this.currentDomain = domain
            this.siteConfigView = 'editor'
            this.searchQuery = ''
            this.$nextTick(() => document.querySelector('.dashboard-scroll-region')?.scrollTo({ top: 0 }))
        },

        selectSiteFromSearch(domain) {
            if (!domain) return
            this.selectSite(domain)
            this.searchQuery = ''
        },

        addNewSite() {
            const domain = prompt('请输入域名（例如: chat.example.com）:')
            if (!domain) return
            this.siteConfigView = 'editor'

            if (this.sites[domain]) {
                this.notify('该站点已存在', 'warning')
                this.currentDomain = domain
                return
            }

            this.sites[domain] = {
                default_preset: '主预设',
                presets: {
                    '主预设': {
                        selectors: {},
                        workflow: [],
                        stealth: false
                    }
                }
            }
            this.currentDomain = domain
            this.notify('已创建站点: ' + domain, 'success')
        },

        confirmDelete(domain) {
            if (!confirm('确定要删除 ' + domain + ' 的配置吗？')) {
                return
            }

            delete this.sites[domain]

            if (this.currentDomain === domain) {
                this.currentDomain = Object.keys(this.sites)[0] || null
            }

            this.notify('已删除: ' + domain, 'info')
        },

        // ========== 选择器操作 ==========

        addSelector(preset) {
            this.showSelectorMenu = false
            const pc = this.getActivePresetConfig()
            if (!pc) return

            let key
            if (preset === 'custom') {
                key = prompt('请输入选择器名称（例如: input_box）')
                if (!key) return
            } else {
                key = preset
            }

            if (pc.selectors[key]) {
                this.notify('选择器 "' + key + '" 已存在', 'warning')
                return
            }

            pc.selectors[key] = ''
            this.notify('已添加选择器: ' + key, 'success')
        },

        removeSelector(key) {
            if (!confirm('确定删除选择器 ' + key + ' 吗？')) {
                return
            }

            const pc = this.getActivePresetConfig()
            if (!pc) return

            delete pc.selectors[key]

                ; (pc.workflow || []).forEach(function (step) {
                    if (step.target === key) {
                        step.target = ''
                    }
                })
        },

        updateSelectorKey(oldKey, newKey) {
            if (!newKey || oldKey === newKey) return

            newKey = newKey.trim()

            const pc = this.getActivePresetConfig()
            if (!pc) return

            if (pc.selectors[newKey]) {
                this.notify('该键名已存在', 'error')
                return
            }

            pc.selectors[newKey] = pc.selectors[oldKey]
            delete pc.selectors[oldKey]

                ; (pc.workflow || []).forEach(function (step) {
                    if (step.target === oldKey) {
                        step.target = newKey
                    }
                })
        },

        // ========== 工作流操作 ==========

        addStep() {
            const pc = this.getActivePresetConfig()
            if (!pc) return

            const defaultStep = {
                action: 'CLICK',
                target: '',
                optional: false,
                value: null
            }

            if (!pc.workflow) pc.workflow = []
            pc.workflow.push(defaultStep)
        },

        removeStep(index) {
            const pc = this.getActivePresetConfig()
            if (!pc || !pc.workflow) return

            pc.workflow.splice(index, 1)
        },

        moveStep(index, direction) {
            const pc = this.getActivePresetConfig()
            if (!pc || !pc.workflow) return

            const arr = pc.workflow
            const newIndex = index + direction

            if (newIndex < 0 || newIndex >= arr.length) return

            const temp = arr[index]
            arr[index] = arr[newIndex]
            arr[newIndex] = temp
        },

        onActionChange(step) {
            if (step.action === 'SELECT_MODEL') {
                if (!step.target) step.target = 'model_select_btn'
                step.value = { timeout: Number(step.value?.timeout ?? 3) }
            } else if (['FILL_INPUT', 'CLICK', 'STREAM_WAIT'].includes(step.action)) {
                step.value = null
                if (!step.target) step.target = ''
            } else if (step.action === 'PAGE_FETCH') {
                step.target = ''
                step.optional = true
                step.value = null
            } else if (step.action === 'READONLY_HINT') {
                step.target = ''
                const current = (step.value && typeof step.value === 'object' && !Array.isArray(step.value))
                    ? step.value
                    : {}
                step.value = {
                    title: String(current.title || '提示'),
                    text: String(current.text || '这是一条只读提示，不会在执行时触发页面操作。'),
                    tone: ['info', 'success', 'warning', 'danger'].includes(String(current.tone || '').trim().toLowerCase())
                        ? String(current.tone || '').trim().toLowerCase()
                        : 'info'
                }
            } else if (step.action === 'COORD_CLICK') {
                step.target = ''
                step.value = {
                    x: Number(step.value?.x ?? 0),
                    y: Number(step.value?.y ?? 0),
                    random_radius: Number(step.value?.random_radius ?? 10)
                }
            } else if (step.action === 'COORD_SCROLL') {
                step.target = ''
                step.value = {
                    start_x: Number(step.value?.start_x ?? 0),
                    start_y: Number(step.value?.start_y ?? 0),
                    end_x: Number(step.value?.end_x ?? 0),
                    end_y: Number(step.value?.end_y ?? 300)
                }
            } else if (step.action === 'KEY_PRESS') {
                step.value = null
                if (!step.target) step.target = 'Enter'
            } else if (step.action === 'JS_EXEC') {
                step.target = ''
                if (!String(step.value || '').trim()) step.value = 'return document.title;'
            } else if (step.action === 'WAIT') {
                step.target = ''
                if (!step.value) step.value = '1.0'
            }
        },

        showTemplates() {
            this.showStepTemplates = true
        },

        applyTemplate(type) {
            const templates = {
                'default': [
                    { action: 'CLICK', target: 'new_chat_btn', optional: true, value: null },
                    { action: 'WAIT', target: '', optional: false, value: '0.5' },
                    { action: 'FILL_INPUT', target: 'input_box', optional: false, value: null },
                    { action: 'CLICK', target: 'send_btn', optional: true, value: null },
                    { action: 'KEY_PRESS', target: 'Enter', optional: true, value: null },
                    { action: 'STREAM_WAIT', target: 'result_container', optional: false, value: null }
                ],
                'simple': [
                    { action: 'FILL_INPUT', target: 'input_box', optional: false, value: null },
                    { action: 'KEY_PRESS', target: 'Enter', optional: false, value: null },
                    { action: 'STREAM_WAIT', target: 'result_container', optional: false, value: null }
                ],
                'experimental_hint': [
                    { action: 'READONLY_HINT', target: '', optional: false, value: { title: '提示', text: '这是一条只读提示，不会影响执行，只用于说明当前工作流中的特殊行为。', tone: 'info' } },
                    { action: 'PAGE_FETCH', target: '', optional: true, value: null },
                    { action: 'FILL_INPUT', target: 'input_box', optional: false, value: null },
                    { action: 'KEY_PRESS', target: 'Enter', optional: false, value: null },
                    { action: 'STREAM_WAIT', target: 'result_container', optional: false, value: null }
                ],
                'battle_winner': [
                    { action: 'WAIT', target: '', optional: false, value: 2 },
                    { action: 'FILL_INPUT', target: 'input_box', optional: false, value: null },
                    { action: 'WAIT', target: '', optional: false, value: 0.5 },
                    { action: 'CLICK', target: 'retry_send_btn', optional: true, value: null },
                    { action: 'WAIT', target: '', optional: false, value: 0.2 },
                    { action: 'CLICK', target: 'send_btn', optional: false, value: null },
                    { action: 'STREAM_WAIT', target: 'result_container', optional: false, value: null }
                ],
                'battle_left': [
                    { action: 'WAIT', target: '', optional: false, value: 1 },
                    { action: 'FILL_INPUT', target: 'input_box', optional: false, value: null },
                    { action: 'CLICK', target: 'retry_send_btn', optional: true, value: null },
                    { action: 'WAIT', target: '', optional: false, value: 0.8 },
                    { action: 'CLICK', target: 'send_btn', optional: true, value: null },
                    { action: 'STREAM_WAIT', target: 'result_container', optional: false, value: null }
                ],
                'battle_right': [
                    { action: 'WAIT', target: '', optional: false, value: 2 },
                    { action: 'FILL_INPUT', target: 'input_box', optional: false, value: null },
                    { action: 'WAIT', target: '', optional: false, value: 0.5 },
                    { action: 'CLICK', target: 'retry_send_btn', optional: true, value: null },
                    { action: 'WAIT', target: '', optional: false, value: 0.2 },
                    { action: 'CLICK', target: 'send_btn', optional: false, value: null },
                    { action: 'STREAM_WAIT', target: 'result_container', optional: false, value: null }
                ]
            }

            if (!confirm('这将覆盖当前的工作流配置，确定继续吗？')) {
                return
            }

            const pc = this.getActivePresetConfig()
            if (!pc) return
            pc.workflow = JSON.parse(JSON.stringify(templates[type]))

            const battleParserMap = {
                battle_winner: 'lmarena_battle_winner',
                battle_left: 'lmarena_battle_side_left',
                battle_right: 'lmarena_battle_side_right'
            }
            if (battleParserMap[type]) {
                pc.selectors = {
                    ...(pc.selectors || {}),
                    retry_send_btn: pc.selectors?.retry_send_btn || 'button[aria-label="Rerun stopped messages"], button[data-slot="tooltip-trigger"]:has(svg path[d*="21.8883"])',
                    send_btn: pc.selectors?.send_btn || 'button[aria-label="Send message"][type="submit"]:not(:disabled):not([aria-disabled="true"]), button[aria-label="Stop generation"]',
                    generating_indicator: pc.selectors?.generating_indicator || 'button[aria-label="Stop generation"]'
                }
                pc.stream_config = {
                    ...((pc && pc.stream_config) || {}),
                    mode: 'network',
                    send_confirmation: {
                        ...(((pc && pc.stream_config) || {}).send_confirmation || {}),
                        max_retry_count: 0,
                        retry_on_unconfirmed_send: false,
                        post_click_observe_window: 0.5,
                        pre_retry_probe_window: 0,
                        retry_observe_window: 0,
                        trust_generating_indicator: true,
                        trust_network_activity: true
                    },
                    network: {
                        ...(((pc && pc.stream_config) || {}).network || {}),
                        parser: battleParserMap[type],
                        url_pattern: '**/nextjs-api/stream/create-evaluation**',
                        method: 'POST',
                        listen_pattern: '/nextjs-api/stream/create-evaluation',
                        silence_threshold: 10,
                        response_interval: 1
                    }
                }
            }
            this.showStepTemplates = false
            this.notify('模板已应用', 'success')
        },
    });
})();
