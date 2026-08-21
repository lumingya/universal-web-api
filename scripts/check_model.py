from app.core import get_browser

b = get_browser(auto_connect=True)
pool = b.tab_pool
for tab_id, tab in pool._tabs.items():
    if 'arena' in tab_id:
        try:
            print(f"Tab {tab_id}: url={tab.tab.url}")
            script = """
            return (() => {
                const results = [];
                // Look for model name triggers / dropdowns
                const selects = document.querySelectorAll('button[role="combobox"], [data-slot="select-trigger"], select');
                for (const s of selects) {
                    results.push('SELECT: ' + s.innerText);
                }
                // Look for header / sidebar active items
                const headings = document.querySelectorAll('h1, h2, h3, [class*="model"], [class*="badge"]');
                for (const h of headings) {
                    if (h.innerText && h.innerText.length < 50) {
                        results.push('HEADING/BADGE: ' + h.innerText);
                    }
                }
                return results;
            })();
            """
            res = tab.tab.run_js(script)
            print(f"Tab {tab_id} elements:", res)
        except Exception as e:
            print(f"Error on {tab_id}: {e}")
