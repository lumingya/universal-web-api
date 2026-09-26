// R2-8：由 dashboard-methods.js 拆分——日志。方法原样迁出，由 dashboard-methods.js 统一组装。
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
        // ========== 日志相关 ==========

        async pollLogs(options = {}) {
            if (this.pauseLogs || document.visibilityState === 'hidden') return;
            if (
                !this.getBrowserConstantBool('LOG_WEB_COLLECTOR_ENABLED', true)
                || this.getBrowserConstantNumber('LOG_WEB_MAX_RECORDS', 5000, { min: 0 }) <= 0
            ) {
                this.logs = [];
                this.lastLogSeq = 0;
                this.lastLogTimestamp = 0;
                this.logPollPending = false;
                return;
            }
            if (this.isPollingLogs) {
                this.logPollPending = true;
                return;
            }
            if (options && options.background) {
                const now = Date.now();
                const backgroundInterval = this.getDashboardPollInterval('DASHBOARD_LOG_BACKGROUND_POLL_INTERVAL_MS', 5000)
                    || this.getDashboardPollInterval('DASHBOARD_LOG_POLL_INTERVAL_MS', 1000);
                if (this.lastBackgroundLogPollAt && backgroundInterval > 0 && now - this.lastBackgroundLogPollAt < backgroundInterval) {
                    return;
                }
                this.lastBackgroundLogPollAt = now;
            }

            this.isPollingLogs = true;
            const generation = Number(this.logGeneration || 0);
            try {
                const result = await this.apiRequest('/api/logs?after_seq=' + this.lastLogSeq);
                if (generation !== Number(this.logGeneration || 0)) {
                    return;
                }
                if (
                    !this.getBrowserConstantBool('LOG_WEB_COLLECTOR_ENABLED', true)
                    || this.getBrowserConstantNumber('LOG_WEB_MAX_RECORDS', 5000, { min: 0 }) <= 0
                ) {
                    this.logs = [];
                    this.lastLogSeq = 0;
                    this.lastLogTimestamp = 0;
                    this.logPollPending = false;
                    return;
                }

                if (result.cleared) {
                    this.logs = [];
                    this.lastLogSeq = 0;
                    this.lastLogTimestamp = 0;
                }

                if (result.logs && result.logs.length > 0) {
                    const nextLogs = result.logs.map(log => {
                        const messageText = log.message_text || log.display_message || log.message || '';
                        const kind = log.kind || log.level;
                        return {
                            id: log.seq || (Date.now() + Math.random()),
                            seq: log.seq || 0,
                            timestamp: new Date(log.timestamp * 1000).toLocaleTimeString() + '.' +
                                String(Math.floor((log.timestamp % 1) * 1000)).padStart(3, '0'),
                            level: this.normalizeLogLevel(kind, messageText),
                            rawLevel: String(log.level || '').toUpperCase(),
                            kind: String(kind || '').toUpperCase(),
                            logger: log.logger || '',
                            requestId: log.request_id || 'SYSTEM',
                            requestTag: log.request_tag || log.request_id || 'SYSTEM',
                            message: log.display_message || log.message || messageText,
                            messageText,
                            originalMessageText: log.original_message_text || messageText,
                            messageAlias: log.message_alias || ''
                        }
                    });
                    const maxLogs = Math.floor(this.getBrowserConstantNumber('LOG_WEB_MAX_RECORDS', 5000, { min: 0, max: 10000 }));
                    this.logs = maxLogs > 0 ? this.logs.concat(nextLogs).slice(-maxLogs) : [];
                }
                this.lastLogSeq = Number(result.next_seq || this.lastLogSeq || 0);
                this.lastLogTimestamp = Number(result.timestamp || this.lastLogTimestamp || 0);
            } catch (error) {
                if (generation === Number(this.logGeneration || 0)) {
                    console.debug('日志轮询失败:', error.message);
                }
            } finally {
                if (generation === Number(this.logGeneration || 0)) {
                    this.isPollingLogs = false;
                    if (this.logPollPending && !this.pauseLogs && document.visibilityState !== 'hidden') {
                        this.logPollPending = false;
                        this.pollLogs({ ...(options || {}), background: false }).catch(() => {});
                    }
                }
            }
        },

        normalizeLogLevel(level, message) {
            const normalized = String(level || '').toUpperCase();
            if (normalized === 'WARNING') return 'WARN';
            if (normalized === 'CRITICAL') return 'ERROR';
            if (normalized === 'SUCCESS') return 'OK';
            if (normalized === 'DEBUG' || normalized === 'WARN' || normalized === 'ERROR') {
                return normalized;
            }

            if (normalized === 'INFO') {
                if (message.includes('[AI]')) return 'AI';
                if (message.includes('[OK]') || message.includes('[SUCCESS]') || message.includes('✅')) return 'OK';
                return 'INFO';
            }

            if (message.includes('[AI]')) return 'AI';
            if (message.includes('[ERROR]')) return 'ERROR';
            if (message.includes('[WARN]') || message.includes('[WARNING]')) return 'WARN';
            if (message.includes('[OK]') || message.includes('[SUCCESS]') || message.includes('✅')) return 'OK';
            return 'INFO';
        },

        getLogColorClass(level) {
            const colors = {
                'DEBUG': 'bg-slate-50 dark:bg-slate-900/30',
                'INFO': 'bg-green-50 dark:bg-green-900/20',
                'AI': 'bg-purple-50 dark:bg-purple-900/20',
                'OK': 'bg-green-50 dark:bg-green-900/20',
                'WARN': 'bg-yellow-50 dark:bg-yellow-900/20',
                'ERROR': 'bg-red-50 dark:bg-red-900/20',
                'KEY': 'bg-sky-50 dark:bg-sky-900/20'
            };
            return colors[level] || colors['INFO'];
        },

        getLogLevelClass(level) {
            const colors = {
                'DEBUG': 'text-slate-500 dark:text-slate-400',
                'INFO': 'text-green-600 dark:text-green-400',
                'AI': 'text-purple-600 dark:text-purple-400',
                'OK': 'text-green-600 dark:text-green-400',
                'WARN': 'text-yellow-600 dark:text-yellow-400',
                'ERROR': 'text-red-600 dark:text-red-400',
                'KEY': 'text-sky-500 dark:text-sky-300'
            };
            return colors[level] || colors['INFO'];
        },

        clearLogs() {
            if (confirm('确定清除所有日志吗？')) {
                this.logGeneration = Number(this.logGeneration || 0) + 1;
                this.logs = [];
                this.lastLogSeq = 0;
                this.lastLogTimestamp = 0;
                this.isPollingLogs = false;
                this.logPollPending = false;

                this.apiRequest('/api/logs', { method: 'DELETE' })
                    .catch(() => { });

                this.notify('日志已清除', 'success');
            }
        },
    });
})();
