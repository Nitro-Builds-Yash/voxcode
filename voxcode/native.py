"""Optional Rust acceleration (the `voxcode-native` package, built from native/).

Hot paths call `native()`: it returns the extension module when it is
installed and NATIVE_ACCEL is not "off", otherwise None and the caller uses
its pure-Python implementation. Both produce identical results.
"""

from __future__ import annotations

from types import ModuleType

from voxcode.config.settings import settings

try:
    import voxcode_native as _module
except ImportError:  # not installed: pure Python everywhere
    _module = None


def native() -> ModuleType | None:
    if _module is None or settings.NATIVE_ACCEL.lower() == "off":
        return None
    return _module


def status() -> dict[str, object]:
    return {
        "installed": _module is not None,
        "version": getattr(_module, "__version__", None),
        "active": native() is not None,
    }
