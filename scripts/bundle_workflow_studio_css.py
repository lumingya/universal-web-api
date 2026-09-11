"""Refresh the offline CSS bundle after editing static/css/workflow-studio.css."""
from pathlib import Path
import json
import re
root = Path(__file__).resolve().parents[1]
script = root / 'static/js/workflow-studio.js'
css = (root / 'static/css/workflow-studio.css').read_text(encoding='utf-8')
source = script.read_text(encoding='utf-8')
replacement = '/* STUDIO_STYLE_START */\nwindow.WORKFLOW_STUDIO_CSS = ' + json.dumps(css, ensure_ascii=False) + ';\n/* STUDIO_STYLE_END */'
source, count = re.subn(r'/\* STUDIO_STYLE_START \*/.*?/\* STUDIO_STYLE_END \*/', lambda _: replacement, source, count=1, flags=re.S)
assert count == 1, 'CSS bundle markers are missing'
script.write_text(source, encoding='utf-8')
