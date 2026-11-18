"""Owns all sessions, the staggered connect queue and the reconnect (retry) list."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QTcpSocket

from ..backend import RdpBackend
from ..model.nodes import ServerNode
from ..model.resolve import resolve
from .session import Session, State


class SessionManager(QObject):
    session_created = Signal(object)          # Session
    session_state_changed = Signal(object)    # Session
    reconnect_list_changed = Signal()

    def __init__(self, backend: RdpBackend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self.sessions: dict[str, Session] = {}
        self._queue: list[ServerNode] = []
        self._queue_timer = QTimer(self)
        self._queue_timer.timeout.connect(self._drain_queue)
        self.connect_delay_ms = 400
        self.connector: Callable[[ServerNode], None] | None = None  # set by UI
        self._connecting = False  # inside connector(); it may be showing a credential prompt
        # reconnect list
        self.reconnect: dict[str, ServerNode] = {}
        self._probes: dict[str, QTcpSocket] = {}
        self._retry_timer = QTimer(self)
        self._retry_timer.timeout.connect(self._probe_all)
        self.retry_interval_s = 15

    # ---------------------------------------------------------------- sessions
    def session(self, server: ServerNode, create: bool = False) -> Session | None:
        s = self.sessions.get(server.id)
        if s is None and create:
            s = Session(server, self.backend, self)
            s.state_changed.connect(lambda _st, s=s: self.session_state_changed.emit(s))
            self.sessions[server.id] = s
            self.session_created.emit(s)
        return s

    def state(self, server: ServerNode) -> State:
        s = self.sessions.get(server.id)
        return s.state if s else State.DISCONNECTED

    def active_sessions(self) -> list[Session]:
        return [s for s in self.sessions.values() if s.state.active]

    def connect_many(self, servers: list[ServerNode]) -> None:
        seen = {s.id for s in self._queue}
        for s in servers:
            if s.id not in seen and not self.state(s).active:
                self._queue.append(s)
                seen.add(s.id)
        if self._queue and not self._queue_timer.isActive():
            self._drain_queue()
            self._queue_timer.start(self.connect_delay_ms)

    def cancel_queue(self) -> None:
        self._queue.clear()
        self._queue_timer.stop()

    def _drain_queue(self) -> None:
        if self._connecting:
            # The connector is showing a modal prompt, whose event loop keeps this
            # timer firing. Wait, so the answer can be reused for the next server
            # instead of stacking one prompt per server.
            return
        if not self._queue:
            self._queue_timer.stop()
            return
        server = self._queue.pop(0)
        self._connect(server)

    def _connect(self, server: ServerNode) -> None:
        if not self.connector or self.state(server).active:
            return
        if self._connecting:
            self.connect_many([server])  # take its turn after the open prompt
            return
        self._connecting = True
        try:
            self.connector(server)
        finally:
            self._connecting = False

    def disconnect(self, server: ServerNode) -> None:
        self._queue = [s for s in self._queue if s.id != server.id]
        s = self.sessions.get(server.id)
        if s:
            s.stop()

    def disconnect_all(self) -> None:
        self.cancel_queue()
        for s in list(self.sessions.values()):
            if s.state.active:
                s.stop()

    def forget(self, server: ServerNode) -> None:
        s = self.sessions.pop(server.id, None)
        if s:
            s.stop()
            s.deleteLater()

    # ------------------------------------------------------------- reconnect
    def add_reconnect(self, server: ServerNode) -> None:
        self.reconnect[server.id] = server
        if not self._retry_timer.isActive():
            self._retry_timer.start(self.retry_interval_s * 1000)
        self.reconnect_list_changed.emit()
        self._probe(server)

    def remove_reconnect(self, server: ServerNode) -> None:
        self.reconnect.pop(server.id, None)
        p = self._probes.pop(server.id, None)
        if p:
            p.abort()
            p.deleteLater()
        if not self.reconnect:
            self._retry_timer.stop()
        self.reconnect_list_changed.emit()

    def _probe_all(self) -> None:
        for server in list(self.reconnect.values()):
            self._probe(server)

    def _probe(self, server: ServerNode) -> None:
        if self.state(server).active or server.id in self._probes:
            return
        port = int(resolve(server)["connection"].get("port") or 3389)
        sock = QTcpSocket(self)
        self._probes[server.id] = sock

        def done(ok: bool) -> None:
            self._probes.pop(server.id, None)
            sock.abort()
            sock.deleteLater()
            if ok and server.id in self.reconnect:
                self.remove_reconnect(server)
                self._connect(server)

        sock.connected.connect(lambda: done(True))
        sock.errorOccurred.connect(lambda _e: done(False))
        QTimer.singleShot(5000, lambda: server.id in self._probes and self._probes[server.id] is sock and done(False))
        sock.connectToHost(server.host or server.name, port)
