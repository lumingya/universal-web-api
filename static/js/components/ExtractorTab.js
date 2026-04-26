// ==================== 提取器管理 Tab 组件 ====================
window.ExtractorTab = {
    name: 'ExtractorTab',
    props: {
        extractors: { type: Array, default: () => [] },
        defaultExtractorId: { type: String, default: '' },
        sites: { type: Object, default: () => ({}) },
        isLoading: { type: Boolean, default: false }
    },
    emits: [
        'set-default',
        'export-config',
        'import-config',
        'set-site-extractor',
        'open-verify-dialog',
        'refresh'
    ],
    data() {
        return {
            activeSection: 'extractors', // 'extractors' | 'bindings'
            searchQuery: '',
            showImportDialog: false,
            importText: '',
            importError: ''
        };
    },
    computed: {
        // 过滤后的站点列表
        filteredSites() {
            const domains = Object.keys(this.sites);
            if (!this.searchQuery) return domains;
            const query = this.searchQuery.toLowerCase();
            return domains.filter(d => d.toLowerCase().includes(query));
        },
        
        // 获取站点的提取器信息
        getSiteExtractorInfo() {
            return (domain) => {
                const site = this.sites[domain];
                const extractorId = site?.extractor_id || this.defaultExtractorId;
                const extractor = this.extractors.find(e => e.id === extractorId);
                return {
                    id: extractorId,
                    name: extractor?.name || extractorId,
                    verified: site?.extractor_verified || false,
                    isDefault: !site?.extractor_id
                };
            };
        }
    },
    methods: {
        handleImport() {
            this.importError = '';
            try {
                const config = JSON.parse(this.importText);
                if (!config.extractors) {
                    this.importError = '无效的配置格式：缺少 extractors 字段';
                    return;
                }
                this.$emit('import-config', config);
                this.showImportDialog = false;
                this.importText = '';
            } catch (e) {
                this.importError = 'JSON 解析失败: ' + e.message;
            }
        },
        
        openImportDialog() {
            this.importText = '';
            this.importError = '';
            this.showImportDialog = true;
        }
    },
    template: `
        <div class="h-full flex flex-col">
            <!-- 顶部标签切换 -->
            <div class="border-b dark:border-gray-700 bg-white dark:bg-gray-800 px-4">
                <div class="flex gap-4">
                    <button @click="activeSection = 'extractors'"
                            :class="['py-3 px-1 border-b-2 font-medium text-sm transition-colors',
                                     activeSection === 'extractors' 
                                     ? 'border-blue-500 text-blue-600 dark:text-blue-400' 
                                     : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200']">
                        📦 提取器列表
                    </button>
                    <button @click="activeSection = 'bindings'"
                            :class="['py-3 px-1 border-b-2 font-medium text-sm transition-colors',
                                     activeSection === 'bindings' 
                                     ? 'border-blue-500 text-blue-600 dark:text-blue-400' 
                                     : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200']">
                        🔗 站点绑定
                    </button>
                </div>
            </div>

            <!-- 内容区域 -->
            <div class="flex-1 overflow-auto p-4">
                <!-- 加载状态 -->
                <div v-if="isLoading" class="flex items-center justify-center h-full">
                    <div class="text-gray-500 dark:text-gray-400">
                        <span class="animate-spin inline-block mr-2">⏳</span> 加载中...
                    </div>
                </div>

                <!-- 提取器列表 -->
                <div v-else-if="activeSection === 'extractors'" class="space-y-4">
                    <!-- 操作栏 -->
                    <div class="flex justify-between items-center">
                        <h3 class="text-lg font-semibold text-gray-900 dark:text-white">
                            可用提取器 ({{ extractors.length }})
                        </h3>
                        <div class="flex gap-2">
                            <button @click="$emit('refresh')"
                                    class="px-3 py-1.5 text-sm border dark:border-gray-600 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-200 transition-colors">
                                🔄 刷新
                            </button>
                            <button @click="openImportDialog"
                                    class="px-3 py-1.5 text-sm border dark:border-gray-600 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-200 transition-colors">
                                📥 导入
                            </button>
                            <button @click="$emit('export-config')"
                                    class="px-3 py-1.5 text-sm border dark:border-gray-600 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-200 transition-colors">
                                📤 导出
                            </button>
                        </div>
                    </div>

                    <!-- 提取器卡片列表 -->
                    <div class="grid gap-4">
                        <div v-for="extractor in extractors" :key="extractor.id"
                             class="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg p-4 hover:shadow-md transition-shadow">
                            <div class="flex justify-between items-start">
                                <div class="flex-1">
                                    <div class="flex items-center gap-2">
                                        <span class="font-semibold text-gray-900 dark:text-white">
                                            {{ extractor.name }}
                                        </span>
                                        <span v-if="extractor.id === defaultExtractorId"
                                              class="px-2 py-0.5 text-xs bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 rounded-full">
                                            默认
                                        </span>
                                        <span v-if="!extractor.enabled"
                                              class="px-2 py-0.5 text-xs bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 rounded-full">
                                            已禁用
                                        </span>
                                    </div>
                                    <div class="text-sm text-gray-500 dark:text-gray-400 mt-1">
                                        ID: <code class="bg-gray-100 dark:bg-gray-700 px-1 rounded">{{ extractor.id }}</code>
                                    </div>
                                    <div class="text-sm text-gray-600 dark:text-gray-300 mt-2">
                                        {{ extractor.description || '暂无描述' }}
                                    </div>
                                </div>
                                <div class="flex gap-2 ml-4">
                                    <button v-if="extractor.id !== defaultExtractorId"
                                            @click="$emit('set-default', extractor.id)"
                                            class="px-3 py-1 text-sm text-blue-600 dark:text-blue-400 border border-blue-300 dark:border-blue-600 rounded hover:bg-blue-50 dark:hover:bg-blue-900/30 transition-colors">
                                        设为默认
                                    </button>
                                </div>
                            </div>
                        </div>

                        <div v-if="extractors.length === 0" 
                             class="text-center py-12 text-gray-400 dark:text-gray-500">
                            暂无可用的提取器
                        </div>
                    </div>
                </div>

                <!-- 站点绑定 -->
                <div v-else-if="activeSection === 'bindings'" class="space-y-4">
                    <!-- 搜索和说明 -->
                    <div class="flex justify-between items-center">
                        <div>
                            <h3 class="text-lg font-semibold text-gray-900 dark:text-white">站点提取器绑定</h3>
                            <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
                                为每个站点指定使用的提取算法
                            </p>
                        </div>
                        <input v-model="searchQuery"
                               type="search"
                               placeholder="搜索站点..."
                               class="border dark:border-gray-600 px-3 py-1.5 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-400">
                    </div>

                    <!-- 站点列表 -->
                    <div class="space-y-2">
                        <div v-for="domain in filteredSites" :key="domain"
                             class="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg p-4">
                            <div class="flex items-center justify-between">
                                <div class="flex-1">
                                    <div class="font-medium text-gray-900 dark:text-white">{{ domain }}</div>
                                    <div class="flex items-center gap-2 mt-1">
                                        <span class="text-sm text-gray-500 dark:text-gray-400">
                                            当前: {{ getSiteExtractorInfo(domain).name }}
                                        </span>
                                        <span v-if="getSiteExtractorInfo(domain).isDefault"
                                              class="text-xs text-gray-400 dark:text-gray-500">
                                            (使用默认)
                                        </span>
                                        <span v-if="getSiteExtractorInfo(domain).verified"
                                              class="px-1.5 py-0.5 text-xs bg-green-100 dark:bg-green-900/50 text-green-700 dark:text-green-300 rounded">
                                            ✓ 已验证
                                        </span>
                                        <span v-else
                                              class="px-1.5 py-0.5 text-xs bg-yellow-100 dark:bg-yellow-900/50 text-yellow-700 dark:text-yellow-300 rounded">
                                            ⚠ 未验证
                                        </span>
                                    </div>
                                </div>
                                <div class="flex items-center gap-2">
                                    <select @change="$emit('set-site-extractor', domain, $event.target.value)"
                                            :value="getSiteExtractorInfo(domain).id"
                                            class="border dark:border-gray-600 px-2 py-1.5 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-400">
                                        <option v-for="ext in extractors" :key="ext.id" :value="ext.id">
                                            {{ ext.name }}
                                        </option>
                                    </select>
                                    <button @click="$emit('open-verify-dialog', domain)"
                                            class="px-3 py-1.5 text-sm text-blue-600 dark:text-blue-400 border border-blue-300 dark:border-blue-600 rounded hover:bg-blue-50 dark:hover:bg-blue-900/30 transition-colors">
                                        测试
                                    </button>
                                </div>
                            </div>
                        </div>

                        <div v-if="filteredSites.length === 0" 
                             class="text-center py-12 text-gray-400 dark:text-gray-500">
                            {{ searchQuery ? '无匹配结果' : '暂无站点配置' }}
                        </div>
                    </div>
                </div>
            </div>

            <!-- 导入弹窗 -->
            <div v-if="showImportDialog" 
                 class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
                 @click.self="showImportDialog = false">
                <div class="bg-white dark:bg-gray-800 rounded-lg shadow-xl w-full max-w-lg mx-4">
                    <div class="px-4 py-3 border-b dark:border-gray-700 flex justify-between items-center">
                        <h3 class="font-semibold text-gray-900 dark:text-white">导入提取器配置</h3>
                        <button @click="showImportDialog = false"
                                class="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200">
                            ✕
                        </button>
                    </div>
                    <div class="p-4 space-y-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                                粘贴 extractors.json 内容
                            </label>
                            <textarea v-model="importText"
                                      rows="10"
                                      class="w-full border dark:border-gray-600 rounded-md p-2 font-mono text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-400"
                                      placeholder='{"extractors": {...}, "default": "..."}'></textarea>
                        </div>
                        <div v-if="importError" class="text-sm text-red-500">
                            {{ importError }}
                        </div>
                    </div>
                    <div class="px-4 py-3 border-t dark:border-gray-700 flex justify-end gap-2">
                        <button @click="showImportDialog = false"
                                class="px-4 py-2 text-sm text-gray-700 dark:text-gray-200 border dark:border-gray-600 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors">
                            取消
                        </button>
                        <button @click="handleImport"
                                class="px-4 py-2 text-sm text-white bg-blue-500 rounded-md hover:bg-blue-600 transition-colors">
                            导入
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `
};