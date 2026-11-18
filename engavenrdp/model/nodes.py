"""Tree nodes: files, groups, smart groups and servers."""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator

from .settings import CATEGORY_BY_KEY, CredentialProfile, default_records


def new_id() -> str:
    return uuid.uuid4().hex


class TreeError(ValueError):
    """Raised when an operation would violate the tree rules."""


@dataclass(eq=False)
class Node:
    name: str
    comment: str = ""
    id: str = field(default_factory=new_id)
    settings: dict[str, dict[str, Any]] = field(default_factory=default_records)
    parent: "ContainerNode | None" = field(default=None, repr=False)

    kind = "node"

    # -- tree navigation ---------------------------------------------------
    def ancestors(self) -> Iterator["ContainerNode"]:
        p = self.parent
        while p is not None:
            yield p
            p = p.parent

    @property
    def file(self) -> "FileNode | None":
        n: Node | None = self
        while n is not None and not isinstance(n, FileNode):
            n = n.parent
        return n

    def path(self) -> str:
        """Slash path from the file root (file name excluded)."""
        parts = [self.name]
        for a in self.ancestors():
            if isinstance(a, FileNode):
                break
            parts.append(a.name)
        return "/".join(reversed(parts))

    @property
    def label(self) -> str:
        return self.name

    # -- settings ----------------------------------------------------------
    def record(self, category: str) -> dict[str, Any]:
        return self.settings.setdefault(category, {"inherit": True})

    def set_record(self, category: str, values: dict[str, Any] | None) -> None:
        """``None`` => inherit; dict => override (missing keys filled with defaults)."""
        if values is None:
            self.settings[category] = {"inherit": True}
        else:
            rec = {"inherit": False, **CATEGORY_BY_KEY[category].defaults()}
            rec.update({k: v for k, v in values.items() if k != "inherit"})
            self.settings[category] = rec

    def inherits(self, category: str) -> bool:
        return bool(self.record(category).get("inherit", True))

    def mark_dirty(self) -> None:
        f = self.file
        if f is not None:
            f.dirty = True


@dataclass(eq=False)
class ServerNode(Node):
    host: str = ""
    display_name: str = ""
    kind = "server"

    @property
    def label(self) -> str:
        return self.display_name or self.name

    def matches(self, text: str) -> bool:
        t = text.lower()
        return any(t in (s or "").lower() for s in (self.name, self.host, self.display_name, self.comment))


@dataclass(eq=False)
class ContainerNode(Node):
    children: list[Node] = field(default_factory=list)
    expanded: bool = True
    kind = "container"

    # A container holds groups (incl. smart groups) OR servers, not both.
    def child_kind(self) -> str | None:
        for c in self.children:
            return "server" if isinstance(c, ServerNode) else "group"
        return None

    def can_accept(self, node: Node) -> bool:
        if isinstance(self, SmartGroupNode):
            return False
        if isinstance(node, FileNode):
            return False
        # no cycles
        if node is self or (isinstance(node, ContainerNode) and self in node.descendants()):
            return False
        want = "server" if isinstance(node, ServerNode) else "group"
        have = self.child_kind()
        if have is None:
            return True
        # if node is the only child (moving within same parent), still fine
        if have != want and not (len(self.children) == 1 and self.children[0] is node):
            return False
        return True

    def add(self, node: Node, index: int | None = None) -> Node:
        if not self.can_accept(node):
            what = "servers" if isinstance(node, ServerNode) else "groups"
            other = "groups" if what == "servers" else "servers"
            raise TreeError(f"“{self.name}” already contains {other}; it cannot also contain {what}.")
        if node.parent is not None:
            node.parent.remove(node)
        node.parent = self
        if index is None or index > len(self.children):
            self.children.append(node)
        else:
            self.children.insert(index, node)
        self.mark_dirty()
        return node

    def remove(self, node: Node) -> None:
        self.children.remove(node)
        node.parent = None
        self.mark_dirty()

    def descendants(self) -> Iterator[Node]:
        for c in self.children:
            yield c
            if isinstance(c, ContainerNode):
                yield from c.descendants()

    def servers(self, recursive: bool = True) -> list[ServerNode]:
        if not recursive:
            return [c for c in self.children if isinstance(c, ServerNode)]
        return [n for n in self.descendants() if isinstance(n, ServerNode)]

    def find_child(self, name: str) -> Node | None:
        for c in self.children:
            if c.name.lower() == name.lower():
                return c
        return None

    def find_path(self, path: str) -> Node | None:
        node: Node | None = self
        for part in [p for p in path.split("/") if p]:
            if not isinstance(node, ContainerNode):
                return None
            node = node.find_child(part)
            if node is None:
                return None
        return node


@dataclass(eq=False)
class GroupNode(ContainerNode):
    kind = "group"


@dataclass(eq=False)
class SmartGroupNode(ContainerNode):
    """A group whose (virtual) members are computed from rules."""

    rules: list[dict[str, str]] = field(default_factory=list)  # {field, op, value}
    match: str = "all"  # all | any
    kind = "smartgroup"

    def members(self) -> list[ServerNode]:
        from .smartgroups import evaluate

        root = self.file
        if root is None:
            return []
        return [s for s in root.servers() if evaluate(self.rules, self.match, s)]


@dataclass(eq=False)
class FileNode(ContainerNode):
    path_on_disk: str | None = None
    profiles: list[CredentialProfile] = field(default_factory=list)
    encryption: dict[str, Any] = field(default_factory=dict)  # header
    dirty: bool = False
    locked: bool = False          # credentials could not be decrypted
    lock_reason: str = ""
    vault: Any = field(default=None, repr=False)
    kind = "file"

    def __post_init__(self) -> None:
        # files never inherit: they are the top of the chain (defaults live in code)
        for key in list(self.settings):
            if self.settings[key].get("inherit", True):
                self.settings[key] = {"inherit": True}

    def profile(self, name: str) -> CredentialProfile | None:
        for p in self.profiles:
            if p.name.lower() == name.lower():
                return p
        return None


def clone(node: Node) -> Node:
    """Deep copy with fresh ids, detached from any parent."""
    parent = node.parent
    node.parent = None
    try:
        dup = copy.deepcopy(node)
    finally:
        node.parent = parent

    def refresh(n: Node) -> None:
        n.id = new_id()
        if isinstance(n, ContainerNode):
            for c in n.children:
                c.parent = n
                refresh(c)

    refresh(dup)
    return dup
