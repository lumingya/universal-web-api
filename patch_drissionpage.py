# patch_drissionpage.py - 自动补丁 DrissionPage Listener
# 用法：python patch_drissionpage.py
# 时机：pip install 之后、项目启动之前

import importlib
import inspect
import shutil
from pathlib import Path
from datetime import datetime

RECOVERY_GUARD_MARKER = "# CODEX_LISTENER_RECOVERY_GUARD_V2"
STREAM_CAPTURE_GUARD_MARKER = "# CODEX_LISTENER_STREAM_CAPTURE_V1"
RECOVERY_GUARD_SNIPPET = """

# CODEX_LISTENER_RECOVERY_GUARD_V2
try:
    _orig_start_v2 = Listener.start

    def _listener_driver_alive_v2(listener):
        driver = getattr(listener, '_driver', None)
        return bool(driver and getattr(driver, 'is_running', False))

    def _listener_mark_stopped_v2(listener):
        listener.listening = False
        if getattr(listener, '_reuse_driver', False):
            listener._network_enabled = False

    def _listener_not_listening_v2(listener):
        _listener_mark_stopped_v2(listener)
        raise RuntimeError(_S._lang.join(_S._lang.NOT_LISTENING))

    def _listener_require_driver_v2(listener):
        if not listener.listening:
            raise RuntimeError(_S._lang.join(_S._lang.NOT_LISTENING))
        if not _listener_driver_alive_v2(listener):
            listener._driver = None
            _listener_not_listening_v2(listener)
        return listener._driver

    def _safe_start_v2(self, targets=None, is_regex=None, method=None, res_type=None):
        if getattr(self, 'listening', False) and not _listener_driver_alive_v2(self):
            self._driver = None
            _listener_mark_stopped_v2(self)
        elif not getattr(self, 'listening', False) and not _listener_driver_alive_v2(self):
            self._driver = None
            if getattr(self, '_reuse_driver', False):
                self._network_enabled = False
        return _orig_start_v2(self, targets, is_regex, method, res_type)

    def _safe_pause_v2(self, clear=True):
        if self.listening:
            driver = getattr(self, '_driver', None)
            if driver is not None:
                for event_name in (
                    'Network.requestWillBeSent',
                    'Network.responseReceived',
                    'Network.loadingFinished',
                    'Network.loadingFailed',
                ):
                    try:
                        driver.set_callback(event_name, None)
                    except Exception:
                        pass
            self.listening = False
        if clear:
            self.clear()

    def _safe_stop_v2(self):
        if self.listening:
            try:
                _safe_pause_v2(self)
            except Exception:
                self.listening = False
                try:
                    self.clear()
                except Exception:
                    pass

        driver = getattr(self, '_driver', None)
        if self._reuse_driver:
            if self._network_enabled and driver:
                try:
                    driver.run('Network.disable')
                except Exception:
                    pass
            self._network_enabled = False
            self._driver = None
        else:
            if driver:
                try:
                    driver.stop()
                except Exception:
                    pass
            self._driver = None

    def _safe_wait_v2(self, count=1, timeout=None, fit_count=True, raise_err=None):
        _listener_require_driver_v2(self)

        fail = False
        if not timeout:
            while self.listening and self._caught.qsize() < count:
                _listener_require_driver_v2(self)
                sleep(.03)
            fail = self._caught.qsize() < count
        else:
            end = perf_counter() + timeout
            while self.listening:
                _listener_require_driver_v2(self)
                if perf_counter() > end:
                    fail = True
                    break
                if self._caught.qsize() >= count:
                    break
                sleep(.03)
            if self._caught.qsize() < count:
                fail = True

        if fail:
            if fit_count or not self._caught.qsize():
                if raise_err is True or (_S.raise_when_wait_failed is True and raise_err is None):
                    raise WaitTimeoutError(_S._lang.join(_S._lang.WAITING_FAILED_, _S._lang.DATA_PACKET, timeout))
                else:
                    return False
            else:
                return [self._caught.get_nowait() for _ in range(self._caught.qsize())]

        if count == 1:
            return self._caught.get_nowait()

        return [self._caught.get_nowait() for _ in range(count)]

    def _safe_steps_v2(self, count=None, timeout=None, gap=1):
        if not self.listening:
            raise RuntimeError(_S._lang.join(_S._lang.NOT_LISTENING))
        caught = 0
        if timeout is None:
            while self.listening:
                _listener_require_driver_v2(self)
                if self._caught.qsize() >= gap:
                    yield self._caught.get_nowait() if gap == 1 else [self._caught.get_nowait() for _ in range(gap)]
                    if count:
                        caught += gap
                        if caught >= count:
                            return
                sleep(.03)

        else:
            end = perf_counter() + timeout
            while self.listening and perf_counter() < end:
                _listener_require_driver_v2(self)
                if self._caught.qsize() >= gap:
                    yield self._caught.get_nowait() if gap == 1 else [self._caught.get_nowait() for _ in range(gap)]
                    end = perf_counter() + timeout
                    if count:
                        caught += gap
                        if caught >= count:
                            return
                sleep(.03)
            return False

    def _safe_wait_silent_v2(self, timeout=None, targets_only=False, limit=0):
        _listener_require_driver_v2(self)
        if timeout is None:
            while ((not targets_only and self._running_requests > limit)
                   or (targets_only and self._running_targets > limit)):
                _listener_require_driver_v2(self)
                sleep(.01)
            return True

        end_time = perf_counter() + timeout
        while perf_counter() < end_time:
            _listener_require_driver_v2(self)
            if ((not targets_only and self._running_requests <= limit)
                    or (targets_only and self._running_targets <= limit)):
                return True
            sleep(.01)
        else:
            return False

    Listener.start = _safe_start_v2
    Listener.pause = _safe_pause_v2
    Listener.stop = _safe_stop_v2
    Listener.wait = _safe_wait_v2
    Listener.steps = _safe_steps_v2
    Listener.wait_silent = _safe_wait_silent_v2
except Exception:
    pass
""".lstrip("\n")

