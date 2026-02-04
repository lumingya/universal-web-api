// ==================== 提取器显示面板 ====================

window.ExtractorPanel = {
    name: 'ExtractorPanel',
    props: {
        extractorId: { type: String, default: null },
        extractorVerified: { type: Boolean, default: false }
    },
    template: `
        <div class="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-sm">
            <div class="px-4 py-3 border-b dark:border-gray-700 flex justify-between items-center">
                <div class="flex items-center gap-2">
                    <h3 class="font-semibold text-gray-900 dark:text-white">🧩 提取器</h3>
                </div>
            </div>
            <div class="p-4">
                <div class="flex items-center justify-between">
                    <div>
                        <div class="text-sm text-gray-600 dark:text-gray-300">
                            当前: <span class="font-medium">{{ extractorId || '默认' }}</span>
                        </div>
                        <div class="flex items-center gap-2 mt-1">
                            <span v-if="extractorVerified"
                                  class="px-2 py-0.5 text-xs bg-green-100 dark:bg-green-900/50 text-green-700 dark:text-green-300 rounded">
                                ✓ 已验证
                            </span>
                            <span v-else
                                  class="px-2 py-0.5 text-xs bg-yellow-100 dark:bg-yellow-900/50 text-yellow-700 dark:text-yellow-300 rounded">
                                ⚠ 未验证
                            </span>
                        </div>
                    </div>
                    <div class="text-sm text-gray-500 dark:text-gray-400">
                        前往「提取器」Tab 管理
                    </div>
                </div>
            </div>
        </div>
    `
};