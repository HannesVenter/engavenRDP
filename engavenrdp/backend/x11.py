"""Small X11 helpers for the embedded FreeRDP window (python-xlib).

Everything degrades gracefully when python-xlib or an X server is missing.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

try:  # pragma: no cover - depends on environment
    from Xlib import X, XK, display as xdisplay
    from Xlib.protocol import event as xevent

    HAVE_XLIB = True
except Exception:  # pragma: no cover
    HAVE_XLIB = False

_display = None


def _dpy():
    global _display
    if not HAVE_XLIB or not os.environ.get("DISPLAY"):
        return None
    if _display is None:
        try:
            _display = xdisplay.Display()
        except Exception as e:  # pragma: no cover
            log.warning("Cannot open X display: %s", e)
            return None
    return _display


def available() -> bool:
    return _dpy() is not None


def child_windows(parent_id: int) -> list[int]:
    d = _dpy()
    if d is None or not parent_id:
        return []
    try:
        w = d.create_resource_object("window", parent_id)
        return [c.id for c in w.query_tree().children]
    except Exception:
        return []


def resize(window_id: int, width: int, height: int) -> None:
    d = _dpy()
    if d is None:
        return
    try:
        w = d.create_resource_object("window", window_id)
        w.configure(x=0, y=0, width=max(1, width), height=max(1, height))
        d.flush()
    except Exception as e:
        log.debug("resize failed: %s", e)


def focus(window_id: int) -> None:
    d = _dpy()
    if d is None:
        return
    try:
        w = d.create_resource_object("window", window_id)
        w.set_input_focus(X.RevertToParent, X.CurrentTime)
        d.flush()
    except Exception as e:
        log.debug("focus failed: %s", e)


# Special key combinations offered in the UI: label -> keysym names
SPECIAL_KEYS: dict[str, list[str]] = {
    "Ctrl+Alt+Del": ["Control_L", "Alt_L", "Delete"],
    "Ctrl+Shift+Esc (Task Manager)": ["Control_L", "Shift_L", "Escape"],
    "Alt+Tab": ["Alt_L", "Tab"],
    "Alt+F4": ["Alt_L", "F4"],
    "Windows key": ["Super_L"],
    "Win+L (lock)": ["Super_L", "l"],
    "Win+R (run)": ["Super_L", "r"],
    "Win+E (explorer)": ["Super_L", "e"],
    "Print Screen": ["Print"],
    "Ctrl+Esc": ["Control_L", "Escape"],
}


def send_keys(window_id: int, keysyms: list[str]) -> bool:
    """Send a key chord to ``window_id`` as synthetic X events (press in order,
    release in reverse). Returns False if X is unavailable."""
    d = _dpy()
    if d is None or not window_id:
        return False
    try:
        win = d.create_resource_object("window", window_id)
        root = d.screen().root
        state = 0
        mods = {"Control_L": X.ControlMask, "Shift_L": X.ShiftMask, "Alt_L": X.Mod1Mask,
                "Super_L": X.Mod4Mask}
        codes = []
        for name in keysyms:
            sym = XK.string_to_keysym(name)
            code = d.keysym_to_keycode(sym)
            if not code:
                return False
            codes.append((name, code))

        def ev(cls, code, st):
            return cls(time=X.CurrentTime, root=root, window=win, same_screen=1, child=X.NONE,
                       root_x=0, root_y=0, event_x=0, event_y=0, state=st, detail=code)

        for name, code in codes:
            win.send_event(ev(xevent.KeyPress, code, state), propagate=True, event_mask=X.KeyPressMask)
            state |= mods.get(name, 0)
        for name, code in reversed(codes):
            state &= ~mods.get(name, 0)
            win.send_event(ev(xevent.KeyRelease, code, state), propagate=True, event_mask=X.KeyReleaseMask)
        d.flush()
        return True
    except Exception as e:
        log.warning("send_keys failed: %s", e)
        return False


def set_input_passthrough(window_id: int, passthrough: bool) -> None:
    """Make ``window_id`` click-through (empty input shape) so mouse events reach
    our own parent widget — used for live thumbnails."""
    d = _dpy()
    if d is None or not window_id:
        return
    try:
        from Xlib.ext import shape

        if not d.has_extension("SHAPE"):
            return
        w = d.create_resource_object("window", window_id)
        if passthrough:
            w.shape_rectangles(shape.SO.Set, shape.SK.Input, X.Unsorted, 0, 0, [])
        else:
            w.shape_mask(shape.SO.Set, shape.SK.Input, 0, 0, X.NONE)
        d.flush()
    except Exception as e:
        log.debug("input shape failed: %s", e)


def geometry(window_id: int) -> tuple[int, int] | None:
    d = _dpy()
    if d is None or not window_id:
        return None
    try:
        g = d.create_resource_object("window", window_id).get_geometry()
        return g.width, g.height
    except Exception:
        return None
