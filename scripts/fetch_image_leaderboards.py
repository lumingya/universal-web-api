import sys
sys.path.insert(0, '.')
from bs4 import BeautifulSoup
from app.core import get_browser

b = get_browser(auto_connect=True)
pool = b.tab_pool
arena_tab = pool._tabs['arena_2'].tab

for route in ['/leaderboard/text-to-image', '/leaderboard/image-edit']:
    url = f"https://arena.ai{route}"
    print("=" * 60)
    print(f"Fetching {url} ...")
    js = f"""
    return (async () => {{
        try {{
            const resp = await fetch('{url}');
            return await resp.text();
        }} catch(e) {{
            return String(e);
        }}
    }})();
    """
    html = arena_tab.run_js(js)
    soup = BeautifulSoup(html, 'html.parser')
    tables = soup.find_all('table')
    print(f"Found {len(tables)} tables on {route}")
    for idx, t in enumerate(tables):
        rows = t.find_all('tr')
        print(f"--- Table {idx} ({len(rows)} rows) ---")
        for r in rows[:15]:
            cols = [c.get_text(strip=True) for c in r.find_all(['th', 'td'])]
            print("  ", cols)
