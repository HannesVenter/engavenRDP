"""Command-line entry point."""
from __future__ import annotations

import argparse
import os
import sys

from . import APP_NAME, __version__


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="engavenrdp", description=f"{APP_NAME} — RDP connection manager for Linux")
    p.add_argument("files", nargs="*", help=".erdp or .rdg files to open")
    p.add_argument("--connect", "-c", metavar="SERVERS",
                   help="comma-separated servers (name, host or display name) to connect at start-up")
    p.add_argument("--connect-group", "-g", metavar="PATH", action="append", default=[],
                   help="connect every server in a group, e.g. 'Prod/Web' (repeatable)")
    p.add_argument("--list", metavar="FILE", help="print the tree of FILE and exit")
    p.add_argument("--check", action="store_true", help="show FreeRDP / environment info and exit")
    p.add_argument("--no-restore", action="store_true", help="don't reopen the files from last time")
    p.add_argument("--theme", choices=("system", "light", "dark"))
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def _print_tree(path: str) -> int:
    from .model import storage
    from .model.nodes import ContainerNode, ServerNode, SmartGroupNode

    f = storage.load(path, None)

    def walk(n, depth):
        pad = "  " * depth
        if isinstance(n, ServerNode):
            extra = f"  ({n.display_name})" if n.display_name else ""
            print(f"{pad}- {n.host}{extra}")
        elif isinstance(n, SmartGroupNode):
            print(f"{pad}* {n.name}  [smart: {len(n.members())} members]")
        elif isinstance(n, ContainerNode):
            print(f"{pad}+ {n.name}")
            for c in n.children:
                walk(c, depth + 1)

    walk(f, 0)
    if f.locked:
        print(f"\n(credentials locked: {f.lock_reason})")
    return 0


def _check() -> int:
    from .backend import get_backend, x11
    from .core.appstate import AppState
    from .security.crypto import keyring_store

    st = AppState.load()
    info = get_backend("freerdp", st.freerdp_command or None).info()
    print(f"{APP_NAME} {__version__}")
    print(f"FreeRDP     : {'OK ' + info.version + ' at ' + info.path if info.available else 'MISSING — ' + info.message}")
    print(f"Session type: {os.environ.get('XDG_SESSION_TYPE', 'unknown')}  DISPLAY={os.environ.get('DISPLAY', '')}")
    print(f"X11 helpers : {'OK' if x11.available() else 'unavailable (no DISPLAY or python-xlib)'}")
    print(f"Keyring     : {keyring_store().backend_name}")
    return 0 if info.available else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list:
        return _print_tree(args.list)
    if args.check:
        return _check()

    # Embedding FreeRDP needs X11. On Wayland, run Qt through XWayland.
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and "QT_QPA_PLATFORM" not in os.environ \
            and os.environ.get("DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .core.appstate import AppState
    from .ui import icons, theme
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("engavenrdp")
    app.setDesktopFileName("engavenrdp")
    state = AppState.load()
    if args.theme:
        state.theme = args.theme
    theme.apply(app, state.theme)
    app.setWindowIcon(icons.app_icon())
    if args.no_restore:
        state.open_files = []
    win = MainWindow(state)
    win.show()

    def startup():
        win.restore_files(args.files)
        targets = []
        if args.connect:
            found, missing = win.find_servers(args.connect.split(","))
            targets += found
            if missing:
                QMessageBox.warning(win, "Not found", "These servers were not found in the open files:\n"
                                    + "\n".join(missing))
        for g in args.connect_group:
            grp = win.find_group(g)
            if grp is None:
                QMessageBox.warning(win, "Not found", f"Group “{g}” was not found.")
            else:
                targets += grp.servers()
        if targets:
            win.manager.connect_many(targets)
            win.tree.select_node(targets[0] if len(targets) == 1 else targets[0].parent)

    QTimer.singleShot(0, startup)
    rc = app.exec()
    state.save()
    return rc


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
