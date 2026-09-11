"""Pure component regressions for the pool/monitor redesign. No network or config writes."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SETUP = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const sandbox={window:{},document:{activeElement:null}};
vm.runInNewContext(fs.readFileSync('static/js/components/TabPoolTab.js','utf8'),sandbox);
vm.runInNewContext(fs.readFileSync('static/js/components/RequestMonitorTab.js','utf8'),sandbox);
function component(def,props={}) {
 const state={...def.data(),...props};
 for(const [k,f] of Object.entries(def.methods))state[k]=f.bind(state);
 for(const [k,f] of Object.entries(def.computed))Object.defineProperty(state,k,{get:()=>f.call(state)});
 if(def.created)def.created.call(state);
 return state;
}
"""


def run_js(script):
    subprocess.run(["node", "-e", SETUP + script], cwd=ROOT, check=True, capture_output=True, text=True)


def test_pool_filters_compose_without_changing_tabs_or_config():
    run_js(r"""
const c=component(sandbox.window.TabPoolTabComponent);
c.tabs=[{persistent_index:1,url:'https://a.test/1',current_domain:'a.test',status:'idle',exposed_model_name:'Writing'},
 {persistent_index:2,url:'https://a.test/2',current_domain:'a.test',status:'busy',exposed_model_name:'Reasoning'},
 {persistent_index:3,url:'https://b.test/3',current_domain:'b.test',status:'closed'},
 {persistent_index:4,url:'https://b.test/4',current_domain:'b.test',status:'error'}];
c.routeGroups=[{id:'writing',members:[{url:c.tabs[0].url},{url:c.tabs[2].url}]}];
const before=JSON.stringify([c.tabs,c.routeGroups,c.allocationMode,c.enabledRouteMethods]);
assert.equal(JSON.stringify(c.poolStatusCounts),JSON.stringify({all:4,idle:1,busy:1,attention:2}));
c.tabStatusFilter='attention';assert.equal(c.displayedTabs.length,2);
c.tabStatusFilter='idle';c.tabSearchQuery='writing';assert.equal(c.displayedTabs.length,1);
c.tabSearchQuery='missing';assert.equal(c.displayedTabs.length,0);
c.tabSearchQuery='';c.tabStatusFilter='all';c.selectedRouteGroupId='writing';assert.equal(c.displayedTabs.length,2);
c.tabStatusFilter='attention';assert.equal(c.displayedTabs[0].status,'closed');
assert.equal(JSON.stringify([c.tabs,c.routeGroups,c.allocationMode,c.enabledRouteMethods]),before);
assert.equal(c.statusText('closed'),'已关闭');
""")


def test_monitor_counts_filters_and_analysis_view_are_independent():
    run_js(r"""
const records=Array.from({length:25},(_,i)=>({id:'r'+i,history_key:'r'+i,status:i===1?'failed':i===2?'cancelled':'completed',success:i!==1&&i!==2,
model:i%2?'model-a':'model-b',target_domain:'a.test',started_at:1700000000-i,finished_at:1700000001-i,duration_ms:2000,
is_multimodal:i%5===0,token_estimate:{prompt:100,response:50}}));
const c=component(sandbox.window.RequestMonitorTab,{records,systemStats:{running_count:2,queued_count:1,total_input_tokens:400,total_output_tokens:200},systemStatsEnabled:true,maxRecords:500,detailLoading:{}});
const before=JSON.stringify(records);assert.equal(c.monitorView,'requests');assert.equal(c.runningCount,2);assert.equal(c.queuedCount,1);
assert.equal(c.visibleRecords.length,20);c.currentPage=2;assert.equal(c.visibleRecords.length,5);
c.currentPage=1;c.statusFilter='failed';assert.equal(c.filteredRecords.length,1);
c.statusFilter='cancelled';assert.equal(c.filteredRecords.length,1);
c.statusFilter='all';c.includeMultimodal=false;assert.equal(c.baseRecords.length,20);
assert.equal(c.successCount+c.failureCount,c.baseRecords.length);
c.query='model-a';const keys=c.filteredRecords.map(r=>r.id).join(',');
c.monitorView='analytics';c.analyticsRange='30d';c.monitorView='requests';assert.equal(c.filteredRecords.map(r=>r.id).join(','),keys);
assert.equal(c.cumulativeTokens,600);assert.equal(c.sampleTokens,3000);
c.systemStatsEnabled=false;assert.equal(c.liveCountsAvailable,false);
assert.equal(JSON.stringify(records),before);
""")


def test_config_not_loaded_prevents_writes_and_default_disclosures_are_closed():
    run_js(r"""
const c=component(sandbox.window.TabPoolTabComponent);let notices=0;c.$emit=()=>notices++;
assert.equal(c.ensureConfigLoaded(),false);assert.equal(notices,1);
c.configLoaded=true;assert.equal(c.ensureConfigLoaded(),true);
assert(sandbox.window.TabPoolTabComponent.template.includes('<details class="ops-routes">'));
assert(sandbox.window.RequestMonitorTab.template.includes('<details class="ops-trend-disclosure">'));
assert(sandbox.window.RequestMonitorTab.template.includes('v-show="monitorView===\'analytics\'"'));
assert(sandbox.window.RequestMonitorTab.template.includes('aria-label="请求详情"'));
""")