STREAM_CAPTURE_SNIPPET = """

# CODEX_LISTENER_STREAM_CAPTURE_V1
try:
    _codecs_v1 = __import__('codecs')
    _orig_listener_set_callback_v1 = Listener._set_callback
    _orig_listener_pause_v1 = Listener.pause
    _orig_listener_stop_v1 = Listener.stop
    _orig_listener_response_received_v1 = Listener._response_received
    _orig_listener_loading_finished_v1 = Listener._loading_finished
    _orig_listener_loading_failed_v1 = Listener._loading_failed
    _orig_data_packet_init_v1 = DataPacket.__init__

    def _data_packet_init_stream_v1(self, tab_id, target):
        _orig_data_packet_init_v1(self, tab_id, target)
        self._stream = {'chunks': [], 'fullText': '', 'complete': False}
        self._stream_enabled = False
        self._stream_emitted = False
        self._stream_decoder = _codecs_v1.getincrementaldecoder('utf-8')('ignore')

    def _listener_stream_dict_v1(packet):
        stream = getattr(packet, '_stream', None)
        if not isinstance(stream, dict):
            stream = {'chunks': [], 'fullText': '', 'complete': False}
            packet._stream = stream
        stream.setdefault('chunks', [])
        stream.setdefault('fullText', '')
        stream.setdefault('complete', False)
        return stream

    def _listener_stream_text_v1(packet, raw_data):
        if raw_data in (None, ''):
            return ''
        if isinstance(raw_data, (bytes, bytearray)):
            raw_bytes = bytes(raw_data)
        elif isinstance(raw_data, str):
            try:
                raw_bytes = b64decode(raw_data)
            except Exception:
                raw_bytes = raw_data.encode('utf-8', errors='ignore')
        else:
            raw_bytes = str(raw_data).encode('utf-8', errors='ignore')

        decoder = getattr(packet, '_stream_decoder', None)
        if decoder is None:
            decoder = _codecs_v1.getincrementaldecoder('utf-8')('ignore')
            packet._stream_decoder = decoder
        try:
            return decoder.decode(raw_bytes)
        except Exception:
            try:
                return raw_bytes.decode('utf-8', errors='ignore')
            except Exception:
                return ''

    def _listener_append_stream_v1(packet, raw_data, source='dataReceived'):
        text = _listener_stream_text_v1(packet, raw_data)
        if not text:
            return ''
        stream = _listener_stream_dict_v1(packet)
        stream['chunks'].append({'data': text, 'source': source})
        stream['fullText'] = f"{stream.get('fullText', '')}{text}"
        packet._raw_body = stream['fullText']
        packet._base64_body = False
        return text

    def _listener_emit_stream_packet_v1(listener, packet):
        if packet and not getattr(packet, '_stream_emitted', False):
            listener._caught.put(packet)
            packet._stream_emitted = True

    def _listener_attach_stream_response_v1(listener, packet, request_id):
        driver = getattr(listener, '_driver', None)
        if not packet or getattr(packet, '_stream_enabled', False):
            return False
        if not driver or not getattr(driver, 'is_running', False):
            return False
        try:
            result = driver.run('Network.streamResourceContent', requestId=request_id)
        except Exception:
            return False

        packet._stream_enabled = True
        stream = _listener_stream_dict_v1(packet)
        buffered = result.get('bufferedData') if isinstance(result, dict) else None
        if buffered not in (None, ''):
            _listener_append_stream_v1(packet, buffered, 'bufferedData')
        stream['complete'] = False
        _listener_emit_stream_packet_v1(listener, packet)
        return True

    def _listener_bind_extra_info_v1(listener, request_id, packet):
        r = listener._extra_info_ids.get(request_id, None)
        if not r:
            return
        obj = r.get('obj', None)
        if obj is False:
            listener._extra_info_ids.pop(request_id, None)
            return
        if isinstance(obj, DataPacket):
            response = r.get('response', None)
            if response:
                obj._requestExtraInfo = r.get('request', None)
                obj._responseExtraInfo = response
                listener._extra_info_ids.pop(request_id, None)

    def _listener_finalize_stream_v1(listener, request_id, failed_kwargs=None):
        packet = listener._request_ids.get(request_id, None)
        if not packet or not getattr(packet, '_stream_enabled', False):
            return False

        if failed_kwargs:
            packet._raw_fail_info = failed_kwargs
            packet._resource_type = failed_kwargs.get('type')
            packet.is_failed = True

        decoder = getattr(packet, '_stream_decoder', None)
        if decoder is not None:
            try:
                tail = decoder.decode(b'', final=True)
            except Exception:
                tail = ''
            if tail:
                stream = _listener_stream_dict_v1(packet)
                stream['chunks'].append({'data': tail, 'source': 'decoder_tail'})
                stream['fullText'] = f"{stream.get('fullText', '')}{tail}"

        stream = _listener_stream_dict_v1(packet)
        packet._raw_body = stream.get('fullText', '')
        packet._base64_body = False
        stream['complete'] = True
        _listener_bind_extra_info_v1(listener, request_id, packet)
        listener._request_ids.pop(request_id, None)
        _listener_emit_stream_packet_v1(listener, packet)
        listener._running_targets -= 1
        return True

    def _listener_set_callback_stream_v1(self):
        _orig_listener_set_callback_v1(self)
        self._driver.set_callback('Network.dataReceived', self._data_received)

    def _listener_pause_stream_v1(self, clear=True):
        if self.listening:
            driver = getattr(self, '_driver', None)
            if driver is not None:
                for event_name in (
                    'Network.requestWillBeSent',
                    'Network.requestWillBeSentExtraInfo',
                    'Network.responseReceived',
                    'Network.responseReceivedExtraInfo',
                    'Network.loadingFinished',
                    'Network.loadingFailed',
                    'Network.dataReceived',
                ):
                    try:
                        driver.set_callback(event_name, None)
                    except Exception:
                        pass
            self.listening = False
        if clear:
            self.clear()

    def _listener_stop_stream_v1(self):
        if self.listening:
            try:
                _listener_pause_stream_v1(self)
            except Exception:
                self.listening = False
                try:
                    self.clear()
                except Exception:
                    pass

        driver = getattr(self, '_driver', None)
        if self._reuse_driver:
            if self._network_enabled and driver:
                try:
                    driver.run('Network.disable')
                except Exception:
                    pass
            self._network_enabled = False
            self._driver = None
        else:
            if driver:
                try:
                    driver.stop()
                except Exception:
                    pass
            self._driver = None

    def _listener_response_received_stream_v1(self, **kwargs):
        _orig_listener_response_received_v1(self, **kwargs)
        packet = self._request_ids.get(kwargs.get('requestId'), None)
        if packet is not None:
            _listener_attach_stream_response_v1(self, packet, kwargs.get('requestId'))

    def _listener_data_received_stream_v1(self, **kwargs):
        packet = self._request_ids.get(kwargs.get('requestId'), None)
        if not packet or not getattr(packet, '_stream_enabled', False):
            return
        _listener_append_stream_v1(packet, kwargs.get('data'), 'dataReceived')

    def _listener_loading_finished_stream_v1(self, **kwargs):
        request_id = kwargs.get('requestId')
        packet = self._request_ids.get(request_id, None)
        if packet and getattr(packet, '_stream_enabled', False):
            self._running_requests -= 1
            if _listener_finalize_stream_v1(self, request_id):
                return
        _orig_listener_loading_finished_v1(self, **kwargs)

    def _listener_loading_failed_stream_v1(self, **kwargs):
        request_id = kwargs.get('requestId')
        packet = self._request_ids.get(request_id, None)
        if packet and getattr(packet, '_stream_enabled', False):
            self._running_requests -= 1
            if _listener_finalize_stream_v1(self, request_id, failed_kwargs=kwargs):
                return
        _orig_listener_loading_failed_v1(self, **kwargs)

    def _response_stream_property_v1(self):
        return getattr(self._data_packet, '_stream', None)

    DataPacket.__init__ = _data_packet_init_stream_v1
    Listener._set_callback = _listener_set_callback_stream_v1
    Listener.pause = _listener_pause_stream_v1
    Listener.stop = _listener_stop_stream_v1
    Listener._response_received = _listener_response_received_stream_v1
    Listener._data_received = _listener_data_received_stream_v1
    Listener._loading_finished = _listener_loading_finished_stream_v1
    Listener._loading_failed = _listener_loading_failed_stream_v1
    Response.stream = property(_response_stream_property_v1)
    Response._stream = property(_response_stream_property_v1)
except Exception:
    pass
""".lstrip("\n")


