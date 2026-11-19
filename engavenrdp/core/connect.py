"""Turn a server node + resolved settings into a backend SessionSpec."""
from __future__ import annotations

from ..backend import CertChange, SessionSpec
from ..model.nodes import ServerNode
from ..model.resolve import Credentials, effective_credentials, resolve
from ..model.settings import CredentialProfile


def credentials_for(server: ServerNode, global_profiles: list[CredentialProfile]) -> Credentials:
    return effective_credentials(server, resolve(server)["logon"], global_profiles)


def build_spec(server: ServerNode, creds: Credentials, global_profiles: list[CredentialProfile],
               *, parent_window: int | None, width: int, height: int,
               fullscreen: bool = False, trusted_cert: CertChange | None = None) -> SessionSpec:
    r = resolve(server)
    gw = dict(r["gateway"])
    gu = gd = ""
    gp: str | None = None
    if gw.get("enabled"):
        if gw.get("use_logon", True):
            gu, gd, gp = creds.username, creds.domain, creds.password_text
        else:
            gu, gd = gw.get("username", ""), gw.get("domain", "")
            sec = gw.get("password")
            gp = sec.plain if sec is not None and getattr(sec, "plain", None) else None
    gw.pop("password", None)
    return SessionSpec(
        host=server.host or server.name,
        port=int(r["connection"].get("port") or 3389),
        title=f"{server.label} — engavenRDP",
        username=creds.username,
        domain=creds.domain,
        password=creds.password_text,
        connection=r["connection"],
        gateway=gw,
        gateway_username=gu,
        gateway_domain=gd,
        gateway_password=gp,
        display=r["display"],
        resources=r["resources"],
        security=r["security"],
        experience=r["experience"],
        parent_window=parent_window,
        width=width,
        height=height,
        fullscreen=fullscreen,
        trusted_cert=trusted_cert,
    )


def wants_external(server: ServerNode) -> bool:
    d = resolve(server)["display"]
    return d.get("window") == "external"
