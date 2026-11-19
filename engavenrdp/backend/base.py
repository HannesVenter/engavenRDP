"""Backend-neutral description of an RDP session and the backend interface.

The UI builds a :class:`SessionSpec` from resolved settings and hands it to a
backend. A backend turns it into a process to run. Swapping FreeRDP for
something else means implementing :class:`RdpBackend` and registering it.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionSpec:
    host: str
    port: int = 3389
    title: str = ""
    username: str = ""
    domain: str = ""
    password: str | None = None
    # categories (resolved dicts, see model.settings)
    connection: dict[str, Any] = field(default_factory=dict)
    gateway: dict[str, Any] = field(default_factory=dict)
    gateway_username: str = ""
    gateway_domain: str = ""
    gateway_password: str | None = None
    display: dict[str, Any] = field(default_factory=dict)
    resources: dict[str, Any] = field(default_factory=dict)
    security: dict[str, Any] = field(default_factory=dict)
    experience: dict[str, Any] = field(default_factory=dict)
    # runtime
    parent_window: int | None = None  # X11 window id to embed into
    width: int = 1280
    height: int = 800
    fullscreen: bool = False
    # A changed certificate the user has just accepted for this connection.
    trusted_cert: "CertChange | None" = None


@dataclass
class CertChange:
    """A server presented a different certificate from the one saved earlier."""

    host: str
    port: int
    new: dict[str, str] = field(default_factory=dict)  # e.g. "Common Name", "Valid to", "Thumbprint"
    old: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def _hex(thumbprint: str) -> str:
        return "".join(ch for ch in thumbprint.lower() if ch in "0123456789abcdef")

    @property
    def new_fingerprint(self) -> str:
        return self._hex(self.new.get("Thumbprint", ""))

    @property
    def old_fingerprint(self) -> str:
        return self._hex(self.old.get("Thumbprint", ""))


class CommandError(ValueError):
    """The session cannot be expressed as a command line for this backend."""


@dataclass
class Command:
    argv: list[str]
    stdin: bytes | None = None
    redacted: list[str] = field(default_factory=list)  # argv safe to show in logs


@dataclass
class BackendInfo:
    name: str
    available: bool
    version: str = ""
    path: str = ""
    message: str = ""
    can_embed: bool = False


class RdpBackend(abc.ABC):
    name = "abstract"

    @abc.abstractmethod
    def info(self) -> BackendInfo: ...

    @abc.abstractmethod
    def build_command(self, spec: SessionSpec) -> Command: ...

    def exit_reason(self, code: int) -> str:
        return f"exit code {code}"

    def is_user_disconnect(self, code: int) -> bool:
        return code == 0

    def is_auth_failure(self, code: int) -> bool:
        """True if ``code`` means the credentials were rejected."""
        return False

    # -- saved server certificates
    def cert_change(self, log_lines: list[str]) -> CertChange | None:
        """The changed certificate reported in the output of a failed attempt, if any."""
        return None

    def forget_certificate(self, change: CertChange) -> bool:
        """Drop the saved certificate ``change`` replaces. True if one was removed."""
        return False

    def stored_fingerprint(self, host: str, port: int) -> str | None:
        """SHA-256 (lower-case hex) of the certificate saved for ``host:port``, if known."""
        return None
