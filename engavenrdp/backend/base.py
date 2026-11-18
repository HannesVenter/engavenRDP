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
