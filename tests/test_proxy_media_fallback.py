from __future__ import annotations

import start

from app.core.browser.media import BrowserMediaMixin


def test_normalize_python_proxy_url_uses_remote_dns_for_socks5() -> None:
    assert (
        start._normalize_python_proxy_url("socks5://127.0.0.1:1080")
        == "socks5h://127.0.0.1:1080"
    )
    assert (
        start._normalize_python_proxy_url("127.0.0.1:7897")
        == "http://127.0.0.1:7897"
    )
    assert start._normalize_python_proxy_url("unsupported://127.0.0.1:1") == ""


def test_service_proxy_config_overrides_http_env_and_merges_bypass(monkeypatch) -> None:
    monkeypatch.setenv("PROXY_ENABLED", "true")
    monkeypatch.setenv("PROXY_ADDRESS", "socks5://127.0.0.1:1080")
    monkeypatch.setenv("PROXY_BYPASS", "localhost,127.0.0.1,::1")
    monkeypatch.setenv("HTTP_PROXY", "http://stale.example:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://stale.example:8080")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,.internal")

    env = start._build_service_env()

    assert env["HTTP_PROXY"] == "socks5h://127.0.0.1:1080"
    assert env["HTTPS_PROXY"] == "socks5h://127.0.0.1:1080"
    assert env["NO_PROXY"] == "127.0.0.1,.internal,localhost,::1"


def test_service_proxy_config_preserves_manual_proxy_when_feature_disabled(monkeypatch) -> None:
    monkeypatch.setenv("PROXY_ENABLED", "false")
    monkeypatch.setenv("HTTP_PROXY", "http://manual.example:8080")

    env = start._build_service_env()

    assert env["HTTP_PROXY"] == "http://manual.example:8080"


class _ScreenshotElement:
    def __init__(self) -> None:
        self.scripts: list[str] = []

    def run_js(self, script: str):
        self.scripts.append(script)
        return "naturalWidth" in script


def test_screenshot_fallback_expands_and_restores_element_styles() -> None:
    element = _ScreenshotElement()

    assert BrowserMediaMixin._expand_image_element_for_screenshot(element) is True
    BrowserMediaMixin._restore_image_element_after_screenshot(element)

    assert "max-height" in element.scripts[0]
    assert "position', 'fixed" in element.scripts[0]
    assert "removeAttribute('style')" in element.scripts[1]


class _GeneratedImageElement:
    def __init__(self, url: str) -> None:
        self.url = url
        self.screenshots: list[str] = []

    def run_js(self, script: str, *_args):
        if "naturalWidth" in script and "return {" in script:
            return {
                "src": self.url,
                "complete": True,
                "natural_width": 1024,
                "natural_height": 1536,
            }
        if "__universalProxyScreenshotStyle" in script:
            return "return true" in script
        return None

    def attr(self, name: str):
        return self.url if name == "src" else None

    def get_screenshot(self, path: str) -> None:
        self.screenshots.append(path)
        with open(path, "wb") as handle:
            handle.write(b"screenshot")


class _ImageRoot:
    def __init__(self, image: _GeneratedImageElement | None, *, generic_only: bool = False) -> None:
        self.image = image
        self.generic_only = generic_only
        self.selectors: list[str] = []

    def eles(self, selector: str, timeout: float = 0.5):
        self.selectors.append(selector)
        if self.image is not None and selector == "css:img":
            return [self.image]
        return []


class _PreexistingImageElement(_GeneratedImageElement):
    def __init__(self, url: str, *, baseline_reference: str) -> None:
        super().__init__(url)
        self.baseline_reference = baseline_reference

    def run_js(self, script: str, *_args):
        if "is_preexisting" in script:
            return {
                "src": self.url,
                "is_preexisting": True,
                "baseline_reference": self.baseline_reference,
            }
        return super().run_js(script)


def test_screenshot_fallback_uses_generic_img_when_strict_selector_is_not_rendered(
    monkeypatch,
    tmp_path,
) -> None:
    stream_url = "https://cdn.example.test/output/final.png?signature=stream"
    dom_url = "https://cdn.example.test/output/final.png?signature=dom"
    image = _GeneratedImageElement(dom_url)
    reply = _ImageRoot(None)
    tab = _ImageRoot(image)
    monkeypatch.chdir(tmp_path)

    def _download_failure(*_args, **_kwargs):
        raise OSError("connection reset")

    monkeypatch.setattr("app.core.browser.media.get_public_remote_resource", _download_failure)
    result = BrowserMediaMixin()._try_screenshot_images_to_local(
        tab,
        reply,
        [{"kind": "url", "url": stream_url, "media_type": "image"}],
        {
            "selector": "img.transition-opacity.duration-500.opacity-100.aspect-square.object-cover",
            "screenshot_ready_wait_seconds": 0,
        },
    )

    assert reply.selectors == [
        "css:img.transition-opacity.duration-500.opacity-100.aspect-square.object-cover",
        "css:img",
    ]
    assert tab.selectors == reply.selectors
    assert image.screenshots
    assert result[0]["url"].startswith("/download_images/")
    assert result[0]["local_path"]


def test_screenshot_fallback_rejects_uploaded_or_history_image_in_request_baseline(
    monkeypatch,
    tmp_path,
) -> None:
    uploaded_url = "https://cdn.example.test/uploads/input.png?signature=upload"
    image = _GeneratedImageElement(uploaded_url)
    root = _ImageRoot(image)
    monkeypatch.chdir(tmp_path)

    result = BrowserMediaMixin()._try_screenshot_images_to_local(
        root,
        root,
        [{"kind": "url", "url": uploaded_url, "media_type": "image"}],
        {"selector": "img", "request_baseline_references": [uploaded_url]},
    )

    assert result == []
    assert image.screenshots == []


def test_screenshot_fallback_rejects_preexisting_dom_node_even_when_url_matches(
    monkeypatch,
    tmp_path,
) -> None:
    stream_url = "https://cdn.example.test/output/final.png?signature=stream"
    image = _PreexistingImageElement(
        "https://cdn.example.test/output/final.png?signature=dom",
        baseline_reference="https://cdn.example.test/uploads/input.png?signature=upload",
    )
    root = _ImageRoot(image)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "app.core.browser.media.get_public_remote_resource",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("connection reset")),
    )

    result = BrowserMediaMixin()._try_screenshot_images_to_local(
        root,
        root,
        [{"kind": "url", "url": stream_url, "media_type": "image"}],
        {
            "selector": "img",
            "request_baseline_token": "request-token",
            "request_baseline_property": "__universalProxyMediaBaseline",
            "request_baseline_exclude_existing_nodes": True,
            "screenshot_ready_wait_seconds": 0,
        },
    )

    assert result[0]["url"] == stream_url
    assert image.screenshots == []


def test_image_url_matching_never_uses_partial_path_or_cross_origin_match() -> None:
    assert not BrowserMediaMixin._remote_image_urls_match(
        "https://cdn.example.test/output/final.png",
        "https://cdn.example.test/output/final.png-old",
    )
    assert not BrowserMediaMixin._remote_image_urls_match(
        "https://cdn.example.test/output/final.png",
        "https://other.example.test/output/final.png",
    )