def find_listener_file():
    """定位 DrissionPage Listener 源码文件"""
    try:
        from DrissionPage._units.listener import Listener
        return Path(inspect.getfile(Listener))
    except ImportError:
        print("❌ DrissionPage 未安装")
        return None


def has_base_patch(content):
    """检查基础复用补丁是否存在"""
    return '_reuse_driver' in content and '_network_enabled' in content


def has_recovery_patch(content):
    """检查恢复补丁是否存在"""
    return RECOVERY_GUARD_MARKER in content


def has_stream_capture_patch(content):
    """检查流式抓包补丁是否存在"""
    return STREAM_CAPTURE_GUARD_MARKER in content


def check_already_patched(content):
    """检查是否已经打过完整补丁"""
    return (
        has_base_patch(content)
        and has_recovery_patch(content)
        and has_stream_capture_patch(content)
    )


def ensure_recovery_patch(content):
    """确保 V2 恢复补丁存在"""
    if has_recovery_patch(content):
        return content, False
    if not content.endswith('\n'):
        content += '\n'
    return content + '\n' + RECOVERY_GUARD_SNIPPET, True


def ensure_stream_capture_patch(content):
    """确保 V1 流式抓包补丁存在"""
    if has_stream_capture_patch(content):
        return content, False
    if not content.endswith('\n'):
        content += '\n'
    return content + '\n' + STREAM_CAPTURE_SNIPPET, True


