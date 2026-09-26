// Dashboard methods 组装器（R2-8 起拆分到 static/js/dashboard/：shared.js 为共享常量与辅助函数，
// methods-*.js 为按主题分组的方法；index.html 需在本文件之前按顺序加载它们）。
(() => {
    const parts = window.DashboardMethodParts || [];
    const merged = {};
    for (const part of parts) {
        for (const key of Object.keys(part)) {
            if (Object.prototype.hasOwnProperty.call(merged, key)) {
                console.warn('[dashboard] 方法重名，后加载的覆盖先加载的：' + key);
            }
            merged[key] = part[key];
        }
    }
    window.DashboardMethods = merged;
})();
