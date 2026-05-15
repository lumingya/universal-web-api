import pytest

from app.core.network_monitor import NetworkMonitor
from app.core.network_monitor import NetworkMonitorTimeout
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


class _DummyFormatter:
    def pack_chunk(self, content, completion_id=None):
        return content


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


def _monitor(wait_impl, pattern="/target/stream", *, parser=None, formatter=None, network_config=None):
    listen = _DummyListen(wait_impl)
    tab = _DummyTab(listen)
    merged_network = {
        "listen_pattern": pattern,
        "stream_match_pattern": pattern,
        "stream_match_mode": "keyword",
    }
    if isinstance(network_config, dict):
        merged_network.update(network_config)
    return NetworkMonitor(
        tab=tab,
        formatter=formatter,
        parser=parser or _DummyParser(),
        stop_checker=lambda: False,
        stream_config={
            "network": merged_network
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


class _BodyParser(ResponseParser):
    def parse_chunk(self, raw_response: str):
        return {
            "content": f"parsed:{raw_response}",
            "images": [],
            "done": True,
            "error": None,
        }

    def reset(self):
        return None

    @classmethod
    def get_id(cls) -> str:
        return "body-parser"


class _TargetRequest:
    url = "https://chat.deepseek.com/api/v0/chat/completion"
    method = "POST"


class _TargetResponse:
    url = _TargetRequest.url
    status = 200
    body = None
    _response = {}


class _TargetPacket:
    request = _TargetRequest()
    response = _TargetResponse()


def test_stream_output_waits_for_first_target_body_without_stream_hint(monkeypatch):
    state = {"calls": 0}

    def wait_impl(listen, timeout):
        state["calls"] += 1
        if state["calls"] == 1:
            return _TargetPacket()
        return False

    monitor = _monitor(
        wait_impl,
        pattern="api/v0/chat/completion",
        parser=_BodyParser(),
        formatter=_DummyFormatter(),
        network_config={
            "response_interval": 0.01,
            "silence_threshold": 0.01,
        },
    )
    monitor._is_listening = True

    monkeypatch.setattr(monitor, "_is_event_stream_response", lambda response: False)
    wait_calls = {"count": 0}

    def fake_wait_for_stream_body(response, initial_body, initial_source, wait_budget=None):
        wait_calls["count"] += 1
        assert wait_budget == monitor._initial_target_body_wait
        return "body-ready", "body"

    monkeypatch.setattr(monitor, "_wait_for_stream_body", fake_wait_for_stream_body)

    chunks = list(monitor._stream_output_phase("cid"))

    assert wait_calls["count"] == 1
    assert chunks == ["parsed:body-ready"]


def test_stream_output_falls_back_when_first_target_body_never_arrives(monkeypatch):
    state = {"calls": 0}

    def wait_impl(listen, timeout):
        state["calls"] += 1
        if state["calls"] == 1:
            return _TargetPacket()
        return False

    monitor = _monitor(
        wait_impl,
        pattern="api/v0/chat/completion",
        parser=_BodyParser(),
        formatter=_DummyFormatter(),
        network_config={
            "response_interval": 0.01,
            "silence_threshold": 0.01,
        },
    )
    monitor._is_listening = True

    monkeypatch.setattr(monitor, "_is_event_stream_response", lambda response: False)
    monkeypatch.setattr(
        monitor,
        "_wait_for_stream_body",
        lambda response, initial_body, initial_source, wait_budget=None: ("", initial_source),
    )

    with pytest.raises(NetworkMonitorTimeout, match="正文未就绪"):
        list(monitor._stream_output_phase("cid"))
