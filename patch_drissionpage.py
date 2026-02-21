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
        print("❌ DrissionPage 未安装")
        return None


def check_already_patched(content):
    """检查是否已经打过补丁"""
    return '_reuse_driver' in content


def apply_patch(filepath):
    """应用补丁"""
    content = filepath.read_text(encoding='utf-8')
    
    if check_already_patched(content):
        print("✅ 已经打过补丁，无需重复操作")
        return True
    
    # 备份原文件
    backup = filepath.with_suffix('.py.bak')
    if not backup.exists():
        shutil.copy2(filepath, backup)
        print(f"📦 已备份原文件: {backup}")
    
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