def apply_patch(filepath):
    """应用补丁"""
    content = filepath.read_text(encoding='utf-8')
    
    if check_already_patched(content):
        print("✅ 已经打过补丁，无需重复操作")
        return True

    base_patched = has_base_patch(content)
    
    # 备份原文件
    backup = filepath.with_suffix('.py.bak')
    if not backup.exists():
        shutil.copy2(filepath, backup)
        print(f"📦 已备份原文件: {backup}")

    if not base_patched:
        # ===== 补丁 1：__init__ 添加标记 =====
        old_init_end = (
            "self._res_type = True"
        )
        new_init_end = (
            "self._res_type = True\n"
            "\n"
            "        # 复用模式：使用 tab 主连接而非创建独立连接\n"
            "        self._reuse_driver = False\n"
            "        self._network_enabled = False"
        )
        
        if old_init_end not in content:
            print("❌ 无法定位 __init__ 补丁点")
            return False
        content = content.replace(old_init_end, new_init_end, 1)
        
        # ===== 补丁 2：start() 支持复用 =====
        old_start = (
            "        self._driver = Driver(self._target_id, self._address)\n"
            "        self._driver.session_id = self._driver.run('Target.attachToTarget', targetId=self._target_id, flatten=True)['sessionId']\n"
            "        self._driver.run('Network.enable')\n"
            "\n"
            "        self._set_callback()\n"
            "        self.listening = True"
        )
        new_start = (
            "        if self._reuse_driver:\n"
            "            self._driver = self._owner.driver\n"
            "            if not self._network_enabled:\n"
            "                self._driver.run('Network.enable')\n"
            "                self._network_enabled = True\n"
            "        else:\n"
            "            self._driver = Driver(self._target_id, self._address)\n"
            "            self._driver.session_id = self._driver.run('Target.attachToTarget', targetId=self._target_id, flatten=True)['sessionId']\n"
            "            self._driver.run('Network.enable')\n"
            "\n"
            "        self._set_callback()\n"
            "        self.listening = True"
        )
        
        if old_start not in content:
            print("❌ 无法定位 start() 补丁点")
            return False
        content = content.replace(old_start, new_start, 1)
        
        # ===== 补丁 3：stop() 复用模式不关闭连接 =====
        old_stop = (
            "    def stop(self):\n"
            "        if self.listening:\n"
            "            self.pause()\n"
            "            self.clear()\n"
            "        self._driver.stop()\n"
            "        self._driver = None"
        )
        new_stop = (
            "    def stop(self):\n"
            "        if self.listening:\n"
            "            self.pause()\n"
            "            self.clear()\n"
            "\n"
            "        if self._reuse_driver:\n"
            "            if self._network_enabled and self._driver:\n"
            "                try:\n"
            "                    self._driver.run('Network.disable')\n"
            "                except Exception:\n"
            "                    pass\n"
            "                self._network_enabled = False\n"
            "            self._driver = None\n"
            "        else:\n"
            "            if self._driver:\n"
            "                self._driver.stop()\n"
            "                self._driver = None"
        )
        
        if old_stop not in content:
            print("❌ 无法定位 stop() 补丁点")
            return False
        content = content.replace(old_stop, new_stop, 1)
        
        # ===== 补丁 4：_to_target() 兼容复用模式 =====
        old_to_target = (
            "    def _to_target(self, target_id, address, owner):\n"
            "        self._target_id = target_id\n"
            "        self._address = address\n"
            "        self._owner = owner\n"
            "        if self._driver:\n"
            "            self._driver.stop()\n"
            "        if self.listening:\n"
            "            self._driver = Driver(self._target_id, self._address)\n"
            "            self._driver.session_id = self._driver.run('Target.attachToTarget',\n"
            "                                                       targetId=target_id, flatten=True)['sessionId']\n"
            "            self._driver.run('Network.enable')\n"
            "            self._set_callback()"
        )
        new_to_target = (
            "    def _to_target(self, target_id, address, owner):\n"
            "        self._target_id = target_id\n"
            "        self._address = address\n"
            "        self._owner = owner\n"
            "        if self._driver and not self._reuse_driver:\n"
            "            self._driver.stop()\n"
            "        if self.listening:\n"
            "            if self._reuse_driver:\n"
            "                if self._network_enabled and self._driver:\n"
            "                    try:\n"
            "                        self._driver.run('Network.disable')\n"
            "                    except Exception:\n"
            "                        pass\n"
            "                self._driver = self._owner.driver\n"
            "                if not self._network_enabled:\n"
            "                    self._driver.run('Network.enable')\n"
            "                    self._network_enabled = True\n"
            "            else:\n"
            "                self._driver = Driver(self._target_id, self._address)\n"
            "                self._driver.session_id = self._driver.run('Target.attachToTarget',\n"
            "                                                           targetId=target_id, flatten=True)['sessionId']\n"
            "                self._driver.run('Network.enable')\n"
            "            self._set_callback()"
        )
        
        if old_to_target not in content:
            print("⚠️ 无法定位 _to_target() 补丁点（非关键，跳过）")
        else:
            content = content.replace(old_to_target, new_to_target, 1)
    else:
        print("ℹ️ 检测到基础复用补丁已存在，跳过基础段")

    content, recovery_added = ensure_recovery_patch(content)
    if recovery_added:
        print("🩹 已追加监听恢复补丁 (V2)")
    content, stream_capture_added = ensure_stream_capture_patch(content)
    if stream_capture_added:
        print("🌊 已追加增量流捕获补丁 (V1)")
    
    # 写入修改后的文件
    filepath.write_text(content, encoding='utf-8')
    print(f"✅ 补丁已应用: {filepath}")
    print(f"   备份位置: {backup}")
    return True


def restore(filepath):
    """恢复原文件"""
    backup = filepath.with_suffix('.py.bak')
    if backup.exists():
        shutil.copy2(backup, filepath)
        print(f"✅ 已恢复原文件: {filepath}")
        return True
    else:
        print("❌ 未找到备份文件")
        return False


def main():
    import sys
    
    filepath = find_listener_file()
    if not filepath:
        return
    
    print(f"📍 Listener 源码: {filepath}")
    
    if len(sys.argv) > 1 and sys.argv[1] == '--restore':
        restore(filepath)
        return
    
    if apply_patch(filepath):
        print("\n🎉 补丁完成！")
        print("   恢复命令: python patch_drissionpage.py --restore")
    else:
        print("\n❌ 补丁失败，请将以上输出发给开发者")


if __name__ == '__main__':
    main()
