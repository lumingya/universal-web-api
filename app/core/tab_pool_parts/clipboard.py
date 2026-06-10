import threading


_clipboard_lock = threading.Lock()


def get_clipboard_lock() -> threading.Lock:
    return _clipboard_lock
