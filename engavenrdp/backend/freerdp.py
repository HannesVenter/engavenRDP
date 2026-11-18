"""FreeRDP (xfreerdp 2.x / xfreerdp3 3.x) backend."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from functools import lru_cache

from .base import BackendInfo, Command, CommandError, RdpBackend, SessionSpec

CANDIDATES = ("xfreerdp3", "xfreerdp")

# FreeRDP client exit codes (freerdp/client.h / xf_client.h). Shared by 2.x and 3.x.
EXIT_REASONS = {
    0: "Session ended",
    1: "Disconnected by the server",
    2: "Logged off",
    3: "Idle timeout",
    4: "Logon timeout",
    5: "Connection replaced by another session",
    6: "Server out of memory",
    7: "Server denied the connection",
    8: "Server denied the connection (FIPS)",
    9: "User lacks privileges",
    10: "Fresh credentials required",
    11: "Disconnected by user",
    12: "Disconnected by user",
    16: "Licensing error",
    17: "Licensing error",
    18: "Licensing: no license server",
    # FreeRDP returns (command-line error & 0xff) = 19..24 for bad options, which
    # overlaps the licensing codes; bad options are far more common in practice.
    19: "Invalid FreeRDP option (see the connection log) or licensing error",
    20: "Invalid FreeRDP option (see the connection log) or licensing error",
    21: "Invalid FreeRDP option (see the connection log) or licensing error",
    22: "Invalid FreeRDP option (see the connection log) or licensing error",
    23: "Invalid FreeRDP option (see the connection log) or licensing error",
    24: "Invalid FreeRDP option (see the connection log) or licensing error",
    25: "Licensing: bad client encryption",
    26: "Licensing: can't upgrade license",
    27: "Licensing: no remote connections",
    128: "Invalid command-line arguments",
    129: "Client out of memory",
    130: "Protocol error",
    131: "Connection failed",
    132: "Authentication failure — check user name / password",
    133: "Security negotiation failed (try a different security protocol)",
    134: "Logon failure",
    135: "Account locked out",
    136: "Pre-connect failed",
    137: "Connection failed (undefined error)",
    138: "Post-connect failed",
    139: "DNS error",
    140: "Host name not found",
    141: "Connection failed",
    142: "MCS connect-initial error",
    143: "TLS connection failed",
    144: "Insufficient privileges",
    145: "Connection cancelled",
    147: "Could not reach the server (transport failed)",
    148: "Password expired",
    149: "Password must be changed",
    150: "Kerberos error",
    151: "No or missing credentials",
    255: "Unknown error",
}


# Exit codes that mean "the user name / password was not accepted".
AUTH_FAILURE_CODES = frozenset({132, 134, 135, 148, 149, 151})


def _gateway_part(key: str, value: str, label: str) -> str:
    """One ``key:value`` element of FreeRDP 3's comma-separated ``/gateway:`` list.

    FreeRDP splits the list on commas unless the whole element is wrapped in
    double quotes. It has no way to carry a quote character, and a trailing
    backslash would swallow the separator that follows, so those are refused
    rather than silently sent wrong.
    """
    if '"' in value or "'" in value or value.endswith("\\"):
        raise CommandError(f"The {label} contains a quote character or ends with a backslash, "
                           "which FreeRDP 3 cannot pass to an RD Gateway. Change it, or connect "
                           "without the gateway.")
    part = f"{key}:{value}"
    return f'"{part}"' if "," in value else part


@lru_cache(maxsize=8)
def _probe(command: tuple[str, ...]) -> tuple[str, int]:
    try:
        out = subprocess.run([*command, "--version"], capture_output=True, text=True, timeout=10)
        text = out.stdout + out.stderr
    except Exception:
        return "", 0
    m = re.search(r"version\s+(\d+)\.(\d+)\.(\d+)", text)
    if not m:
        return text.strip()[:60], 0
    return ".".join(m.groups()), int(m.group(1))


class FreeRdpBackend(RdpBackend):
    name = "freerdp"

    def __init__(self, command: str | None = None) -> None:
        """``command`` overrides auto-detection; may contain arguments (e.g. 'flatpak run ...')."""
        self._override = shlex.split(command) if command else None
        self._major_override: int | None = None

    # ------------------------------------------------------------------ info
    def _binary(self) -> list[str] | None:
        if self._override:
            return self._override
        for c in CANDIDATES:
            p = shutil.which(c)
            if p:
                return [p]
        return None

    def major(self) -> int:
        if self._major_override:
            return self._major_override
        b = self._binary()
        if not b:
            return 3
        _, major = _probe(tuple(b))
        if major:
            return major
        # Version unknown (a wrapper that doesn't answer --version). Assume 3, which
        # keeps passwords off the command line, unless it is plainly the 2.x binary.
        return 2 if os.path.basename(b[0]) == "xfreerdp" else 3

    def info(self) -> BackendInfo:
        b = self._binary()
        if not b:
            return BackendInfo(
                self.name, False,
                message="FreeRDP was not found. Install it: Ubuntu/Debian “sudo apt install freerdp3-x11” "
                        "(or freerdp2-x11), Fedora “sudo dnf install freerdp”, Arch “sudo pacman -S freerdp”.")
        version, major = _probe(tuple(b))
        return BackendInfo(self.name, True, version or "unknown", " ".join(b),
                           message=f"FreeRDP {version}", can_embed=True)

    # ------------------------------------------------------------- command
    def build_command(self, spec: SessionSpec) -> Command:
        v3 = self.major() >= 3
        args: list[str] = []
        redacted: list[str] = []  # same arguments, safe to show in logs
        has_secret = False

        def add(a: str, shown: str | None = None) -> None:
            nonlocal has_secret
            has_secret = has_secret or shown is not None
            args.append(a)
            redacted.append(a if shown is None else shown)

        port = int(spec.port or 3389)
        add(f"/v:{spec.host}" + (f":{port}" if port != 3389 else ""))
        if spec.username:
            add(f"/u:{spec.username}")
        if spec.domain:
            add(f"/d:{spec.domain}")
        if spec.password is not None:
            add(f"/p:{spec.password}", "/p:********")

        title = spec.title or spec.host
        add(f"/title:{title}" if v3 else f"/t:{title}")

        # -- connection
        c = spec.connection
        if c.get("console"):
            add("/admin")
        if c.get("start_program"):
            add(f"/shell:{c['start_program']}")
        if c.get("work_dir"):
            add(f"/shell-dir:{c['work_dir']}")
        if c.get("load_balance_info"):
            add(f"/load-balance-info:{c['load_balance_info']}")
        if c.get("auto_reconnect", True):
            add("+auto-reconnect")
            add("/auto-reconnect-max-retries:10")
        if c.get("timeout"):
            add(f"/timeout:{int(c['timeout']) * 1000}")

        # -- gateway
        g = spec.gateway
        if g.get("enabled") and g.get("host"):
            ghost = g["host"] + (f":{g.get('port')}" if g.get("port") and int(g["port"]) != 443 else "")
            usage = "detect" if g.get("bypass_local", True) else "direct"
            gu, gd, gp = spec.gateway_username, spec.gateway_domain, spec.gateway_password
            if v3:
                parts = [_gateway_part("g", ghost, "gateway host")]
                if gu:
                    parts.append(_gateway_part("u", gu, "gateway user name"))
                if gd:
                    parts.append(_gateway_part("d", gd, "gateway domain"))
                tail = [f"usage-method:{usage}"]
                if gp:
                    add("/gateway:" + ",".join(parts + [_gateway_part("p", gp, "gateway password")] + tail),
                        "/gateway:" + ",".join(parts + ["p:********"] + tail))
                else:
                    add("/gateway:" + ",".join(parts + tail))
            else:
                add(f"/g:{ghost}")
                if gu:
                    add(f"/gu:{gu}")
                if gd:
                    add(f"/gd:{gd}")
                if gp:
                    add(f"/gp:{gp}", "/gp:********")
                add(f"/gateway-usage-method:{usage}")

        # -- display
        d = spec.display
        external = d.get("window") == "external" or spec.parent_window is None
        if spec.fullscreen or (external and d.get("mode") == "fullscreen"):
            add("/f")
            if d.get("multimon"):
                add("/multimon")
            if d.get("monitors"):
                add(f"/monitors:{d['monitors']}")
        else:
            if d.get("mode") == "fixed":
                add(f"/size:{int(d.get('width', 1920))}x{int(d.get('height', 1080))}")
            else:
                add(f"/size:{max(200, spec.width)}x{max(200, spec.height)}")
            if d.get("smart_sizing", True) or spec.parent_window is not None:
                add("/smart-sizing")
            elif external:
                add("/dynamic-resolution")
        add(f"/bpp:{d.get('color_depth', '32')}")
        if spec.parent_window is not None and not external:
            add(f"/parent-window:{spec.parent_window}")

        # -- local resources
        r = spec.resources
        add("+clipboard" if r.get("clipboard", True) else "-clipboard")
        if r.get("home_drive"):
            add("+home-drive")
        for i, line in enumerate((r.get("drives") or "").splitlines()):
            line = line.strip()
            if not line:
                continue
            name, sep, path = line.partition("=")
            if not sep:
                path, name = line, os.path.basename(line.rstrip("/")) or f"drive{i}"
            add(f"/drive:{name.strip()},{os.path.expanduser(path.strip())}")
        if r.get("printers"):
            add("/printer")
        audio = r.get("audio", "local")
        if audio == "local":
            add("/sound")
        elif audio == "remote":
            add("/audio-mode:1")
        else:
            add("/audio-mode:2")
        if r.get("microphone"):
            add("/microphone")
        if r.get("smartcards"):
            add("/smartcard")

        # -- security
        s = spec.security
        if s.get("protocol", "auto") != "auto":
            add(f"/sec:{s['protocol']}")
        cert = s.get("cert", "tofu")
        if v3:
            add(f"/cert:{cert}")
        elif cert in ("tofu", "ignore"):
            add(f"/cert-{cert}")
        if s.get("restricted_admin"):
            add("/restricted-admin")

        # -- experience
        e = spec.experience
        add(f"/network:{e.get('network', 'auto')}")
        add("+wallpaper" if e.get("wallpaper") else "-wallpaper")
        add("+themes" if e.get("themes", True) else "-themes")
        add("+fonts" if e.get("font_smoothing", True) else "-fonts")
        if e.get("gfx", True):
            add("/gfx")

        binary = self._binary() or ["xfreerdp3" if v3 else "xfreerdp"]

        if v3 and has_secret:
            # Keep secrets out of the process list: FreeRDP 3 can read its whole
            # command line from stdin, one argument per line.
            return Command(binary + ["/args-from:stdin"], ("\n".join(args) + "\n").encode(),
                           binary + redacted)
        # FreeRDP 2 masks /p: in its own argv immediately after parsing.
        return Command(binary + args, None, binary + redacted)

    # ---------------------------------------------------------------- exits
    def exit_reason(self, code: int) -> str:
        return EXIT_REASONS.get(code, f"FreeRDP exited with code {code}")

    def is_user_disconnect(self, code: int) -> bool:
        return code in (0, 2, 11, 12)

    def is_auth_failure(self, code: int) -> bool:
        return code in AUTH_FAILURE_CODES
