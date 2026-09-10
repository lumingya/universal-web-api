"""Frontend config mutations and backend save compatibility (no browser needed)."""
import asyncio
import copy
import json
import subprocess
from pathlib import Path

from app.api import config_routes
from app.services.config.engine import ConfigEngine

ROOT = Path(__file__).resolve().parents[1]


def test_attachment_panel_configuration_mutations():
    script = r"""
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const ctx={window:{}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
const component=ctx.window.FilePastePanel;
const state={...component.data(), filePasteConfig:{enabled:false,temp_file_type:'pdf'}};
for(const [key,fn] of Object.entries(component.methods)) state[key]=fn.bind(state);
for(const [key,fn] of Object.entries(component.computed)) Object.defineProperty(state,key,{get:()=>fn.call(state)});
const before=JSON.stringify(state.filePasteConfig);
assert.equal(state.resolvedAttachments.max_count,8);
assert.equal(JSON.stringify(state.filePasteConfig),before,'reading defaults mutated preset');
assert.equal(state.longTextStrategy,'attachment');
state.updateAttachmentLimit('max_count',999,32);
assert.equal(state.filePasteConfig.attachments.max_count,32);
state.updateAllowedTypes('IMAGE/*, .PDF; audio/* .PDF');
assert.equal(JSON.stringify(state.filePasteConfig.attachments.allowed_types),JSON.stringify(['image/*','.pdf','audio/*']));
state.updateAttachmentField('enabled',false);
assert.equal(state.filePasteConfig.enabled,false,'attachment toggle altered long-text mode');
state.moveTransport(1,-1);
assert.equal(state.filePasteConfig.attachments.transport_order[0],'cdp_drop');
for(const value of [...state.resolvedAttachments.transport_order]) state.toggleTransport(value);
assert.equal(state.resolvedAttachments.transport_order.length,0,'empty list must not restore defaults');
state.toggleTransport('image_clipboard');
assert.equal(state.resolvedAttachments.transport_order[0],'image_clipboard');
state.updateLongTextStrategy('chunk');
assert.equal(state.filePasteConfig.temp_file_type,'chunk');
state.updateLongTextStrategy('attachment');
assert.equal(state.filePasteConfig.temp_file_type,'txt');
assert(component.template.includes('通用附件管线'));
"""
    subprocess.run(["node", "-e", script, str(ROOT / "static/js/components/panels/FilePastePanel.js")], check=True, capture_output=True, text=True)


def test_attachment_config_save_api_roundtrip(monkeypatch):
    engine = ConfigEngine.__new__(ConfigEngine)
    stored = {"file_paste": {"enabled": True, "temp_file_type": "pdf", "threshold": 60000}}
    engine.refresh_if_changed = lambda: None
    engine._get_site_data = lambda *args: stored
    def save(data, field, value):
        data[field] = copy.deepcopy(value)
        return True
    engine._assign_preset_field_and_save = save
    monkeypatch.setattr(config_routes, "config_engine", engine)
    monkeypatch.setattr(config_routes, "_resolve_requested_preset_or_404", lambda domain, preset: "main")
    settings = {"enabled": False, "max_count": 4, "allowed_types": [".pdf"], "transport_order": [], "error_selectors": [".upload-error"]}
    class Request:
        async def json(self):
            return {"preset_name": "main", "file_paste": {"attachments": settings}}
    result = asyncio.run(config_routes.set_site_file_paste_config("example.com", Request(), authenticated=True))
    assert result["status"] == "success"
    loaded = asyncio.run(config_routes.get_site_file_paste_config("example.com", "main", authenticated=True))["file_paste"]
    assert loaded["enabled"] and loaded["temp_file_type"] == "pdf"
    assert all(loaded["attachments"][key] == value for key, value in settings.items())
    assert "attachments" not in engine._validate_file_paste_config({"enabled": False}), "opening old configuration must not migrate all sites"
