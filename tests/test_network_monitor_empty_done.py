from app.core.network_monitor import NetworkMonitor


class FallbackParser:
    def should_fallback_to_dom_when_no_visible_content(self):
        return True


class PlainParser:
    def should_fallback_to_dom_when_no_visible_content(self):
        return False


def _monitor(parser):
    monitor = NetworkMonitor.__new__(NetworkMonitor)
    monitor.parser = parser
    monitor._total_chunks = 0
    monitor._last_stream_media_items = []
    return monitor


def test_empty_done_falls_back_for_dom_fallback_parsers():
    monitor = _monitor(FallbackParser())

    assert monitor._should_fallback_on_empty_done({"content": "", "images": [], "done": True}) is True


def test_empty_done_does_not_fallback_when_parser_does_not_request_dom_fallback():
    monitor = _monitor(PlainParser())

    assert monitor._should_fallback_on_empty_done({"content": "", "images": [], "done": True}) is False


def test_empty_done_does_not_fallback_when_media_was_extracted():
    monitor = _monitor(FallbackParser())

    assert monitor._should_fallback_on_empty_done(
        {"content": "", "images": [{"url": "https://example.test/image.png"}], "done": True}
    ) is False


def test_empty_done_does_not_fallback_when_previous_media_was_recorded():
    monitor = _monitor(FallbackParser())
    monitor._last_stream_media_items = [{"url": "https://example.test/image.png"}]

    assert monitor._should_fallback_on_empty_done({"content": "", "images": [], "done": True}) is False
