"""Right-hand client area: welcome page, per-server session pages, thumbnail grid."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QStackedWidget, QVBoxLayout, QWidget

from ..core.manager import SessionManager
from ..core.session import Session, State
from ..model.nodes import ContainerNode, Node, ServerNode, SmartGroupNode
from .session_view import FullScreenWindow, SessionWidget
from .thumbnails import ThumbnailGrid
from .tree import VirtualGroup


class ClientArea(QStackedWidget):
    select_server = Signal(object)      # ask the main window to select this server in the tree
    connect_requested = Signal(object)
    disconnect_requested = Signal(object)
    reconnect_requested = Signal(object)
    trust_cert_requested = Signal(object)
    properties_requested = Signal(object)
    connect_group_requested = Signal(list)
    disconnect_group_requested = Signal(list)

    def __init__(self, manager: SessionManager, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.widgets: dict[str, SessionWidget] = {}
        self.fullscreen: dict[str, FullScreenWindow] = {}
        self.current: object = None
        self._group_servers: list[ServerNode] = []

        self.welcome = QWidget()
        wl = QVBoxLayout(self.welcome)
        title = QLabel("engavenRDP", objectName="title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = title.font()
        f.setPointSize(f.pointSize() + 10)
        title.setFont(f)
        self.welcome_text = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True,
                                   objectName="subtitle")
        self.welcome_text.setTextFormat(Qt.TextFormat.RichText)
        wl.addStretch(1)
        wl.addWidget(title)
        wl.addWidget(self.welcome_text)
        wl.addStretch(2)
        self.addWidget(self.welcome)

        self.grid = ThumbnailGrid()
        self.grid.server_clicked.connect(self.select_server.emit)
        self.grid.server_activated.connect(self._activate)
        self.grid.server_connect_toggle.connect(self._toggle)
        self.grid.connect_all.connect(lambda: self.connect_group_requested.emit(list(self._group_servers)))
        self.grid.disconnect_all.connect(lambda: self.disconnect_group_requested.emit(list(self._group_servers)))
        self.addWidget(self.grid)

        manager.session_state_changed.connect(self.on_session_state)

    # ----------------------------------------------------------- widgets
    def session_widget(self, server: ServerNode) -> SessionWidget:
        w = self.widgets.get(server.id)
        if w is None:
            w = SessionWidget(server)
            w.request_connect.connect(self.connect_requested.emit)
            w.request_disconnect.connect(self.disconnect_requested.emit)
            w.request_reconnect.connect(self.reconnect_requested.emit)
            w.request_trust_cert.connect(self.trust_cert_requested.emit)
            w.request_fullscreen.connect(self.toggle_fullscreen)
            w.request_properties.connect(self.properties_requested.emit)
            self.addWidget(w)
            self.widgets[server.id] = w
            s = self.manager.session(server)
            if s is not None:
                w.attach(s)
        return w

    def prepare_session(self, session: Session) -> SessionWidget:
        """Make sure the session has a host window before FreeRDP starts."""
        w = self.session_widget(session.server)
        w.attach(session)
        # if we are currently showing a grid that includes this server, put the host there
        if self.currentWidget() is self.grid and self.grid.tile(session.server.id):
            self._grid_adopt(session.server)
        return w

    def remote_size_hint(self) -> tuple[int, int]:
        r = self.devicePixelRatioF()
        return int(self.width() * r), int(max(200, self.height() - 44) * r)

    # -------------------------------------------------------------- showing
    def _return_grid_hosts(self) -> None:
        for host in self.grid.clear():
            sw = self.widgets.get(host.session.server.id)
            if sw:
                sw.dock_host()

    def show_welcome(self, html: str) -> None:
        self._return_grid_hosts()
        self.welcome_text.setText(html)
        self.setCurrentWidget(self.welcome)
        self.current = None

    def show_server(self, server: ServerNode) -> None:
        self._return_grid_hosts()
        w = self.session_widget(server)
        if server.id not in self.fullscreen:
            w.dock_host()
        w.refresh()
        self.setCurrentWidget(w)
        self.current = server

    def show_group(self, group) -> None:
        self._return_grid_hosts()
        if isinstance(group, SmartGroupNode):
            servers = group.members()
        elif isinstance(group, (ContainerNode, VirtualGroup)):
            servers = group.servers()
        else:
            servers = []
        self._group_servers = servers
        title = group.label if hasattr(group, "label") else str(group)
        self.grid.populate(title, servers, self.manager.state, self._error_of)
        for s in servers:
            self._grid_adopt(s)
        self.setCurrentWidget(self.grid)
        self.current = group

    def _grid_adopt(self, server: ServerNode) -> None:
        tile = self.grid.tile(server.id)
        sess = self.manager.session(server)
        if not tile or not sess or server.id in self.fullscreen or tile.host is not None:
            return
        w = self.widgets.get(server.id)
        if w and w.host and sess.embedded and sess.state.active:
            tile.adopt_host(w.host)

    def _error_of(self, server: ServerNode) -> str:
        s = self.manager.session(server)
        return s.last_error if s else ""

    def refresh_current(self) -> None:
        if isinstance(self.current, ServerNode):
            self.show_server(self.current)
        elif self.current is not None:
            self.show_group(self.current)

    # ------------------------------------------------------------- events
    def on_session_state(self, session: Session) -> None:
        sid = session.server.id
        w = self.widgets.get(sid)
        if w:
            w.refresh()
        tile = self.grid.tile(sid)
        if tile and self.currentWidget() is self.grid:
            if session.state.active:
                self._grid_adopt(session.server)
            elif tile.host is not None:
                h = tile.release_host()
                if w:
                    w.dock_host()
                del h
            tile.set_state(session.state, session.last_error)
            n_conn = sum(1 for s in self._group_servers if self.manager.state(s) == State.CONNECTED)
            self.grid.subtitle.setText(f"{len(self._group_servers)} servers  ·  {n_conn} connected")
        if not session.state.active and sid in self.fullscreen:
            self._exit_fullscreen(sid)

    def _activate(self, server: ServerNode) -> None:
        self.select_server.emit(server)

    def _toggle(self, server: ServerNode) -> None:
        if self.manager.state(server).active:
            self.disconnect_requested.emit(server)
        else:
            self.connect_requested.emit(server)

    # ---------------------------------------------------------- fullscreen
    def toggle_fullscreen(self, server: ServerNode, screen=None) -> None:
        if server.id in self.fullscreen:
            self._exit_fullscreen(server.id)
            return
        sess = self.manager.session(server)
        if not sess or not sess.state.active or not sess.embedded:
            return
        w = self.session_widget(server)
        # pull the host out of a thumbnail tile if needed
        tile = self.grid.tile(server.id)
        if tile and tile.host is not None:
            tile.release_host()
        screen = screen or self.window().screen()
        fs = FullScreenWindow(w, screen)
        fs.restore_requested.connect(lambda: self._exit_fullscreen(server.id))
        fs.disconnect_requested.connect(lambda: self.disconnect_requested.emit(server))
        self.fullscreen[server.id] = fs
        fs.showFullScreen()
        fs.activateWindow()
        w.refresh()

    def _exit_fullscreen(self, sid: str) -> None:
        fs = self.fullscreen.pop(sid, None)
        if fs is None:
            return
        fs.take_back()
        fs.close()
        fs.deleteLater()
        if self.currentWidget() is self.grid:
            server = self.widgets[sid].server
            self._grid_adopt(server)
        self.window().activateWindow()

    def fullscreen_current(self, screen=None) -> None:
        if isinstance(self.current, ServerNode):
            self.toggle_fullscreen(self.current, screen)
