"""Application preferences and per-user state (~/.config/engavenrdp)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    p = Path(base) / "engavenrdp"
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class AppState:
    open_files: list[str] = field(default_factory=list)
    auto_save: bool = True
    theme: str = "system"  # system | light | dark
    freerdp_command: str = ""
    favorites: list[dict[str, str]] = field(default_factory=list)  # {file, id}
    recent: list[dict[str, str]] = field(default_factory=list)
    reconnect_interval: int = 15
    group_connect_delay_ms: int = 400
    thumbnail_size: int = 260
    confirm_disconnect: bool = True
    show_virtual_groups: bool = True
    hidden_virtual_groups: list[str] = field(default_factory=list)  # e.g. ["favorites", "recent"]
    geometry: str = ""
    window_state: str = ""
    splitter: list[int] = field(default_factory=list)  # [server list, client area]
    server_list_right: bool = False
    reconnect_on_start: list[dict[str, str]] = field(default_factory=list)

    MAX_RECENT = 20

    @classmethod
    def path(cls) -> Path:
        return config_dir() / "settings.json"

    @classmethod
    def load(cls) -> "AppState":
        try:
            data: dict[str, Any] = json.loads(cls.path().read_text())
        except Exception:
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self) -> None:
        p = self.path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        tmp.replace(p)

    # -- favorites / recent ------------------------------------------------
    @staticmethod
    def ref(file_path: str | None, server_id: str) -> dict[str, str]:
        return {"file": file_path or "", "id": server_id}

    def is_favorite(self, file_path: str | None, server_id: str) -> bool:
        return self.ref(file_path, server_id) in self.favorites or any(
            f["id"] == server_id for f in self.favorites)

    def set_favorite(self, file_path: str | None, server_id: str, on: bool) -> None:
        self.favorites = [f for f in self.favorites if f["id"] != server_id]
        if on:
            self.favorites.append(self.ref(file_path, server_id))

    def add_recent(self, file_path: str | None, server_id: str) -> None:
        self.recent = [r for r in self.recent if r["id"] != server_id]
        self.recent.insert(0, self.ref(file_path, server_id))
        del self.recent[self.MAX_RECENT:]
