// ==================== 首页首次访问引导 ====================
(function () {
    const STORAGE_KEY = 'uwa_onboarding_v1';

    const MODULE_STEPS = [
        {
            key: 'config',
            code: 'Config',
            title: '站点配置',
            summary: '定义一个网站如何被转换为稳定的 API。',
            detail: '在这里管理反代站点规则、CSS / XPath 选择器、数据提取器、响应拦截检测，以及输入与输出转换工作流。'
        },
        {
            key: 'tabpool',
            code: 'TabPool',
            title: '标签页池',
            summary: '掌握受控 Chrome 的实时工作状态。',
            detail: '查看多标签页并发池、网页匹配状态、会话隔离方式，以及每个 Tab 当前的线程占用情况。'
        },
        {
            key: 'monitor',
            code: 'Monitor',
            title: '请求监控',
            summary: '观察每一次真实请求如何流经系统。',
            detail: '抓取并检查 HTTP / WebSocket 请求流量，快速核对请求 Payload、目标站点响应与最终提取结果。'
        },
        {
            key: 'commands',
            code: 'Commands',
            title: '命令系统',
            summary: '把重复的浏览器操作编排成自动化流程。',
            detail: '配置和触发自动登录、点击交互、页面操作等动作序列，构建可重复执行的 UI 工作流。'
        },
        {
            key: 'logs',
            code: 'Logs',
            title: '运行日志',
            summary: '从底层运行信息中快速定位异常。',
            detail: '实时查看后端 Python 服务和控制台日志，用于追踪请求链路、脚本执行过程与错误原因。'
        },
        {
            key: 'settings',
            code: 'Settings',
            title: '设置',
            summary: '集中管理服务、安全与本地配置。',
            detail: '调整监听端口、面板 API Token、浏览器启动参数，并完成配置文件的导入导出、备份与恢复。'
        }
    ];

    const SOP_STEPS = [
        {
            key: 'principle',
            eyebrow: '核心原理',
            title: '网页交互，如何变成标准 API？',
            intro: 'Universal Web API 让受控 Chrome 保持真实网页的登录态和交互能力，再把网页会话桥接为 OpenAI 兼容接口。',
            note: '第三方客户端只需访问本地 API，无需直接理解每个网站的页面结构。'
        },
        {
            key: 'browser',
            eyebrow: '步骤 1',
            title: '在受控浏览器中打开目标网页',
            intro: '运行系统后，在自动弹出的受控 Chrome 中打开需要反代的 AI 网站，并完成正常登录。',
            note: '例如打开 gemini.google.com。请始终使用系统启动的受控浏览器，而不是另一个普通浏览器窗口。'
        },
        {
            key: 'interactive',
            eyebrow: '步骤 2 · 关键事项',
            title: '确保页面元素显性可交互',
            intro: '工作流要点击或提取的元素必须真实出现在页面中，不能处于折叠、遮挡或语言不匹配的状态。',
            note: '以 Gemini 为例，开始请求前请完成下面两项检查。'
        },
        {
            key: 'routing',
            eyebrow: '步骤 3',
            title: '在标签页池确认网页并选择路由',
            intro: '登录成功后，标签页池会显示已捕获网页。根据使用场景选择全局、域名路径或 Query 参数路由。',
            note: '多站点同时运行时，显式指定域名最容易保持请求边界清晰。'
        },
        {
            key: 'client',
            eyebrow: '步骤 4',
            title: '连接前端或第三方客户端',
            intro: '最后在 NextChat、LobeChat、LangChain 等客户端中填写 Base URL 与 API Key。',
            note: '未在设置中启用 Token 认证时，API Key 可以留空；若客户端强制要求，也可以填写任意值。'
        }
    ];

    window.OnboardingGuide = {
        name: 'OnboardingGuide',
        emits: ['tour-start', 'tour-end'],
        data() {
            return {
                visible: false,
                view: 'welcome',
                phase: 'modules',
                moduleIndex: 0,
                sopIndex: 0,
                targetRect: null,
                cardStyle: {},
                initialTimer: null,
                resizeHandler: null,
                keyHandler: null
            };
        },
        computed: {
            moduleSteps() {
                return MODULE_STEPS;
            },
            sopSteps() {
                return SOP_STEPS;
            },
            currentModule() {
                return this.moduleSteps[this.moduleIndex] || this.moduleSteps[0];
            },
            currentSop() {
                return this.sopSteps[this.sopIndex] || this.sopSteps[0];
            },
            totalTourSteps() {
                return this.moduleSteps.length + this.sopSteps.length;
            },
            completedTourSteps() {
                return this.phase === 'modules'
                    ? this.moduleIndex + 1
                    : this.moduleSteps.length + this.sopIndex + 1;
            },
            progressPercent() {
                return Math.round((this.completedTourSteps / this.totalTourSteps) * 100);
            },
            spotlightStyle() {
                if (!this.targetRect) {
                    return { display: 'none' };
                }
                return {
                    left: this.targetRect.left + 'px',
                    top: this.targetRect.top + 'px',
                    width: this.targetRect.width + 'px',
                    height: this.targetRect.height + 'px'
                };
            },
            shadeStyles() {
                if (!this.targetRect) {
                    return [{ inset: '0' }];
                }
                const rect = this.targetRect;
                return [
                    { left: '0', top: '0', right: '0', height: rect.top + 'px' },
                    { left: '0', top: rect.top + 'px', width: rect.left + 'px', height: rect.height + 'px' },
                    { left: (rect.left + rect.width) + 'px', top: rect.top + 'px', right: '0', height: rect.height + 'px' },
                    { left: '0', top: (rect.top + rect.height) + 'px', right: '0', bottom: '0' }
                ];
            }
        },
        mounted() {
            this.resizeHandler = () => this.schedulePositionUpdate();
            this.keyHandler = (event) => this.handleKeydown(event);
            window.addEventListener('resize', this.resizeHandler, { passive: true });
            window.addEventListener('scroll', this.resizeHandler, true);
            document.addEventListener('keydown', this.keyHandler);

            this.initialTimer = window.setTimeout(() => {
                if (!this.hasSavedDecision()) {
                    this.open(false);
                }
            }, 650);
        },
        beforeUnmount() {
            window.clearTimeout(this.initialTimer);
            window.removeEventListener('resize', this.resizeHandler);
            window.removeEventListener('scroll', this.resizeHandler, true);
            document.removeEventListener('keydown', this.keyHandler);
        },
        methods: {
            hasSavedDecision() {
                try {
                    return !!localStorage.getItem(STORAGE_KEY);
                } catch (error) {
                    return false;
                }
            },
            saveDecision(status) {
                try {
                    localStorage.setItem(STORAGE_KEY, JSON.stringify({
                        version: 1,
                        status: status,
                        updatedAt: new Date().toISOString()
                    }));
                } catch (error) {
                    // Storage is optional; the guide remains usable for this session.
                }
            },
            open(startDirectly) {
                window.clearTimeout(this.initialTimer);
                this.visible = true;
                this.view = startDirectly ? 'tour' : 'welcome';
                this.phase = 'modules';
                this.moduleIndex = 0;
                this.sopIndex = 0;
                this.targetRect = null;
                if (startDirectly) {
                    this.$emit('tour-start');
                    this.$nextTick(() => this.updateSpotlight());
                }
                this.$nextTick(() => this.focusDialog());
            },
            startTour() {
                this.view = 'tour';
                this.phase = 'modules';
                this.moduleIndex = 0;
                this.$emit('tour-start');
                this.$nextTick(() => {
                    this.updateSpotlight();
                    this.focusDialog();
                });
            },
            dismissWelcome() {
                this.saveDecision('dismissed');
                this.close(false);
            },
            skipTour() {
                this.saveDecision('skipped');
                this.close(true);
            },
            finishTour() {
                this.saveDecision('completed');
                this.close(true);
            },
            close(wasTour) {
                this.visible = false;
                this.targetRect = null;
                if (wasTour || this.view === 'tour') {
                    this.$emit('tour-end');
                }
            },
            previous() {
                if (this.phase === 'modules') {
                    if (this.moduleIndex > 0) {
                        this.moduleIndex -= 1;
                        this.$nextTick(() => this.updateSpotlight());
                    }
                    return;
                }
                if (this.sopIndex > 0) {
                    this.sopIndex -= 1;
                    return;
                }
                this.phase = 'modules';
                this.moduleIndex = this.moduleSteps.length - 1;
                this.$nextTick(() => this.updateSpotlight());
            },
            next() {
                if (this.phase === 'modules') {
                    if (this.moduleIndex < this.moduleSteps.length - 1) {
                        this.moduleIndex += 1;
                        this.$nextTick(() => this.updateSpotlight());
                        return;
                    }
                    this.phase = 'sop';
                    this.sopIndex = 0;
                    this.targetRect = null;
                    this.$nextTick(() => this.focusDialog());
                    return;
                }
                if (this.sopIndex < this.sopSteps.length - 1) {
                    this.sopIndex += 1;
                    this.$nextTick(() => this.focusDialog());
                    return;
                }
                this.finishTour();
            },
            goToSop(index) {
                this.sopIndex = index;
                this.$nextTick(() => this.focusDialog());
            },
            schedulePositionUpdate() {
                if (!this.visible || this.view !== 'tour' || this.phase !== 'modules') {
                    return;
                }
                window.requestAnimationFrame(() => this.updateSpotlight());
            },
            updateSpotlight() {
                if (!this.visible || this.phase !== 'modules') {
                    return;
                }
                const selector = '[data-tour-target="' + this.currentModule.key + '"]';
                const target = document.querySelector(selector);
                if (!target) {
                    this.targetRect = null;
                    this.cardStyle = {};
                    return;
                }

                target.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                window.setTimeout(() => {
                    const bounds = target.getBoundingClientRect();
                    const gap = 7;
                    const rect = {
                        left: Math.max(0, bounds.left - gap),
                        top: Math.max(0, bounds.top - gap),
                        width: Math.min(window.innerWidth, bounds.width + gap * 2),
                        height: Math.min(window.innerHeight, bounds.height + gap * 2)
                    };
                    this.targetRect = rect;
                    this.cardStyle = this.getCardStyle(rect);
                }, 120);
            },
            getCardStyle(rect) {
                const viewportWidth = window.innerWidth;
                const viewportHeight = window.innerHeight;
                if (viewportWidth <= 700) {
                    const estimatedHeight = 268;
                    const placeAtTop = rect.top > viewportHeight * 0.48;
                    return {
                        left: '14px',
                        right: '14px',
                        top: placeAtTop ? '14px' : 'auto',
                        bottom: placeAtTop ? 'auto' : '14px'
                    };
                }

                const cardWidth = 390;
                const preferredLeft = rect.left + rect.width + 20;
                const left = preferredLeft + cardWidth <= viewportWidth - 18
                    ? preferredLeft
                    : Math.max(18, rect.left - cardWidth - 20);
                const top = Math.max(18, Math.min(rect.top - 22, viewportHeight - 340));
                return { left: left + 'px', top: top + 'px', width: cardWidth + 'px' };
            },
            focusDialog() {
                const dialog = this.$el && this.$el.querySelector('[role="dialog"]');
                if (dialog && typeof dialog.focus === 'function') {
                    dialog.focus({ preventScroll: true });
                }
            },
            handleKeydown(event) {
                if (!this.visible) {
                    return;
                }
                if (event.key === 'Escape') {
                    event.preventDefault();
                    this.view === 'welcome' ? this.dismissWelcome() : this.skipTour();
                    return;
                }
                if (this.view !== 'tour') {
                    return;
                }
                if (event.key === 'ArrowLeft') {
                    event.preventDefault();
                    this.previous();
                } else if (event.key === 'ArrowRight' || event.key === 'Enter') {
                    event.preventDefault();
                    this.next();
                }
            }
        },
        template: `
            <div v-if="visible" class="uwa-onboarding-root" @click.stop>
                <template v-if="view === 'welcome'">
                    <div class="uwa-onboarding-backdrop"></div>
                    <section class="uwa-welcome-dialog" role="dialog" aria-modal="true" aria-labelledby="uwa-welcome-title" tabindex="-1">
                        <button type="button" class="uwa-tour-icon-button" @click="dismissWelcome" title="关闭" aria-label="关闭欢迎引导" v-html="$icons.xMark"></button>
                        <div class="uwa-welcome-mark">
                            <img src="/static/images/logo.svg" alt="">
                            <span>首次使用</span>
                        </div>
                        <p class="uwa-tour-kicker">UNIVERSAL WEB API</p>
                        <h2 id="uwa-welcome-title">欢迎来到控制面板</h2>
                        <p class="uwa-welcome-copy">检测到这是您第一次使用。用大约 3 分钟认识核心模块，并完成第一次网页反代配置。</p>
                        <div class="uwa-welcome-preview" aria-hidden="true">
                            <span><i>1</i>认识控制台</span>
                            <b></b>
                            <span><i>2</i>连接目标网页</span>
                            <b></b>
                            <span><i>3</i>接入客户端</span>
                        </div>
                        <div class="uwa-welcome-actions">
                            <button type="button" class="uwa-tour-button is-secondary" @click="dismissWelcome">暂不需要</button>
                            <button type="button" class="uwa-tour-button is-primary" @click="startTour">
                                <span>开始教程</span>
                                <span v-html="$icons.arrowRight"></span>
                            </button>
                        </div>
                        <p class="uwa-welcome-footnote">之后可随时从侧边栏底部的“新手指南”重新打开</p>
                    </section>
                </template>

                <template v-else-if="phase === 'modules'">
                    <div v-for="(style, index) in shadeStyles" :key="index" class="uwa-tour-shade" :style="style"></div>
                    <div class="uwa-tour-spotlight" :style="spotlightStyle" aria-hidden="true"></div>
                    <section class="uwa-module-card" :style="cardStyle" role="dialog" aria-modal="true" :aria-label="currentModule.title + '介绍'" tabindex="-1">
                        <header class="uwa-tour-card-header">
                            <div>
                                <span class="uwa-tour-stage">阶段 1 / 2</span>
                                <span class="uwa-tour-step-count">模块 {{ moduleIndex + 1 }} / {{ moduleSteps.length }}</span>
                            </div>
                            <button type="button" class="uwa-tour-icon-button" @click="skipTour" title="退出教程" aria-label="退出教程" v-html="$icons.xMark"></button>
                        </header>
                        <div class="uwa-tour-progress" aria-hidden="true"><i :style="{ width: progressPercent + '%' }"></i></div>
                        <div class="uwa-module-heading">
                            <span class="uwa-module-number">{{ String(moduleIndex + 1).padStart(2, '0') }}</span>
                            <div>
                                <p>{{ currentModule.code }}</p>
                                <h2>{{ currentModule.title }}</h2>
                            </div>
                        </div>
                        <h3>{{ currentModule.summary }}</h3>
                        <p class="uwa-module-detail">{{ currentModule.detail }}</p>
                        <footer class="uwa-tour-footer">
                            <button type="button" class="uwa-tour-skip" @click="skipTour">跳过教程</button>
                            <div>
                                <button type="button" class="uwa-tour-button is-secondary is-compact" @click="previous" :disabled="moduleIndex === 0">上一步</button>
                                <button type="button" class="uwa-tour-button is-primary is-compact" @click="next">
                                    <span>{{ moduleIndex === moduleSteps.length - 1 ? '进入实操' : '下一步' }}</span>
                                    <span v-html="$icons.arrowRight"></span>
                                </button>
                            </div>
                        </footer>
                    </section>
                </template>

                <template v-else>
                    <div class="uwa-onboarding-backdrop is-strong"></div>
                    <section class="uwa-sop-dialog" role="dialog" aria-modal="true" aria-labelledby="uwa-sop-title" tabindex="-1">
                        <aside class="uwa-sop-rail">
                            <div class="uwa-sop-rail-heading">
                                <span class="uwa-tour-stage">阶段 2 / 2</span>
                                <strong>反代实操</strong>
                                <small>从网页到 API</small>
                            </div>
                            <nav aria-label="实操教程步骤">
                                <button v-for="(step, index) in sopSteps" :key="step.key" type="button"
                                        :class="{ 'is-active': sopIndex === index, 'is-done': sopIndex > index }"
                                        @click="goToSop(index)" :aria-current="sopIndex === index ? 'step' : null">
                                    <i>{{ sopIndex > index ? '✓' : index }}</i>
                                    <span>{{ index === 0 ? '工作原理' : '步骤 ' + index }}</span>
                                </button>
                            </nav>
                            <div class="uwa-sop-rail-progress">
                                <span>整体进度</span><strong>{{ progressPercent }}%</strong>
                                <div><i :style="{ width: progressPercent + '%' }"></i></div>
                            </div>
                        </aside>

                        <div class="uwa-sop-content">
                            <header class="uwa-sop-topbar">
                                <span>{{ currentSop.eyebrow }}</span>
                                <button type="button" class="uwa-tour-icon-button" @click="skipTour" title="退出教程" aria-label="退出教程" v-html="$icons.xMark"></button>
                            </header>
                            <div class="uwa-sop-scroll">
                                <p class="uwa-tour-kicker">STEP-BY-STEP SOP</p>
                                <h2 id="uwa-sop-title">{{ currentSop.title }}</h2>
                                <p class="uwa-sop-intro">{{ currentSop.intro }}</p>

                                <div v-if="currentSop.key === 'principle'" class="uwa-bridge-flow">
                                    <div><span v-html="$icons.globe"></span><strong>真实 Web 界面</strong><small>登录态与页面交互</small></div>
                                    <i v-html="$icons.arrowRight"></i>
                                    <div><span v-html="$icons.server"></span><strong>受控 Chrome</strong><small>执行操作与提取响应</small></div>
                                    <i v-html="$icons.arrowRight"></i>
                                    <div class="is-accent"><span v-html="$icons.arrowPathRoundedSquare"></span><strong>OpenAI API</strong><small>统一请求与响应格式</small></div>
                                </div>

                                <div v-else-if="currentSop.key === 'browser'" class="uwa-browser-example">
                                    <div class="uwa-browser-bar"><i></i><i></i><i></i><span>gemini.google.com</span></div>
                                    <div class="uwa-browser-body">
                                        <span v-html="$icons.globe"></span>
                                        <div><strong>在受控浏览器完成登录</strong><small>保持目标网页打开，系统会自动捕获标签页</small></div>
                                    </div>
                                </div>

                                <div v-else-if="currentSop.key === 'interactive'" class="uwa-check-list">
                                    <div><i>1</i><span><strong>展开网页侧边栏</strong><small>避免对话切换等按钮因折叠而不可见</small></span></div>
                                    <div><i>2</i><span><strong>页面语言设为“汉语（中文）”</strong><small>防止选择器依赖的文案因语言不同而匹配失败</small></span></div>
                                </div>

                                <div v-else-if="currentSop.key === 'routing'" class="uwa-route-list">
                                    <article><span>全局路由</span><code>/v1/chat/completions</code><p>根据模型名或当前激活 Tab 自动路由。</p></article>
                                    <article class="is-recommended"><span>推荐 · 域名路由</span><code>/site/gemini.google.com/v1/chat/completions</code><p>在路径中固定目标域名，多站点运行互不干扰。</p></article>
                                    <article><span>Query 路由</span><code>/v1/chat/completions?domain=gemini.google.com</code><p>通过查询参数指定本次请求的目标站点。</p></article>
                                </div>

                                <div v-else-if="currentSop.key === 'client'" class="uwa-client-fields">
                                    <label><span>Base URL</span><code>http://127.0.0.1:8199/v1</code></label>
                                    <label><span>API Key</span><code>留空或任意值</code><small>启用 Token 认证后，请改为设置中的真实 Token</small></label>
                                </div>

                                <div class="uwa-sop-note"><span>提示</span><p>{{ currentSop.note }}</p></div>
                            </div>
                            <footer class="uwa-sop-footer">
                                <button type="button" class="uwa-tour-skip" @click="skipTour">退出教程</button>
                                <div>
                                    <button type="button" class="uwa-tour-button is-secondary is-compact" @click="previous">上一步</button>
                                    <button type="button" class="uwa-tour-button is-primary is-compact" @click="next">
                                        <span>{{ sopIndex === sopSteps.length - 1 ? '完成教程' : '下一步' }}</span>
                                        <span v-html="$icons.arrowRight"></span>
                                    </button>
                                </div>
                            </footer>
                        </div>
                    </section>
                </template>
            </div>
        `
    };
})();
