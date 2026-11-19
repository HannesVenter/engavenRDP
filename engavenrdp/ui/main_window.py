"""Main application window: wires model, sessions and widgets together."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton,
                               QSplitter, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..backend import get_backend
from ..core.appstate import AppState
from ..core.connect import build_spec, credentials_for, wants_external
from ..core.manager import SessionManager
from ..core.profiles import GlobalProfiles
from ..core.session import State
from ..model import storage
from ..model.nodes import (ContainerNode, FileNode, GroupNode, Node, ServerNode, SmartGroupNode,
                           TreeError, clone)
from ..model.patterns import PatternError, parse_host_list
from ..model.resolve import resolve
from ..model.secret import Secret
from ..security.crypto import KeyUnavailable, Vault, WrongPassphrase, keyring_store
from . import icons, theme
from .client_area import ClientArea
from .dialogs import (SHORTCUTS_HTML, AddServerDialog, BulkAddDialog, CredentialDialog, PassphraseDialog,
                      PreferencesDialog, ProfilesDialog, PropertiesDialog)
from .tree import VIRTUAL_GROUPS, ServerTree, VirtualGroup

FILE_FILTER = "engavenRDP files (*.erdp);;Windows .rdg files (*.rdg);;All files (*)"


class MainWindow(QMainWindow):
    def __init__(self, state: AppState | None = None) -> None:
        super().__init__()
        self.state = state or AppState.load()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.resize(1400, 860)

        self.backend = get_backend("freerdp", self.state.freerdp_command or None)
        self.backend_info = self.backend.info()
        self.manager = SessionManager(self.backend, self)
        self.manager.connector = lambda s: self.connect_server(s)
        self.manager.connect_delay_ms = self.state.group_connect_delay_ms
        self.manager.retry_interval_s = self.state.reconnect_interval
        self.gprofiles = GlobalProfiles()
        self.files: list[FileNode] = []
        self._clip: tuple[str, list[Node]] | None = None
        self._session_creds: dict[str, tuple[str, str, str]] = {}  # typed this run, by credential source
        self._session_cred_keys: dict[str, str] = {}  # server id -> key in _session_creds it last used

        self._build_ui()
        self._build_actions()
        self._build_menus()

        self.manager.session_state_changed.connect(self._on_session_state)
        self.manager.reconnect_list_changed.connect(self._schedule_rebuild)

        self._save_timer = QTimer(self, singleShot=True, interval=1500)
        self._save_timer.timeout.connect(self._auto_save)
        self._rebuild_timer = QTimer(self, singleShot=True, interval=120)
        self._rebuild_timer.timeout.connect(self._rebuild)

        if self.state.geometry:
            self.restoreGeometry(QByteArray.fromBase64(self.state.geometry.encode()))
        if self.state.window_state:
            self.restoreState(QByteArray.fromBase64(self.state.window_state.encode()))
        if self.state.splitter:
            self._set_splitter_sizes(self.state.splitter)

    # ================================================================== UI
    def _build_ui(self) -> None:
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.sidebar = QWidget()
        lv = QVBoxLayout(self.sidebar)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)
        self.banner = QFrame(objectName="banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(10, 6, 6, 6)
        self.banner_text = QLabel(wordWrap=True)
        self.banner_btn = QPushButton("Unlock…")
        self.banner_btn.clicked.connect(self.unlock_files)
        bl.addWidget(self.banner_text, 1)
        bl.addWidget(self.banner_btn)
        self.banner.hide()
        lv.addWidget(self.banner)
        self.search = QLineEdit(objectName="search")
        self.search.setPlaceholderText("Search servers…   (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda t: self.tree.apply_filter(t))
        lv.addWidget(self.search)
        self.tree = ServerTree()
        self.tree.files = self.files
        self.tree.show_virtual = self.state.show_virtual_groups
        self.tree.hidden_virtual = set(self.state.hidden_virtual_groups)
        self.tree.state_of = self.manager.state
        self.tree.is_favorite = lambda s: self.state.is_favorite(s.file.path_on_disk if s.file else None, s.id)
        self.tree.virtual_sources = {
            "connected": lambda: [s.server for s in self.manager.active_sessions()],
            "favorites": lambda: self._resolve_refs(self.state.favorites),
            "recent": lambda: self._resolve_refs(self.state.recent),
            "reconnect": lambda: list(self.manager.reconnect.values()),
        }
        self.tree.node_selected.connect(self._on_select)
        self.tree.activated_server.connect(self._activate_server)
        self.tree.structure_changed.connect(self.changed)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        lv.addWidget(self.tree, 1)
        self.splitter.addWidget(self.sidebar)

        self.client = ClientArea(self.manager)
        self.client.grid.set_tile_width(self.state.thumbnail_size)
        self.client.grid.size_changed.connect(lambda w: setattr(self.state, "thumbnail_size", w))
        self.client.select_server.connect(self.tree.select_node)
        self.client.connect_requested.connect(lambda s: self.connect_server(s))
        self.client.disconnect_requested.connect(self.disconnect_server)
        self.client.reconnect_requested.connect(self.reconnect_server)
        self.client.trust_cert_requested.connect(self.trust_changed_certificate)
        self.client.properties_requested.connect(lambda n: self.properties(n))
        self.client.connect_group_requested.connect(self.manager.connect_many)
        self.client.disconnect_group_requested.connect(self._disconnect_many)
        self.splitter.addWidget(self.client)
        self._place_sidebar(self.state.server_list_right)
        self._set_splitter_sizes([300, 1100])
        self.setCentralWidget(self.splitter)

        self.status_label = QLabel()
        self.backend_label = QLabel()
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.backend_label)
        self._update_status()
        self._show_welcome()

    def _place_sidebar(self, right: bool) -> None:
        """Put the server list on the left or right of the client area."""
        sizes = self._splitter_sizes()
        self.splitter.insertWidget(1 if right else 0, self.sidebar)
        self.splitter.setStretchFactor(self.splitter.indexOf(self.sidebar), 0)
        self.splitter.setStretchFactor(self.splitter.indexOf(self.client), 1)
        self._set_splitter_sizes(sizes)

    def _splitter_sizes(self) -> list[int]:
        """Splitter sizes as [server list, client area], whichever side the list is on."""
        sizes = self.splitter.sizes()
        return sizes[::-1] if self.splitter.indexOf(self.sidebar) == 1 else sizes

    def _set_splitter_sizes(self, sizes: list[int]) -> None:
        self.splitter.setSizes(list(sizes)[::-1] if self.splitter.indexOf(self.sidebar) == 1 else list(sizes))

    def _toggle_sidebar_side(self, right: bool) -> None:
        self.state.server_list_right = right
        self._place_sidebar(right)
        self.state.save()

    def _set_virtual_group_visible(self, key: str, visible: bool) -> None:
        hidden = set(self.state.hidden_virtual_groups)
        hidden.discard(key) if visible else hidden.add(key)
        self.state.hidden_virtual_groups = [k for k, _ in VIRTUAL_GROUPS if k in hidden]
        self.tree.hidden_virtual = hidden
        self.state.save()
        self._rebuild()

    def _fill_groups_menu(self) -> None:
        self.groups_menu.clear()
        for key, label in VIRTUAL_GROUPS:
            a = self.groups_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(key not in self.state.hidden_virtual_groups)
            a.toggled.connect(lambda on, k=key: self._set_virtual_group_visible(k, on))
        self.groups_menu.setEnabled(self.state.show_virtual_groups)

    def _act(self, text: str, slot, shortcut=None, tip: str = "") -> QAction:
        a = QAction(text, self)
        a.triggered.connect(lambda _=False: slot())
        if shortcut:
            seqs = shortcut if isinstance(shortcut, (list, tuple)) else [shortcut]
            a.setShortcuts([QKeySequence(s) for s in seqs])
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        self.addAction(a)
        return a

    def _build_actions(self) -> None:
        A = self._act
        self.a_new = A("&New file…", self.new_file, "Ctrl+Shift+N")
        self.a_open = A("&Open…", self.open_dialog, "Ctrl+O")
        self.a_save = A("&Save", self.save_current, "Ctrl+S")
        self.a_save_as = A("Save &as…", lambda: self.save_current(as_=True), "Ctrl+Shift+S")
        self.a_save_all = A("Save a&ll", self.save_all)
        self.a_close = A("&Close file", self.close_current_file, "Ctrl+W")
        self.a_rekey = A("Credential &encryption…", self.rekey_current)
        self.a_quit = A("&Quit", self.close, "Ctrl+Q")

        self.a_add_server = A("Add &server…", self.add_server, "Ctrl+N", "Add a server")
        self.a_bulk = A("Add servers in &bulk…", self.bulk_add, "Ctrl+B", "Add many servers using patterns")
        self.a_import_txt = A("&Import servers from text file…", self.import_hosts)
        self.a_add_group = A("Add &group…", self.add_group, "Ctrl+G", "Add a group")
        self.a_add_smart = A("Add s&mart group…", self.add_smart_group, "Ctrl+Shift+G")
        self.a_rename = A("&Rename", self.rename, "F2")
        self.a_delete = A("&Delete", self.delete, "Del")
        self.a_cut = A("Cu&t", lambda: self.clip("cut"), "Ctrl+X")
        self.a_copy = A("&Copy", lambda: self.clip("copy"), "Ctrl+C")
        self.a_paste = A("&Paste", self.paste, "Ctrl+V")
        self.a_dup = A("D&uplicate", self.duplicate, "Ctrl+D")
        self.a_find = A("&Find…", lambda: (self.search.setFocus(), self.search.selectAll()), "Ctrl+F")
        self.a_props = A("P&roperties…", lambda: self.properties(), "Alt+Return", "Properties")

        self.a_connect = A("&Connect", self.connect_selected, "Ctrl+Return", "Connect the selection")
        self.a_connect_as = A("Connect &as…", lambda: self.connect_selected(prompt=True))
        self.a_disconnect = A("&Disconnect", self.disconnect_selected, "Ctrl+Shift+D", "Disconnect the selection")
        self.a_reconnect = A("&Reconnect", self.reconnect_selected, "F5")
        self.a_disconnect_all = A("Disconnect a&ll", self.disconnect_all)
        self.a_retry = A("Add to Reconnect list (retry until online)", self.add_to_reconnect)
        self.a_retry_remove = A("Remove from Reconnect list", self.remove_from_reconnect)
        self.a_fullscreen = A("&Full screen", lambda: self.client.fullscreen_current(), "F11")
        self.a_cad = A("Send Ctrl+Alt+Del", self.send_cad, None, "Send Ctrl+Alt+Del to the session")
        self.a_fav = A("Toggle &favorite", self.toggle_favorite, "Ctrl+K")

        self.a_profiles = A("Credential &profiles…", self.edit_profiles)
        self.a_export = A("&Export group…", self.export_group)
        self.a_import_grp = A("I&mport group…", self.import_group)
        self.a_prefs = A("&Preferences…", self.preferences, "Ctrl+,")
        self.a_expand = A("Expand all", lambda: self.tree.expandAll())
        self.a_collapse = A("Collapse all", lambda: self.tree.collapseAll())
        self.a_sidebar_right = QAction("Server list on &right", self, checkable=True)
        self.a_sidebar_right.setChecked(self.state.server_list_right)
        self.a_sidebar_right.toggled.connect(self._toggle_sidebar_side)
        self.a_shortcuts = A("&Keyboard shortcuts", self.show_shortcuts)
        self.a_about = A("&About", self.about)

        tb = self.addToolBar("Main")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        for a in (self.a_connect, self.a_disconnect, self.a_fullscreen, self.a_cad):
            tb.addAction(a)
        tb.addSeparator()
        for a in (self.a_add_server, self.a_bulk, self.a_add_group, self.a_props):
            tb.addAction(a)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&File")
        for a in (self.a_new, self.a_open, None, self.a_save, self.a_save_as, self.a_save_all, None,
                  self.a_close, self.a_rekey, None, self.a_quit):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Edit")
        for a in (self.a_add_server, self.a_bulk, self.a_import_txt, self.a_add_group, self.a_add_smart, None,
                  self.a_cut, self.a_copy, self.a_paste, self.a_dup, self.a_rename, self.a_delete, None,
                  self.a_find, self.a_props):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Session")
        for a in (self.a_connect, self.a_connect_as, self.a_disconnect, self.a_reconnect, self.a_disconnect_all,
                  None, self.a_retry, self.a_retry_remove, self.a_fav, None, self.a_fullscreen):
            m.addSeparator() if a is None else m.addAction(a)
        self.screens_menu = m.addMenu("Full screen on monitor")
        self.screens_menu.aboutToShow.connect(self._fill_screens)
        from .session_view import special_keys_menu

        m.addMenu(special_keys_menu(self, self._send_keys_current))
        m = mb.addMenu("&View")
        m.addAction(self.a_expand)
        m.addAction(self.a_collapse)
        m.addSeparator()
        self.groups_menu = m.addMenu("Show groups")
        self.groups_menu.aboutToShow.connect(self._fill_groups_menu)
        self._fill_groups_menu()
        m.addAction(self.a_sidebar_right)
        m = mb.addMenu("&Tools")
        for a in (self.a_profiles, self.a_export, self.a_import_grp, None, self.a_prefs):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Help")
        m.addAction(self.a_shortcuts)
        m.addAction(self.a_about)

    def _fill_screens(self) -> None:
        self.screens_menu.clear()
        for i, sc in enumerate(QGuiApplication.screens()):
            g = sc.geometry()
            a = self.screens_menu.addAction(f"{i + 1}: {sc.name()}  ({g.width()}×{g.height()})")
            a.triggered.connect(lambda _=False, s=sc: self.client.fullscreen_current(s))

    # ============================================================ helpers
    def _show_welcome(self) -> None:
        if not self.files:
            html = ("<p>Create a file (<b>File → New</b>) or open one (<b>Ctrl+O</b>) to start.</p>"
                    "<p>Windows <code>.rdg</code> files can be opened directly.</p>")
        else:
            html = "<p>Select a server to see its session, or a group to see live thumbnails.</p>"
        if not self.backend_info.available:
            html += f"<p style='color:#b45309'>⚠ {self.backend_info.message}</p>"
        self.client.show_welcome(html)

    def _all_servers(self) -> list[ServerNode]:
        return [s for f in self.files for s in f.servers()]

    def _resolve_refs(self, refs: list[dict[str, str]]) -> list[ServerNode]:
        by_id = {s.id: s for s in self._all_servers()}
        return [by_id[r["id"]] for r in refs if r["id"] in by_id]

    def _containers(self) -> list[tuple[str, ContainerNode]]:
        out = []
        for f in self.files:
            out.append((f.name, f))
            for n in f.descendants():
                if isinstance(n, ContainerNode) and not isinstance(n, SmartGroupNode):
                    out.append((f"{f.name} / {n.path()}", n))
        return out

    def _target_container(self, for_server: bool) -> ContainerNode | None:
        n = self.tree.current_node()
        if isinstance(n, ServerNode):
            return n.parent
        if isinstance(n, SmartGroupNode):
            return n.parent
        if isinstance(n, ContainerNode):
            return n
        return self.files[0] if self.files else None

    def _real_nodes(self) -> list[Node]:
        return [n for n in self.tree.selected_nodes() if isinstance(n, Node)]

    def changed(self, structure: bool = True) -> None:
        if structure:
            self._schedule_rebuild()
        if self.state.auto_save:
            self._save_timer.start()
        self._update_banner()

    def _schedule_rebuild(self) -> None:
        self._rebuild_timer.start()

    def _rebuild(self) -> None:
        self.tree.rebuild()
        self._update_status()

    def _update_status(self) -> None:
        act = self.manager.active_sessions()
        n_conn = sum(1 for s in act if s.state == State.CONNECTED)
        n_ing = len(act) - n_conn
        parts = [f"{n_conn} connected"]
        if n_ing:
            parts.append(f"{n_ing} connecting")
        if self.manager.reconnect:
            parts.append(f"{len(self.manager.reconnect)} waiting to reconnect")
        parts.append(f"{len(self._all_servers())} servers in {len(self.files)} file(s)")
        self.status_label.setText("   ·   ".join(parts))
        bi = self.backend_info
        self.backend_label.setText(f"FreeRDP {bi.version}" if bi.available else "⚠ FreeRDP not found")

    def _update_banner(self) -> None:
        locked = [f for f in self.files if f.locked]
        if locked:
            names = ", ".join(f"“{f.name}”" for f in locked)
            self.banner_text.setText(f"🔒 Saved passwords in {names} can't be decrypted on this computer. "
                                     f"You'll be asked for passwords when connecting.")
            self.banner.show()
        else:
            self.banner.hide()

    # ===================================================== files
    def _ask_passphrase(self, msg: str) -> str | None:
        return PassphraseDialog.ask(self, msg)

    def new_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "New engavenRDP file", str(Path.home() / "servers.erdp"),
                                              "engavenRDP files (*.erdp)")
        if not path:
            return
        if not path.endswith(".erdp"):
            path += ".erdp"
        f = FileNode(name=Path(path).stem, path_on_disk=path)
        self.files.append(f)
        self._save(f)
        self._remember_open()
        self._rebuild()
        self.tree.select_node(f)

    def open_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Open", str(Path.home()), FILE_FILTER)
        for p in paths:
            self.open_file(p)

    def open_file(self, path: str, quiet: bool = False) -> FileNode | None:
        path = os.path.abspath(os.path.expanduser(path))
        for f in self.files:
            if f.path_on_disk and os.path.abspath(f.path_on_disk) == path:
                self.tree.select_node(f)
                return f
        try:
            f = storage.load(path, self._ask_passphrase)
        except Exception as e:
            if not quiet:
                QMessageBox.critical(self, "Open failed", f"Could not open {path}:\n{e}")
            return None
        self.files.append(f)
        if path.lower().endswith(".rdg"):
            msg = (f"Imported “{f.name}” from a Windows .rdg file ({len(f.servers())} servers).\n\n"
                   "Save it (File → Save as) to keep it in the Linux format.")
            if f.lock_reason:
                msg += "\n\n" + f.lock_reason
            QMessageBox.information(self, "Imported .rdg", msg)
        self._remember_open()
        self._rebuild()
        self._update_banner()
        self._show_welcome() if self.client.current is None else None
        return f

    def _remember_open(self) -> None:
        self.state.open_files = [f.path_on_disk for f in self.files if f.path_on_disk]
        self.state.save()

    def _current_file(self) -> FileNode | None:
        n = self.tree.current_node()
        if isinstance(n, Node):
            return n.file
        return self.files[0] if len(self.files) == 1 else None

    def save_current(self, as_: bool = False) -> None:
        f = self._current_file()
        if f:
            self._save(f, as_)

    def save_all(self) -> None:
        for f in self.files:
            if f.dirty:
                self._save(f)

    def _save(self, f: FileNode, as_: bool = False) -> bool:
        path = f.path_on_disk
        if as_ or not path:
            start = path or str(Path.home() / f"{f.name}.erdp")
            path, _ = QFileDialog.getSaveFileName(self, f"Save “{f.name}”", start, "engavenRDP files (*.erdp)")
            if not path:
                return False
            if not path.endswith(".erdp"):
                path += ".erdp"
        for attempt in range(2):
            try:
                storage.save(f, path)
                break
            except KeyUnavailable:
                if attempt or not self.ensure_vault(f):
                    QMessageBox.warning(self, "Not saved",
                                        f"“{f.name}” contains passwords but no encryption key is set up, "
                                        f"so it was not saved.")
                    return False
            except OSError as e:
                QMessageBox.critical(self, "Save failed", str(e))
                return False
        self._remember_open()
        self.tree.refresh_decorations()
        self._update_banner()
        return True

    def _auto_save(self) -> None:
        for f in self.files:
            if f.dirty and f.path_on_disk:
                self._save(f)

    def ensure_vault(self, f: FileNode) -> bool:
        if f.vault is not None and f.vault.unlocked:
            return True
        if f.locked:
            r = QMessageBox.question(
                self, "Credentials are locked",
                f"“{f.name}” has saved passwords that can't be decrypted here ({f.lock_reason}).\n\n"
                "To save new passwords the file needs a new encryption key. The old, unreadable "
                "passwords will be removed. Continue?")
            if r != QMessageBox.StandardButton.Yes:
                return False
            for s in storage.iter_secrets(f):
                if s.locked:
                    s.cipher = None
            self._drop_empty_secrets(f)
            f.locked = False
            f.lock_reason = ""
        return self._create_vault(f)

    def _drop_empty_secrets(self, f: FileNode) -> None:
        for p in f.profiles:
            if isinstance(p.password, Secret) and not p.password:
                p.password = None
        for n in [f, *f.descendants()]:
            for rec in n.settings.values():
                for k, v in list(rec.items()):
                    if isinstance(v, Secret) and not v:
                        rec[k] = None

    def _create_vault(self, f: FileNode, choose: bool = False) -> bool:
        use_keyring = keyring_store().available
        if choose:
            items = (["Desktop keyring (recommended — no passphrase to type)"] if use_keyring else []) + \
                    ["Passphrase (portable between computers)"]
            item, ok = QInputDialog.getItem(self, "Credential encryption",
                                            f"How should passwords in “{f.name}” be protected?", items, 0, False)
            if not ok:
                return False
            use_keyring = item.startswith("Desktop")
        if use_keyring:
            try:
                f.vault = Vault.create_keyring()
                f.encryption = f.vault.header
                return True
            except KeyUnavailable as e:
                QMessageBox.warning(self, "Keyring", f"Could not use the desktop keyring ({e}). "
                                                     "Choose a passphrase instead.")
        pw = PassphraseDialog.ask(self, f"Choose a passphrase to protect the passwords in “{f.name}”.",
                                  confirm=True, title="New passphrase")
        if not pw:
            return False
        f.vault = Vault.create_passphrase(pw)
        f.encryption = f.vault.header
        return True

    def rekey_current(self) -> None:
        f = self._current_file()
        if not f:
            return
        if f.locked and not self.ensure_vault(f):
            return
        old = f.vault
        if not self._create_vault(f, choose=True):
            f.vault = old
            return
        for s in storage.iter_secrets(f):
            s.cipher = None  # force re-encryption with the new key
        f.mark_dirty()
        self._save(f)

    def unlock_files(self) -> None:
        for f in [f for f in self.files if f.locked]:
            while True:
                try:
                    pw = None
                    if f.encryption.get("mode") == "passphrase":
                        pw = self._ask_passphrase(f"Passphrase for “{f.name}”")
                        if pw is None:
                            break
                    storage.unlock(f, Vault.open(f.encryption, pw))
                    break
                except WrongPassphrase:
                    continue
                except KeyUnavailable as e:
                    r = QMessageBox.question(self, "Still locked",
                                             f"“{f.name}”: {e}\n\nSet up a new key for this file now? "
                                             "(the unreadable passwords will be removed)")
                    if r == QMessageBox.StandardButton.Yes:
                        self.ensure_vault(f)
                        f.mark_dirty()
                        self.changed()
                    break
        self.tree.refresh_decorations()
        self._update_banner()

    def close_current_file(self) -> None:
        f = self._current_file()
        if not f:
            return
        if not self._confirm_close_files([f]):
            return
        for s in f.servers():
            self.manager.forget(s)
        self.files.remove(f)
        self._remember_open()
        self._rebuild()
        self._show_welcome()

    def _confirm_close_files(self, files: list[FileNode]) -> bool:
        dirty = [f for f in files if f.dirty]
        if not dirty:
            return True
        r = QMessageBox.question(self, "Unsaved changes",
                                 "Save changes to " + ", ".join(f"“{f.name}”" for f in dirty) + "?",
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                 QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Cancel:
            return False
        if r == QMessageBox.StandardButton.Save:
            return all(self._save(f) for f in dirty)
        return True

    # ===================================================== selection
    def _on_select(self, obj) -> None:
        if isinstance(obj, ServerNode):
            self.client.show_server(obj)
        elif isinstance(obj, (ContainerNode, VirtualGroup)):
            self.client.show_group(obj)
        else:
            self._show_welcome()

    def _activate_server(self, server: ServerNode) -> None:
        if self.manager.state(server).active:
            self.client.show_server(server)
            s = self.manager.session(server)
            if s:
                s.focus()
        else:
            self.connect_server(server)

    # ===================================================== sessions
    def _can_embed(self) -> bool:
        return QGuiApplication.platformName() == "xcb"

    def connect_selected(self, prompt: bool = False) -> None:
        servers = self.tree.selected_servers()
        if len(servers) == 1:
            self.connect_server(servers[0], prompt=prompt)
        elif servers:
            if prompt:
                for s in servers:
                    self.connect_server(s, prompt=True)
            else:
                self.manager.connect_many(servers)

    def connect_server(self, server: ServerNode, prompt: bool = False, trusted_cert=None) -> bool:
        if not self.backend_info.available:
            QMessageBox.warning(self, "FreeRDP not found", self.backend_info.message)
            return False
        if self.manager.state(server).active:
            return True
        creds = credentials_for(server, self.gprofiles.profiles)
        r = resolve(server)
        src = r.sources.get("logon")
        key = f"profile:{creds.profile}" if creds.profile else f"node:{src.id if src else 'defaults'}"
        if prompt or creds.needs_password or creds.missing_profile:
            self._session_cred_keys[server.id] = key
            cached = None if prompt else self._session_creds.get(key)
            if cached:
                creds.username, creds.domain = cached[0], cached[1]
                creds.password = Secret(plain=cached[2])
            else:
                msg = ""
                if creds.missing_profile:
                    msg = f"Credential profile “{creds.profile}” was not found."
                elif creds.password and creds.password.locked:
                    msg = "The saved password can't be decrypted on this computer."
                d = CredentialDialog(server, creds.username, creds.domain, msg, parent=self)
                if d.exec() != QDialog.DialogCode.Accepted:
                    return False
                creds.username, creds.domain = d.user.text().strip(), d.domain.text().strip()
                pw = d.password.text()
                creds.password = Secret(plain=pw) if pw else None
                self._session_creds[key] = (creds.username, creds.domain, pw)
                if d.save.isChecked():
                    self._remember_credentials(server, creds.profile if not creds.missing_profile else "",
                                               creds.username, creds.domain, pw)
        session = self.manager.session(server, create=True)
        sw = self.client.prepare_session(session)
        external = wants_external(server) or not self._can_embed()
        if external:
            parent_id = None
        else:
            parent_id = sw.host.x_id()
        w, h = self.client.remote_size_hint()
        spec = build_spec(server, creds, self.gprofiles.profiles, parent_window=parent_id, width=w, height=h,
                          trusted_cert=trusted_cert)
        session.start(spec)
        self.state.add_recent(server.file.path_on_disk if server.file else None, server.id)
        self._schedule_rebuild()
        return True

    def trust_changed_certificate(self, server: ServerNode) -> None:
        """Forget the saved certificate the server no longer presents, then connect again.

        The connection that follows saves the certificate it is shown; the session
        drops it again if that is not the one the user has just accepted.
        """
        session = self.manager.session(server)
        change = session.cert_change if session else None
        if change is None or session.state.active:
            return
        if not self.backend.forget_certificate(change):
            QMessageBox.warning(
                self, "Certificate",
                f"The saved certificate for {change.host}:{change.port} could not be found or removed, "
                "so FreeRDP will keep refusing the new one. Remove it by hand from FreeRDP's "
                "configuration folder (usually ~/.config/freerdp).")
            return
        self.connect_server(server, trusted_cert=change)

    def _remember_credentials(self, server: ServerNode, profile: str, user: str, domain: str, pw: str) -> None:
        from ..model.resolve import find_profile

        if profile:
            p = find_profile(server, profile, self.gprofiles.profiles)
            if p is not None:
                p.username, p.domain, p.password = user, domain, Secret(plain=pw) if pw else None
                if p in self.gprofiles.profiles:
                    self._save_global_profiles()
                else:
                    server.mark_dirty()
                    self.changed(False)
                return
        src = resolve(server).sources.get("logon") or server
        vals = dict(resolve(src)["logon"]) if not src.inherits("logon") else {}
        vals.update(username=user, domain=domain, password=Secret(plain=pw) if pw else None, profile="")
        src.set_record("logon", vals)
        src.mark_dirty()
        self.changed(False)

    def disconnect_server(self, server: ServerNode) -> None:
        self.manager.disconnect(server)

    def _disconnect_many(self, servers: list[ServerNode]) -> None:
        active = [s for s in servers if self.manager.state(s).active]
        if len(active) > 1 and self.state.confirm_disconnect:
            if QMessageBox.question(self, "Disconnect", f"Disconnect {len(active)} sessions?") \
                    != QMessageBox.StandardButton.Yes:
                return
        for s in servers:
            self.manager.disconnect(s)

    def disconnect_selected(self) -> None:
        self._disconnect_many(self.tree.selected_servers())

    def disconnect_all(self) -> None:
        self._disconnect_many([s.server for s in self.manager.active_sessions()])

    def reconnect_server(self, server: ServerNode) -> None:
        s = self.manager.session(server)
        if s and s.state.active:
            s.state_changed.connect(self._reconnect_after_stop)
            s.stop()
        else:
            self.connect_server(server)

    def _reconnect_after_stop(self, st) -> None:
        sess = self.sender()
        if st.active:
            return
        try:
            sess.state_changed.disconnect(self._reconnect_after_stop)
        except (RuntimeError, TypeError):
            pass
        QTimer.singleShot(300, lambda: self.connect_server(sess.server))

    def reconnect_selected(self) -> None:
        for s in self.tree.selected_servers():
            self.reconnect_server(s)

    def add_to_reconnect(self) -> None:
        for s in self.tree.selected_servers():
            self.manager.add_reconnect(s)
        self._update_status()

    def remove_from_reconnect(self) -> None:
        for s in self.tree.selected_servers():
            self.manager.remove_reconnect(s)
        self._update_status()

    def _send_keys_current(self, keys: list[str]) -> None:
        n = self.client.current
        if isinstance(n, ServerNode):
            self.client.session_widget(n).send_keys(keys)

    def send_cad(self) -> None:
        self._send_keys_current(["Control_L", "Alt_L", "Delete"])

    def _on_session_state(self, session) -> None:
        self.tree.update_server(session.server)
        self._schedule_rebuild()
        if session.state == State.ERROR and session.auth_failed:
            # Don't keep reusing typed credentials the server has just rejected.
            key = self._session_cred_keys.pop(session.server.id, None)
            if key:
                self._session_creds.pop(key, None)
        if session.state == State.ERROR and self.client.current is not session.server:
            self.statusBar().showMessage(f"{session.server.label}: {session.last_error}", 8000)

    def toggle_favorite(self) -> None:
        for s in [n for n in self._real_nodes() if isinstance(n, ServerNode)]:
            fp = s.file.path_on_disk if s.file else None
            self.state.set_favorite(fp, s.id, not self.state.is_favorite(fp, s.id))
        self.state.save()
        self._rebuild()

    # ===================================================== editing
    def _add_node(self, parent: ContainerNode, node: Node) -> bool:
        try:
            parent.add(node)
        except TreeError as e:
            QMessageBox.warning(self, "Cannot add", str(e))
            return False
        parent.expanded = True
        self.changed()
        self._rebuild()
        self.tree.select_node(node)
        return True

    def _need_file(self) -> bool:
        if not self.files:
            QMessageBox.information(self, APP_NAME, "Create or open a file first (File → New).")
            return False
        return True

    def add_server(self) -> None:
        if not self._need_file():
            return
        d = AddServerDialog(self._containers(), self._target_container(True), self)
        if d.exec():
            server, parent = d.result_server()
            self._add_node(parent, server)

    def bulk_add(self, text: str = "") -> None:
        if not self._need_file():
            return
        d = BulkAddDialog(self._containers(), self._target_container(True), text, self)
        if not d.exec():
            return
        target = d.target()
        existing = {s.host.lower() for s in target.servers(recursive=False)}
        added = 0
        try:
            for h in d.hosts:
                if d.skip.isChecked() and h.lower() in existing:
                    continue
                target.add(ServerNode(name=h, host=h))
                added += 1
        except TreeError as e:
            QMessageBox.warning(self, "Cannot add", str(e))
        target.expanded = True
        self.changed()
        self.statusBar().showMessage(f"Added {added} server(s) to {target.name}", 5000)

    def import_hosts(self) -> None:
        if not self._need_file():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import servers", str(Path.home()),
                                              "Text files (*.txt *.csv *.list);;All files (*)")
        if not path:
            return
        try:
            entries = parse_host_list(Path(path).read_text(encoding="utf-8", errors="replace"))
        except (OSError, PatternError) as e:
            QMessageBox.warning(self, "Import", str(e))
            return
        target = self._target_container(True)
        if target is None or not target.can_accept(ServerNode(name="x")):
            name, ok = QInputDialog.getText(self, "Import", "Target group contains groups. "
                                            "Create a new group for the imported servers:", text=Path(path).stem)
            if not ok or not name:
                return
            g = GroupNode(name=name)
            try:
                (target or self.files[0]).add(g)
            except TreeError as e:
                QMessageBox.warning(self, "Import", str(e))
                return
            target = g
        if QMessageBox.question(self, "Import", f"Add {len(entries)} server(s) to “{target.name}”?") \
                != QMessageBox.StandardButton.Yes:
            return
        existing = {s.host.lower() for s in target.servers(recursive=False)}
        for host, display in entries:
            if host.lower() not in existing:
                target.add(ServerNode(name=host, host=host, display_name=display))
                existing.add(host.lower())
        self.changed()

    def add_group(self) -> None:
        if not self._need_file():
            return
        parent = self._target_container(False)
        name, ok = QInputDialog.getText(self, "Add group", f"Name of the new group in “{parent.name}”:")
        if ok and name.strip():
            self._add_node(parent, GroupNode(name=name.strip()))

    def add_smart_group(self) -> None:
        if not self._need_file():
            return
        parent = self._target_container(False)
        sg = SmartGroupNode(name="Smart group")
        d = PropertiesDialog(sg, [], self)
        # attach temporarily so preview of rules can resolve the file
        if d.exec():
            self._add_node(parent, sg)

    def rename(self) -> None:
        n = self.tree.current_node()
        if not isinstance(n, Node):
            return
        if isinstance(n, ServerNode):
            text, ok = QInputDialog.getText(self, "Rename", "Display name (empty = use server name):",
                                            text=n.display_name or n.name)
            if ok:
                n.display_name = "" if text.strip() == n.name else text.strip()
        else:
            text, ok = QInputDialog.getText(self, "Rename", "Name:", text=n.name)
            if ok and text.strip():
                n.name = text.strip()
        n.mark_dirty()
        self.changed()

    def delete(self) -> None:
        from .tree import ROLE_MEMBER, ROLE_NODE

        nodes = []
        for it in self.tree.selectedItems():
            n = it.data(0, ROLE_NODE)
            if isinstance(n, Node) and not isinstance(n, FileNode) and not it.data(0, ROLE_MEMBER) \
                    and n not in nodes:
                nodes.append(n)
        if not nodes:
            if isinstance(self.tree.current_node(), FileNode):
                self.close_current_file()
            return
        count = sum(1 + (len(n.servers()) if isinstance(n, ContainerNode) else 0) for n in nodes)
        what = nodes[0].label if len(nodes) == 1 else f"{len(nodes)} items"
        if QMessageBox.question(self, "Delete", f"Delete {what}" + (f" ({count} objects)" if count > 1 else "") +
                                "? This cannot be undone.") != QMessageBox.StandardButton.Yes:
            return
        for n in nodes:
            for s in ([n] if isinstance(n, ServerNode) else n.servers() if isinstance(n, ContainerNode) else []):
                self.manager.forget(s)
                self.manager.remove_reconnect(s)
            if n.parent is not None:
                n.parent.remove(n)
        self.changed()
        self._show_welcome()

    def clip(self, mode: str) -> None:
        nodes = [n for n in self._real_nodes() if not isinstance(n, FileNode)]
        if nodes:
            self._clip = (mode, nodes)
            self.statusBar().showMessage(f"{'Cut' if mode == 'cut' else 'Copied'} {len(nodes)} item(s)", 3000)

    def paste(self) -> None:
        if not self._clip:
            return
        mode, nodes = self._clip
        target = self._target_container(True)
        cur = self.tree.current_node()
        if isinstance(cur, ContainerNode) and not isinstance(cur, SmartGroupNode):
            target = cur
        if target is None:
            return
        try:
            for n in nodes:
                if mode == "cut":
                    if n.parent is not None:
                        n.parent.mark_dirty()
                    target.add(n)
                else:
                    target.add(clone(n))
        except TreeError as e:
            QMessageBox.warning(self, "Paste", str(e))
        if mode == "cut":
            self._clip = None
        self.changed()

    def duplicate(self) -> None:
        for n in [n for n in self._real_nodes() if not isinstance(n, FileNode) and n.parent is not None]:
            dup = clone(n)
            dup.name = n.name if isinstance(n, ServerNode) else f"{n.name} (copy)"
            n.parent.add(dup, n.parent.children.index(n) + 1)
        self.changed()

    def _profile_names(self, node: Node) -> list[str]:
        names = [p.name for p in (node.file.profiles if node.file else [])]
        names += [p.name for p in self.gprofiles.profiles if p.name not in names]
        return names

    def properties(self, node: Node | None = None) -> None:
        node = node or self.tree.current_node()
        if not isinstance(node, Node):
            return
        d = PropertiesDialog(node, self._profile_names(node), self)
        if d.exec():
            self.changed()
            if isinstance(node, ServerNode):
                w = self.client.widgets.get(node.id)
                if w:
                    w.refresh()
            elif self.client.current is node:
                self.client.refresh_current()

    # ===================================================== tools
    def edit_profiles(self) -> None:
        f = self._current_file()
        scopes = []
        if f:
            scopes.append((f"This file ({f.name})", f.profiles))
        scopes.append(("Global (all files)", self.gprofiles.profiles))
        d = ProfilesDialog(scopes, self)
        d.exec()
        for i in d.changed:
            if f and i == 0:
                f.mark_dirty()
                self.changed(False)
            else:
                self._save_global_profiles()

    def _save_global_profiles(self) -> bool:
        gp = self.gprofiles
        if not gp.ensure_vault(self._ask_passphrase):
            if not gp.locked:
                QMessageBox.warning(self, "Profiles", "The global credential profiles were not saved: "
                                                      "no encryption key is set up.")
                return False
            r = QMessageBox.question(
                self, "Credentials are locked",
                f"Your global credential profiles have saved passwords that can't be decrypted here "
                f"({gp.message}).\n\nTo save changes they need a new encryption key. The old, unreadable "
                "passwords will be removed. Continue?")
            if r != QMessageBox.StandardButton.Yes:
                return False
            gp.discard_locked()
            if not gp.ensure_vault(self._ask_passphrase):
                return False
        try:
            gp.save()
        except (KeyUnavailable, OSError) as e:
            QMessageBox.warning(self, "Profiles", str(e))
            return False
        return True

    def export_group(self) -> None:
        n = self.tree.current_node()
        if not isinstance(n, ContainerNode) or isinstance(n, (FileNode, SmartGroupNode)):
            QMessageBox.information(self, "Export", "Select a group to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export group", str(Path.home() / f"{n.name}.erdp"),
                                              "engavenRDP files (*.erdp)")
        if not path:
            return
        f = storage.export_group(n)
        f.path_on_disk = path
        if storage.has_plain_secrets(f):
            r = QMessageBox.question(self, "Export", "Include saved passwords in the export? "
                                     "(They will be protected with a passphrase you choose.)")
            if r == QMessageBox.StandardButton.Yes:
                pw = PassphraseDialog.ask(self, "Passphrase for the exported file", confirm=True)
                if not pw:
                    return
                f.vault = Vault.create_passphrase(pw)
            else:
                for s in storage.iter_secrets(f):
                    s.plain = s.cipher = None
                self._drop_empty_secrets(f)
        try:
            storage.save(f, path)
            self.statusBar().showMessage(f"Exported {n.name} to {path}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))

    def import_group(self) -> None:
        if not self._need_file():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import group", str(Path.home()), FILE_FILTER)
        if not path:
            return
        try:
            src = storage.load(path, self._ask_passphrase)
        except Exception as e:
            QMessageBox.critical(self, "Import failed", str(e))
            return
        target = self._target_container(False)
        if target is None:
            return
        g = GroupNode(name=src.name, comment=src.comment, settings=src.settings)
        for c in list(src.children):
            src.remove(c)
            g.children.append(c)
            c.parent = g
        for p in src.profiles:
            if target.file and not target.file.profile(p.name):
                target.file.profiles.append(p)
        self._add_node(target, g)

    def preferences(self) -> None:
        d = PreferencesDialog(self.state, self.backend_info, self)
        if not d.exec():
            return
        old_cmd = self.backend.__dict__.get("_override")
        d.apply()
        self.state.save()
        theme.apply(QApplication.instance(), self.state.theme)
        icons.server_icon.cache_clear()
        self.manager.connect_delay_ms = self.state.group_connect_delay_ms
        self.manager.retry_interval_s = self.state.reconnect_interval
        self.tree.show_virtual = self.state.show_virtual_groups
        new = get_backend("freerdp", self.state.freerdp_command or None)
        if getattr(new, "_override", None) != old_cmd:
            self.backend = new
            self.backend_info = new.info()
            self.manager.backend = new
            for s in self.manager.sessions.values():  # servers already used this run
                s.backend = new
        self._rebuild()

    def show_shortcuts(self) -> None:
        QMessageBox.information(self, "Keyboard shortcuts", SHORTCUTS_HTML)

    def about(self) -> None:
        bi = self.backend_info
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<h3>{APP_NAME} {__version__}</h3>"
                          "<p>RDP connection manager for Linux.</p>"
                          f"<p>RDP engine: {'FreeRDP ' + bi.version + ' (' + bi.path + ')' if bi.available else bi.message}</p>"
                          f"<p>Qt platform: {QGuiApplication.platformName()} "
                          f"({'sessions are embedded' if self._can_embed() else 'sessions open in their own windows'})</p>"
                          f"<p>Keyring: {keyring_store().backend_name}</p>")

    # ===================================================== context menu
    def _context_menu(self, pos) -> None:
        obj = self.tree.current_node()
        m = QMenu(self)
        if isinstance(obj, ServerNode):
            active = self.manager.state(obj).active
            m.addAction(self.a_disconnect if active else self.a_connect)
            m.addAction(self.a_connect_as)
            m.addAction(self.a_reconnect)
            if active:
                m.addAction(self.a_fullscreen)
                from .session_view import special_keys_menu

                m.addMenu(special_keys_menu(m, self._send_keys_current))
            m.addSeparator()
            m.addAction(self.a_retry_remove if obj.id in self.manager.reconnect else self.a_retry)
            fav = self.state.is_favorite(obj.file.path_on_disk if obj.file else None, obj.id)
            self.a_fav.setText("Remove from favorites" if fav else "Add to favorites")
            m.addAction(self.a_fav)
            m.addSeparator()
            for a in (self.a_cut, self.a_copy, self.a_dup, self.a_rename, self.a_delete):
                m.addAction(a)
            m.addSeparator()
            m.addAction(self.a_props)
        elif isinstance(obj, VirtualGroup):
            m.addAction("Connect all", lambda: self.manager.connect_many(obj.servers()))
            m.addAction("Disconnect all", lambda: self._disconnect_many(obj.servers()))
            if obj.key == "recent":
                m.addAction("Clear", lambda: (self.state.recent.clear(), self.state.save(), self._rebuild()))
            if obj.key == "reconnect":
                m.addAction("Stop retrying all", lambda: [self.manager.remove_reconnect(s) for s in obj.servers()])
            m.addSeparator()
            m.addAction("Hide this group", lambda: self._set_virtual_group_visible(obj.key, False))
        elif isinstance(obj, ContainerNode):
            m.addAction("Connect group", lambda: self.manager.connect_many(
                obj.members() if isinstance(obj, SmartGroupNode) else obj.servers()))
            m.addAction("Disconnect group", lambda: self._disconnect_many(
                obj.members() if isinstance(obj, SmartGroupNode) else obj.servers()))
            m.addAction(self.a_retry)
            m.addSeparator()
            if not isinstance(obj, SmartGroupNode):
                kind = obj.child_kind()
                if kind != "group":
                    m.addAction(self.a_add_server)
                    m.addAction(self.a_bulk)
                    m.addAction(self.a_import_txt)
                if kind != "server":
                    m.addAction(self.a_add_group)
                    m.addAction(self.a_add_smart)
                m.addSeparator()
                if self._clip:
                    m.addAction(self.a_paste)
            if isinstance(obj, FileNode):
                for a in (self.a_save, self.a_save_as, self.a_rekey, self.a_profiles, self.a_close):
                    m.addAction(a)
            else:
                for a in (self.a_cut, self.a_copy, self.a_dup, self.a_rename, self.a_delete):
                    m.addAction(a)
                if not isinstance(obj, SmartGroupNode):
                    m.addAction(self.a_export)
            m.addSeparator()
            m.addAction(self.a_props)
        else:
            m.addAction(self.a_new)
            m.addAction(self.a_open)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    # ===================================================== lifecycle
    def restore_files(self, extra: list[str]) -> None:
        self.gprofiles.load(self._ask_passphrase)
        for p in self.state.open_files:
            if os.path.exists(p):
                self.open_file(p, quiet=True)
        for p in extra:
            self.open_file(p)
        self._rebuild()
        self._update_banner()
        self._show_welcome()

    def find_servers(self, names: list[str]) -> tuple[list[ServerNode], list[str]]:
        found, missing = [], []
        servers = self._all_servers()
        for name in names:
            n = name.strip().lower()
            if not n:
                continue
            match = [s for s in servers if n in (s.name.lower(), s.host.lower(), s.display_name.lower())]
            if match:
                found.append(match[0])
            else:
                missing.append(name)
        return found, missing

    def find_group(self, path: str) -> ContainerNode | None:
        for f in self.files:
            n = f.find_path(path)
            if isinstance(n, ContainerNode):
                return n
            if f.name.lower() == path.split("/")[0].lower():
                n = f.find_path("/".join(path.split("/")[1:]))
                if isinstance(n, ContainerNode):
                    return n
        return None

    def closeEvent(self, e) -> None:
        if not self._confirm_close_files(self.files):
            e.ignore()
            return
        active = self.manager.active_sessions()
        if active and QMessageBox.question(self, "Quit", f"{len(active)} session(s) are still open. "
                                           "Disconnect and quit?") != QMessageBox.StandardButton.Yes:
            e.ignore()
            return
        for fs in list(self.client.fullscreen.values()):
            fs.close()
        self.manager.disconnect_all()
        self.state.geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.state.window_state = bytes(self.saveState().toBase64()).decode()
        self.state.splitter = self._splitter_sizes()
        self._remember_open()
        e.accept()
