# patch_drissionpage.py - 自动补丁 DrissionPage Listener
# 用法：python patch_drissionpage.py
# 时机：pip install 之后、项目启动之前

import importlib
import inspect
import shutil
from pathlib import Path
from datetime import datetime

RECOVERY_GUARD_MARKER = "# CODEX_LISTENER_RECOVERY_GUARD_V2"
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


def check_already_patched(content):
    """检查是否已经打过完整补丁"""
    return has_base_patch(content) and has_recovery_patch(content)


def ensure_recovery_patch(content):
    """确保 V2 恢复补丁存在"""
    if has_recovery_patch(content):
        return content, False
    if not content.endswith('\n'):
        content += '\n'
    return content + '\n' + RECOVERY_GUARD_SNIPPET, True


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
