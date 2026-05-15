from app.core.network_monitor import NetworkMonitor
from app.core.parsers.base import ResponseParser


class _DummyParser(ResponseParser):
    def parse_chunk(self, raw_response: str):
        return {
            "content": "",
            "images": [],
            "done": False,
            "error": None,
        }

    def reset(self):
        return None

    @classmethod
    def get_id(cls) -> str:
        return "dummy"


class _DummyDriver:
    is_running = True


class _DummyQueue:
    def qsize(self):
        return 0


class _DummyListen:
    def __init__(self, wait_impl):
        self.listening = True
        self._driver = _DummyDriver()
        self._reuse_driver = True
        self._running_targets = 0
        self._running_requests = 0
        self._caught = _DummyQueue()
        self._wait_impl = wait_impl

    def wait(self, timeout=None):
        return self._wait_impl(self, timeout)


class _DummyTab:
    def __init__(self, listen):
        self.listen = listen


def _monitor(wait_impl, pattern="/target/stream"):
    listen = _DummyListen(wait_impl)
    tab = _DummyTab(listen)
    return NetworkMonitor(
        tab=tab,
        formatter=None,
        parser=_DummyParser(),
        stop_checker=lambda: False,
        stream_config={
            "network": {
                "listen_pattern": pattern,
                "stream_match_pattern": pattern,
                "stream_match_mode": "keyword",
            }
        },
    )


def test_poll_send_activity_matches_request_started_without_response():
    state = {"calls": 0}

    def wait_impl(listen, timeout):
        state["calls"] += 1
        if state["calls"] == 1:
            listen._running_targets = 1
        return False

    monitor = _monitor(wait_impl)
    monitor.mark_send_attempt()

    result = monitor.poll_send_activity(timeout=0.15)

    assert result["matched"] is True
    assert result["source"] == "request_started"
    assert result["running_targets"] == 1


def test_poll_send_activity_keeps_response_packet_match():
    class _Request:
        url = "https://chatglm.cn/chatglm/backend-api/assistant/stream"
        method = "POST"

    class _Response:
        url = _Request.url
        status = 200

    class _Packet:
        request = _Request()
        response = _Response()

    def wait_impl(listen, timeout):
        return _Packet()

    monitor = _monitor(wait_impl, pattern="/chatglm/backend-api/assistant/stream")
    monitor.mark_send_attempt()

    result = monitor.poll_send_activity(timeout=0.05)

    assert result["matched"] is True
    assert result["source"] == "response_packet"
    assert result["event"]["method"] == "POST"
