"""Setting categories, their field schemas and built-in defaults.

Every node stores one record per category::

    {"inherit": True}                         # take the parent's value
    {"inherit": False, "port": 3390, ...}     # override

The schemas here drive both inheritance resolution and the generic
Properties-dialog forms, so adding a field means adding one FieldSpec.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: str  # str | int | bool | choice | secret | profile | text | paths
    default: Any = None
    choices: tuple[tuple[str, str], ...] = ()  # (value, label)
    minimum: int = 0
    maximum: int = 65535
    help: str = ""
    enabled_if: tuple[str, Any] | None = None  # (other_key, value) — UI only


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    fields: tuple[FieldSpec, ...]
    description: str = ""

    def defaults(self) -> dict[str, Any]:
        return {f.key: f.default for f in self.fields}

    def field(self, key: str) -> FieldSpec:
        for f in self.fields:
            if f.key == key:
                return f
        raise KeyError(key)


LOGON = Category(
    "logon",
    "Logon Credentials",
    (
        FieldSpec("profile", "Credential profile", "profile", "",
                  help="Use a named credential profile instead of the fields below."),
        FieldSpec("username", "User name", "str", ""),
        FieldSpec("domain", "Domain", "str", ""),
        FieldSpec("password", "Password", "secret", None),
        FieldSpec("prompt", "Always prompt for password", "bool", False),
    ),
    "Who to log on as.",
)

CONNECTION = Category(
    "connection",
    "Connection",
    (
        FieldSpec("port", "Port", "int", 3389, minimum=1, maximum=65535),
        FieldSpec("console", "Connect to console / admin session", "bool", False),
        FieldSpec("start_program", "Start program", "str", ""),
        FieldSpec("work_dir", "Working directory", "str", ""),
        FieldSpec("load_balance_info", "Load-balance info", "str", ""),
        FieldSpec("auto_reconnect", "Auto-reconnect if the connection drops", "bool", True),
        FieldSpec("timeout", "Connect timeout (seconds)", "int", 30, minimum=5, maximum=600),
    ),
)

GATEWAY = Category(
    "gateway",
    "Gateway",
    (
        FieldSpec("enabled", "Use an RD Gateway", "bool", False),
        FieldSpec("host", "Gateway host", "str", "", enabled_if=("enabled", True)),
        FieldSpec("port", "Gateway port", "int", 443, minimum=1, enabled_if=("enabled", True)),
        FieldSpec("use_logon", "Use logon credentials for the gateway", "bool", True,
                  enabled_if=("enabled", True)),
        FieldSpec("username", "Gateway user name", "str", "", enabled_if=("use_logon", False)),
        FieldSpec("domain", "Gateway domain", "str", "", enabled_if=("use_logon", False)),
        FieldSpec("password", "Gateway password", "secret", None, enabled_if=("use_logon", False)),
        FieldSpec("bypass_local", "Bypass gateway for local addresses", "bool", True,
                  enabled_if=("enabled", True)),
    ),
)

DISPLAY = Category(
    "display",
    "Display",
    (
        FieldSpec("mode", "Remote desktop size", "choice", "fit",
                  choices=(("fit", "Fit to client area"), ("fixed", "Fixed size"),
                           ("fullscreen", "Full screen"))),
        FieldSpec("width", "Width", "int", 1920, minimum=200, maximum=8192,
                  enabled_if=("mode", "fixed")),
        FieldSpec("height", "Height", "int", 1080, minimum=200, maximum=8192,
                  enabled_if=("mode", "fixed")),
        FieldSpec("color_depth", "Colour depth", "choice", "32",
                  choices=(("32", "32-bit"), ("24", "24-bit"), ("16", "16-bit"), ("15", "15-bit"),
                           ("8", "8-bit"))),
        FieldSpec("smart_sizing", "Scale the remote desktop to the window", "bool", True),
        FieldSpec("window", "Open session", "choice", "embedded",
                  choices=(("embedded", "Embedded in engavenRDP"), ("external", "In an external window"))),
        FieldSpec("multimon", "Span all monitors (external full screen only)", "bool", False),
        FieldSpec("monitors", "Monitor list (e.g. 0,1)", "str", ""),
    ),
)

RESOURCES = Category(
    "resources",
    "Local Resources",
    (
        FieldSpec("clipboard", "Share clipboard", "bool", True),
        FieldSpec("home_drive", "Share my home folder", "bool", False),
        FieldSpec("drives", "Extra folders to share (one per line, name=path or path)", "paths", ""),
        FieldSpec("printers", "Share printers", "bool", False),
        FieldSpec("audio", "Remote audio", "choice", "local",
                  choices=(("local", "Play on this computer"), ("remote", "Play on remote computer"),
                           ("off", "Do not play"))),
        FieldSpec("microphone", "Record from this computer (microphone)", "bool", False),
        FieldSpec("smartcards", "Smart cards", "bool", False),
    ),
)

SECURITY = Category(
    "security",
    "Security",
    (
        FieldSpec("protocol", "Security protocol", "choice", "auto",
                  choices=(("auto", "Negotiate (recommended)"), ("nla", "NLA only"),
                           ("tls", "TLS"), ("rdp", "Legacy RDP"))),
        FieldSpec("cert", "Server certificate", "choice", "tofu",
                  choices=(("tofu", "Trust on first use"), ("ignore", "Ignore (unsafe)"),
                           ("deny", "Reject unknown certificates"))),
        FieldSpec("restricted_admin", "Restricted admin mode", "bool", False),
    ),
)

EXPERIENCE = Category(
    "experience",
    "Experience",
    (
        FieldSpec("network", "Connection speed", "choice", "auto",
                  choices=(("auto", "Detect automatically"), ("lan", "LAN"),
                           ("broadband", "Broadband"), ("modem", "Modem"))),
        FieldSpec("wallpaper", "Desktop background", "bool", False),
        FieldSpec("themes", "Visual styles / themes", "bool", True),
        FieldSpec("font_smoothing", "Font smoothing", "bool", True),
        FieldSpec("gfx", "Use the graphics pipeline (GFX)", "bool", True),
    ),
)

CATEGORIES: tuple[Category, ...] = (LOGON, CONNECTION, GATEWAY, DISPLAY, RESOURCES, SECURITY, EXPERIENCE)
CATEGORY_BY_KEY = {c.key: c for c in CATEGORIES}


def default_records(inherit: bool = True) -> dict[str, dict[str, Any]]:
    return {c.key: ({"inherit": True} if inherit else {"inherit": False, **c.defaults()})
            for c in CATEGORIES}


@dataclass
class CredentialProfile:
    name: str
    username: str = ""
    domain: str = ""
    password: Any = None  # Secret | None
    id: str = field(default="")
