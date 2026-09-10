"""Shared file transports and conservative upload transaction coordination.

An action acknowledgement is not upload readiness. Once an action might have
happened, no other transport is tried without reconciliation (fail closed).
"""
from __future__ import annotations

import base64
import json
import mimetypes
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.core.config import WorkflowError
from app.core.tab_pool import get_clipboard_lock
from app.utils.attachments import attachment_config, type_allowed, MIB
from app.utils.platform import get_primary_modifier_key


class DispatchState(str, Enum):
    NOT_DISPATCHED = "not_dispatched"
    DISPATCHED = "dispatched"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DispatchResult:
    state: DispatchState
    transport: str
    reason: str = ""


class AttachmentUploadCoordinator:
    def __init__(self, tab, *, selectors=None, monitor=None, config=None,
                 cancelled=lambda: False, focus=None):
        self.tab, self.monitor = tab, monitor
        self.selectors = selectors or {}
        self.config = attachment_config(config)
        self.cancelled, self.focus = cancelled, focus
        self.last_result = None
        self.completed_paths = []
        self.total_bytes = 0
        self.tainted = False
        self.resource_batch = None
        self.planned_items = []

    def _check(self):
        if self.cancelled():
            raise WorkflowError("attachment_cancelled")
        if self.tainted:
            raise WorkflowError("attachment_upload_unconfirmed")

    def _element(self, selector):
        if not selector:
            return None
        try:
            return self.tab.ele(selector, timeout=0.6)
        except Exception:
            return None

    def _inputs(self):
        configured = self.selectors.get("file_input")
        try:
            if configured:
                return list(self.tab.eles(configured, timeout=0.6) or [])
            # Prefer composer-scoped lookup; retain legacy page lookup if no root was configured.
            root_selector = self.selectors.get("composer_root")
            root = self._element(root_selector) if root_selector else self.tab
            return list(root.eles('css:input[type="file"]', timeout=0.6) or []) if root else []
        except Exception:
            return []

    def _file_input(self, path, mime):
        for ele in self._inputs():
            self._check()
            try:
                if ele.attr("disabled") is not None:
                    continue
                accept = [v.strip().lower() for v in str(ele.attr("accept") or "").split(",") if v.strip()]
                if not type_allowed(path.name, mime, accept):
                    continue
            except Exception:
                continue  # not a usable target; no side effect yet
            try:
                # DrissionPage's file input uses CDP and dispatches its own change event.
                # Never dispatch a second change here: frameworks may upload twice.
                ele.input(str(path))
                return DispatchResult(DispatchState.DISPATCHED, "file_input")
            except Exception:
                return DispatchResult(DispatchState.UNKNOWN, "file_input", "input_acknowledgement_missing")
        return DispatchResult(DispatchState.NOT_DISPATCHED, "file_input", "no_compatible_input")

    def _cdp_drop(self, path, mime):
        zone = self._element(self.selectors.get("drop_zone"))
        if zone is None:
            return DispatchResult(DispatchState.NOT_DISPATCHED, "cdp_drop")
        dropped = False
        try:
            point = zone.run_js("this.scrollIntoView({block:'center'}); const r=this.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2};")
            if not isinstance(point, dict) or not all(isinstance(point.get(k), (int, float)) and point[k] > 0 for k in ("x", "y")):
                return DispatchResult(DispatchState.NOT_DISPATCHED, "cdp_drop", "invalid_target")
            data = {"items": [], "files": [str(path)], "dragOperationsMask": 1}
            for event in ("dragEnter", "dragOver", "drop"):
                self._check()
                dropped = event == "drop"
                self.tab.run_cdp("Input.dispatchDragEvent", type=event, x=point["x"], y=point["y"], data=data, modifiers=0)
            return DispatchResult(DispatchState.DISPATCHED, "cdp_drop")
        except WorkflowError:
            raise
        except Exception:
            return DispatchResult(DispatchState.UNKNOWN if dropped else DispatchState.NOT_DISPATCHED, "cdp_drop", "cdp_unavailable")

    def _js_drop(self, path, mime):
        zone = self._element(self.selectors.get("drop_zone"))
        if zone is None or path.stat().st_size > 4 * MIB:
            return DispatchResult(DispatchState.NOT_DISPATCHED, "js_drop", "no_target_or_over_4mb")
        # Bound the fallback's memory amplification. Large files use CDP/file input.
        payload = json.dumps({"name": path.name, "mime": mime, "data": base64.b64encode(path.read_bytes()).decode("ascii")})
        script = """
const p=PAYLOAD;
const bytes=Uint8Array.from(atob(p.data), c=>c.charCodeAt(0));
const dt=new DataTransfer(); dt.items.add(new File([bytes],p.name,{type:p.mime}));
for(const type of ['dragenter','dragover','drop']) this.dispatchEvent(new DragEvent(type,{bubbles:true,cancelable:true,dataTransfer:dt}));
return true;
""".replace("PAYLOAD", payload)
        try:
            zone.run_js(script)
            return DispatchResult(DispatchState.DISPATCHED, "js_drop")
        except Exception:
            return DispatchResult(DispatchState.UNKNOWN, "js_drop", "drop_acknowledgement_missing")

    def _clipboard(self, path, mime, image=False):
        from app.utils.system_clipboard import (copy_file_to_native_clipboard, copy_image_to_native_clipboard,
                                                ClipboardUnsupportedError, ClipboardDependencyError)
        name = "image_clipboard" if image else "file_clipboard"
        if image and not mime.startswith("image/"):
            return DispatchResult(DispatchState.NOT_DISPATCHED, name)
        pasted = False
        try:
            with get_clipboard_lock():
                self._check()
                if not self.focus or not self.focus():
                    return DispatchResult(DispatchState.NOT_DISPATCHED, name, "focus_not_confirmed")
                copier = copy_image_to_native_clipboard if image else copy_file_to_native_clipboard
                copier(str(path))
                modifier = get_primary_modifier_key()
                pasted = True
                self.tab.actions.key_down(modifier).key_down("V").key_up("V").key_up(modifier)
            return DispatchResult(DispatchState.DISPATCHED, name)
        except (ClipboardUnsupportedError, ClipboardDependencyError):
            return DispatchResult(DispatchState.NOT_DISPATCHED, name, "clipboard_unavailable")
        except WorkflowError:
            raise
        except Exception:
            return DispatchResult(DispatchState.UNKNOWN if pasted else DispatchState.NOT_DISPATCHED, name)

    def _dispatch(self, name, path, mime):
        if name == "file_input":
            result = self._file_input(path, mime)
            if result.state == DispatchState.NOT_DISPATCHED:
                button = self._element(self.selectors.get("upload_btn"))
                if button is not None:
                    try:
                        button.click()
                    except Exception:
                        pass  # opening a chooser does not attach a file
                    self._check()
                    result = self._file_input(path, mime)
            return result
        if name == "cdp_drop":
            return self._cdp_drop(path, mime)
        if name == "js_drop":
            return self._js_drop(path, mime)
        return self._clipboard(path, mime, image=name == "image_clipboard")

    def upload(self, filepath, mime=None):
        self._check()
        if not self.config["enabled"]:
            raise WorkflowError("attachments_disabled")
        if self.monitor is None:
            raise WorkflowError("attachment_monitor_unavailable")
        path = Path(filepath).resolve()
        mime = mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            size = path.stat().st_size
        except OSError:
            raise WorkflowError("attachment_file_unavailable") from None
        if not size:
            raise WorkflowError("attachment_empty")
        # Include not-yet-uploaded API attachments when uploading a generated prompt file.
        # This catches combined quotas BEFORE the first upload side effect.
        pending = [item for item in self.planned_items if str(item.path.resolve()) != str(path)
                   and str(item.path.resolve()) not in self.completed_paths]
        if len(self.completed_paths) + 1 + len(pending) > self.config["max_count"]:
            raise WorkflowError("attachment_count_exceeded")
        if self.total_bytes + size + sum(item.byte_size for item in pending) > self.config["max_total_mb"] * MIB:
            raise WorkflowError("attachment_size_exceeded")
        if not type_allowed(path.name, mime, self.config["allowed_types"]):
            raise WorkflowError("attachment_type_unsupported")
        if len(self.completed_paths) >= self.config["max_count"]:
            raise WorkflowError("attachment_count_exceeded")
        if size > self.config["max_file_mb"] * MIB or self.total_bytes + size > self.config["max_total_mb"] * MIB:
            raise WorkflowError("attachment_size_exceeded")
        deadline = time.monotonic() + self.config["ready_timeout"]
        names = [path.name]  # no stem-only match against unrelated composer text
        for name in self.config["transport_order"]:
            self._check()
            if time.monotonic() >= deadline:
                break
            baseline = self.monitor.begin_tracking(expected_names=names)
            if not baseline:
                raise WorkflowError("attachment_monitor_unavailable")
            if self.resource_batch is not None:
                self.resource_batch.may_be_reading = True
            try:
                result = self._dispatch(name, path, mime)
            except WorkflowError:
                raise
            except Exception:
                result = DispatchResult(DispatchState.UNKNOWN, name, "transport_failed")
            self.last_result = result
            if result.state == DispatchState.NOT_DISPATCHED:
                continue
            # Once a side effect is possible, no blind retry or transport fallback.
            self.tainted = True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WorkflowError("attachment_upload_unconfirmed")
            ready = self.monitor.wait_until_ready(
                expected_names=names, require_observed=True, require_send_enabled=False,
                accept_existing=False, start_new_tracking=False, require_fresh_preview=True,
                max_wait=remaining, hard_max_wait=remaining, idle_timeout=remaining,
                label=f"attachment:{name}",
            )
            if self.cancelled():
                raise WorkflowError("attachment_cancelled")
            if not ready.get("success"):
                raise WorkflowError("attachment_upload_rejected" if ready.get("reason") == "upload_rejected" else "attachment_upload_unconfirmed")
            self.tainted = False
            self.completed_paths.append(str(path))
            self.total_bytes += size
            return True
        raise WorkflowError("attachment_transport_unavailable")
