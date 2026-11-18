"""Smart group rule evaluation."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .nodes import ServerNode

FIELDS = (
    ("name", "Server name"),
    ("display_name", "Display name"),
    ("host", "Host"),
    ("comment", "Comment"),
    ("group", "Parent group path"),
)
OPERATORS = (
    ("contains", "contains"),
    ("not_contains", "does not contain"),
    ("equals", "equals"),
    ("starts", "starts with"),
    ("ends", "ends with"),
    ("regex", "matches regex"),
)


def field_value(server: "ServerNode", field: str) -> str:
    if field == "group":
        return server.parent.path() if server.parent is not None and server.parent.parent is not None else ""
    if field == "display_name":
        return server.label  # falls back to the name
    return str(getattr(server, field, "") or "")


def rule_matches(rule: dict[str, str], server: "ServerNode") -> bool:
    val = field_value(server, rule.get("field", "name")).lower()
    target = (rule.get("value") or "").lower()
    op = rule.get("op", "contains")
    if op == "contains":
        return target in val
    if op == "not_contains":
        return target not in val
    if op == "equals":
        return val == target
    if op == "starts":
        return val.startswith(target)
    if op == "ends":
        return val.endswith(target)
    if op == "regex":
        try:
            return re.search(rule.get("value") or "", field_value(server, rule.get("field", "name")),
                             re.IGNORECASE) is not None
        except re.error:
            return False
    return False


def evaluate(rules: list[dict[str, str]], match: str, server: "ServerNode") -> bool:
    if not rules:
        return False
    results = (rule_matches(r, server) for r in rules)
    return any(results) if match == "any" else all(results)
