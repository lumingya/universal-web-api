        // 站点配置数据（域名已更新）
        let sites = [];

        const tutorialState = {
            currentLanguage: 'zh',
            currentCategory: localStorage.getItem('tutorial-category') || 'getting-started',
            searchOpen: false
        };

        const tutorialTranslations = {
            zh: null,
            en: window.TUTORIAL_I18N_EN || null
        };

        const navTitleEl = document.getElementById('navTitle');
        const pageTitleEl = document.getElementById('pageTitle');
        const projectLinkLabelTextEl = document.getElementById('projectLinkLabelText');
        const projectLinkDescriptionEl = document.getElementById('projectLinkDescription');
        const projectLinkButtonTextEl = document.getElementById('projectLinkButtonText');
        const hamburgerBtn = document.getElementById('hamburgerBtn');
        const languageDropdownEl = document.getElementById('languageDropdown');
        const languageTriggerEl = document.getElementById('languageTrigger');
        const languageMenuEl = document.getElementById('languageMenu');
        const languageCurrentTextEl = document.getElementById('languageCurrentText');
        const languageButtons = document.querySelectorAll('[data-lang-option]');
        const languageNames = {
            zh: '简体中文',
            en: 'English'
        };

        function normalizeLanguage(language) {
            return String(language || '').toLowerCase().startsWith('en') ? 'en' : 'zh';
        }

        function moveAuthorNoteSection() {
            const authorNote = document.getElementById('author-note');
            const faq = document.getElementById('faq');
            if (!authorNote || !faq || faq.nextElementSibling === authorNote) {
                return;
            }
            faq.insertAdjacentElement('afterend', authorNote);
        }

        function captureChineseBundle() {
            if (tutorialTranslations.zh) {
                return;
            }

            const sectionContent = {};
            document.querySelectorAll('section').forEach(section => {
                sectionContent[section.id] = section.innerHTML;
            });

            const navContent = {};
            document.querySelectorAll('.nav-link').forEach(link => {
                navContent[link.getAttribute('href').slice(1)] = link.textContent.trim();
            });

            tutorialTranslations.zh = {
                meta: {
                    htmlLang: 'zh-CN',
                    pageTitle: document.title
                },
                ui: {
                    navTitle: navTitleEl.textContent.trim(),
                    pageHeaderTitle: pageTitleEl.textContent.trim(),
                    projectLinkLabel: projectLinkLabelTextEl.textContent.trim(),
                    projectLinkDescription: projectLinkDescriptionEl.textContent.trim(),
                    projectLinkButton: projectLinkButtonTextEl.textContent.trim(),
                    languageLabel: languageTriggerEl.getAttribute('aria-label') || '切换语言',
                    themeLight: '日间模式',
                    themeDark: '夜间模式',
                    hamburgerOpenLabel: '打开导航菜单',
                    hamburgerCloseLabel: '关闭导航菜单',
                    siteCardHint: '点击复制网址，去受控浏览器打开',
                    siteCardCopied: '已复制网址，请到受控浏览器粘贴打开',
                    siteCardCopyFailed: '复制失败，请手动复制并到受控浏览器打开',
                    siteCardEmpty: '当前没有读取到站点列表，请直接以控制面板中的站点配置为准。'
                },
                nav: navContent,
                sections: sectionContent
            };
        }

        function getCurrentBundle() {
            return tutorialTranslations[tutorialState.currentLanguage] || tutorialTranslations.zh;
        }

        function getSiteCardTexts() {
            const bundle = getCurrentBundle() || {};
            const fallback = tutorialTranslations.zh || {};
            const bundleUi = bundle.ui || {};
            const fallbackUi = fallback.ui || {};
            return {
                hint: bundleUi.siteCardHint || fallbackUi.siteCardHint || '点击复制网址，去受控浏览器打开',
                copied: bundleUi.siteCardCopied || fallbackUi.siteCardCopied || '已复制网址，请到受控浏览器粘贴打开',
                copyFailed: bundleUi.siteCardCopyFailed || fallbackUi.siteCardCopyFailed || '复制失败，请手动复制并到受控浏览器打开',
                empty: bundleUi.siteCardEmpty || fallbackUi.siteCardEmpty || '当前没有读取到站点列表，请直接以控制面板中的站点配置为准。'
            };
        }

        function showSiteCopyToast(message, isError = false) {
            let toast = document.getElementById('siteCopyToast');
            if (!toast) {
                toast = document.createElement('div');
                toast.id = 'siteCopyToast';
                toast.className = 'site-copy-toast';
                toast.setAttribute('role', 'status');
                toast.setAttribute('aria-live', 'polite');
                document.body.appendChild(toast);
            }

            toast.textContent = message;
            toast.classList.toggle('error', isError);
            toast.classList.add('visible');

            clearTimeout(showSiteCopyToast.timer);
            showSiteCopyToast.timer = setTimeout(() => {
                toast.classList.remove('visible');
            }, 2600);
        }

        async function copyTextToClipboard(text) {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(text);
                return;
            }

            const helper = document.createElement('textarea');
            helper.value = text;
            helper.setAttribute('readonly', 'readonly');
            helper.style.position = 'fixed';
            helper.style.opacity = '0';
            helper.style.pointerEvents = 'none';
            document.body.appendChild(helper);
            helper.focus();
            helper.select();

            try {
                const copied = document.execCommand('copy');
                if (!copied) {
                    throw new Error('copy_failed');
                }
            } finally {
                document.body.removeChild(helper);
            }
        }

        async function handleSiteCardClick(url) {
            const texts = getSiteCardTexts();
            try {
                await copyTextToClipboard(url);
                showSiteCopyToast(`${texts.copied}：${url}`);
            } catch (error) {
                showSiteCopyToast(`${texts.copyFailed}：${url}`, true);
            }
        }

        async function loadSiteCatalog() {
            try {
                const response = await fetch('/api/startup/controlled-browser-guide-data', { cache: 'no-store' });
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                const payload = await response.json();
                sites = Array.isArray(payload.sites)
                    ? payload.sites.map(site => ({
                        url: String(site?.url || '').trim(),
                        name: String(site?.name || site?.domain || '').trim(),
                        id: String(site?.id || site?.domain || '').trim(),
                        domain: String(site?.domain || '').trim()
                    })).filter(site => site.url && site.name)
                    : [];
            } catch (error) {
                console.warn('loadSiteCatalog failed:', error);
                sites = [];
            }

            renderSiteGrid();
        }

        async function getRequestGeneratorText() {
            const scriptUrl = new URL('./%E6%8B%A6%E6%88%AA%E8%AF%B7%E6%B1%82%E5%8F%91%E7%94%9F%E5%99%A8.txt', window.location.href);

            try {
                const response = await fetch(scriptUrl.href, { cache: 'no-store' });
                if (response.ok) {
                    const text = await response.text();
                    if (text && text.trim()) {
                        return text;
                    }
                }
            } catch (error) {
                // file:// 场景下 fetch 可能被浏览器限制，继续尝试 iframe 回退
            }

            const frame = document.getElementById('requestGeneratorFrame');
            try {
                const doc = frame?.contentDocument || frame?.contentWindow?.document;
                const text = doc?.body?.innerText || doc?.documentElement?.innerText || '';
                if (text && text.trim()) {
                    return text.replace(/\r\n/g, '\n');
                }
            } catch (error) {
                // 某些浏览器对本地文件 iframe 访问更严格，继续走统一失败提示
            }

            throw new Error('request_generator_unavailable');
        }

        async function handleRequestGeneratorCopy() {
            try {
                const text = await getRequestGeneratorText();
                await copyTextToClipboard(text);
                showSiteCopyToast('已复制拦截请求发生器脚本，可直接粘贴到 Console');
            } catch (error) {
                showSiteCopyToast('复制脚本失败，请先展开下方内容后手动复制', true);
            }
        }

        // 渲染站点卡片
        function renderSiteGrid() {
            const grid = document.getElementById('siteGrid');
            if (!grid) {
                return;
            }
            const texts = getSiteCardTexts();
            if (!sites.length) {
                grid.innerHTML = `<div class="site-card site-card-empty">${texts.empty}</div>`;
                return;
            }
            grid.innerHTML = sites.map(site => `
                <button type="button" class="site-card site-card-button" data-site-url="${site.url}">
                    <span class="site-card-main">
                        <span class="site-card-icon" aria-hidden="true">📋</span>
                        <span class="site-card-name">${site.name}</span>
                        <span class="model-id">${site.id}</span>
                    </span>
                    <span class="site-card-hint">${texts.hint}</span>
                </button>
            `).join('');

            grid.querySelectorAll('.site-card-button').forEach(button => {
                button.addEventListener('click', () => {
                    handleSiteCardClick(button.dataset.siteUrl || '');
                });
            });
        }

        function updateLanguageButtons() {
            languageButtons.forEach(button => {
                const isActive = button.dataset.langOption === tutorialState.currentLanguage;
                button.classList.toggle('active', isActive);
                button.setAttribute('aria-pressed', isActive ? 'true' : 'false');
                button.setAttribute('aria-checked', isActive ? 'true' : 'false');
            });
            languageCurrentTextEl.textContent = languageNames[tutorialState.currentLanguage] || languageNames.zh;
        }

        function setLanguageDropdownOpen(isOpen) {
            languageDropdownEl.classList.toggle('open', isOpen);
            languageTriggerEl.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        }

        function toggleLanguageDropdown() {
            setLanguageDropdownOpen(!languageDropdownEl.classList.contains('open'));
        }

        function updateHamburgerLabel(isOpen = document.getElementById('sidebar').classList.contains('open')) {
            const bundle = getCurrentBundle();
            hamburgerBtn.setAttribute(
                'aria-label',
                isOpen ? bundle.ui.hamburgerCloseLabel : bundle.ui.hamburgerOpenLabel
            );
        }

        function applyLanguage(language) {
            const normalized = normalizeLanguage(language);
            const fallback = tutorialTranslations.zh;
            const hasRequestedBundle = Boolean(tutorialTranslations[normalized]);
            const effectiveLanguage = hasRequestedBundle ? normalized : 'zh';
            const bundle = tutorialTranslations[effectiveLanguage] || fallback;

            tutorialState.currentLanguage = effectiveLanguage;

            document.documentElement.lang = bundle.meta?.htmlLang || fallback.meta.htmlLang;
            document.title = bundle.meta?.pageTitle || fallback.meta.pageTitle;

            navTitleEl.textContent = bundle.ui?.navTitle || fallback.ui.navTitle;
            pageTitleEl.textContent = bundle.ui?.pageHeaderTitle || fallback.ui.pageHeaderTitle;
            projectLinkLabelTextEl.textContent = bundle.ui?.projectLinkLabel || fallback.ui.projectLinkLabel;
            projectLinkDescriptionEl.textContent = bundle.ui?.projectLinkDescription || fallback.ui.projectLinkDescription;
            projectLinkButtonTextEl.textContent = bundle.ui?.projectLinkButton || fallback.ui.projectLinkButton;
            languageTriggerEl.setAttribute('aria-label', bundle.ui?.languageLabel || fallback.ui.languageLabel);
            languageMenuEl.setAttribute('aria-label', bundle.ui?.languageLabel || fallback.ui.languageLabel);

            navLinks.forEach(link => {
                const sectionId = link.getAttribute('href').slice(1);
                link.textContent = bundle.nav?.[sectionId] || fallback.nav?.[sectionId] || link.textContent;
            });

            sections.forEach(section => {
                const translatedHtml = bundle.sections?.[section.id] || fallback.sections?.[section.id];
                if (typeof translatedHtml === 'string') {
                    section.innerHTML = translatedHtml;
                }
            });

            renderSiteGrid();
            updateLanguageButtons();
            updateThemeButton(document.documentElement.getAttribute('data-theme') || 'light');
            updateHamburgerLabel();
            updateActiveNavLink();
        }

        function setLanguage(language) {
            const normalized = normalizeLanguage(language);
            localStorage.setItem('tutorial-language', normalized);
            applyLanguage(normalized);
        }

        // 夜间模式
        function toggleTheme() {
            const current = document.documentElement.getAttribute('data-theme');
            const next = current === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', next);
            localStorage.setItem('tutorial-theme', next);
            updateThemeButton(next);
        }

        function updateThemeButton(theme) {
            const icon = document.getElementById('themeIcon');
            const text = document.getElementById('themeText');
            const bundle = getCurrentBundle();
            if (theme === 'dark') {
                icon.textContent = '☀️';
                text.textContent = bundle.ui.themeLight;
            } else {
                icon.textContent = '🌙';
                text.textContent = bundle.ui.themeDark;
            }
        }

        function initTheme() {
            const saved = localStorage.getItem('tutorial-theme');
            let theme = 'light';
            if (saved) {
                theme = saved;
            } else if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
                theme = 'dark';
            }
            document.documentElement.setAttribute('data-theme', theme);
            updateThemeButton(theme);
        }

        moveAuthorNoteSection();

        // 滚动高亮逻辑
        const sections = document.querySelectorAll('section');
        const navLinks = document.querySelectorAll('.nav-link');

        function updateActiveNavLink() {
            let current = '';
            sections.forEach(section => {
                const sectionTop = section.offsetTop;
                if (scrollY >= sectionTop - 100) {
                    current = section.getAttribute('id');
                }
            });

            navLinks.forEach(link => {
                link.classList.remove('active');
                if (link.getAttribute('href') === '#' + current) {
                    link.classList.add('active');
                }
            });
        }

        window.addEventListener('scroll', updateActiveNavLink);

        // 侧边栏开关
        function toggleSidebar() {
            const sidebar = document.getElementById('sidebar');
            const overlay = document.getElementById('sidebarOverlay');
            const isOpen = sidebar.classList.contains('open');

            sidebar.classList.toggle('open');
            hamburgerBtn.classList.toggle('active');

            if (!isOpen) {
                overlay.style.display = 'block';
                requestAnimationFrame(() => overlay.classList.add('active'));
            } else {
                overlay.classList.remove('active');
                setTimeout(() => { overlay.style.display = 'none'; }, 300);
            }

            updateHamburgerLabel(!isOpen);
        }

        // 手机端点击导航链接后自动关闭侧边栏
        navLinks.forEach(link => {
            link.addEventListener('click', () => {
                if (window.innerWidth <= 900) {
                    toggleSidebar();
                }
            });
        });

        languageButtons.forEach(button => {
            button.addEventListener('click', () => {
                setLanguage(button.dataset.langOption);
                setLanguageDropdownOpen(false);
            });
        });

        languageTriggerEl.addEventListener('click', (event) => {
            event.stopPropagation();
            toggleLanguageDropdown();
        });

        languageMenuEl.addEventListener('click', (event) => {
            event.stopPropagation();
        });

        document.addEventListener('click', (event) => {
            if (!languageDropdownEl.contains(event.target)) {
                setLanguageDropdownOpen(false);
            }
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                setLanguageDropdownOpen(false);
            }
        });

        document.addEventListener('click', (event) => {
            const copyButton = event.target.closest('#copyRequestGeneratorBtn');
            if (copyButton) {
                handleRequestGeneratorCopy();
            }
        });

        // 页面加载时初始化
        const docsCategories = [
            {
                id: 'getting-started',
                label: { zh: '开始使用', en: 'Start Here' },
                heroTitle: { zh: '开始使用', en: 'Start Here' },
                subtitle: {
                    zh: '先把受控浏览器、控制台和 API 连接跑通，再逐步深入更细的站点配置。',
                    en: 'Get the controlled browser, dashboard, and API connection working first, then move into deeper site configuration.'
                },
                quickLinks: [
                    {
                        targetId: 'quick-start',
                        title: { zh: '快速开始', en: 'Quick Start' },
                        description: { zh: '先分清两个浏览器，再按顺序完成第一次接入。', en: 'Understand the two-browser setup and finish your first connection in order.' }
                    },
                    {
                        targetId: 'connect-api',
                        title: { zh: '连接 API', en: 'Connect API' },
                        description: { zh: '直接看 Base URL、API Key 和模型填写方式。', en: 'Jump straight to the Base URL, API key, and model setup.' }
                    },
                    {
                        targetId: 'dashboard-tour',
                        title: { zh: '控制台导览', en: 'Dashboard Tour' },
                        description: { zh: '搞清楚站点、标签页、日志和设置分别在哪里。', en: 'See where sites, tabs, logs, and settings live in the dashboard.' }
                    },
                    {
                        targetId: 'faq',
                        title: { zh: '常见问题', en: 'FAQ' },
                        description: { zh: '连接失败、登录态失效、标签页异常先看这里。', en: 'Start here for connection failures, profile issues, or tab problems.' }
                    }
                ],
                groups: [
                    {
                        title: { zh: '上手主线', en: 'Core Flow' },
                        sectionIds: ['quick-start', 'connect-api']
                    },
                    {
                        title: { zh: '控制台与调度', en: 'Dashboard and Routing' },
                        sectionIds: ['dashboard-tour', 'tab-pool']
                    },
                    {
                        title: { zh: '答疑与预期', en: 'FAQ and Expectations' },
                        sectionIds: ['faq', 'author-note']
                    }
                ]
            },
            {
                id: 'site-adaptation',
                label: { zh: '接入站点', en: 'Add a Site' },
                heroTitle: { zh: '接入站点', en: 'Add a Site' },
                subtitle: {
                    zh: '这一组负责把新站点真正跑起来，重点是新增站点、预设、选择器和工作流。',
                    en: 'This track focuses on making a new site actually work: adding the site, presets, selectors, and workflow.'
                },
                quickLinks: [
                    {
                        targetId: 'add-site-guide',
                        title: { zh: '新增站点', en: 'Add Site' },
                        description: { zh: '先选自动识别还是手动配置，再按最小配置起步。', en: 'Choose auto-detection or manual setup, then start from the minimum viable config.' }
                    },
                    {
                        targetId: 'presets',
                        title: { zh: '预设系统', en: 'Presets' },
                        description: { zh: '同一站点拆分不同用途时，优先用预设而不是复制站点。', en: 'Use presets, not duplicate sites, when one domain needs different behaviors.' }
                    },
                    {
                        targetId: 'selectors',
                        title: { zh: '选择器配置', en: 'Selectors' },
                        description: { zh: '输入框、发送按钮和结果容器的定位都在这里。', en: 'Map the input, send button, and result container here.' }
                    },
                    {
                        targetId: 'workflow',
                        title: { zh: '工作流配置', en: 'Workflow' },
                        description: { zh: '决定点击、等待、输入和发送的执行顺序。', en: 'Define the order of click, wait, fill, and send actions.' }
                    }
                ],
                groups: [
                    {
                        title: { zh: '新增与拆分', en: 'Add and Split' },
                        sectionIds: ['add-site-guide', 'presets']
                    },
                    {
                        title: { zh: '交互动作', en: 'Interaction Flow' },
                        sectionIds: ['selectors', 'workflow']
                    }
                ]
            },
            {
                id: 'parsing-streaming',
                label: { zh: '解析与流式', en: 'Parsing and Streaming' },
                heroTitle: { zh: '解析与流式', en: 'Parsing and Streaming' },
                subtitle: {
                    zh: '如果你在找“怎么创建解析器”，或者想分清提取器和网络响应解析器的职责，这一组就是入口。',
                    en: 'If you are looking for parser creation or want to separate extractor work from network-response parsing, this is the right entry point.'
                },
                quickLinks: [
                    {
                        targetId: 'extractor-vs-parser',
                        title: { zh: '提取器还是解析器？', en: 'Extractor or Parser?' },
                        description: { zh: '先判断问题属于 HTML 提取，还是网络响应解析。', en: 'Decide first whether the issue belongs to HTML extraction or network response parsing.' }
                    },
                    {
                        targetId: 'response-detection-parser-guide',
                        title: { zh: '创建解析器', en: 'Create a Parser' },
                        description: { zh: '直接跳到“我想创建一个解析器”这段入口说明。', en: 'Jump directly to the “I want to create a parser” entry section.' }
                    },
                    {
                        targetId: 'response-detection',
                        title: { zh: '响应检测', en: 'Response Detection' },
                        description: { zh: '看 DOM 模式、网络拦截模式和超时参数应该怎么配。', en: 'Review DOM mode, network interception, and timeout settings.' }
                    },
                    {
                        targetId: 'image-extraction',
                        title: { zh: '多模态与附件', en: 'Multimodal and Files' },
                        description: { zh: '多模态提取和文件粘贴都在这里串起来看。', en: 'Follow multimodal extraction and file attach behavior together from here.' }
                    }
                ],
                groups: [
                    {
                        title: { zh: '判断该改哪里', en: 'Choose the Right Layer' },
                        sectionIds: ['extractors', 'response-detection']
                    },
                    {
                        title: { zh: '特殊内容', en: 'Special Content' },
                        sectionIds: ['image-extraction', 'file-paste']
                    }
                ]
            },
            {
                id: 'advanced',
                label: { zh: '高级能力', en: 'Advanced' },
                heroTitle: { zh: '高级能力', en: 'Advanced' },
                subtitle: {
                    zh: '这里收拢函数调用、命令、AI 元素识别以及全局环境配置，适合已经跑通基础流程后再看。',
                    en: 'This area collects function calling, commands, AI element recognition, and global environment settings once the basics already work.'
                },
                quickLinks: [
                    {
                        targetId: 'function-calling',
                        title: { zh: '函数调用', en: 'Function Calling' },
                        description: { zh: '先看边界，再决定要不要把工具调用接进来。', en: 'Review the boundaries before wiring tools into your workflow.' }
                    },
                    {
                        targetId: 'commands',
                        title: { zh: '自动化命令', en: 'Commands' },
                        description: { zh: '切代理、执行脚本和命令编排都在这一节。', en: 'Proxy switching, scripts, and command orchestration live here.' }
                    },
                    {
                        targetId: 'ai-recognition',
                        title: { zh: 'AI 元素识别', en: 'AI Recognition' },
                        description: { zh: '遇到结构复杂的新站点，可以先靠它起步。', en: 'Use it to bootstrap element discovery on unfamiliar sites.' }
                    },
                    {
                        targetId: 'env-config',
                        title: { zh: '环境配置', en: 'Environment' },
                        description: { zh: '全局超时、端口、浏览器和保存策略统一在这里看。', en: 'Review ports, timeouts, browser settings, and persistence in one place.' }
                    }
                ],
                groups: [
                    {
                        title: { zh: '扩展能力', en: 'Extended Capabilities' },
                        sectionIds: ['function-calling', 'stealth-mode', 'commands']
                    },
                    {
                        title: { zh: '识别与环境', en: 'Recognition and Environment' },
                        sectionIds: ['ai-recognition', 'env-config', 'browser-config', 'config-manage']
                    }
                ]
            }
        ];

        const docsSectionAliases = {
            'quick-start': '入门 上手 第一次使用 双浏览器 controlled browser',
            'add-site-guide': '新增站点 新网站 接入站点 自动识别 手动新增',
            'selectors': '选择器 selector input_box send_btn result_container message_wrapper',
            'extractors': '提取器 extractor html markdown dom 文本提取',
            'image-extraction': '多模态提取 图片提取 音频提取 视频提取 media audio video image',
            'response-detection': '解析器 parser 创建解析器 listen_pattern 网络拦截 stream streaming 响应检测',
            'workflow': '工作流 workflow click wait fill_input key_press stream_wait',
            'file-paste': '附件 上传 长文本 文件粘贴 附件发送判定 高级附件规则 attachment_monitor attachment_selectors pending_selectors Gemini',
            'commands': '命令 自动化 代理 切换命令',
            'ai-recognition': 'AI识别 element detection 自动分析',
            'env-config': '环境变量 helper api key base url model'
        };

        const docsUiCopy = {
            zh: {
                brandTitle: 'Universal Web-to-API',
                brandSubtitle: '教程中心',
                sidebarTitle: '本分类内容',
                searchTrigger: '搜索章节、配置项、解析器或关键词',
                searchHint: 'Ctrl + K',
                searchPlaceholder: '试试搜索：解析器、选择器、工作流、代理、登录态...',
                searchIdle: '输入关键词开始搜索。会自动跳到对应分类和章节。',
                searchEmpty: '没有找到匹配内容，可以试试“解析器”“选择器”“工作流”“登录态”等关键词。',
                badgeSection: '章节',
                badgeHeading: '小节',
                tocKicker: '目录',
                tocTitle: '当前页导航',
                tocEmpty: '当前章节没有更细的子标题，可以直接阅读正文。',
                shareLink: '分享链接',
                shareCopied: '已复制当前章节链接',
                closeSearch: '关闭搜索'
            },
            en: {
                brandTitle: 'Universal Web-to-API',
                brandSubtitle: 'Docs Hub',
                sidebarTitle: 'Current Category',
                searchTrigger: 'Search sections, settings, parsers, or keywords',
                searchHint: 'Ctrl + K',
                searchPlaceholder: 'Try: parser, selector, workflow, proxy, login profile...',
                searchIdle: 'Start typing to search. The page will switch to the matching category automatically.',
                searchEmpty: 'No results found. Try terms like "parser", "selector", "workflow", or "profile".',
                badgeSection: 'SECTION',
                badgeHeading: 'TOPIC',
                tocKicker: 'On This Page',
                tocTitle: 'Current Page',
                tocEmpty: 'This section has no deeper sub-headings, so you can read the body directly.',
                shareLink: 'Copy Link',
                shareCopied: 'Copied the current section link',
                closeSearch: 'Close Search'
            }
        };

        const docsSectionToCategory = docsCategories.reduce((map, category) => {
            category.groups.forEach(group => {
                group.sectionIds.forEach(sectionId => {
                    map[sectionId] = category.id;
                });
            });
            return map;
        }, {});

        const docsNavLinkMap = Array.from(navLinks).reduce((map, link) => {
            const targetId = (link.getAttribute('href') || '').replace(/^#/, '');
            if (targetId) {
                map[targetId] = link;
            }
            return map;
        }, {});

        const docsRuntime = {
            layoutReady: false,
            eventsBound: false,
            activeSectionId: '',
            activeHeadingId: '',
            searchIndex: []
        };

        function getDocsText(value) {
            if (typeof value === 'string') {
                return value;
            }
            if (!value || typeof value !== 'object') {
                return '';
            }
            return value[tutorialState.currentLanguage] || value.zh || value.en || '';
        }

        function getDocsUi() {
            return docsUiCopy[tutorialState.currentLanguage] || docsUiCopy.zh;
        }

        function getTutorialDashboardUrl() {
            if (window.location.protocol === 'http:' || window.location.protocol === 'https:') {
                return `${window.location.origin}/`;
            }
            return 'http://127.0.0.1:8199/';
        }

        function syncDashboardLinks() {
            const dashboardUrl = getTutorialDashboardUrl();
            document.querySelectorAll('[data-dashboard-link="true"]').forEach(link => {
                link.setAttribute('href', dashboardUrl);
            });
        }

        function normalizeDocsCategory(categoryId) {
            return docsCategories.some(category => category.id === categoryId) ? categoryId : docsCategories[0].id;
        }

        function getDocsCategory(categoryId = tutorialState.currentCategory) {
            const normalized = normalizeDocsCategory(categoryId);
            return docsCategories.find(category => category.id === normalized) || docsCategories[0];
        }

        function escapeDocsHtml(value) {
            return String(value || '')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        function escapeDocsRegExp(value) {
            return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        }

        function stripDocsText(value) {
            return String(value || '').replace(/\s+/g, ' ').trim();
        }

        function getVisibleDocsSections() {
            return Array.from(sections).filter(section => !section.classList.contains('is-hidden-by-category'));
        }

        function getDocsTargetMeta(targetId) {
            const target = document.getElementById(targetId);
            if (!target) {
                return null;
            }

            const section = target.closest('section');
            if (!section) {
                return null;
            }

            return {
                target,
                section,
                sectionId: section.id,
                categoryId: section.dataset.docsCategory || docsSectionToCategory[section.id] || docsCategories[0].id
            };
        }

        function assignDocsCategories() {
            sections.forEach(section => {
                const categoryId = docsSectionToCategory[section.id] || docsCategories[0].id;
                section.dataset.docsCategory = categoryId;
                section.dataset.searchAliases = docsSectionAliases[section.id] || '';
            });

            navLinks.forEach(link => {
                const sectionId = (link.getAttribute('href') || '').replace(/^#/, '');
                link.dataset.docsCategory = docsSectionToCategory[sectionId] || docsCategories[0].id;
            });
        }

        function ensureDocsPageHeader() {
            const header = document.querySelector('.page-header');
            const projectBanner = header?.querySelector('.project-link-banner');
            if (!header || !projectBanner) {
                return;
            }

            if (!document.getElementById('pageKicker')) {
                const kicker = document.createElement('div');
                kicker.id = 'pageKicker';
                kicker.className = 'page-kicker';
                header.insertBefore(kicker, pageTitleEl);
            }

            if (!document.getElementById('pageSubtitle')) {
                const subtitle = document.createElement('p');
                subtitle.id = 'pageSubtitle';
                subtitle.className = 'page-subtitle';
                pageTitleEl.insertAdjacentElement('afterend', subtitle);
            }

            if (!document.getElementById('pageQuickLinks')) {
                const quickLinks = document.createElement('div');
                quickLinks.id = 'pageQuickLinks';
                quickLinks.className = 'page-quick-links';
                header.insertBefore(quickLinks, projectBanner);
            }
        }

        function renderDocsTopTabs() {
            const tabsEl = document.getElementById('topTabs');
            if (!tabsEl) {
                return;
            }

            tabsEl.innerHTML = docsCategories.map(category => `
                <button type="button"
                        class="top-tab${category.id === tutorialState.currentCategory ? ' active' : ''}"
                        data-docs-category="${category.id}">
                    ${escapeDocsHtml(getDocsText(category.label))}
                </button>
            `).join('');
        }

        function renderDocsSidebarGroups() {
            const nav = document.querySelector('#sidebar nav');
            if (!nav) {
                return;
            }

            const category = getDocsCategory();
            const activeSectionId = docsRuntime.activeSectionId || getVisibleDocsSections()[0]?.id || category.groups[0]?.sectionIds[0] || '';

            nav.innerHTML = '';
            nav.classList.add('sidebar-nav');

            category.groups.forEach((group, index) => {
                const details = document.createElement('details');
                details.className = 'nav-group';
                details.open = index === 0 || group.sectionIds.includes(activeSectionId);

                const summary = document.createElement('summary');
                summary.className = 'nav-group-summary';
                summary.textContent = getDocsText(group.title);
                details.appendChild(summary);

                const linksWrap = document.createElement('div');
                linksWrap.className = 'nav-group-links';

                group.sectionIds.forEach(sectionId => {
                    const link = docsNavLinkMap[sectionId];
                    if (!link) {
                        return;
                    }

                    link.classList.remove('is-hidden-by-category');
                    linksWrap.appendChild(link);
                });

                details.appendChild(linksWrap);
                nav.appendChild(details);
            });
        }

        function ensureDocsLayout() {
            const sidebar = document.getElementById('sidebar');
            const mainContent = document.querySelector('.main-content');
            if (!sidebar || !mainContent) {
                return;
            }

            if (!document.getElementById('docsTopbar')) {
                const topbar = document.createElement('div');
                topbar.className = 'docs-topbar';
                topbar.id = 'docsTopbar';
                topbar.innerHTML = `
                    <div class="docs-topbar-inner">
                        <a href="#quick-start" class="docs-brand" data-target-id="quick-start">
                            <span class="docs-brand-mark" aria-hidden="true">⌘</span>
                            <span class="docs-brand-copy">
                                <strong id="docsBrandTitle"></strong>
                                <span id="docsBrandSubtitle"></span>
                            </span>
                        </a>
                        <div class="top-tabs" id="topTabs"></div>
                        <div class="docs-actions" id="docsActions">
                            <button type="button" class="search-trigger" id="searchTrigger">
                                <span class="search-trigger-text" id="searchTriggerText"></span>
                                <kbd id="searchTriggerHint">Ctrl + K</kbd>
                            </button>
                        </div>
                    </div>
                `;

                sidebar.parentNode.insertBefore(topbar, sidebar);
            }

            if (!document.getElementById('docsShell')) {
                const shell = document.createElement('div');
                shell.id = 'docsShell';
                shell.className = 'docs-shell';
                sidebar.parentNode.insertBefore(shell, sidebar);
                shell.appendChild(sidebar);

                const pageShell = document.createElement('div');
                pageShell.id = 'pageShell';
                pageShell.className = 'page-shell';
                shell.appendChild(pageShell);
                pageShell.appendChild(mainContent);
            }

            const pageShell = document.getElementById('pageShell');
            if (pageShell && !document.getElementById('pageToc')) {
                const toc = document.createElement('aside');
                toc.className = 'page-toc';
                toc.id = 'pageToc';
                toc.innerHTML = `
                    <div class="page-toc-card">
                        <p class="page-toc-kicker" id="pageTocKicker"></p>
                        <h2 class="page-toc-title" id="pageTocTitle"></h2>
                        <div class="page-toc-links" id="pageTocLinks"></div>
                        <button type="button" class="toc-share-btn" id="tocShareBtn"></button>
                    </div>
                `;
                pageShell.appendChild(toc);
            }

            if (!document.getElementById('searchOverlay')) {
                const overlay = document.createElement('div');
                overlay.className = 'search-overlay';
                overlay.id = 'searchOverlay';
                overlay.innerHTML = `
                    <div class="search-modal" role="dialog" aria-modal="true" aria-labelledby="searchModalLabel">
                        <div class="search-modal-head">
                            <span id="searchModalLabel"></span>
                            <button type="button" class="search-modal-close" id="searchCloseBtn">Esc</button>
                        </div>
                        <div class="search-modal-input-wrap">
                            <input type="search" class="search-modal-input" id="searchInput" autocomplete="off" spellcheck="false">
                        </div>
                        <div class="search-results" id="searchResults"></div>
                    </div>
                `;
                document.body.appendChild(overlay);
            }

            const docsActions = document.getElementById('docsActions');
            if (docsActions && languageDropdownEl && languageDropdownEl.parentElement !== docsActions) {
                docsActions.appendChild(languageDropdownEl);
            }

            ensureDocsPageHeader();
            renderDocsTopTabs();
            renderDocsSidebarGroups();
            docsRuntime.layoutReady = true;
        }

        function syncDocsChrome() {
            const ui = getDocsUi();
            const category = getDocsCategory();
            const quickLinks = document.getElementById('pageQuickLinks');
            const pageKicker = document.getElementById('pageKicker');
            const pageSubtitle = document.getElementById('pageSubtitle');
            const brandTitle = document.getElementById('docsBrandTitle');
            const brandSubtitle = document.getElementById('docsBrandSubtitle');
            const searchTriggerText = document.getElementById('searchTriggerText');
            const searchTriggerHint = document.getElementById('searchTriggerHint');
            const searchModalLabel = document.getElementById('searchModalLabel');
            const searchInput = document.getElementById('searchInput');
            const searchCloseBtn = document.getElementById('searchCloseBtn');
            const tocKicker = document.getElementById('pageTocKicker');
            const tocShareBtn = document.getElementById('tocShareBtn');

            navTitleEl.textContent = getDocsText(category.label) || ui.sidebarTitle;
            pageTitleEl.textContent = getDocsText(category.heroTitle);

            if (pageKicker) {
                pageKicker.textContent = ui.brandSubtitle;
            }

            if (pageSubtitle) {
                pageSubtitle.textContent = getDocsText(category.subtitle);
            }

            if (brandTitle) {
                brandTitle.textContent = ui.brandTitle;
            }

            if (brandSubtitle) {
                brandSubtitle.textContent = ui.brandSubtitle;
            }

            if (searchTriggerText) {
                searchTriggerText.textContent = ui.searchTrigger;
            }

            if (searchTriggerHint) {
                searchTriggerHint.textContent = ui.searchHint;
            }

            if (searchModalLabel) {
                searchModalLabel.textContent = ui.searchTrigger;
            }

            if (searchInput) {
                searchInput.placeholder = ui.searchPlaceholder;
            }

            if (searchCloseBtn) {
                searchCloseBtn.textContent = 'Esc';
                searchCloseBtn.setAttribute('aria-label', ui.closeSearch);
            }

            if (tocKicker) {
                tocKicker.textContent = ui.tocKicker;
            }

            if (tocShareBtn) {
                tocShareBtn.textContent = ui.shareLink;
            }

            if (quickLinks) {
                quickLinks.innerHTML = category.quickLinks.map(link => `
                    <a href="#${link.targetId}" class="quick-link-card" data-target-id="${link.targetId}">
                        <strong>${escapeDocsHtml(getDocsText(link.title))}</strong>
                        <span>${escapeDocsHtml(getDocsText(link.description))}</span>
                    </a>
                `).join('');
            }

            renderDocsTopTabs();
        }

        function ensureDocsExtractorNote() {
            const extractorsSection = document.getElementById('extractors');
            if (!extractorsSection || extractorsSection.querySelector('[data-docs-extractor-note]')) {
                return;
            }

            const note = document.createElement('div');
            note.className = 'clarity-note';
            note.dataset.docsExtractorNote = 'true';
            note.innerHTML = tutorialState.currentLanguage === 'en'
                ? `
                    <h3 class="task-guide-title" id="extractor-vs-parser">One-minute rule: extractor or parser?</h3>
                    <p>If the full answer is already visible on the page and only the captured result looks messy, incomplete, or badly formatted, start with the <strong>extractor</strong>.</p>
                    <p>If the answer is not rendered yet, or you want to read the underlying JSON / text directly, you need the <strong>parser</strong> and network interception instead.</p>
                    <p>If your goal is to create a parser for a new site, jump straight to the parser guide in the response detection section below.</p>
                `
                : `
                    <h3 class="task-guide-title" id="extractor-vs-parser">一分钟搞懂：改「提取器」还是改「解析器」？</h3>
                    <p>网页上已经有完整的字了，只是抓下来比较乱、缺内容、丢格式？先改<strong>提取器</strong>。它负责把网页 HTML 清洗成干净的 Markdown。</p>
                    <p>字还没渲染出来，或者你想直接拦截底层 JSON / 文本？那就去看<strong>解析器</strong>和网络拦截。它负责把响应体转换成最终输出。</p>
                    <p>如果你的目标是给新站点创建解析器，直接跳到下方「我想创建一个解析器，该从哪里看起？」这一段就行。</p>
                `;

            const firstParagraph = extractorsSection.querySelector('p');
            if (firstParagraph) {
                firstParagraph.insertAdjacentElement('afterend', note);
            } else {
                extractorsSection.prepend(note);
            }
        }

        function ensureDocsParserGuide() {
            const responseSection = document.getElementById('response-detection');
            if (!responseSection || responseSection.querySelector('[data-docs-parser-guide]')) {
                return;
            }

            const guide = document.createElement('div');
            guide.className = 'task-guide';
            guide.dataset.docsParserGuide = 'true';
            guide.innerHTML = tutorialState.currentLanguage === 'en'
                ? `
                    <div class="task-guide-badge">Hands-on</div>
                    <h3 class="task-guide-title" id="response-detection-parser-guide">I want to create a parser. Where should I start?</h3>
                    <p>If the site already renders the answer in the DOM and only the extracted text looks wrong, stay with extractors. You only need a parser when the real answer lives inside the intercepted request or response.</p>
                    <ol>
                        <li>Use the built-in request generator in this section and export one real request sample.</li>
                        <li>Send that export to AI together with <code>app/core/parsers/base.py</code>, <code>app/core/parsers/__init__.py</code>, and one or two similar parsers.</li>
                        <li>Ask AI to create <code>app/core/parsers/xxx_parser.py</code> and tell you how to register it in <code>__init__.py</code>.</li>
                        <li>Return to the dashboard, enable network interception, then fill in <code>listen_pattern</code> and the parser ID you just created.</li>
                    </ol>
                    <p>The detailed walkthrough is still right below. This entry is here so parser creation no longer stays buried in the middle of a long chapter.</p>
                `
                : `
                    <div class="task-guide-badge">实战入口</div>
                    <h3 class="task-guide-title" id="response-detection-parser-guide">我想创建一个解析器，该从哪里看起？</h3>
                    <p>如果站点的回复已经渲染在 DOM 里，只是提取结果不对，那还是先看提取器。只有当真实内容藏在网络请求 / 响应里时，才需要自己写解析器。</p>
                    <ol>
                        <li>先用本节内置的拦截脚本导出一次真实请求样本。</li>
                        <li>把导出文件和 <code>app/core/parsers/base.py</code>、<code>app/core/parsers/__init__.py</code>、一两个相近解析器一起发给 AI。</li>
                        <li>让 AI 新建 <code>app/core/parsers/xxx_parser.py</code>，并告诉你 <code>__init__.py</code> 里该怎么注册。</li>
                        <li>回到控制台开启网络拦截模式，再填写 <code>listen_pattern</code> 和你刚创建的 parser ID。</li>
                    </ol>
                    <p>真正的详细示例步骤还在本节下面，这里只是先给你一个清晰入口，避免“创建解析器”的说明继续埋在长章节中间。</p>
                `;

            const firstParagraph = responseSection.querySelector('p');
            if (firstParagraph) {
                firstParagraph.insertAdjacentElement('afterend', guide);
            } else {
                responseSection.prepend(guide);
            }
        }

        function ensureDocsHeadingAnchors() {
            sections.forEach(section => {
                let h3Index = 0;
                let h4Index = 0;

                section.querySelectorAll('h3, h4').forEach(heading => {
                    if (heading.id) {
                        return;
                    }

                    if (heading.tagName === 'H3') {
                        h3Index += 1;
                        heading.id = `${section.id}-h3-${h3Index}`;
                    } else {
                        h4Index += 1;
                        heading.id = `${section.id}-h4-${h4Index}`;
                    }
                });
            });
        }

        function highlightDocsTerms(text, terms) {
            let html = escapeDocsHtml(text);

            terms.forEach(term => {
                if (!term) {
                    return;
                }

                const matcher = new RegExp(`(${escapeDocsRegExp(term)})`, 'ig');
                html = html.replace(matcher, '<mark>$1</mark>');
            });

            return html;
        }

        function getDocsSearchSnippet(item, terms) {
            const source = stripDocsText(item.snippetSource || item.title || '');
            if (!source) {
                return item.sectionTitle ? escapeDocsHtml(item.sectionTitle) : '';
            }

            const lowerSource = source.toLowerCase();
            const firstIndex = terms.reduce((current, term) => {
                const index = lowerSource.indexOf(term);
                if (index === -1) {
                    return current;
                }
                if (current === -1) {
                    return index;
                }
                return Math.min(current, index);
            }, -1);

            let snippet = source;
            if (firstIndex > 72) {
                snippet = `...${source.slice(firstIndex - 48, firstIndex + 120)}`;
            } else {
                snippet = source.slice(0, 168);
            }

            if (snippet.length < source.length && !snippet.endsWith('...')) {
                snippet += '...';
            }

            return highlightDocsTerms(snippet, terms);
        }

        function buildDocsSearchIndex() {
            const index = [];

            sections.forEach(section => {
                const sectionTitle = stripDocsText(section.querySelector('h2')?.textContent || section.id);
                const sectionText = stripDocsText(section.textContent);
                const categoryId = section.dataset.docsCategory || docsSectionToCategory[section.id] || docsCategories[0].id;
                const aliases = stripDocsText(section.dataset.searchAliases || '');

                index.push({
                    type: 'section',
                    targetId: section.id,
                    sectionId: section.id,
                    categoryId,
                    title: sectionTitle,
                    sectionTitle,
                    snippetSource: sectionText,
                    searchText: `${sectionTitle} ${aliases} ${sectionText}`.toLowerCase()
                });

                section.querySelectorAll('h3, h4').forEach(heading => {
                    const headingText = stripDocsText(heading.textContent);
                    if (!headingText) {
                        return;
                    }

                    const fragments = [headingText];
                    let sibling = heading.nextElementSibling;
                    let guard = 0;
                    while (sibling && !/^H[34]$/.test(sibling.tagName) && guard < 4) {
                        const text = stripDocsText(sibling.textContent);
                        if (text) {
                            fragments.push(text);
                        }
                        sibling = sibling.nextElementSibling;
                        guard += 1;
                    }

                    index.push({
                        type: 'heading',
                        targetId: heading.id,
                        sectionId: section.id,
                        categoryId,
                        title: headingText,
                        sectionTitle,
                        snippetSource: fragments.join(' '),
                        searchText: `${headingText} ${sectionTitle} ${aliases} ${fragments.join(' ')}`.toLowerCase()
                    });
                });
            });

            docsRuntime.searchIndex = index;
        }

        function renderDocsSearchResults(query) {
            const resultsEl = document.getElementById('searchResults');
            if (!resultsEl) {
                return;
            }

            const ui = getDocsUi();
            const normalizedQuery = stripDocsText(query).toLowerCase();
            const terms = normalizedQuery.split(/\s+/).filter(Boolean);

            if (!terms.length) {
                resultsEl.innerHTML = `<div class="search-empty">${escapeDocsHtml(ui.searchIdle)}</div>`;
                return;
            }

            const results = docsRuntime.searchIndex
                .filter(item => terms.every(term => item.searchText.includes(term)))
                .map(item => {
                    const titleLower = item.title.toLowerCase();
                    const score = terms.reduce((total, term) => {
                        let next = total;
                        if (titleLower.includes(term)) {
                            next += 12;
                        }
                        if (item.sectionTitle.toLowerCase().includes(term)) {
                            next += 4;
                        }
                        if (item.searchText.includes(term)) {
                            next += 2;
                        }
                        return next;
                    }, item.type === 'heading' ? 1 : 0);

                    return { ...item, score };
                })
                .sort((left, right) => right.score - left.score)
                .slice(0, 14);

            if (!results.length) {
                resultsEl.innerHTML = `<div class="search-empty">${escapeDocsHtml(ui.searchEmpty)}</div>`;
                return;
            }

            resultsEl.innerHTML = results.map(item => {
                const categoryLabel = getDocsText(getDocsCategory(item.categoryId).label);
                const badge = item.type === 'heading' ? ui.badgeHeading : ui.badgeSection;
                const snippet = getDocsSearchSnippet(item, terms);

                return `
                    <button type="button" class="search-result" data-target-id="${item.targetId}">
                        <div class="search-result-head">
                            <span class="search-result-title">${highlightDocsTerms(item.title, terms)}</span>
                            <span class="search-result-badge">${escapeDocsHtml(badge)}</span>
                        </div>
                        <div class="search-result-snippet">${escapeDocsHtml(categoryLabel)} / ${escapeDocsHtml(item.sectionTitle)} · ${snippet}</div>
                    </button>
                `;
            }).join('');
        }

        function openDocsSearch(initialQuery = '') {
            const overlay = document.getElementById('searchOverlay');
            const input = document.getElementById('searchInput');
            if (!overlay || !input) {
                return;
            }

            tutorialState.searchOpen = true;
            overlay.classList.add('open');
            input.value = initialQuery;
            renderDocsSearchResults(initialQuery);

            requestAnimationFrame(() => {
                input.focus();
                input.select();
            });
        }

        function closeDocsSearch() {
            const overlay = document.getElementById('searchOverlay');
            if (!overlay) {
                return;
            }

            tutorialState.searchOpen = false;
            overlay.classList.remove('open');
        }

        function getCurrentDocsSectionId() {
            const visibleSections = getVisibleDocsSections();
            let current = visibleSections[0]?.id || '';

            visibleSections.forEach(section => {
                if (window.scrollY >= section.offsetTop - 140) {
                    current = section.id;
                }
            });

            return current;
        }

        function getCurrentDocsHeadingId(sectionId = docsRuntime.activeSectionId) {
            const section = document.getElementById(sectionId);
            if (!section) {
                return '';
            }

            let current = '';
            section.querySelectorAll('h3, h4').forEach(heading => {
                if (window.scrollY >= heading.offsetTop - 148) {
                    current = heading.id;
                }
            });

            return current;
        }

        function updateDocsTocActiveState() {
            const activeHeadingId = docsRuntime.activeHeadingId;
            const activeSectionId = docsRuntime.activeSectionId;

            document.querySelectorAll('.toc-link').forEach(link => {
                const targetId = (link.getAttribute('href') || '').replace(/^#/, '');
                const isActive = activeHeadingId
                    ? targetId === activeHeadingId
                    : targetId === activeSectionId;
                link.classList.toggle('active', isActive);
            });
        }

        function renderDocsToc() {
            const tocTitle = document.getElementById('pageTocTitle');
            const tocLinks = document.getElementById('pageTocLinks');
            if (!tocTitle || !tocLinks) {
                return;
            }

            const ui = getDocsUi();
            const sectionId = docsRuntime.activeSectionId || getCurrentDocsSectionId();
            const section = document.getElementById(sectionId);

            if (!section) {
                tocTitle.textContent = ui.tocTitle;
                tocTitle.dataset.sectionId = '';
                tocLinks.innerHTML = `<div class="toc-empty">${escapeDocsHtml(ui.tocEmpty)}</div>`;
                return;
            }

            const sectionTitle = stripDocsText(section.querySelector('h2')?.textContent || section.id);
            const headings = Array.from(section.querySelectorAll('h3, h4'));

            tocTitle.textContent = sectionTitle;
            tocTitle.dataset.sectionId = section.id;

            if (!headings.length) {
                tocLinks.innerHTML = `
                    <a href="#${section.id}" class="toc-link" data-target-id="${section.id}">
                        ${escapeDocsHtml(sectionTitle)}
                    </a>
                `;
                updateDocsTocActiveState();
                return;
            }

            tocLinks.innerHTML = headings.map(heading => `
                <a href="#${heading.id}"
                   class="toc-link level-${heading.tagName.toLowerCase()}"
                   data-target-id="${heading.id}">
                    ${escapeDocsHtml(stripDocsText(heading.textContent))}
                </a>
            `).join('');

            updateDocsTocActiveState();
        }

        function scrollDocsToTarget(targetId, behavior = 'smooth', options = {}) {
            const target = document.getElementById(targetId);
            if (!target) {
                return;
            }

            const offset = window.innerWidth <= 900 ? 92 : 118;
            const top = target.getBoundingClientRect().top + window.scrollY - offset;

            window.scrollTo({
                top: Math.max(top, 0),
                behavior
            });

            if (options.updateHash !== false) {
                history.replaceState(null, '', `#${targetId}`);
            }
        }

        function revealDocsTarget(targetId, options = {}) {
            const meta = getDocsTargetMeta(targetId);
            if (!meta) {
                return;
            }

            const behavior = options.behavior || 'smooth';
            if (meta.categoryId !== tutorialState.currentCategory) {
                setDocsCategory(meta.categoryId, {
                    persist: options.persist,
                    skipScroll: true
                });
            }

            requestAnimationFrame(() => {
                scrollDocsToTarget(targetId, behavior, {
                    updateHash: options.updateHash !== false
                });
                updateActiveNavLink();
            });

            if (window.innerWidth <= 900) {
                const sidebar = document.getElementById('sidebar');
                if (sidebar?.classList.contains('open')) {
                    toggleSidebar();
                }
            }
        }

        function copyDocsCurrentLink() {
            const ui = getDocsUi();
            const targetId = docsRuntime.activeHeadingId
                || docsRuntime.activeSectionId
                || getVisibleDocsSections()[0]?.id
                || 'quick-start';

            const url = new URL(window.location.href);
            url.hash = targetId;

            copyTextToClipboard(url.toString())
                .then(() => showSiteCopyToast(ui.shareCopied))
                .catch(() => showSiteCopyToast(ui.shareCopied, true));
        }

        function reorderDocsSections(categoryId) {
            const mainContent = document.querySelector('.main-content');
            const header = mainContent?.querySelector('.page-header');
            if (!mainContent || !header) {
                return;
            }

            const orderedIds = getDocsCategory(categoryId).groups.flatMap(group => group.sectionIds);
            const remainingIds = Array.from(sections)
                .map(section => section.id)
                .filter(sectionId => !orderedIds.includes(sectionId));

            [...orderedIds, ...remainingIds].forEach(sectionId => {
                const section = document.getElementById(sectionId);
                if (section) {
                    mainContent.appendChild(section);
                }
            });
        }

        function setDocsCategory(categoryId, options = {}) {
            const normalized = normalizeDocsCategory(categoryId);
            tutorialState.currentCategory = normalized;

            if (options.persist !== false) {
                localStorage.setItem('tutorial-category', normalized);
            }

            reorderDocsSections(normalized);

            sections.forEach(section => {
                const isVisible = section.dataset.docsCategory === normalized;
                section.classList.toggle('is-hidden-by-category', !isVisible);
            });

            navLinks.forEach(link => {
                const isVisible = link.dataset.docsCategory === normalized;
                link.classList.toggle('is-hidden-by-category', !isVisible);
            });

            renderDocsSidebarGroups();
            syncDocsChrome();
            docsRuntime.activeSectionId = '';
            docsRuntime.activeHeadingId = '';
            updateActiveNavLink();

            if (!options.skipScroll) {
                const firstSectionId = options.targetId || getDocsCategory(normalized).groups[0]?.sectionIds[0];
                if (firstSectionId) {
                    scrollDocsToTarget(firstSectionId, options.behavior || 'smooth', {
                        updateHash: options.updateHash !== false
                    });
                }
            }
        }

        function handleDocsHashChange() {
            const hashTarget = decodeURIComponent(window.location.hash.replace(/^#/, ''));
            if (!hashTarget) {
                return;
            }

            const meta = getDocsTargetMeta(hashTarget);
            if (!meta) {
                return;
            }

            revealDocsTarget(hashTarget, {
                behavior: 'auto',
                persist: false,
                updateHash: false
            });
        }

        function bindDocsEvents() {
            if (docsRuntime.eventsBound) {
                return;
            }

            docsRuntime.eventsBound = true;

            document.addEventListener('click', event => {
                const navLink = event.target.closest('.nav-link');
                if (navLink) {
                    const href = navLink.getAttribute('href') || '';
                    const targetId = decodeURIComponent(href.replace(/^#/, ''));
                    if (targetId) {
                        event.preventDefault();
                        revealDocsTarget(targetId);
                        return;
                    }
                }

                const categoryTrigger = event.target.closest('.top-tab[data-docs-category]');
                if (categoryTrigger) {
                    event.preventDefault();
                    const categoryId = categoryTrigger.getAttribute('data-docs-category');
                    setDocsCategory(categoryId, {
                        targetId: getDocsCategory(categoryId).groups[0]?.sectionIds[0]
                    });
                    return;
                }

                const targetedLink = event.target.closest('[data-target-id]');
                if (targetedLink) {
                    event.preventDefault();
                    const targetId = targetedLink.getAttribute('data-target-id');
                    if (targetedLink.classList.contains('search-result')) {
                        closeDocsSearch();
                    }
                    revealDocsTarget(targetId);
                    return;
                }

                if (event.target.closest('#searchTrigger')) {
                    event.preventDefault();
                    openDocsSearch(document.getElementById('searchInput')?.value || '');
                    return;
                }

                if (event.target.closest('#searchCloseBtn') || event.target.id === 'searchOverlay') {
                    event.preventDefault();
                    closeDocsSearch();
                    return;
                }

                if (event.target.closest('#tocShareBtn')) {
                    event.preventDefault();
                    copyDocsCurrentLink();
                }
            });

            document.addEventListener('keydown', event => {
                if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
                    event.preventDefault();
                    openDocsSearch(document.getElementById('searchInput')?.value || '');
                    return;
                }

                if (tutorialState.searchOpen && event.key === 'Escape') {
                    closeDocsSearch();
                }
            });

            const searchInput = document.getElementById('searchInput');
            if (searchInput) {
                searchInput.addEventListener('input', event => {
                    renderDocsSearchResults(event.target.value);
                });

                searchInput.addEventListener('keydown', event => {
                    if (event.key === 'Enter') {
                        const firstResult = document.querySelector('.search-result');
                        if (firstResult) {
                            event.preventDefault();
                            firstResult.click();
                        }
                    }
                });
            }

            window.addEventListener('hashchange', handleDocsHashChange);
        }

        function updateActiveNavLink() {
            const currentSectionId = getCurrentDocsSectionId();
            docsRuntime.activeSectionId = currentSectionId;
            docsRuntime.activeHeadingId = getCurrentDocsHeadingId(currentSectionId);

            navLinks.forEach(link => {
                const targetId = (link.getAttribute('href') || '').replace(/^#/, '');
                const isActive = targetId === currentSectionId;
                link.classList.toggle('active', isActive);
            });

            const tocTitle = document.getElementById('pageTocTitle');
            if (tocTitle?.dataset.sectionId !== currentSectionId) {
                renderDocsToc();
            } else {
                updateDocsTocActiveState();
            }
        }

        function applyLanguage(language) {
            const normalized = normalizeLanguage(language);
            const fallback = tutorialTranslations.zh;
            const bundle = tutorialTranslations[normalized] || fallback;

            tutorialState.currentLanguage = normalized;

            document.documentElement.lang = bundle.meta?.htmlLang || fallback.meta.htmlLang;
            document.title = bundle.meta?.pageTitle || fallback.meta.pageTitle;

            navTitleEl.textContent = bundle.ui?.navTitle || fallback.ui.navTitle;
            pageTitleEl.textContent = bundle.ui?.pageHeaderTitle || fallback.ui.pageHeaderTitle;
            projectLinkLabelTextEl.textContent = bundle.ui?.projectLinkLabel || fallback.ui.projectLinkLabel;
            projectLinkDescriptionEl.textContent = bundle.ui?.projectLinkDescription || fallback.ui.projectLinkDescription;
            projectLinkButtonTextEl.textContent = bundle.ui?.projectLinkButton || fallback.ui.projectLinkButton;
            languageTriggerEl.setAttribute('aria-label', bundle.ui?.languageLabel || fallback.ui.languageLabel);
            languageMenuEl.setAttribute('aria-label', bundle.ui?.languageLabel || fallback.ui.languageLabel);

            navLinks.forEach(link => {
                const sectionId = (link.getAttribute('href') || '').replace(/^#/, '');
                link.textContent = bundle.nav?.[sectionId] || fallback.nav?.[sectionId] || link.textContent;
            });

            sections.forEach(section => {
                const translatedHtml = bundle.sections?.[section.id] || fallback.sections?.[section.id];
                if (typeof translatedHtml === 'string') {
                    section.innerHTML = translatedHtml;
                }
            });

            syncDashboardLinks();
            renderSiteGrid();
            updateLanguageButtons();
            updateThemeButton(document.documentElement.getAttribute('data-theme') || 'light');
            updateHamburgerLabel();

            assignDocsCategories();
            ensureDocsExtractorNote();
            ensureDocsParserGuide();
            ensureDocsHeadingAnchors();
            ensureDocsLayout();
            bindDocsEvents();
            syncDocsChrome();
            buildDocsSearchIndex();

            const hashTarget = decodeURIComponent(window.location.hash.replace(/^#/, ''));
            const hashMeta = hashTarget ? getDocsTargetMeta(hashTarget) : null;
            const nextCategory = hashMeta?.categoryId
                || normalizeDocsCategory(localStorage.getItem('tutorial-category') || tutorialState.currentCategory);

            setDocsCategory(nextCategory, {
                persist: false,
                skipScroll: true
            });

            const currentTocTitle = document.getElementById('pageTocTitle');
            if (currentTocTitle) {
                currentTocTitle.dataset.sectionId = '';
            }

            if (hashMeta) {
                requestAnimationFrame(() => {
                    scrollDocsToTarget(hashTarget, 'auto', { updateHash: false });
                    updateActiveNavLink();
                });
            } else {
                updateActiveNavLink();
            }

            if (tutorialState.searchOpen) {
                renderDocsSearchResults(document.getElementById('searchInput')?.value || '');
            }
        }

        captureChineseBundle();
        initTheme();
        applyLanguage(normalizeLanguage(localStorage.getItem('tutorial-language') || 'zh'));
        loadSiteCatalog();
    
