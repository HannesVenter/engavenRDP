"""A single RDP session: process lifecycle and state machine."""
from __future__ import annotations

import enum
import time

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from ..backend import CommandError, RdpBackend, SessionSpec, x11
from ..model.nodes import ServerNode


class State(enum.Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"

    @property
    def active(self) -> bool:
        return self in (State.CONNECTING, State.CONNECTED)


class Session(QObject):
    state_changed = Signal(object)  # State
    log_appended = Signal(str)
    embedded_window_ready = Signal(int)

    MAX_LOG = 400

    def __init__(self, server: ServerNode, backend: RdpBackend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.server = server
        self.backend = backend
        self.state = State.DISCONNECTED
        self.last_error = ""
        self.auth_failed = False  # the last attempt ended because the credentials were rejected
        self.log_lines: list[str] = []
        self.proc: QProcess | None = None
        self.embed_window_id: int | None = None  # the host widget's X id
        self.child_window_id: int | None = None  # FreeRDP's own window
        self.embedded = False
        self.started_at = 0.0
        self.connected_at = 0.0
        self.spec: SessionSpec | None = None
        self._user_stop = False
        self._poll = QTimer(self)
        self._poll.setInterval(250)
        self._poll.timeout.connect(self._poll_window)
        self.size_provider = None  # callable -> (w, h) of the host

    # ------------------------------------------------------------------ api
    def start(self, spec: SessionSpec) -> None:
        if self.state.active:
            return
        self.spec = spec
        self.embedded = spec.parent_window is not None
        self.embed_window_id = spec.parent_window
        self.child_window_id = None
        self._user_stop = False
        self.last_error = ""
        self.auth_failed = False
        try:
            cmd = self.backend.build_command(spec)
        except CommandError as e:
            self.last_error = str(e)
            self._log(self.last_error)
            self._set_state(State.CONNECTING)  # so listeners see a transition into ERROR
            self._set_state(State.ERROR)
            return
        self._log("$ " + " ".join(cmd.redacted))
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(self._error)
        self._set_state(State.CONNECTING)
        self.started_at = time.monotonic()
        self.proc.start(cmd.argv[0], cmd.argv[1:])
        if cmd.stdin is not None:
            self.proc.write(cmd.stdin)
            self.proc.closeWriteChannel()
        self._poll.start()

    def stop(self) -> None:
        self._user_stop = True
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.terminate()
            QTimer.singleShot(3000, self._kill_if_running)
        else:
            self._set_state(State.DISCONNECTED)

    def send_keys(self, keysyms: list[str]) -> bool:
        if self.child_window_id:
            return x11.send_keys(self.child_window_id, keysyms)
        return False

    def sync_size(self, width: int, height: int) -> None:
        if self.child_window_id:
            x11.resize(self.child_window_id, width, height)

    def set_input_enabled(self, enabled: bool) -> None:
        if self.child_window_id:
            x11.set_input_passthrough(self.child_window_id, not enabled)

    def focus(self) -> None:
        if self.child_window_id:
            x11.focus(self.child_window_id)

    @property
    def uptime(self) -> float:
        return time.monotonic() - self.connected_at if self.state == State.CONNECTED else 0.0

    # ------------------------------------------------------------- internals
    def _kill_if_running(self) -> None:
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.kill()

    def _log(self, line: str) -> None:
        self.log_lines.append(line)
        if len(self.log_lines) > self.MAX_LOG:
            del self.log_lines[: len(self.log_lines) - self.MAX_LOG]
        self.log_appended.emit(line)

    def _read(self) -> None:
        if not self.proc:
            return
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        for line in data.splitlines():
            if line.strip():
                self._log(line.rstrip())
                low = line.lower()
                if self.state == State.CONNECTING and not self.embedded and (
                        "connected to" in low or "xf_post_connect" in low):
                    self._mark_connected()

    def _poll_window(self) -> None:
        if self.state not in (State.CONNECTING, State.CONNECTED):
            self._poll.stop()
            return
        self._ticks = getattr(self, "_ticks", 0) + 1
        if self.embedded and self.embed_window_id and x11.available():
            kids = x11.child_windows(self.embed_window_id)
            if kids and kids[-1] != self.child_window_id:
                # first appearance, or FreeRDP re-created its window after an auto-reconnect
                self.child_window_id = kids[-1]
                self._fit()
                self.embedded_window_ready.emit(self.child_window_id)
                self._mark_connected()
            elif self.child_window_id and self._ticks % 4 == 0:
                # FreeRDP may resize its own window after connecting; keep it fitted.
                self._fit(only_if_different=True)
            return
        if self.state == State.CONNECTING and time.monotonic() - self.started_at > 3.0:
            # external windows, or no X11 helpers: assume connected after a grace period
            self._mark_connected()
        if self.state == State.CONNECTED:
            self._poll.stop()

    def _fit(self, only_if_different: bool = False) -> None:
        if not self.size_provider or not self.child_window_id:
            return
        want = self.size_provider()
        if only_if_different and x11.geometry(self.child_window_id) == tuple(want):
            return
        self.sync_size(*want)

    def _mark_connected(self) -> None:
        if self.state != State.CONNECTED:
            self.connected_at = time.monotonic()
            self._set_state(State.CONNECTED)

    def _error(self, err: QProcess.ProcessError) -> None:
        if err == QProcess.ProcessError.FailedToStart:
            self.last_error = "Could not start FreeRDP — is it installed?"
            self._log(self.last_error)
            self._poll.stop()
            self._set_state(State.ERROR)

    def _finished(self, code: int, status: QProcess.ExitStatus) -> None:
        self._poll.stop()
        self.child_window_id = None
        if self._user_stop or (status == QProcess.ExitStatus.NormalExit and self.backend.is_user_disconnect(code)):
            reason = "Disconnected" if self._user_stop else self.backend.exit_reason(code)
            self.last_error = ""
            self._log(f"— {reason} (exit {code})")
            self._set_state(State.DISCONNECTED)
        else:
            normal = status == QProcess.ExitStatus.NormalExit
            self.last_error = self.backend.exit_reason(code) if normal else "FreeRDP crashed"
            self.auth_failed = normal and self.backend.is_auth_failure(code)
            self._log(f"— {self.last_error} (exit {code})")
            self._set_state(State.ERROR)

    def _set_state(self, st: State) -> None:
        if st != self.state:
            self.state = st
            self.state_changed.emit(st)
