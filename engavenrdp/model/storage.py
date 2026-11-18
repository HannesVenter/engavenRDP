"""Load / save .erdp (JSON) files, and import Windows .rdg (XML) files."""
from __future__ import annotations

import json
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

from ..security.crypto import PREFIX, KeyUnavailable, Vault
from .nodes import ContainerNode, FileNode, GroupNode, Node, ServerNode, SmartGroupNode
from .secret import Secret
from .settings import CATEGORY_BY_KEY, CredentialProfile

FORMAT = "engavenrdp"
VERSION = 1
EXTENSION = ".erdp"


class FormatError(ValueError):
    pass


# ======================================================================
# serialisation helpers
# ======================================================================
def _enc_secret(s: Any, vault: Vault | None) -> str | None:
    if not isinstance(s, Secret) or not s:
        return None
    if s.plain is not None:
        if vault is None or not vault.unlocked:
            raise KeyUnavailable("needs-vault")
        # Re-use the existing ciphertext when the value is unchanged (stable diffs)
        if s.cipher:
            try:
                if vault.decrypt(s.cipher) == s.plain:
                    return s.cipher
            except Exception:
                pass
        s.cipher = vault.encrypt(s.plain)
        return s.cipher
    return s.cipher  # locked value preserved verbatim


def _dec_secret(value: Any) -> Secret | None:
    if not value:
        return None
    if isinstance(value, str) and value.startswith(PREFIX):
        return Secret(cipher=value)
    # plaintext found on disk (hand-edited file): accept, will be encrypted on save
    return Secret(plain=str(value))


