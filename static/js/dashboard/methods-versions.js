// R2-8：由 dashboard-methods.js 拆分——版本管理。方法原样迁出，由 dashboard-methods.js 统一组装。
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
        // ========== 版本管理方法 ==========

        applyUpdateCheck(data) {
            const current = this.updateCheck && typeof this.updateCheck === 'object'
                ? this.updateCheck
                : {};
            // 修复：检查失败时后端会把 latest_* 显式清空，这里若继续用 `||` 回落旧值，
            // 界面会同时显示"最新版本号"和"检查失败"，语义矛盾。
            // 只有后端确实没带这些键（如轮询中的部分响应）才回落。
            const hasKey = (key) => !!data && Object.prototype.hasOwnProperty.call(data, key);
            const pick = (key) => String((hasKey(key) ? data[key] : current[key]) || '');
            this.updateCheck = {
                checked: !!(data && data.checked),
                checking: !!(data && data.checking),
                available: !!(data && data.available),
                current_version: String((data && data.current_version) || current.current_version || ''),
                latest_version: pick('latest_version'),
                latest_tag: pick('latest_tag'),
                published_at: pick('published_at'),
                repo: String((data && data.repo) || current.repo || ''),
                checked_at: data && data.checked_at ? data.checked_at : (current.checked_at || null),
                error: String((data && data.error) || '')
            };
            if (this.updateCheck.current_version && !this.releasesCurrentVersion) {
                this.releasesCurrentVersion = this.updateCheck.current_version;
            }
            return this.updateCheck;
        },

        async loadUpdateCheck({ silent = false } = {}) {
            try {
                const data = await this.apiRequest('/api/update/check', { timeoutMs: 2500 });
                return this.applyUpdateCheck(data);
            } catch (error) {
                if (!silent) {
                    this.notify('版本检查状态读取失败: ' + error.message, 'error');
                }
                throw error;
            }
        },

        async refreshUpdateCheck() {
            // 补齐入口：后端 POST /api/update/check 会真正访问 GitHub 重查一次，
            // 而 GET 只读启动时的缓存状态（system.py 注释明确「不会访问 GitHub」）。
            // 此前前端只调 GET，用户没有任何"立即检查更新"的入口。
            if (this.updateCheckRefreshing) return;
            this.updateCheckRefreshing = true;
            try {
                const data = await this.apiRequest('/api/update/check', { method: 'POST', timeoutMs: 20000 });
                const applied = this.applyUpdateCheck(data);
                if (applied.error) {
                    this.notify('版本检查失败: ' + applied.error, 'error');
                } else if (applied.available) {
                    this.notify('发现新版本: ' + (applied.latest_tag || applied.latest_version), 'info');
                } else {
                    this.notify('已是最新版本', 'success');
                }
                return applied;
            } catch (error) {
                this.notify('版本检查失败: ' + error.message, 'error');
            } finally {
                this.updateCheckRefreshing = false;
            }
        },

        scheduleUpdateCheckStatusRefresh(status, attempt = 0) {
            if (this.updateCheckTimer) {
                clearTimeout(this.updateCheckTimer);
                this.updateCheckTimer = null;
            }
            const stillPending = status && (status.checking || !status.checked);
            if (!stillPending || attempt >= 12) {
                return;
            }
            this.updateCheckTimer = setTimeout(() => {
                this.updateCheckTimer = null;
                this.loadUpdateCheck({ silent: true })
                    .then((nextStatus) => this.scheduleUpdateCheckStatusRefresh(nextStatus, attempt + 1))
                    .catch(() => {});
            }, 1500);
        },

        async loadReleases() {
            this.releasesLoading = true;
            this.releasesError = '';
            try {
                const data = await this.apiRequest('/api/update/releases');
                this.releases = Array.isArray(data.releases) ? data.releases : [];
                this.releasesCurrentVersion = data.current_version || '';
                if (data.update_check) {
                    this.applyUpdateCheck(data.update_check);
                }
            } catch (error) {
                this.releasesError = '加载失败: ' + error.message;
                this.releases = [];
            } finally {
                this.releasesLoading = false;
            }
        },

        async switchToVersion(tag) {
            if (this.switchingTag) {
                this.notify('已有版本切换任务正在运行，请稍候', 'warning');
                return;
            }
            if (!confirm('确定要切换到 ' + tag + ' 吗？\n切换完成后服务将自动重启，页面需要手动刷新。')) {
                return;
            }
            this.switchingTag = tag;
            try {
                await this.apiRequest('/api/update/switch', {
                    method: 'POST',
                    body: JSON.stringify({ tag: tag })
                });
                this.notify('版本切换任务已启动：' + tag + '，下载中...', 'info');
                this.startSwitchStatusPolling();
            } catch (error) {
                this.notify('启动版本切换失败: ' + error.message, 'error');
                this.switchingTag = null;
            }
        },

        startSwitchStatusPolling() {
            this.stopSwitchStatusPolling();
            this.switchStatusPollingActive = true;
            const pollStatus = async () => {
                if (!this.switchStatusPollingActive || this.switchStatusPollingInFlight) {
                    return;
                }
                this.switchStatusPollingInFlight = true;
                let shouldContinue = true;
                try {
                    const status = await this.apiRequest('/api/update/status');
                    if (!status.running) {
                        shouldContinue = false;
                        this.stopSwitchStatusPolling();
                        if (status.success === true) {
                            this.notify('版本 ' + status.tag + ' 切换成功，服务正在重启，请稍后刷新页面', 'success');
                        } else if (status.success === false) {
                            var errMsg = status.error ? '：' + status.error : '';
                            this.notify('版本 ' + status.tag + ' 切换失败' + errMsg, 'error');
                            this.switchingTag = null;
                        }
                    }
                } catch (e) {
                    // 服务重启中，连接可能断开
                } finally {
                    this.switchStatusPollingInFlight = false;
                    if (shouldContinue && this.switchStatusPollingActive) {
                        this.switchStatusPolling = setTimeout(() => {
                            this.switchStatusPolling = null;
                            pollStatus();
                        }, 2000);
                    }
                }
            };
            pollStatus();
        },

        stopSwitchStatusPolling() {
            this.switchStatusPollingActive = false;
            if (this.switchStatusPolling) {
                clearTimeout(this.switchStatusPolling);
                this.switchStatusPolling = null;
            }
            this.switchStatusPollingInFlight = false;
        },

        showChangelog(tag, body) {
            this.changelogTag = tag;
            this.changelogContent = body || '（无更新说明）';
            this.showChangelogModal = true;
        },

        formatReleaseDate(isoStr) {
            if (!isoStr) return '—';
            try {
                var d = new Date(isoStr);
                var pad = function(n) { return String(n).padStart(2, '0'); };
                return d.getFullYear() + '/' + pad(d.getMonth()+1) + '/' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
            } catch (e) {
                return isoStr;
            }
        },
    });
})();
