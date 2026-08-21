import re
from pathlib import Path

path = Path('app/core/stream_monitor.py')
text = path.read_text(encoding='utf-8')

# 1. _get_snapshot_prefer_anchor
p1 = r'(target,\s*target_anchor\s*=\s*self\._select_candidate_element\(eles,\s*prefer_anchor\)\s*if\s*target\s*is\s*None:\s*target,\s*target_anchor\s*=\s*self\._select_candidate_element\(eles\)\s*)(last_text\s*=\s*self\.extractor\.extract_text\(target\))'
r1 = r'\1if target is None:\n                return result\n\n            \2'

# 2. _get_active_turn_text
p2 = r'(last_text = self\.extractor\.extract_text\(target\)\s*if last_text and last_text\.strip\(\):\s*return last_text\.strip\(\))\s*for i in range\(len\(eles\) - 2, -1, -1\):\s*t = self\.extractor\.extract_text\(eles\[i\]\)\s*if t and t\.strip\(\):\s*return t\.strip\(\)'
r2 = r'\1'

# 3. final_image_urls fallback
p3 = r'elif final_image_urls:\s*self\._final_images = \[\s*\{\s*" kind\: \url\,'
r3 = r'elif final_image_urls:\n column = self._get_latest_visual_column()\n if column not in {\left\, \right\}:\n self._final_images = [\n {\n \kind\: \url\,'

text1, c1 = re.subn(p1, r1, text)
text2, c2 = re.subn(p2, r2, text1)
text3, c3 = re.subn(p3, r3, text2)

print(f'Replacements: c1={c1}, c2={c2}, c3={c3}')
assert c1 == 1, 'c1 failed'
assert c2 == 1, 'c2 failed'
assert c3 == 1, 'c3 failed'

path.write_text(text3, encoding='utf-8')
print('Successfully applied all 3 fixes!')