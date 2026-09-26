// R2-8：由 dashboard-methods.js 拆分——各方法分组共用的常量与辅助函数（原 IIFE 头部，原样迁出）。
(() => {
    const DEFAULT_SELECTOR_DEFINITIONS = window.DEFAULT_SELECTOR_DEFINITIONS || []
    const BROWSER_CONSTANTS_SCHEMA = window.BROWSER_CONSTANTS_SCHEMA || {}
    const ENV_CONFIG_SCHEMA = window.ENV_CONFIG_SCHEMA || {}
    const DASHBOARD_TOKEN_STORAGE_KEY = 'dashboard_token'
    const LEGACY_API_TOKEN_STORAGE_KEY = 'api_token'
    // H14：导入文件大小上限（整个文件读进内存再 JSON.parse，超大文件会卡死页面）。
    // 正常站点配置 / 完整备份都在数百 KB 级，上限留足余量。
    const SITE_CONFIG_IMPORT_MAX_BYTES = 8 * 1024 * 1024
    const SETTINGS_BACKUP_IMPORT_MAX_BYTES = 32 * 1024 * 1024

    function importFileSizeError(file, maxBytes, label) {
        const size = Number(file && file.size) || 0
        if (size <= maxBytes) return ''
        const mb = (n) => (n / 1024 / 1024).toFixed(n >= 10 * 1024 * 1024 ? 0 : 1)
        return `${label}过大（${mb(size)} MiB），上限 ${mb(maxBytes)} MiB`
    }
    window.importFileSizeError = importFileSizeError

    function formatGitCompareErrorText(error) {
        const raw = String((error && error.message) || error || '').trim()
        return raw || '读取官方配置失败'
    }
    window.formatGitCompareErrorText = formatGitCompareErrorText

    function getStoredDashboardToken() {
        try {
            return String(
                localStorage.getItem(DASHBOARD_TOKEN_STORAGE_KEY)
                || localStorage.getItem(LEGACY_API_TOKEN_STORAGE_KEY)
                || ''
            ).trim()
        } catch (e) {
            return ''
        }
    }

    function setStoredDashboardToken(token) {
        const value = String(token || '').trim()
        if (value) {
            localStorage.setItem(DASHBOARD_TOKEN_STORAGE_KEY, value)
            return
        }
        localStorage.removeItem(DASHBOARD_TOKEN_STORAGE_KEY)
        localStorage.removeItem(LEGACY_API_TOKEN_STORAGE_KEY)
    }

    // 站点友好名称：解决 "domain.split('.')[0]" 在 chat.deepseek.com / chat.qwen.ai
    // 或 aistudio.google.com / aistudio.xiaomimimo.com 这类站点上撞出一堆同名（chat、aistudio）的问题
    const SITE_FRIENDLY_NAMES = {
        'aistudio.google.com': 'Google AI Studio',
        'aistudio.xiaomimimo.com': '小米 MiMo AI Studio',
        'grok.com': 'Grok',
        'chatgpt.com': 'ChatGPT',
        'gemini.google.com': 'Gemini',
        'chat.deepseek.com': 'DeepSeek',
        'www.doubao.com': '豆包 Doubao',
        'claude.ai': 'Claude',
        'arena.ai': 'LMArena',
        'chat.qwen.ai': '通义千问 Qwen',
        'www.kimi.com': 'Kimi',
        'chatglm.cn': '智谱清言 ChatGLM',
        'ai.google.dev': 'Google AI (ai.google.dev)',
        'www.google.com': 'Google'
    }
    // 域名首段是这些通用词时，单独显示会分不清是哪个站点（比如两个 "chat"）
    const GENERIC_DOMAIN_LABELS = new Set(['www', 'chat', 'ai', 'app', 'api', 'web', 'my', 'portal', 'console', 'platform'])

    function siteDisplayName(domain) {
        const value = String(domain || '').trim()
        if (!value) return domain
        if (SITE_FRIENDLY_NAMES[value]) return SITE_FRIENDLY_NAMES[value]

        const labels = value.replace(/^www\./, '').split('.')
        const first = labels[0] || value
        if (GENERIC_DOMAIN_LABELS.has(first.toLowerCase()) && labels.length > 1) {
            return first + ' · ' + labels[1]
        }
        return first
    }
    window.siteDisplayName = siteDisplayName

    window.DashboardShared = {
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
    };
})();
