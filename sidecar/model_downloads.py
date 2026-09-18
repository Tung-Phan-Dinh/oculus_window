"""Windows-safe model cache setup, called only in model-owning workers."""

import os
import threading


_install_lock = threading.Lock()
_probe_lock = threading.Lock()
_configured = False


def prepare_model_downloads() -> None:
    """Serialize huggingface_hub 0.x's optimistic symlink capability probe.

    It writes True to its cache before trying os.symlink. A concurrent download
    can observe that provisional True and fail with WinError 1314 on ordinary
    Windows accounts. Let the first probe finish before any caller uses it;
    the library's existing copy/move fallback then works without elevation.
    """
    global _configured
    if os.name != "nt":
        return
    with _install_lock:
        if _configured:
            return
        from huggingface_hub import file_download

        original = file_download.are_symlinks_supported

        def serialized_probe(cache_dir=None):
            with _probe_lock:
                return original(cache_dir)

        file_download.are_symlinks_supported = serialized_probe
        _configured = True
