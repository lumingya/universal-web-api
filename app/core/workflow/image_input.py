"""Backward-compatible image entry point; all attachments share one coordinator."""
import mimetypes
import time
from .attachment_upload import AttachmentUploadCoordinator


class ImageInputHandler:
    def __init__(self, tab, stealth_mode, smart_delay_fn, check_cancelled_fn,
                 attachment_monitor=None, focus_input_fn=None, selectors=None,
                 coordinator=None):
        self._coordinator = coordinator or AttachmentUploadCoordinator(
            tab, selectors=selectors, monitor=attachment_monitor,
            cancelled=check_cancelled_fn, focus=focus_input_fn,
        )
        self._focus_input = focus_input_fn
        self._recent_image_upload_at = 0.0

    def has_recent_attachment_upload(self, window=45.0):
        return self._recent_image_upload_at > 0 and time.time() - self._recent_image_upload_at <= window

    def paste_attachments(self, attachments):
        self._coordinator.focus = self._focus_input
        for item in attachments:
            self._coordinator.upload(item.path, item.mime)
            self._recent_image_upload_at = time.time()
        return True

    def paste_images(self, image_paths):
        self._coordinator.focus = self._focus_input
        for path in image_paths:
            self._coordinator.upload(path, mimetypes.guess_type(str(path))[0] or "image/png")
            self._recent_image_upload_at = time.time()
        return True
