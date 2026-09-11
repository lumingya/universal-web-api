"""Pure-JS transfer and parent-draft contracts; no browser or server needed."""
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_scoped_transfer_parser_atomic_apply_and_draft_management():
    script=r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const ctx={window:{},console,confirm:()=>true,alert:()=>{},setTimeout,clearTimeout};
vm.createContext(ctx);
for(const f of ['static/js/components/panels/PresetTransfer.js','static/js/components/ConfigTab.js'])vm.runInContext(fs.readFileSync(f,'utf8'),ctx);
const support=ctx.window.PresetTransferSupport,methods=ctx.window.PresetTransfer.methods;
const parse=support.sources,plain=x=>JSON.parse(JSON.stringify(x));
const source={domain:'source.example',default_preset:'a',advanced:{shared:true},presets:{a:{workflow:[],selectors:{input:'textarea'},extension:{future:123},domain:'keep-inner-domain',preset_name:'keep-inner-name'},b:{workflow:[]}}};
const parsed=parse(source,'target.example');
assert.equal(parsed[0].presets.a.domain,'keep-inner-domain');assert.equal(parsed[0].presets.a.preset_name,'keep-inner-name');
assert.equal(parsed[0].domain,'source.example');assert.equal(parsed[0].settings.advanced.shared,true);
parsed[0].presets.a.extension.future=456;assert.equal(source.presets.a.extension.future,123);
assert.deepEqual(Object.keys(parse({workflow:[],selectors:{}},'legacy.example')[0].presets),['主预设']);
assert.equal(parse({'one.example':source,'two.example':source,_metadata:{}},'target').length,2);
for(const bad of [[],{}, {kind:'workflow',workflow:[]},{presets:{bad:{workflow:{}}}},{presets:{bad:{selectors:[]}}},JSON.parse('{"presets":{"__proto__":{}}}')])assert.throws(()=>parse(bad,'target'));
assert.equal(support.safeName('constructor'),false);
(async()=>{
 const make=()=>({domain:'target.example',generation:1,busy:false,error:'',mode:'site',source:parse(source,'target')[0],names:['a','b'],existing:{a:{workflow:[]}},overwrite:false,conflicts:['a'],includeSettings:false,preset:'a',sourcePreset:'a',targetName:'new',calls:[],emitted:[],validate:async function(c){this.calls.push(c)},close(){this.open=false},$emit(event,payload){this.emitted.push(payload)}});
 let state=make();await methods.apply.call(state);
 assert.deepEqual(Object.keys(state.emitted[0].presets),['b']);assert.equal(state.emitted[0].settings,null);assert.equal(state.emitted[0].domain,'target.example');
 state=make();state.overwrite=true;state.includeSettings=true;await methods.apply.call(state);
 assert.deepEqual(Object.keys(state.emitted[0].presets),['a','b']);assert.equal(state.emitted[0].settings.advanced.shared,true);
 state=make();state.mode='preset';await methods.apply.call(state);
 assert.deepEqual(Object.keys(state.emitted[0].presets),['new']);assert.equal(state.emitted[0].select,'new');
 state=make();state.overwrite=true;state.validate=async function(){if(this.calls.length++)throw Error('second invalid')};
 await methods.apply.call(state);assert.equal(state.emitted.length,0);assert.equal(state.error,'second invalid');
 state=make();state.validate=async function(){this.generation++};await methods.apply.call(state);assert.equal(state.emitted.length,0);
 state=make();state.validate=async function(){this.domain='other.example'};await methods.apply.call(state);assert.equal(state.emitted.length,0);
 state=make();state.mode='preset';state.targetName='a';ctx.confirm=()=>false;await methods.apply.call(state);assert.equal(state.emitted.length,0);assert.equal(state.calls.length,0);
 const tab=ctx.window.ConfigTab.methods;
 const parent={currentDomain:'target.example',currentConfig:{default_preset:'a',presets:{a:{workflow:[],custom:1},keep:{custom:2}},advanced:{shared:'keep'}},selectedPreset:'a',defaultPreset:'a',pendingPresetTransfer:0,flushMutableSectionDrafts(){},ensurePresetMutableSections(){}};
 tab.applyPresetTransfer.call(parent,{domain:'wrong.example',presets:{bad:{}}});assert.equal(parent.pendingPresetTransfer,0);
 tab.applyPresetTransfer.call(parent,{domain:'target.example',presets:{new:{custom:'new'}},settings:null,select:'new'});
 assert.equal(parent.pendingPresetTransfer,1);assert.equal(parent.selectedPreset,'new');assert.equal(parent.currentConfig.advanced.shared,'keep');
 assert.equal(tab.stagePresetOperation.call(parent,'rename','renamed'),true);assert.equal(parent.currentConfig.presets.new,undefined);assert.equal(parent.currentConfig.presets.renamed.custom,'new');
 tab.stagePresetOperation.call(parent,'create','cloned');assert.equal(parent.currentConfig.presets.cloned.custom,'new');
 tab.stagePresetOperation.call(parent,'default');assert.equal(parent.currentConfig.default_preset,'cloned');
 tab.stagePresetOperation.call(parent,'delete');assert.equal(parent.currentConfig.presets.cloned,undefined);assert.ok(parent.currentConfig.presets[parent.currentConfig.default_preset]);
 assert.equal(parent.currentConfig.presets.keep.custom,2);
 console.log('PASS scoped transfer parser, non-destructive/atomic import, conflict cancellation, stale request guards, draft rename/clone/default/delete');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result=subprocess.run(['node','-e',script],cwd=ROOT,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
