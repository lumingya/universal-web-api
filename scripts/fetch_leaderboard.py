import sys
sys.path.insert(0, '.')
import json
from app.core import get_browser

b = get_browser(auto_connect=True)
pool = b.tab_pool
arena_tab = pool._tabs['arena_2'].tab

js = r"""
return (async () => {
    try {
        const resp = await fetch('https://arena.ai/leaderboard');
        const text = await resp.text();
        const match = text.match(/<script id="__NEXT_DATA__" type="application\/json">(.*?)<\/script>/s);
        if (match) {
            return JSON.parse(match[1]);
        }
        return {html_len: text.length, snippet: text.slice(0, 1000)};
    } catch(e) {
        return {err: String(e)};
    }
})();
"""

res = arena_tab.run_js(js)
print("Keys:", list(res.keys()) if isinstance(res, dict) else type(res))
if isinstance(res, dict) and "props" in res:
    page_props = res.get("props", {}).get("pageProps", {})
    print("pageProps keys:", list(page_props.keys()))
    with open("scripts/leaderboard_props.json", "w", encoding="utf-8") as f:
        json.dump(page_props, f, indent=2, ensure_ascii=False)
    print("Saved pageProps to scripts/leaderboard_props.json")
else:
    print("Result snippet:", str(res)[:1000])
