"""步骤 9：patch_drissionpage.py 的失败必须"响"，不能静默降级。

背景（见 docs/review/HEALTH_ASSESSMENT-2026-10.md P2-1）：
本项目在启动时**直接改写已安装 DrissionPage 的 Listener 源码**。一旦
DrissionPage 升级导致注入点消失，补丁会失效——而失效后的表现只是网络监听
模式不可用，进程照常启动，排查时极难联想到补丁。

修复前 ``main()`` 从不返回非零退出码，于是 bootstrap.py 里那句
``if result.returncode != 0`` 的告警**永远不会触发**，失败完全静默。

现在约定退出码：
    0 = 补丁已应用 / 已打过 / 已恢复
    1 = 找到了 Listener 源码，但补丁失败或写回后复核未通过
    2 = 环境里没有 DrissionPage / 定位不到 Listener 源码

本测试锁住这三个语义，以及新增的 verify_patch() 复核逻辑。
"""

from __future__ import annotations

import pytest

import patch_drissionpage as pdp

# apply_patch() 注入的基础段（片段里可能也出现同名标记，故单独定义一份）
BASE_SEGMENT = (
    "        self._reuse_driver = False\n"
    "        self._network_enabled = False"
)


def _patched_content(*, with_base=True, with_recovery=True, with_stream=True) -> str:
    """拼出 Listener 源码。

    使用模块里**真实的注入片段**（RECOVERY_GUARD_SNIPPET / STREAM_CAPTURE_SNIPPET），
    而不是手工凑标记：has_stream_capture_patch() 除 marker 外还校验 7 个能力子串
    （Network.eventSourceMessageReceived、_uwapi_stream_lock_v2 等）并要求
    DRISSION_STREAM_CAPTURE_MAX_CHARS 不存在，手写极易漏项；用真实片段天然与
    检查逻辑对齐，也不会随片段演进而失配。
    """
    parts = ["class Listener:"]
    if with_base:
        parts.append(BASE_SEGMENT)
    if with_recovery:
        parts.append(pdp.RECOVERY_GUARD_SNIPPET)
    if with_stream:
        parts.append(pdp.STREAM_CAPTURE_SNIPPET)
    text = "\n".join(parts)
    if not with_base:
        # 两个片段内部也含 _reuse_driver / _network_enabled，
        # 必须全局剔除才能真正让 has_base_patch() 判定为未打补丁。
        text = text.replace("_reuse_driver", "").replace("_network_enabled", "")
    return text


# --------------------------------------------------------------------------- #
# verify_patch()
# --------------------------------------------------------------------------- #


def test_verify_patch_accepts_fully_patched_file(tmp_path):
    target = tmp_path / "listener.py"
    target.write_text(_patched_content(), encoding="utf-8")
    assert pdp.verify_patch(target) is True


def test_verify_patch_rejects_unpatched_file(tmp_path):
    target = tmp_path / "listener.py"
    target.write_text("class Listener:\n    pass\n", encoding="utf-8")
    assert pdp.verify_patch(target) is False


@pytest.mark.parametrize("drop", ["base", "recovery", "stream"])
def test_verify_patch_rejects_partially_patched_file(tmp_path, drop):
    """缺任何一段都必须被拦下。

    这是"部分命中"最容易静默发生的场景：例如 DrissionPage 升级后只有某个
    注入点还在，补丁脚本照常打印成功，实际能力却缺了一截。
    """
    content = _patched_content(**{f"with_{drop}": False})
    target = tmp_path / "listener.py"
    target.write_text(content, encoding="utf-8")
    assert pdp.verify_patch(target) is False


def test_verify_patch_rejects_unreadable_file(tmp_path):
    assert pdp.verify_patch(tmp_path / "does_not_exist.py") is False


# --------------------------------------------------------------------------- #
# main() 的退出码语义
# --------------------------------------------------------------------------- #


def test_main_exits_2_when_drissionpage_missing(monkeypatch):
    """退出码 2 专门表示"没找到 DrissionPage"，与"补丁失败"区分开。"""
    monkeypatch.setattr(pdp, "find_listener_file", lambda: None)
    with pytest.raises(SystemExit) as exc:
        pdp.main()
    assert exc.value.code == 2


def test_main_exits_1_when_apply_patch_fails(monkeypatch, tmp_path):
    """本次修复的核心：补丁失败必须让 bootstrap 的告警真的能触发。"""
    target = tmp_path / "listener.py"
    target.write_text("class Listener:\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(pdp, "find_listener_file", lambda: target)
    monkeypatch.setattr(pdp, "apply_patch", lambda filepath: False)

    with pytest.raises(SystemExit) as exc:
        pdp.main()
    assert exc.value.code == 1


def test_main_exits_1_when_written_content_fails_verification(monkeypatch, tmp_path):
    """apply_patch 返回 True 但文件里没有标记 —— 同样要失败，不能放行。"""
    target = tmp_path / "listener.py"
    target.write_text("class Listener:\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(pdp, "find_listener_file", lambda: target)
    monkeypatch.setattr(pdp, "apply_patch", lambda filepath: True)

    with pytest.raises(SystemExit) as exc:
        pdp.main()
    assert exc.value.code == 1


def test_main_exits_0_on_success(monkeypatch, tmp_path):
    target = tmp_path / "listener.py"
    target.write_text(_patched_content(), encoding="utf-8")
    monkeypatch.setattr(pdp, "find_listener_file", lambda: target)
    monkeypatch.setattr(pdp, "apply_patch", lambda filepath: True)

    with pytest.raises(SystemExit) as exc:
        pdp.main()
    assert exc.value.code == 0
