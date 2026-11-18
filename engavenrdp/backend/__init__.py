"""RDP backends. ``get_backend()`` returns the configured backend instance."""
from __future__ import annotations

from .base import BackendInfo, Command, CommandError, RdpBackend, SessionSpec
from .freerdp import FreeRdpBackend

_BACKENDS = {"freerdp": FreeRdpBackend}


def get_backend(name: str = "freerdp", command: str | None = None) -> RdpBackend:
    cls = _BACKENDS.get(name, FreeRdpBackend)
    return cls(command) if cls is FreeRdpBackend else cls()


def register_backend(name: str, cls: type[RdpBackend]) -> None:
    _BACKENDS[name] = cls


__all__ = ["BackendInfo", "Command", "CommandError", "RdpBackend", "SessionSpec", "get_backend", "register_backend"]
