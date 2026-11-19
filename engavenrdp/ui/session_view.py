"""Widgets that host an embedded FreeRDP session."""
from __future__ import annotations

from html import escape

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QPalette
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QPlainTextEdit, QPushButton,
                               QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from ..backend import x11
from ..core.session import Session, State
from ..model.nodes import ServerNode
from . import icons


class EmbedHost(QWidget):
    """A native X11 window FreeRDP draws into (``/parent-window``)."""

    clicked = Signal()
    double_clicked = Signal()

    def __init__(self, session: Session, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor("#000000"))
        self.setPalette(pal)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.interactive = True
        session.size_provider = self.device_size
        session.embedded_window_ready.connect(lambda _w: self._sync())

    def connect_tile(self, on_click, on_double) -> None:
        self.disconnect_tile_signals()
        self._tile_conns = [self.clicked.connect(on_click), self.double_clicked.connect(on_double)]

    def disconnect_tile_signals(self) -> None:
        for c in getattr(self, "_tile_conns", []):
            try:
                self.disconnect(c)
            except (RuntimeError, TypeError):
                pass
        self._tile_conns = []

    def x_id(self) -> int:
        return int(self.winId())

    def device_size(self) -> tuple[int, int]:
        r = self.devicePixelRatioF()
        return max(1, int(self.width() * r)), max(1, int(self.height() * r))

    def _sync(self) -> None:
        self.session.sync_size(*self.device_size())
        self.session.set_input_enabled(self.interactive)

    def set_interactive(self, on: bool) -> None:
        self.interactive = on
        self.session.set_input_enabled(on)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._sync()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        QTimer.singleShot(0, self._sync)

    def mousePressEvent(self, e) -> None:
        if self.interactive:
            self.session.focus()
        else:
            self.clicked.emit()
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:
        if not self.interactive:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(e)

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        if self.interactive:
            self.session.focus()


def special_keys_menu(parent: QWidget, send) -> QMenu:
    m = QMenu("Send keys", parent)
    for label, keys in x11.SPECIAL_KEYS.items():
        a = m.addAction(label)
        a.triggered.connect(lambda _=False, k=keys: send(k))
    return m


