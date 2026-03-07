# patch_drissionpage.py - 自动补丁 DrissionPage Listener
# 用法：python patch_drissionpage.py
# 时机：pip install 之后、项目启动之前

import importlib
import inspect
import shutil
from pathlib import Path
from datetime import datetime


def find_listener_file():
    """定位 DrissionPage Listener 源码文件"""
    try:
        from DrissionPage._units.listener import Listener
        return Path(inspect.getfile(Listener))
    except ImportError:
        print("[ERROR] DrissionPage 未安装")
        return None

def find_driver_file():
    """定位 DrissionPage Driver 源码文件"""
    try:
        from DrissionPage._base.driver import Driver
        return Path(inspect.getfile(Driver))
    except ImportError:
        print("[ERROR] DrissionPage 未安装")
        return None


def check_already_patched(content):
    """检查是否已经打过补丁"""
    return '_reuse_driver' in content

def check_stability_patched(content):
    """检查是否已经打过稳定性补丁"""
    return 'CODEX_STABILITY_GUARD_V1' in content


def apply_patch(filepath):
    """应用补丁"""
    content = filepath.read_text(encoding='utf-8')
    changed = False
    base_patched = check_already_patched(content)
    
    # 备份原文件
    backup = filepath.with_suffix('.py.bak')
    if not backup.exists():
        shutil.copy2(filepath, backup)
        print(f"[BACKUP] 已备份原文件: {backup}")

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
            print("[ERROR] 无法定位 __init__ 补丁点")
            return False
        content = content.replace(old_init_end, new_init_end, 1)
        changed = True

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
            print("[ERROR] 无法定位 start() 补丁点")
            return False
        content = content.replace(old_start, new_start, 1)
        changed = True

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
            print("[ERROR] 无法定位 stop() 补丁点")
            return False
        content = content.replace(old_stop, new_stop, 1)
        changed = True

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
            print("[WARN] 无法定位 _to_target() 补丁点（非关键，跳过）")
        else:
            content = content.replace(old_to_target, new_to_target, 1)
            changed = True
    else:
        print("[OK] 基础补丁已存在，跳过基础替换")

    # ===== 补丁 5：稳定性保护（避免 _driver 被置空后回调线程崩溃）=====
    if not check_stability_patched(content):
        content += (
            "\n\n# CODEX_STABILITY_GUARD_V1\n"
            "try:\n"
            "    _orig_req = Listener._requestWillBeSent\n"
            "    _orig_finish = Listener._loading_finished\n"
            "\n"
            "    def _safe_requestWillBeSent(self, **kwargs):\n"
            "        driver = getattr(self, '_driver', None)\n"
            "        if driver is None:\n"
            "            return\n"
            "        try:\n"
            "            return _orig_req(self, **kwargs)\n"
            "        except AttributeError as e:\n"
            "            if \"NoneType\" in str(e) and \"run\" in str(e):\n"
            "                return\n"
            "            raise\n"
            "\n"
            "    def _safe_loading_finished(self, **kwargs):\n"
            "        try:\n"
            "            return _orig_finish(self, **kwargs)\n"
            "        except AttributeError as e:\n"
            "            if \"NoneType\" in str(e) and \"run\" in str(e):\n"
            "                rid = kwargs.get('requestId')\n"
            "                if rid:\n"
            "                    try:\n"
            "                        self._request_ids.pop(rid, None)\n"
            "                    except Exception:\n"
            "                        pass\n"
            "                return\n"
            "            raise\n"
            "\n"
            "    Listener._requestWillBeSent = _safe_requestWillBeSent\n"
            "    Listener._loading_finished = _safe_loading_finished\n"
            "except Exception:\n"
            "    pass\n"
        )
        changed = True

    # 写入修改后的文件
    if changed:
        filepath.write_text(content, encoding='utf-8')
        print(f"[OK] 补丁已应用: {filepath}")
    else:
        print("[OK] 无需改动（已是最新补丁）")
    print(f"   备份位置: {backup}")
    return True


def apply_driver_stability_patch(filepath):
    """给 Driver 追加稳定性补丁，防止事件回调异常导致线程退出。"""
    content = filepath.read_text(encoding='utf-8')
    marker = "CODEX_DRIVER_STABILITY_GUARD_V1"
    if marker in content:
        print("[OK] Driver 稳定性补丁已存在")
        return True

    backup = filepath.with_suffix('.py.bak')
    if not backup.exists():
        shutil.copy2(filepath, backup)
        print(f"[BACKUP] 已备份原文件: {backup}")

    content += (
        "\n\n# CODEX_DRIVER_STABILITY_GUARD_V1\n"
        "try:\n"
        "    _orig_handle_event_loop = Driver._handle_event_loop\n"
        "\n"
        "    def _safe_handle_event_loop(self):\n"
        "        while self.is_running:\n"
        "            try:\n"
        "                _orig_handle_event_loop(self)\n"
        "            except Exception:\n"
        "                continue\n"
        "\n"
        "    Driver._handle_event_loop = _safe_handle_event_loop\n"
        "except Exception:\n"
        "    pass\n"
    )
    filepath.write_text(content, encoding='utf-8')
    print(f"[OK] Driver 稳定性补丁已应用: {filepath}")
    print(f"   备份位置: {backup}")
    return True


def restore(filepath):
    """恢复原文件"""
    backup = filepath.with_suffix('.py.bak')
    if backup.exists():
        shutil.copy2(backup, filepath)
        print(f"[OK] 已恢复原文件: {filepath}")
        return True
    else:
        print("[ERROR] 未找到备份文件")
        return False


def main():
    import sys
    
    filepath = find_listener_file()
    if not filepath:
        return
    driver_path = find_driver_file()

    print(f"[PATH] Listener 源码: {filepath}")
    if driver_path:
        print(f"[PATH] Driver 源码: {driver_path}")
    
    if len(sys.argv) > 1 and sys.argv[1] == '--restore':
        restore(filepath)
        if driver_path:
            restore(driver_path)
        return

    if apply_patch(filepath):
        if driver_path:
            apply_driver_stability_patch(driver_path)
        print("\n[OK] 补丁完成！")
        print("   恢复命令: python patch_drissionpage.py --restore")
    else:
        print("\n[ERROR] 补丁失败，请将以上输出发给开发者")


if __name__ == '__main__':
    main()
