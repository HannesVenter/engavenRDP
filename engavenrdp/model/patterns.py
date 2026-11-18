"""Bulk-add host pattern expansion.

Supported syntax (combinable, cartesian product)::

    web[01-10]          -> web01 .. web10   (zero padding kept from the start bound)
    web[1-3,7,9-10]     -> web1 web2 web3 web7 web9 web10
    node[a-d]           -> nodea .. noded
    {prod,stage}-db     -> prod-db stage-db
    10.0.0.[1-5]        -> 10.0.0.1 .. 10.0.0.5

Multiple patterns may be separated by whitespace, commas outside brackets/braces,
semicolons or new lines.
"""
from __future__ import annotations

import re

MAX_HOSTS = 2000


class PatternError(ValueError):
    pass


def _expand_range(body: str) -> list[str]:
    out: list[str] = []
    for part in body.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)-(\d+)", part)
        if m:
            a, b = m.group(1), m.group(2)
            width = len(a) if a.startswith("0") and len(a) > 1 else 0
            width = max(width, len(a) if len(a) == len(b) and a.startswith("0") else 0)
            lo, hi = int(a), int(b)
            step = 1 if hi >= lo else -1
            if abs(hi - lo) + 1 > MAX_HOSTS:
                raise PatternError(f"Range [{part}] is too large")
            out.extend(str(i).zfill(width) for i in range(lo, hi + step, step))
            continue
        m = re.fullmatch(r"([A-Za-z])-([A-Za-z])", part)
        if m:
            lo, hi = ord(m.group(1)), ord(m.group(2))
            step = 1 if hi >= lo else -1
            out.extend(chr(c) for c in range(lo, hi + step, step))
            continue
        if re.fullmatch(r"[\w.-]+", part):
            out.append(part)
            continue
        raise PatternError(f"Cannot understand range “[{part}]”")
    return out


def expand(pattern: str) -> list[str]:
    """Expand a single pattern."""
    pattern = pattern.strip()
    if not pattern:
        return []
    # find first bracket or brace group
    m = re.search(r"\[([^\[\]]*)\]|\{([^{}]*)\}", pattern)
    if not m:
        if re.search(r"[\[\]{}]", pattern):
            raise PatternError(f"Unbalanced brackets in “{pattern}”")
        return [pattern]
    prefix, suffix = pattern[: m.start()], pattern[m.end():]
    options = _expand_range(m.group(1)) if m.group(1) is not None else [o.strip() for o in m.group(2).split(",")]
    results: list[str] = []
    rest = expand(suffix) if suffix else [""]
    for o in options:
        for r in rest:
            results.append(f"{prefix}{o}{r}")
            if len(results) > MAX_HOSTS:
                raise PatternError(f"Pattern expands to more than {MAX_HOSTS} hosts")
    # prefix may not contain groups (we matched the first), so done
    return results


def split_patterns(text: str) -> list[str]:
    """Split on whitespace / ; / newline, and on commas that are not inside [] or {}."""
    parts, buf, depth = [], [], 0
    for ch in text:
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth = max(0, depth - 1)
        if (ch in " \t\r\n;" or (ch == "," and depth == 0)) and depth == 0:
            if buf:
                parts.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def expand_many(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for p in split_patterns(text):
        for h in expand(p):
            seen.setdefault(h, None)
            if len(seen) > MAX_HOSTS:
                raise PatternError(f"More than {MAX_HOSTS} hosts")
    return list(seen)


def parse_host_list(text: str) -> list[tuple[str, str]]:
    """Parse an import file: one host per line, optional display name after whitespace,
    '#' comments. Lines may themselves be patterns."""
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        host, _, display = line.partition(" ")
        if "\t" in host:
            host, _, display = line.partition("\t")
        for h in expand(host):
            out.append((h, display.strip()))
    return out
