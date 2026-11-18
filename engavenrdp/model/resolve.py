"""Inheritance resolution: walk up the tree to find the effective settings."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .nodes import FileNode, Node
from .secret import Secret
from .settings import CATEGORIES, CATEGORY_BY_KEY, CredentialProfile


@dataclass
class Resolved:
    """Effective values per category, plus the node each category came from."""

    values: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: dict[str, Node | None] = field(default_factory=dict)  # None => built-in defaults

    def __getitem__(self, cat: str) -> dict[str, Any]:
        return self.values[cat]

    def source_label(self, cat: str) -> str:
        src = self.sources.get(cat)
        if src is None:
            return "built-in defaults"
        if isinstance(src, FileNode):
            return f"file “{src.name}”"
        return f"{src.kind} “{src.label}”"


def resolve_category(node: Node, category: str, include_self: bool = True) -> tuple[dict[str, Any], Node | None]:
    chain = ([node] if include_self else []) + list(node.ancestors())
    for n in chain:
        rec = n.settings.get(category, {"inherit": True})
        if not rec.get("inherit", True):
            vals = CATEGORY_BY_KEY[category].defaults()
            vals.update({k: v for k, v in rec.items() if k != "inherit"})
            return vals, n
    return CATEGORY_BY_KEY[category].defaults(), None


def resolve(node: Node) -> Resolved:
    r = Resolved()
    for cat in CATEGORIES:
        r.values[cat.key], r.sources[cat.key] = resolve_category(node, cat.key)
    return r


@dataclass
class Credentials:
    username: str = ""
    domain: str = ""
    password: Secret | None = None
    prompt: bool = False
    profile: str = ""
    missing_profile: bool = False

    @property
    def password_text(self) -> str | None:
        return self.password.plain if self.password else None

    @property
    def needs_password(self) -> bool:
        return self.prompt or not (self.password and self.password.available)


def find_profile(node: Node, name: str, global_profiles: list[CredentialProfile]) -> CredentialProfile | None:
    if not name:
        return None
    f = node.file
    if f is not None:
        p = f.profile(name)
        if p:
            return p
    for p in global_profiles:
        if p.name.lower() == name.lower():
            return p
    return None


def effective_credentials(node: Node, logon: dict[str, Any],
                          global_profiles: list[CredentialProfile]) -> Credentials:
    if logon.get("profile"):
        prof = find_profile(node, logon["profile"], global_profiles)
        if prof is None:
            return Credentials(profile=logon["profile"], missing_profile=True, prompt=True)
        return Credentials(prof.username, prof.domain, prof.password,
                           bool(logon.get("prompt")), prof.name)
    return Credentials(logon.get("username", ""), logon.get("domain", ""),
                       logon.get("password"), bool(logon.get("prompt")))
