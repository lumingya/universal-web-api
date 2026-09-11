"""Explicit, repeatable shipped-config upgrade; not a startup migration.
Gemini preserves its four presets. DeepSeek is explicitly consolidated into 主预设.
Non-workflow settings are retained from the normal DeepSeek preset, not experimental transport.
The before snapshot in docs/migrations is importable using the existing site importer.
"""
from __future__ import annotations
import copy
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def group(label, steps, **variables):
    return {"action": "GROUP", "label": label, "value": {"variables": variables, "steps": steps}}


def capture(name, source, target="", **spec):
    return {"action": "CAPTURE", "target": target, "label": spec.pop("label", name),
            "value": {"name": name, "source": source, "timeout": 2, **spec}}


def branch(label, left, op, right, steps):
    return {"action": "IF", "label": label, "value": {
        "condition": {"left": left, "op": op, "right": right}, "then": steps, "else": []}}


def click(target, label, optional=False, verification=None):
    step = {"action": "CLICK", "target": target, "optional": optional, "value": None, "label": label}
    if verification:
        step["execution"] = {"retry": {"enabled": False}, "verification": {
            "enabled": True, "match": "all", "timeout": 3, "poll_interval": .1,
            "conditions": [{"target": verification[0], "state": verification[1]}]}}
    return step


def original_leaves(workflow):
    for step in workflow:
        if step.get("action") in {"GROUP", "TRY", "IF"}:
            for key in ("steps", "then", "else", "fallback"):
                yield from original_leaves(step.get("value", {}).get(key, []))
        else:
            yield copy.deepcopy(step)


def gemini(name, preset):
    leaves = list(original_leaves(preset['workflow']))
    hints = [n for n in leaves if n['action'] == 'READONLY_HINT']
    model_match = re.search(r"contains\(\.,\s*'([^']+)'\)", preset['selectors']['选择模型'])
    if not model_match:
        raise ValueError('Cannot infer model from existing selector; refusing to guess')
    model = model_match.group(1)
    pattern = r'(?i)(?<![\w.])' + re.escape(model).replace(r'\ ', r'\s*') + r'(?![\w-])'
    prepare = group('准备新对话与隐私模式', [
        click('new_chat_btn', '新建对话（不再重复按快捷键）'),
        branch('此预设需要临时对话', '{temporary_chat}', 'eq', True, [
            capture('temporary_pressed', 'attribute', '临时对话按钮', attribute='aria-pressed',
                    default=False, label='读取临时对话开关状态'),
            branch('未启用时才开启临时对话', '{temporary_pressed}', 'ne', 'true', [
                click('临时对话按钮', '开启临时对话（不可用则停止，不降级为普通对话）')])
        ])
    ], temporary_chat=name != '3.7 非隐私对话')
    choose = {
        'action':'IF', 'label':'已匹配则保留，否则切换模型', 'value':{
            'condition':{'left':'{current_model}','op':'matches','right':'{desired_pattern}'},
            'then':[], 'else':[
                capture('menu_expanded','attribute','点击模型选择',attribute='aria-expanded',default='false',label='读取模型菜单是否已展开'),
                branch('菜单关闭时才展开','{menu_expanded}','ne','true',[
                    click('点击模型选择','展开模型菜单',verification=('选择模型','visible'))]),
                click('选择模型','选择预设模型，并等待菜单关闭',verification=('选择模型','hidden'))
            ]}}
    select = group('只在需要时切换模型', [
        capture('current_model', 'text', '点击模型选择', default='', label='读取工具栏上的当前模型'),
        choose
    ], model_label=model, desired_pattern=pattern)
    sends = [n for n in leaves if n['action'] in {'FILL_INPUT','STREAM_WAIT'} or
             (n['action']=='CLICK' and n.get('target')=='send_btn')]
    for step in sends:
        step['label'] = {'FILL_INPUT':'填写本次请求','CLICK':'发送消息（沿用原发送确认）','STREAM_WAIT':'监听并读取回复'}[step['action']]
    return hints + [prepare, select, group('发送并读取回复', sends)]


def deepseek_main(site):
    """Explicit user-requested consolidation; keep normal transport and page-selected model."""
    result = copy.deepcopy(site)
    presets = result['presets']
    if set(presets) == {'主预设'}:
        result['default_preset'] = '主预设'
        return result
    source = presets.get('主预设') or presets.get('专家模式')
    if source is None:
        raise ValueError('Missing normal DeepSeek source preset; refusing to inherit experimental transport')
    preset = copy.deepcopy(source)
    leaves = list(original_leaves(preset['workflow']))
    new_chat = next(n for n in leaves if n['action']=='CLICK' and n.get('target')=='new_chat_btn')
    send = [n for n in leaves if n['action'] in {'FILL_INPUT','KEY_PRESS','STREAM_WAIT'}]
    if [n['action'] for n in send] != ['FILL_INPUT','KEY_PRESS','STREAM_WAIT'] or send[1].get('target') != 'Enter':
        raise ValueError('Unexpected normal DeepSeek send sequence; refusing to guess')
    if preset.get('stream_config',{}).get('request_transport',{}).get('mode') == 'page_fetch':
        raise ValueError('Normal DeepSeek preset must not use experimental page_fetch')
    new_chat['label'] = '新建对话（入口不存在时沿用当前会话）'
    for step,label in zip(send,['填写本次请求','回车发送','监听并读取回复']):
        step['label']=label
    send[1]['optional']=False
    preset['workflow'] = [group('准备新对话',[new_chat]),group('发送并读取回复',send)]
    for key in ('专家','快速'):
        preset.get('selectors',{}).pop(key,None)
    result['presets'] = {'主预设':preset}
    result['default_preset'] = '主预设'
    return result


def upgrade(data):
    result=copy.deepcopy(data)
    targets = {
        'gemini.google.com': (gemini, {'3.7flash','3.1 pro','3.5 flash lite','3.7 非隐私对话'}),
    }
    for domain, (build,names) in targets.items():
        for name,preset in result[domain]['presets'].items():
            if name in names: preset['workflow']=build(name,preset)
    result['chat.deepseek.com'] = deepseek_main(result['chat.deepseek.com'])
    archived = json.loads((ROOT/'docs/migrations/gemini-deepseek-v2.before.json').read_text())
    if result.get('www.google.com') == archived.get('www.google.com'):
        result.pop('www.google.com',None)
    return result


if __name__=='__main__':
    import sys
    sys.path.insert(0, str(ROOT))
    from app.core.workflow.flow_runtime import validate_workflow
    path=ROOT/'config/sites.json'
    original=json.loads(path.read_text())
    changed=upgrade(original)
    for domain in ('gemini.google.com','chat.deepseek.com'):
        for name,preset in changed[domain]['presets'].items():
            validate_workflow(preset['workflow'])
            print(domain,name,'→',len(preset['workflow']),'top-level stages')
    path.write_text(json.dumps(changed,ensure_ascii=False,indent=2)+'\n')
