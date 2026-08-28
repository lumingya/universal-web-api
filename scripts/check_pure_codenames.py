import json

models = json.load(open('scripts/arena_models_cache.json', encoding='utf-8'))

print("=== 正在分析前端绝对搜不到 6 大厂商名字的代号暗池 ===")

# 属于大厂但名字里没有大厂公开关键词（搜 glm, deepseek, openai, gemini, kimi, claude 绝对搜不到）
# 只有通过代号/UUID 才能访问的真正暗池
pure_codename_models = []

for m in models:
    if m.get('userSelectable') is not True:
        continue
    
    mid = m.get('id', '').replace('-', '')
    if len(mid) != 32 or mid[12] != '7':
        continue
    ts = int(mid[:12], 16)
    if ts < 1775001600000:  # 2026-04-01
        continue
    
    disp = (m.get('displayName') or '').strip().lower()
    name = (m.get('name') or '').strip().lower()
    pub = (m.get('publicName') or '').strip().lower()
    all_names = f"{disp} {name} {pub}"

    # 如果名字直接包含了 6 大厂商公开字眼，前端搜这些字眼就会显示出来
    has_public_keyword = any(k in all_names for k in [
        'deepseek', 'glm', 'openai', 'gpt', 'o1', 'o3', 'o4', 'claude', 'anthropic', 'gemini', 'google', 'kimi', 'moonshot'
    ])

    if not has_public_keyword:
        # 这就是前端搜厂商名绝对搜不到的真正暗池！
        pure_codename_models.append({
            'displayName': m.get('displayName'),
            'id': m.get('id'),
            'org': m.get('organization'),
            'prov': m.get('provider'),
            'ts_str': m.get('id')
        })

print(f"搜 6 大厂名字绝对搜不到的纯暗池代号模型共 {len(pure_codename_models)} 个：")
for item in pure_codename_models:
    print(f"  - [{item['displayName']}] (org: {item['org']}, prov: {item['prov']}) -> UUID: {item['id']}")
