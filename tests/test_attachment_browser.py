"""Optional real Chromium tests on a local synthetic composer (no AI/login/network).
Install: pip install playwright; python -m playwright install --with-deps chromium
"""
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
from app.core.config import WorkflowError
from app.core.workflow.attachment_monitor import AttachmentMonitor
from app.core.workflow.attachment_upload import AttachmentUploadCoordinator

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def page():
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        except playwright.Error as exc:
            pytest.skip(f"Chromium unavailable: {str(exc).splitlines()[0]}")
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        yield page
        browser.close()


def test_vue_panel_renders_and_edits_real_controls(page):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_content('<main id="app"></main>')
    page.add_script_tag(path=str(ROOT / "static/vendor/vue.global.prod.js"))
    page.add_script_tag(path=str(ROOT / "static/js/components/panels/FilePastePanel.js"))
    page.evaluate("""() => {
      const app=Vue.createApp({components:{panel:window.FilePastePanel},
        data:()=>({config:{enabled:false,temp_file_type:'txt'}}),
        template:'<panel :file-paste-config="config" :collapsed="false" current-domain="example.com" selected-preset="main" />'});
      app.config.globalProperties.$icons={chevronDown:'',chevronUp:''};
      window.testConfig=app.mount('#app').config;
    }""")
    assert page.get_by_text("通用附件管线", exact=True).is_visible()
    page.get_by_label("最多附件数").fill("6")
    page.get_by_label("最多附件数").press("Tab")
    page.get_by_label("允许的 MIME / 扩展名").fill("image/*, .pdf")
    page.get_by_label("允许的 MIME / 扩展名").press("Tab")
    page.get_by_label("上传错误 CSS 选择器", exact=False).fill(".upload-error\n.failed-file")
    page.get_by_label("上传错误 CSS 选择器", exact=False).press("Tab")
    page.get_by_role("button", name="浏览器原生拖拽上移").click()
    # Styled toggle uses a visually hidden input; click its actual label.
    page.locator('label[title="允许当前预设上传附件"]').click()
    state = page.evaluate("window.testConfig")
    assert state["attachments"]["max_count"] == 6
    assert state["attachments"]["allowed_types"] == ["image/*", ".pdf"]
    assert state["attachments"]["error_selectors"] == [".upload-error", ".failed-file"]
    assert state["attachments"]["transport_order"][0] == "cdp_drop"
    assert state["attachments"]["enabled"] is False and state["enabled"] is False
    assert not errors


class Element:
    def __init__(self, locator, ack_error=False): self.locator, self.ack_error = locator, ack_error
    def attr(self, key): return self.locator.get_attribute(key)
    def input(self, path):
        self.locator.set_input_files(path)
        if self.ack_error: raise TimeoutError("ack missing")


class Tab:
    def __init__(self, page, ack_error=False): self.page, self.ack_error = page, ack_error
    def run_js(self, script): return self.page.evaluate("() => {" + script + "}")
    def eles(self, selector, **kw):
        return [Element(value, self.ack_error) for value in self.page.locator(selector.removeprefix("css:")).all()]
    def ele(self, selector, **kw): return None


def composer(page, mode="ready", ack_error=False):
    page.set_content('''<div class="history"><div class="attachment">notes.txt</div></div>
      <form class="composer"><textarea></textarea><input type="file" accept=".txt">
      <div id="previews"></div><button type="button" id="send">Send</button></form>''')
    page.evaluate("""mode => {
      window.uploadCalls=0;
      document.querySelector('input[type=file]').addEventListener('change', event=>{
        window.uploadCalls++;
        if(mode==='silent') return;
        const node=document.createElement('div'); node.className='attachment';
        node.textContent=event.target.files[0].name;
        document.querySelector('#previews').append(node);
        event.target.value=''; // common framework behavior: input is reset immediately
        if(mode==='error') {node.dataset.uploadState='error'; return;}
        node.setAttribute('aria-busy','true');
        if(mode==='ready') setTimeout(()=>node.removeAttribute('aria-busy'),80);
      });
    }""", mode)
    tab = Tab(page, ack_error)
    selectors = {"input_box": "textarea", "send_btn": "#send", "file_input": "css:input[type=file]"}
    monitor = AttachmentMonitor(tab, selectors=selectors, config={"root_selectors": [".composer"]}, check_cancelled_fn=lambda: False)
    uploader = AttachmentUploadCoordinator(tab, selectors=selectors, monitor=monitor,
                                          config={"ready_timeout": 2, "transport_order": ["file_input", "js_drop"]})
    return uploader


@pytest.mark.parametrize("ack_error", [False, True])
def test_actual_dom_upload_handles_input_reset_and_lost_ack(page, tmp_path, ack_error):
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    uploader = composer(page, ack_error=ack_error)
    assert uploader.upload(path)
    assert page.evaluate("window.uploadCalls") == 1
    assert not uploader.tainted


@pytest.mark.parametrize("mode,code", [("pending", "attachment_upload_unconfirmed"), ("silent", "attachment_upload_unconfirmed"), ("error", "attachment_upload_rejected")])
def test_actual_dom_never_accepts_failed_pending_or_input_only_upload(page, tmp_path, mode, code):
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    uploader = composer(page, mode=mode)
    with pytest.raises(WorkflowError, match=code):
        uploader.upload(path)
    assert page.evaluate("window.uploadCalls") == 1
    assert uploader.tainted