def _settings_out(node: Node, vault: Vault | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for cat, rec in node.settings.items():
        if rec.get("inherit", True) or cat not in CATEGORY_BY_KEY:
            continue
        spec = CATEGORY_BY_KEY[cat]
        clean: dict[str, Any] = {}
        for f in spec.fields:
            v = rec.get(f.key, f.default)
            if f.kind == "secret":
                v = _enc_secret(v, vault)
            clean[f.key] = v
        out[cat] = clean
    return out


def _settings_in(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {k: {"inherit": True} for k in CATEGORY_BY_KEY}
    for cat, rec in (data or {}).items():
        if cat not in CATEGORY_BY_KEY or not isinstance(rec, dict):
            continue
        if rec.get("inherit", False):
            continue
        spec = CATEGORY_BY_KEY[cat]
        vals = {"inherit": False, **spec.defaults()}
        for f in spec.fields:
            if f.key in rec:
                vals[f.key] = _dec_secret(rec[f.key]) if f.kind == "secret" else rec[f.key]
        out[cat] = vals
    return out


def _node_out(node: Node, vault: Vault | None) -> dict[str, Any]:
    d: dict[str, Any] = {"type": node.kind, "id": node.id, "name": node.name}
    if node.comment:
        d["comment"] = node.comment
    if isinstance(node, ServerNode):
        d["host"] = node.host
        if node.display_name:
            d["display_name"] = node.display_name
    if isinstance(node, SmartGroupNode):
        d["match"] = node.match
        d["rules"] = node.rules
    elif isinstance(node, ContainerNode):
        d["expanded"] = node.expanded
    s = _settings_out(node, vault)
    if s:
        d["settings"] = s
    if isinstance(node, ContainerNode) and not isinstance(node, SmartGroupNode):
        d["children"] = [_node_out(c, vault) for c in node.children]
    return d


def _node_in(d: dict[str, Any]) -> Node:
    t = d.get("type", "group")
    common = dict(name=str(d.get("name", "")), comment=str(d.get("comment", "")),
                  settings=_settings_in(d.get("settings", {})))
    if d.get("id"):
        common["id"] = str(d["id"])
    if t == "server":
        return ServerNode(host=str(d.get("host") or d.get("name", "")),
                          display_name=str(d.get("display_name", "")), **common)
    if t == "smartgroup":
        return SmartGroupNode(rules=list(d.get("rules", [])), match=d.get("match", "all"), **common)
    g = GroupNode(expanded=bool(d.get("expanded", True)), **common)
    for c in d.get("children", []):
        child = _node_in(c)
        child.parent = g
        g.children.append(child)
    return g


def profiles_out(profiles: list[CredentialProfile], vault: Vault | None) -> list[dict[str, Any]]:
    return [{"id": p.id, "name": p.name, "username": p.username, "domain": p.domain,
             "password": _enc_secret(p.password, vault)} for p in profiles]


def profiles_in(data: list[dict[str, Any]]) -> list[CredentialProfile]:
    return [CredentialProfile(name=p.get("name", ""), username=p.get("username", ""),
                              domain=p.get("domain", ""), password=_dec_secret(p.get("password")),
                              id=p.get("id", "")) for p in data or []]


def iter_secrets(file: FileNode):
    for p in file.profiles:
        if isinstance(p.password, Secret):
            yield p.password
    for n in [file, *file.descendants()]:
        for cat, rec in n.settings.items():
            for f in CATEGORY_BY_KEY[cat].fields if cat in CATEGORY_BY_KEY else ():
                v = rec.get(f.key)
                if f.kind == "secret" and isinstance(v, Secret):
                    yield v


def has_plain_secrets(file: FileNode) -> bool:
    return any(s.plain is not None for s in iter_secrets(file))


def has_any_secrets(file: FileNode) -> bool:
    return any(bool(s) for s in iter_secrets(file))


# ======================================================================
# public API
# ======================================================================
def to_dict(file: FileNode) -> dict[str, Any]:
    vault = file.vault
    d = {
        "format": FORMAT,
        "version": VERSION,
        "id": file.id,
        "name": file.name,
        "comment": file.comment,
        "expanded": file.expanded,
    }
    if file.encryption:
        d["encryption"] = file.encryption
    s = _settings_out(file, vault)
    if s:
        d["settings"] = s
    d["profiles"] = profiles_out(file.profiles, vault)
    d["children"] = [_node_out(c, vault) for c in file.children]
    return d


def from_dict(d: dict[str, Any], path: str | None = None) -> FileNode:
    if d.get("format") != FORMAT:
        raise FormatError("Not an engavenRDP file")
    try:
        version = int(d.get("version", VERSION))
    except (TypeError, ValueError):
        raise FormatError("Invalid file version") from None
    if version > VERSION:
        # Saving would silently drop whatever the newer format added.
        raise FormatError(f"This file was saved by a newer version of engavenRDP (file format {version}; "
                          f"this version reads up to {VERSION}). Update engavenRDP to open it.")
    f = FileNode(name=d.get("name") or (Path(path).stem if path else "Untitled"),
                 comment=d.get("comment", ""), settings=_settings_in(d.get("settings", {})),
                 path_on_disk=path, encryption=d.get("encryption", {}) or {},
                 profiles=profiles_in(d.get("profiles", [])), expanded=d.get("expanded", True))
    if d.get("id"):
        f.id = d["id"]
    for c in d.get("children", []):
        child = _node_in(c)
        child.parent = f
        f.children.append(child)
    f.dirty = False
    return f


def unlock(file: FileNode, vault: Vault) -> list[str]:
    """Decrypt every secret in the file with ``vault``. Returns error messages."""
    errors = []
    for s in iter_secrets(file):
        if s.cipher and s.plain is None:
            try:
                s.plain = vault.decrypt(s.cipher)
            except Exception as e:  # pragma: no cover - defensive
                errors.append(str(e))
    file.vault = vault
    file.locked = bool(errors)
    file.lock_reason = errors[0] if errors else ""
    return errors


def load(path: str | os.PathLike, passphrase_cb: Callable[[str], str | None] | None = None) -> FileNode:
    """Load a file. ``passphrase_cb(message)`` is asked for a passphrase if needed;
    returning None leaves the file locked (still usable)."""
    path = str(path)
    if path.lower().endswith(".rdg"):
        return import_rdg(path)
    with open(path, "r", encoding="utf-8") as fh:
        try:
            d = json.load(fh)
        except json.JSONDecodeError as e:
            raise FormatError(f"Invalid JSON: {e}") from e
    f = from_dict(d, path)
    if not f.encryption:
        return f
    msg = "Enter the passphrase for the credentials in this file."
    while True:
        try:
            pw = None
            if f.encryption.get("mode") == "passphrase":
                if passphrase_cb is None:
                    raise KeyUnavailable("A passphrase is required to decrypt the credentials.")
                pw = passphrase_cb(msg)
                if pw is None:
                    raise KeyUnavailable("Passphrase not entered — credentials are locked.")
            unlock(f, Vault.open(f.encryption, pw))
            break
        except KeyUnavailable as e:
            from ..security.crypto import WrongPassphrase

            if isinstance(e, WrongPassphrase) and passphrase_cb is not None:
                msg = "Wrong passphrase — try again."
                continue
            f.locked = has_any_secrets(f)
            f.lock_reason = str(e)
            break
    return f


def save(file: FileNode, path: str | None = None) -> None:
    path = path or file.path_on_disk
    if not path:
        raise ValueError("No path to save to")
    data = to_dict(file)  # may raise KeyUnavailable("needs-vault")
    if file.vault is not None and file.vault.unlocked:
        data["encryption"] = file.encryption = file.vault.header
    text = json.dumps(data, indent=2, ensure_ascii=False)
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".erdp-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    file.path_on_disk = path
    file.dirty = False


def export_group(node: ContainerNode) -> FileNode:
    """Wrap a copy of ``node`` in a new FileNode (secrets dropped unless plaintext available)."""
    from .nodes import clone

    f = FileNode(name=node.name)
    dup = clone(node)
    if isinstance(dup, FileNode):  # pragma: no cover
        return dup
    for n in [dup, *(dup.descendants() if isinstance(dup, ContainerNode) else [])]:
        for rec in n.settings.values():
            for k, v in list(rec.items()):
                if isinstance(v, Secret) and v.plain is None:
                    rec[k] = None
    f.add(dup)
    # carry effective inherited settings onto the exported top node
    from .resolve import resolve_category

    for cat in CATEGORY_BY_KEY:
        if dup.inherits(cat):
            vals, src = resolve_category(node, cat)
            if src is not None:
                dup.set_record(cat, vals)
    return f


# ======================================================================
# Windows .rdg import
# ======================================================================
def _txt(el: ET.Element | None, tag: str, default: str = "") -> str:
    if el is None:
        return default
    c = el.find(tag)
    return (c.text or "").strip() if c is not None and c.text else default


def _bool(s: str, default: bool = False) -> bool:
    if not s:
        return default
    return s.strip().lower() in ("true", "1", "yes")


def _rdg_settings(el: ET.Element, node: Node, notes: list[str]) -> None:
    lc = el.find("logonCredentials")
    if lc is not None and lc.get("inherit", "FromParent") == "None":
        prof = _txt(lc, "profileName")
        vals: dict[str, Any] = {"username": _txt(lc, "userName"), "domain": _txt(lc, "domain")}
        if prof and prof.lower() not in ("custom", ""):
            vals["profile"] = prof
        if _txt(lc, "password"):
            notes.append("passwords")
        node.set_record("logon", vals)
    cs = el.find("connectionSettings")
    if cs is not None and cs.get("inherit", "FromParent") == "None":
        node.set_record("connection", {
            "console": _bool(_txt(cs, "connectToConsole")),
            "start_program": _txt(cs, "startProgram"),
            "work_dir": _txt(cs, "workingDir"),
            "port": int(_txt(cs, "port", "3389") or 3389),
            "load_balance_info": _txt(cs, "loadBalanceInfo"),
        })
    gs = el.find("gatewaySettings")
    if gs is not None and gs.get("inherit", "FromParent") == "None":
        node.set_record("gateway", {
            "enabled": _bool(_txt(gs, "enabled")),
            "host": _txt(gs, "hostName"),
            "bypass_local": _bool(_txt(gs, "localBypass"), True),
            "use_logon": _bool(_txt(gs, "credSharing"), True),
            "username": _txt(gs, "userName"),
            "domain": _txt(gs, "domain"),
        })
    rd = el.find("remoteDesktop")
    if rd is not None and rd.get("inherit", "FromParent") == "None":
        vals = {"color_depth": _txt(rd, "colorDepth", "32") or "32"}
        if _bool(_txt(rd, "fullScreen")):
            vals["mode"] = "fullscreen"
        elif not _bool(_txt(rd, "sameSizeAsClientArea"), True):
            size = _txt(rd, "size", "1024 x 768").lower().replace(" ", "")
            try:
                w, h = size.split("x")
                vals.update(mode="fixed", width=int(w), height=int(h))
            except ValueError:
                pass
        node.set_record("display", vals)
    lr = el.find("localResources")
    if lr is not None and lr.get("inherit", "FromParent") == "None":
        audio = _txt(lr, "audioRedirection", "Client").lower()
        node.set_record("resources", {
            "audio": {"client": "local", "remote": "remote", "nosound": "off"}.get(audio, "local"),
            "clipboard": _bool(_txt(lr, "redirectClipboard"), True),
            "home_drive": _bool(_txt(lr, "redirectDrives")),
            "printers": _bool(_txt(lr, "redirectPrinters")),
            "smartcards": _bool(_txt(lr, "redirectSmartCards")),
        })
    ss = el.find("securitySettings")
    if ss is not None and ss.get("inherit", "FromParent") == "None":
        auth = _txt(ss, "authentication", "Warn").lower()
        node.set_record("security", {"cert": "ignore" if auth == "none" else "tofu"})


def _rdg_name(el: ET.Element) -> tuple[str, str, str]:
    props = el.find("properties")
    src = props if props is not None else el
    return _txt(src, "name"), _txt(src, "displayName"), _txt(src, "comment")


def _rdg_children(el: ET.Element, parent: ContainerNode, notes: list[str]) -> None:
    for child in el:
        if child.tag == "group":
            name, _, comment = _rdg_name(child)
            g = GroupNode(name=name or "Group", comment=comment)
            props = child.find("properties")
            g.expanded = _bool(_txt(props, "expanded"), True)
            _rdg_settings(child, g, notes)
            parent.children.append(g)
            g.parent = parent
            _rdg_children(child, g, notes)
        elif child.tag == "server":
            host, display, comment = _rdg_name(child)
            s = ServerNode(name=host, host=host, display_name=display if display != host else "",
                           comment=comment)
            _rdg_settings(child, s, notes)
            parent.children.append(s)
            s.parent = parent
        elif child.tag == "smartGroup":
            name, _, comment = _rdg_name(child)
            rg = child.find("ruleGroup")
            rules = []
            if rg is not None:
                for r in rg.findall("rule"):
                    prop = _txt(r, "property").lower()
                    field = {"displayname": "display_name", "comment": "comment",
                             "parent": "group"}.get(prop, "name")
                    op = _txt(r, "operator").lower()
                    rules.append({"field": field,
                                  "op": "not_contains" if "not" in op else "contains",
                                  "value": _txt(r, "value")})
            sg = SmartGroupNode(name=name or "Smart group", comment=comment, rules=rules,
                                match="any" if rg is not None and rg.get("operator", "All") == "Any" else "all")
            parent.children.append(sg)
            sg.parent = parent


def import_rdg(path: str) -> FileNode:
    tree = ET.parse(path)
    root = tree.getroot()
    fel = root.find("file") if root.tag == "RDCMan" else root
    if fel is None:
        raise FormatError("No <file> element found in .rdg")
    name, _, comment = _rdg_name(fel)
    f = FileNode(name=name or Path(path).stem, comment=comment)
    notes: list[str] = []
    _rdg_settings(fel, f, notes)
    for cp in fel.findall("credentialsProfiles/credentialsProfile"):
        f.profiles.append(CredentialProfile(name=_txt(cp, "profileName"), username=_txt(cp, "userName"),
                                            domain=_txt(cp, "domain")))
        if _txt(cp, "password"):
            notes.append("passwords")
    _rdg_children(fel, f, notes)
    f.path_on_disk = None  # must "save as" .erdp
    f.dirty = True
    f.lock_reason = ("Imported from a Windows .rdg file. Windows-encrypted (DPAPI) passwords cannot be "
                     "decrypted on Linux and were not imported." if "passwords" in notes else "")
    f.import_source = path  # type: ignore[attr-defined]
    return f
