"""Best effort: the Testudo shield as the terminal window's icon while the TUI runs (X11 only).

A terminal program can't set its window's icon through escape sequences;
on X11 the icon is the window's `_NET_WM_ICON` property, which any client
may change. This module finds the terminal's window, saves that property,
sets the shield, and puts the saved one back on exit -- through plain
ctypes calls into libX11 (no python-xlib dependency, no subprocess).

Finding the window: `$WINDOWID` when the terminal exports it (xterm,
urxvt, Alacritty, Konsole). VTE terminals (GNOME Terminal, Terminator)
don't, so otherwise the terminal's title is set to a one-off marker and
the window manager's client list is searched for the window showing it.

Skipped silently when it can't work: no `$DISPLAY`, a Wayland session
(its native windows aren't X windows) without `$WINDOWID`, no libX11, no
window found. Every X error is swallowed, since Xlib's default handler
would exit the process. Never used by `--headless` or the text report.
"""
from __future__ import annotations

import atexit
import ctypes
import ctypes.util
import importlib.resources
import logging
import os
import struct
import time
import uuid
import zlib
from collections.abc import Callable, Mapping

_logger = logging.getLogger(__name__)

#: How long the title-marker search waits for the terminal to show the marker.
FIND_TIMEOUT_SECONDS = 0.5
_FIND_POLL_SECONDS = 0.02

_XA_CARDINAL = 6
_PROP_MODE_REPLACE = 0
_ANY_PROPERTY_TYPE = 0
_MAX_PROPERTY_LONGS = 1 << 22


def should_try(environ: Mapping[str, str]) -> bool:
    """Whether an X11 window icon can work here: an X display, and an X window to find on it.

    A Wayland session's native terminals have no X window, even with
    XWayland's `$DISPLAY` set, so there only an explicit `$WINDOWID` counts.
    """
    if not environ.get("DISPLAY"):
        return False
    if environ.get("WAYLAND_DISPLAY") or environ.get("XDG_SESSION_TYPE") == "wayland":
        return bool(environ.get("WINDOWID"))
    return True


def window_id_from_env(environ: Mapping[str, str]) -> int | None:
    """`$WINDOWID` as an X window id, or None when unset or not a number."""
    try:
        return int(environ.get("WINDOWID", ""), 0) or None
    except ValueError:
        return None


def unpack_icon(data: bytes) -> list[int]:
    """The packaged icon file (zlib, little-endian uint32) as `_NET_WM_ICON` cardinals."""
    raw = zlib.decompress(data)
    return list(struct.unpack(f"<{len(raw) // 4}I", raw))


def icon_sizes(cardinals: list[int]) -> list[tuple[int, int]]:
    """The (width, height) of each image in a `_NET_WM_ICON` value; raises ValueError if malformed."""
    sizes, index = [], 0
    while index < len(cardinals):
        if index + 2 > len(cardinals):
            raise ValueError("truncated icon header")
        width, height = cardinals[index], cardinals[index + 1]
        index += 2 + width * height
        if width == 0 or height == 0 or index > len(cardinals):
            raise ValueError("truncated icon pixels")
        sizes.append((width, height))
    return sizes


def _shield_cardinals() -> list[int]:
    return unpack_icon(importlib.resources.files("testudo.tui.assets").joinpath("window_icon.bin").read_bytes())


_ErrorHandler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


