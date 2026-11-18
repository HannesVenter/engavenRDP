"""Thumbnail grid shown when a group is selected.

Connected embedded sessions are *live*: the session's EmbedHost is moved into the
tile and FreeRDP's ``/smart-sizing`` scales the remote desktop down. The FreeRDP
window is made click-through so clicks select the tile instead of reaching the
remote machine.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QSlider, QToolButton, QVBoxLayout, QWidget)

from ..core.session import State
from ..model.nodes import ServerNode
from . import icons


class Tile(QFrame):
    clicked = Signal(object)
    activated = Signal(object)
    connect_clicked = Signal(object)

    def __init__(self, server: ServerNode, width: int) -> None:
        super().__init__(objectName="tile")
        self.server = server
        self.host = None
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 4, 6, 6)
        v.setSpacing(4)
        head = QHBoxLayout()
        self.dot = QLabel()
        self.name = QLabel(server.label)
        self.name.setToolTip(server.host)
        f = self.name.font()
        f.setBold(True)
        self.name.setFont(f)
        self.btn = QToolButton(text="▶")
        self.btn.setAutoRaise(True)
        self.btn.clicked.connect(lambda: self.connect_clicked.emit(self.server))
        head.addWidget(self.dot)
        head.addWidget(self.name, 1)
        head.addWidget(self.btn)
        v.addLayout(head)
        self.slot = QWidget()
        # The slot must have its own X window *before* a session is moved into it,
        # otherwise X parents the session to the tile and it covers the header.
        self.slot.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.slot_layout = QVBoxLayout(self.slot)
        self.slot_layout.setContentsMargins(0, 0, 0, 0)
        self.placeholder = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self.placeholder.setObjectName("subtitle")
        self.slot_layout.addWidget(self.placeholder)
        v.addWidget(self.slot, 1)
        self.set_width(width)

    def set_width(self, w: int) -> None:
        self.setFixedSize(w, int(w * 10 / 16) + 34)

    def set_state(self, state: State, error: str = "") -> None:
        self.dot.setPixmap(icons.dot_pixmap(state, 10))
        self.btn.setText("■" if state.active else "▶")
        self.btn.setToolTip("Disconnect" if state.active else "Connect")
        if self.host is None:
            self.placeholder.setText({
                State.CONNECTING: "Connecting…",
                State.CONNECTED: "Connected",
                State.ERROR: error or "Error",
            }.get(state, "Disconnected"))

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def adopt_host(self, host) -> None:
        self.host = host
        self.placeholder.hide()
        self.slot.winId()
        host.setParent(self.slot)
        self.slot_layout.addWidget(host)
        host.set_interactive(False)
        host.show()
        host.connect_tile(lambda: self.clicked.emit(self.server), lambda: self.activated.emit(self.server))

    def release_host(self):
        h, self.host = self.host, None
        if h is not None:
            h.disconnect_tile_signals()
            self.slot_layout.removeWidget(h)
        self.placeholder.show()
        return h

    def mousePressEvent(self, e) -> None:
        self.clicked.emit(self.server)
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:
        self.activated.emit(self.server)
        super().mouseDoubleClickEvent(e)


class ThumbnailGrid(QWidget):
    server_clicked = Signal(object)
    server_activated = Signal(object)
    server_connect_toggle = Signal(object)
    connect_all = Signal()
    disconnect_all = Signal()
    size_changed = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        header = QWidget(objectName="paneHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(12, 6, 8, 6)
        self.title = QLabel(objectName="title")
        self.subtitle = QLabel(objectName="subtitle")
        h.addWidget(self.title)
        h.addSpacing(8)
        h.addWidget(self.subtitle, 1)
        h.addWidget(QLabel("Size"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(140, 640)
        self.slider.setFixedWidth(120)
        self.slider.valueChanged.connect(self._resize_tiles)
        h.addWidget(self.slider)
        self.btn_all = QPushButton("Connect all", objectName="primary")
        self.btn_all.clicked.connect(self.connect_all.emit)
        self.btn_none = QPushButton("Disconnect all")
        self.btn_none.clicked.connect(self.disconnect_all.emit)
        h.addWidget(self.btn_none)
        h.addWidget(self.btn_all)
        lay.addWidget(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.inner = QWidget()
        self.grid = QGridLayout(self.inner)
        self.grid.setContentsMargins(14, 14, 14, 14)
        self.grid.setSpacing(12)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(self.inner)
        lay.addWidget(self.scroll, 1)
        self.empty = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, objectName="subtitle")
        lay.addWidget(self.empty)
        self.tiles: dict[str, Tile] = {}
        self._order: list[str] = []
        self._selected: str | None = None
        self.tile_width = 260

    def set_tile_width(self, w: int) -> None:
        self.tile_width = w
        self.slider.blockSignals(True)
        self.slider.setValue(w)
        self.slider.blockSignals(False)

    def populate(self, title: str, servers: list[ServerNode], state_of, error_of) -> None:
        self.clear()
        self.title.setText(title)
        n_conn = sum(1 for s in servers if state_of(s) == State.CONNECTED)
        self.subtitle.setText(f"{len(servers)} servers  ·  {n_conn} connected")
        self.empty.setVisible(not servers)
        self.scroll.setVisible(bool(servers))
        self.empty.setText("This group has no servers yet.")
        self.btn_all.setEnabled(bool(servers))
        for s in servers:
            t = Tile(s, self.tile_width)
            t.set_state(state_of(s), error_of(s))
            t.clicked.connect(self._on_click)
            t.activated.connect(self.server_activated.emit)
            t.connect_clicked.connect(self.server_connect_toggle.emit)
            self.tiles[s.id] = t
            self._order.append(s.id)
        self._relayout()

    def clear(self) -> list:
        """Remove all tiles; returns hosts that were adopted so the caller can re-dock them."""
        hosts = []
        for t in self.tiles.values():
            h = t.release_host()
            if h is not None:
                hosts.append(h)
            self.grid.removeWidget(t)
            t.deleteLater()
        self.tiles.clear()
        self._order.clear()
        self._selected = None
        return hosts

    def tile(self, server_id: str) -> Tile | None:
        return self.tiles.get(server_id)

    def _on_click(self, server: ServerNode) -> None:
        if self._selected and self._selected in self.tiles:
            self.tiles[self._selected].set_selected(False)
        self._selected = server.id
        self.tiles[server.id].set_selected(True)
        self.server_clicked.emit(server)

    def _resize_tiles(self, w: int) -> None:
        self.tile_width = w
        for t in self.tiles.values():
            t.set_width(w)
        self._relayout()
        self.size_changed.emit(w)

    def _relayout(self) -> None:
        avail = max(1, self.scroll.viewport().width() - 28)
        cols = max(1, (avail + 12) // (self.tile_width + 12))
        for i, sid in enumerate(self._order):
            t = self.tiles[sid]
            self.grid.removeWidget(t)
            self.grid.addWidget(t, i // cols, i % cols)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._relayout()

    def wheelEvent(self, e) -> None:
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            step = 30 if e.angleDelta().y() > 0 else -30
            self.slider.setValue(self.slider.value() + step)
            e.accept()
            return
        super().wheelEvent(e)
