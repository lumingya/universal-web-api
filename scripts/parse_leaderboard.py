import sys
sys.path.insert(0, '.')
import json
import re
from app.core import get_browser

b = get_browser(auto_connect=True)
pool = b.tab_pool
arena_tab = pool._tabs['arena_2'].tab

js = r"""
return (async () => {
    try {
        const resp = await fetch('https://arena.ai/leaderboard');
        const html = await resp.text();
        return html;
    } catch(e) {
        return String(e);
    }
})();
"""

html = arena_tab.run_js(js)
with open("scripts/leaderboard.html", "w", encoding="utf-8") as f:
    f.write(html)

print("Saved leaderboard.html (len=", len(html), ")")

# 寻找排行榜相关的 JSON 结构或模型列表
matches = re.findall(r'(\{"(?:id|name|model_name|rank|elo|rating|arena_score)":.+?\})', html)
print(f"Found {len(matches)} potential model objects in html")
if matches:
    print("Sample matches:", matches[:10])