class SessionWidget(QWidget):
    """Header + (status card | embedded session) for one server."""

    request_connect = Signal(object)        # ServerNode
    request_disconnect = Signal(object)
    request_reconnect = Signal(object)
    request_fullscreen = Signal(object)
    request_properties = Signal(object)
    request_trust_cert = Signal(object)

    def __init__(self, server: ServerNode, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.server = server
        self.session: Session | None = None
        self.host: EmbedHost | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ---- header
        header = QWidget(objectName="sessionHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(10, 2, 6, 2)
        self.dot = QLabel()
        self.title = QLabel(objectName="title")
        self.subtitle = QLabel(objectName="subtitle")
        h.addWidget(self.dot)
        h.addWidget(self.title)
        h.addSpacing(6)
        h.addWidget(self.subtitle, 1)
        self.btn_connect = QPushButton("Connect", objectName="primary")
        self.btn_connect.clicked.connect(self._toggle)
        self.btn_keys = QToolButton(text="Keys")
        self.btn_keys.setToolTip("Send special keys (Ctrl+Alt+Del, …)")
        self.btn_keys.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_keys.setMenu(special_keys_menu(self, self.send_keys))
        self.btn_fs = QToolButton(text="Full screen")
        self.btn_fs.setToolTip("Full screen (F11)")
        self.btn_fs.clicked.connect(lambda: self.request_fullscreen.emit(self.server))
        self.btn_more = QToolButton(text="⋯")
        self.btn_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more = QMenu(self)
        more.addAction("Reconnect", lambda: self.request_reconnect.emit(self.server))
        more.addAction("Show connection log", self.toggle_log)
        more.addAction("Properties…", lambda: self.request_properties.emit(self.server))
        self.btn_more.setMenu(more)
        for w in (self.btn_keys, self.btn_fs, self.btn_more, self.btn_connect):
            h.addWidget(w)
        lay.addWidget(header)

        # ---- body
        self.body = QStackedWidget()
        self.card_page = QWidget()
        cl = QVBoxLayout(self.card_page)
        cl.addStretch(1)
        card = QFrame(objectName="card")
        card.setMaximumWidth(640)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(28, 24, 28, 24)
        self.card_title = QLabel(objectName="title")
        self.card_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card_msg = QLabel(wordWrap=True)
        self.card_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card_msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.card_btn = QPushButton("Connect", objectName="primary")
        self.card_btn.clicked.connect(self._toggle)
        cv.addWidget(self.card_title)
        cv.addWidget(self.card_msg)
        cv.addSpacing(8)
        cv.addWidget(self.card_btn, 0, Qt.AlignmentFlag.AlignCenter)
        self.trust_btn = QPushButton("Trust new certificate and connect")
        self.trust_btn.setVisible(False)
        self.trust_btn.clicked.connect(lambda: self.request_trust_cert.emit(self.server))
        cv.addWidget(self.trust_btn, 0, Qt.AlignmentFlag.AlignCenter)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card, 3)
        row.addStretch(1)
        cl.addLayout(row)
        cl.addStretch(2)
        self.body.addWidget(self.card_page)

        self.host_page = QWidget()
        self.host_page.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.host_layout = QVBoxLayout(self.host_page)
        self.host_layout.setContentsMargins(0, 0, 0, 0)
        self.body.addWidget(self.host_page)
        lay.addWidget(self.body, 1)

        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumHeight(160)
        self.log.setVisible(False)
        f = self.log.font()
        f.setFamily("monospace")
        self.log.setFont(f)
        lay.addWidget(self.log)

        self._uptime = QTimer(self)
        self._uptime.timeout.connect(self.refresh)
        self._uptime.start(30_000)
        self.refresh()

    # ------------------------------------------------------------------ api
    def attach(self, session: Session) -> None:
        if self.session is session:
            return
        self.session = session
        self.host = EmbedHost(session)
        self.dock_host()
        session.state_changed.connect(lambda _s: self.refresh())
        session.log_appended.connect(self._append_log)
        session.embedded_window_ready.connect(lambda _w: self.refresh())
        self.log.setPlainText("\n".join(session.log_lines))

    def dock_host(self) -> None:
        if self.host is None:
            return
        if self.host.parent() is not self.host_page:
            self.host_page.winId()
            self.host.setParent(self.host_page)
            self.host_layout.addWidget(self.host)
        self.host.set_interactive(True)
        self.host.show()
        self.refresh()

    @property
    def host_docked(self) -> bool:
        return self.host is not None and self.host.parent() is self.host_page

    def body_device_size(self) -> tuple[int, int]:
        r = self.devicePixelRatioF()
        return int(self.body.width() * r), int(self.body.height() * r)

    def send_keys(self, keys: list[str]) -> None:
        if self.session and not self.session.send_keys(keys):
            self._append_log("Could not send keys (session not embedded or X11 unavailable)")

    def toggle_log(self) -> None:
        self.log.setVisible(not self.log.isVisible())

    def _append_log(self, line: str) -> None:
        self.log.appendPlainText(line)

    def _toggle(self) -> None:
        st = self.session.state if self.session else State.DISCONNECTED
        if st.active:
            self.request_disconnect.emit(self.server)
        else:
            self.request_connect.emit(self.server)

    def refresh(self) -> None:
        s = self.server
        st = self.session.state if self.session else State.DISCONNECTED
        self.dot.setPixmap(icons.dot_pixmap(st, 11))
        self.title.setText(s.label)
        sub = [s.host] if s.host != s.label else []
        sub.append(st.value.capitalize())
        if self.session and st == State.CONNECTED and self.session.uptime > 60:
            mins = int(self.session.uptime // 60)
            sub.append(f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins} min")
        if self.session and self.session.embedded is False and st.active:
            sub.append("external window")
        self.subtitle.setText("  ·  ".join(sub))
        self.btn_connect.setText("Disconnect" if st.active else "Connect")
        self.btn_connect.setObjectName("" if st.active else "primary")
        self.btn_connect.style().unpolish(self.btn_connect)
        self.btn_connect.style().polish(self.btn_connect)
        live = bool(self.session and self.session.child_window_id and st.active)
        self.btn_keys.setEnabled(live)
        self.btn_fs.setEnabled(st.active and bool(self.session and self.session.embedded))

        show_host = live and self.host_docked
        if show_host:
            self.body.setCurrentWidget(self.host_page)
            return
        self.body.setCurrentWidget(self.card_page)
        self.card_title.setText(s.label)
        self.card_btn.setText("Disconnect" if st.active else "Connect")
        self.card_btn.setObjectName("" if st.active else "primary")
        self.card_btn.style().unpolish(self.card_btn)
        self.card_btn.style().polish(self.card_btn)
        change = self.session.cert_change if self.session and st == State.ERROR else None
        self.trust_btn.setVisible(change is not None)
        if change is not None:
            self.card_btn.setText("Try again")
            self.card_btn.setObjectName("")
            self.card_btn.style().unpolish(self.card_btn)
            self.card_btn.style().polish(self.card_btn)
            self.card_msg.setText(_cert_change_html(change))
            return
        if st == State.CONNECTING:
            msg = f"Connecting to {s.host}…"
        elif st == State.CONNECTED and self.session and not self.session.embedded:
            msg = "This session is open in an external window."
        elif st == State.CONNECTED and not self.host_docked:
            msg = "This session is shown in full screen or a thumbnail."
        elif st == State.ERROR and self.session:
            msg = f"<b>Connection failed</b><br>{self.session.last_error}<br><br>" \
                  f"<span style='color:gray'>Use ⋯ → Show connection log for details.</span>"
        else:
            msg = f"{s.host}" + (f"<br><span style='color:gray'>{s.comment}</span>" if s.comment else "")
            msg += "<br><br><span style='color:gray'>Press Enter or double-click in the tree to connect.</span>"
        self.card_msg.setText(msg)


def _cert_change_html(change) -> str:
    """What the user needs to decide whether a changed server certificate is expected."""
    labels = (("Common Name", "Name"), ("Issuer", "Issuer"), ("Valid from", "Valid from"),
              ("Valid to", "Valid to"), ("Thumbprint", "Thumbprint"))

    def value(key: str, text: str) -> str:
        parts = text.split(":")
        if key == "Thumbprint" and len(parts) > 16:  # too long for one line of the card
            half = len(parts) // 2
            text = ":".join(parts[:half]) + ":\n" + ":".join(parts[half:])
            return "<span style='font-family:monospace'>" + escape(text).replace("\n", "<br>") + "</span>"
        return escape(text)

    def table(d: dict) -> str:
        rows = "".join(f"<tr><td style='color:gray; white-space:nowrap'>{label}&nbsp;&nbsp;&nbsp;</td>"
                       f"<td>{value(key, d[key])}</td></tr>" for key, label in labels if d.get(key))
        return f"<table align='center'>{rows or '<tr><td>(no details reported)</td></tr>'}</table>"

    return (f"<b>The certificate of {escape(change.host)}:{change.port} has changed</b>"
            "<p>This is normal when the server renewed its certificate or was reinstalled. It can also "
            "mean that another machine is answering at this address.</p>"
            f"<p><b>New certificate</b></p>{table(change.new)}"
            f"<p><b>Saved certificate</b></p>{table(change.old)}"
            "<p style='color:gray'>Trust it only if you expect the change. To be sure, compare the "
            "new SHA-256 thumbprint with the certificate on the server itself.</p>")


class FullScreenWindow(QWidget):
    """Borderless full-screen window with a slide-down connection bar."""

    restore_requested = Signal()
    disconnect_requested = Signal()

    def __init__(self, session_widget: SessionWidget, screen=None) -> None:
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.sw = session_widget
        self.host = session_widget.host
        self.setWindowTitle(f"{session_widget.server.label} — engavenRDP")
        self.setWindowIcon(icons.app_icon())
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor("black"))
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        self.winId()
        self.host.setParent(self)
        self.host.set_interactive(True)
        self.host.show()

        self.bar = QFrame(self, objectName="fsbar")
        self.bar.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        bl = QHBoxLayout(self.bar)
        bl.setContentsMargins(10, 2, 6, 2)
        self.pin = QToolButton(text="📌", checkable=True)
        self.pin.setToolTip("Keep the connection bar visible")
        bl.addWidget(self.pin)
        bl.addWidget(QLabel(session_widget.server.label), 1, Qt.AlignmentFlag.AlignCenter)
        keys = QToolButton(text="Keys")
        keys.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        keys.setMenu(special_keys_menu(self, session_widget.send_keys))
        bl.addWidget(keys)
        mini = QToolButton(text="—")
        mini.setToolTip("Minimize")
        mini.clicked.connect(self.showMinimized)
        bl.addWidget(mini)
        rest = QToolButton(text="❐")
        rest.setToolTip("Leave full screen")
        rest.clicked.connect(self.restore_requested.emit)
        bl.addWidget(rest)
        close = QToolButton(text="✕")
        close.setToolTip("Disconnect")
        close.clicked.connect(self.disconnect_requested.emit)
        bl.addWidget(close)
        self.bar.installEventFilter(self)
        self._collapse = QTimer(self, singleShot=True, interval=1200)
        self._collapse.timeout.connect(lambda: self._expand(False))
        self._expanded = True
        self._collapse.start(2500)
        if screen is not None:
            self.setScreen(screen)
            self.setGeometry(screen.geometry())

    def eventFilter(self, obj, ev) -> bool:
        from PySide6.QtCore import QEvent

        if obj is self.bar:
            if ev.type() == QEvent.Type.Enter:
                self._collapse.stop()
                self._expand(True)
            elif ev.type() == QEvent.Type.Leave and not self.pin.isChecked():
                self._collapse.start()
        return False

    def _expand(self, on: bool) -> None:
        if not on and self.pin.isChecked():
            return
        self._expanded = on
        self._layout_bar()

    def _layout_bar(self) -> None:
        w = min(560, self.width() - 40)
        h = 34 if self._expanded else 5
        self.bar.setGeometry((self.width() - w) // 2, 0, w, h)
        for c in self.bar.findChildren(QWidget):
            c.setVisible(self._expanded)
        self.bar.raise_()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.host.setGeometry(0, 0, self.width(), self.height())
        self._layout_bar()

    def take_back(self) -> None:
        self.sw.dock_host()

    def closeEvent(self, e) -> None:
        # Closed from the window manager (Alt+F4, …): leave full screen properly,
        # otherwise the session keeps running inside a hidden window.
        if self.host.parent() is self:
            e.ignore()
            self.restore_requested.emit()
        else:
            super().closeEvent(e)