class _X11:
    """The handful of libX11 calls needed, on one display connection."""

    def __init__(self, library: str) -> None:
        lib = ctypes.CDLL(library)
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XInternAtom.restype = ctypes.c_ulong
        lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.XGetWindowProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long, ctypes.c_int,
            ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p),
        ]  # fmt: skip
        lib.XChangeProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_int,
        ]  # fmt: skip
        lib.XDeleteProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
        lib.XFree.argtypes = [ctypes.c_void_p]
        lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        lib.XSetErrorHandler.restype = ctypes.c_void_p
        lib.XSetErrorHandler.argtypes = [_ErrorHandler]
        # Kept referenced for as long as the library may call it.
        self._ignore_errors = _ErrorHandler(lambda _display, _event: 0)
        lib.XSetErrorHandler(self._ignore_errors)
        self._lib = lib
        self.display = lib.XOpenDisplay(None)
        if not self.display:
            raise OSError("cannot open the X display")
        self.root = lib.XDefaultRootWindow(self.display)

    def atom(self, name: str) -> int:
        return self._lib.XInternAtom(self.display, name.encode(), 0)

    def get(self, window: int, prop: str) -> tuple[int, int, bytes | list[int]] | None:
        """(type atom, format, value) of `window`'s `prop`; None when it isn't set.

        Format-32 values come back as ints: Xlib hands them over as C longs,
        whatever their width, which `ctypes.c_ulong` matches.
        """
        actual_type, actual_format = ctypes.c_ulong(), ctypes.c_int()
        count, remaining, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
        status = self._lib.XGetWindowProperty(
            self.display, window, self.atom(prop), 0, _MAX_PROPERTY_LONGS, 0, _ANY_PROPERTY_TYPE,
            ctypes.byref(actual_type), ctypes.byref(actual_format), ctypes.byref(count),
            ctypes.byref(remaining), ctypes.byref(data),
        )  # fmt: skip
        try:
            if status != 0 or not data or actual_type.value == 0:
                return None
            if actual_format.value == 32:
                value: bytes | list[int] = list((ctypes.c_ulong * count.value).from_address(data.value))
            elif actual_format.value == 16:
                value = ctypes.string_at(data.value, 2 * count.value)
            else:
                value = ctypes.string_at(data.value, count.value)
            return actual_type.value, actual_format.value, value
        finally:
            if data:
                self._lib.XFree(data)

    def set(self, window: int, prop: str, type_atom: int, fmt: int, value: bytes | list[int]) -> None:
        if fmt == 32:
            buffer = (ctypes.c_ulong * len(value))(*value)
            count = len(value)
        else:
            buffer = ctypes.create_string_buffer(bytes(value), len(value))
            count = len(value) // (fmt // 8)
        self._lib.XChangeProperty(self.display, window, self.atom(prop), type_atom, fmt, _PROP_MODE_REPLACE, buffer, count)
        self._lib.XSync(self.display, 0)

    def delete(self, window: int, prop: str) -> None:
        self._lib.XDeleteProperty(self.display, window, self.atom(prop))
        self._lib.XSync(self.display, 0)

    def client_windows(self) -> list[int]:
        found = self.get(self.root, "_NET_CLIENT_LIST")
        return list(found[2]) if found is not None and found[1] == 32 else []

    def window_name(self, window: int) -> str:
        for prop in ("_NET_WM_NAME", "WM_NAME"):
            found = self.get(window, prop)
            if found is not None and isinstance(found[2], bytes):
                return found[2].decode("utf-8", "replace")
        return ""

    def close(self) -> None:
        if self.display:
            self._lib.XCloseDisplay(self.display)
            self.display = None


def _find_by_title(x11: _X11, show_title: Callable[[str], None]) -> int | None:
    marker = f"testudo-{uuid.uuid4().hex[:12]}"
    show_title(marker)
    deadline = time.monotonic() + FIND_TIMEOUT_SECONDS
    while True:
        for window in x11.client_windows():
            if marker in x11.window_name(window):
                return window
        if time.monotonic() >= deadline:
            return None
        time.sleep(_FIND_POLL_SECONDS)


def set_window_icon(
    show_title: Callable[[str], None], environ: Mapping[str, str] | None = None
) -> Callable[[], None] | None:
    """Set the shield as the terminal window's icon; return the function that restores the old one.

    `show_title` writes a title to the terminal (used to find a window with
    no `$WINDOWID`); the caller sets its real title afterwards. Returns None
    when the icon couldn't be set, which is never an error. The restore
    function is idempotent and also registered with `atexit`.
    """
    environ = os.environ if environ is None else environ
    if not should_try(environ):
        return None
    library = ctypes.util.find_library("X11")
    if library is None:
        return None
    try:
        x11 = _X11(library)
    except OSError:
        return None
    try:
        window = window_id_from_env(environ) or _find_by_title(x11, show_title)
        if window is None:
            x11.close()
            return None
        previous = x11.get(window, "_NET_WM_ICON")
        x11.set(window, "_NET_WM_ICON", _XA_CARDINAL, 32, _shield_cardinals())
    except Exception:  # noqa: BLE001 -- best effort: an icon is never worth a crash
        _logger.debug("could not set the window icon", exc_info=True)
        x11.close()
        return None

    def restore() -> None:
        if x11.display is None:
            return
        try:
            if previous is None:
                x11.delete(window, "_NET_WM_ICON")
            else:
                x11.set(window, "_NET_WM_ICON", *previous)
        except Exception:  # noqa: BLE001
            _logger.debug("could not restore the window icon", exc_info=True)
        finally:
            x11.close()
            atexit.unregister(restore)

    atexit.register(restore)
    return restore
