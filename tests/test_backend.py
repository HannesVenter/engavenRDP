import ctypes
import glob

import pytest

from engavenrdp.backend.base import CommandError
from engavenrdp.backend.freerdp import FreeRdpBackend, _gateway_part
from engavenrdp.core.connect import build_spec, credentials_for
from engavenrdp.model.nodes import FileNode, GroupNode, ServerNode
from engavenrdp.model.secret import Secret


def make(major):
    b = FreeRdpBackend()
    b._major_override = major
    return b


def spec():
    f = FileNode(name="f")
    g = f.add(GroupNode(name="g"))
    s = g.add(ServerNode(name="srv", host="srv.lab"))
    g.set_record("logon", {"username": "admin", "domain": "LAB", "password": Secret(plain="p,w")})
    g.set_record("connection", {"port": 3390, "console": True})
    g.set_record("gateway", {"enabled": True, "host": "gw.lab"})
    g.set_record("resources", {"drives": "share=/tmp\n/home/x/data", "audio": "off"})
    creds = credentials_for(s, [])
    return build_spec(s, creds, [], parent_window=1234, width=1000, height=700)


def test_v3_stdin_secrets():
    cmd = make(3).build_command(spec())
    assert cmd.argv[-1] == "/args-from:stdin"
    lines = cmd.stdin.decode().splitlines()
    assert "/v:srv.lab:3390" in lines and "/u:admin" in lines and "/p:p,w" in lines
    assert "/admin" in lines and "/parent-window:1234" in lines and "/smart-sizing" in lines
    assert "/size:1000x700" in lines and "/cert:tofu" in lines and "/audio-mode:2" in lines
    assert "/drive:share,/tmp" in lines and "/drive:data,/home/x/data" in lines
    # a comma in the gateway password must not split FreeRDP's comma-separated list
    assert '/gateway:g:gw.lab,u:admin,d:LAB,"p:p,w",usage-method:detect' in lines
    assert "/gateway:g:gw.lab,u:admin,d:LAB,p:********,usage-method:detect" in cmd.redacted
    assert "p,w" not in " ".join(cmd.redacted)


def test_v2_args():
    cmd = make(2).build_command(spec())
    assert "/t:srv — engavenRDP" in cmd.argv and "/g:gw.lab" in cmd.argv and "/gp:p,w" in cmd.argv
    assert "/cert-tofu" in cmd.argv and cmd.stdin is None
    assert "/p:********" in cmd.redacted


def test_gateway_values_freerdp_cannot_carry_are_refused():
    for bad in ('pa"ss', "pa'ss", "pass\\"):
        sp = spec()
        sp.gateway_password = bad
        with pytest.raises(CommandError):
            make(3).build_command(sp)
        make(2).build_command(sp)  # FreeRDP 2 takes it as a separate argument


def test_gateway_quoting_matches_freerdp_parser():
    """Feed our /gateway: list to FreeRDP's own splitter, if it is installed."""
    libs = sorted(glob.glob("/usr/lib/*/libwinpr3.so.3")) + sorted(glob.glob("/usr/lib*/libwinpr3.so.3"))
    if not libs:
        pytest.skip("libwinpr3 not installed")
    parse = ctypes.CDLL(libs[0]).CommandLineParseCommaSeparatedValuesEx
    parse.restype = ctypes.POINTER(ctypes.c_char_p)
    parse.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_size_t)]
    for pw in ("plain", "p,w", "a,b,c", "back\\slash,x", "sp ace;#,"):
        parts = ["g:gw.lab", _gateway_part("u", "us,er", "user"), _gateway_part("p", pw, "password"),
                 "usage-method:detect"]
        n = ctypes.c_size_t(0)
        out = parse(b"gateway", ",".join(parts).encode(), ctypes.byref(n))
        got = [out[i].decode() for i in range(n.value)]
        assert got == ["gateway", "g:gw.lab", "u:us,er", f"p:{pw}", "usage-method:detect"]


def test_auth_failure_codes():
    b = make(3)
    assert b.is_auth_failure(132) and not b.is_auth_failure(131) and not b.is_auth_failure(0)


def test_custom_wrapper_command_is_not_assumed_to_be_freerdp_2(tmp_path):
    sh = tmp_path / "wrap"
    sh.write_text("#!/bin/sh\necho 'This is FreeRDP version 3.5.1 (n/a)'\n")
    sh.chmod(0o755)
    b = FreeRdpBackend(f"{sh} run some.app")  # e.g. "flatpak run com.freerdp.FreeRDP"
    assert b.major() == 3 and b.info().version == "3.5.1"
    cmd = b.build_command(spec())
    assert cmd.argv == [str(sh), "run", "some.app", "/args-from:stdin"]
    assert not any("p,w" in a for a in cmd.argv)
    assert FreeRdpBackend("/bin/true run x").major() == 3  # unknown version: keep secrets off argv


CERT_CHANGED_OUTPUT = """\
[10:22:24:650] [1:2] [WARN][com.freerdp.crypto] - [verify_cb]: Certificate verification failure
!!!Certificate for 192.0.2.47:3389 (RDP-Server) has changed!!!

New Certificate details:
	Common Name: WINSRV01
	Subject:     CN = WINSRV01
	Issuer:      CN = WINSRV01
	Valid from:  Nov 12 02:32:55 2026 GMT
	Valid to:    May 14 02:32:55 2027 GMT
	Thumbprint:  a1:a8:af:b6:bd:c4:cb:d2:d9:e0:e7:ee:f5:fc:03:0a:11:18:1f:26:2d:34:3b:42:49:50:57:5e:65:6c:73:7a

Old Certificate details:
	Subject:     CN = WINSRV01
	Issuer:      CN = WINSRV01
	Valid from:  Jun 14 01:53:54 2026 GMT
	Valid to:    Dec 14 01:53:54 2026 GMT
	Thumbprint:  3c:47:52:5d:68:73:7e:89:94:9f:aa:b5:c0:cb:d6:e1:ec:f7:02:0d:18:23:2e:39:44:4f:5a:65:70:7b:86:91

The above X.509 certificate does not match the certificate used for previous connections.
This may indicate that the certificate has been tampered with.
Please contact the administrator of the RDP server and clarify.
Do you trust the above certificate? (Y/T/N) [10:22:24:710] [1:2] [ERROR][com.freerdp.crypto] - [freerdp_tls_handshake]: certificate not trusted, aborting.
Error: Could not read answer from stdin.
""".splitlines()


def _pem(der: bytes) -> str:
    import base64
    return "-----BEGIN CERTIFICATE-----\n" + base64.encodebytes(der).decode() + "-----END CERTIFICATE-----\n"


def test_changed_certificate_is_recognised_in_freerdp_output():
    b = make(3)
    assert b.cert_change(["[ERROR] ERRCONNECT_CONNECT_TRANSPORT_FAILED", "— Connection failed (exit 131)"]) is None
    c = b.cert_change(CERT_CHANGED_OUTPUT)
    assert (c.host, c.port) == ("192.0.2.47", 3389)
    assert c.new["Common Name"] == "WINSRV01" and c.new["Valid to"] == "May 14 02:32:55 2027 GMT"
    assert c.new_fingerprint == "".join(f"{(i * 7 + 0xa1) % 256:02x}" for i in range(32))
    assert c.old_fingerprint.startswith("3c4752") and len(c.old_fingerprint) == 64
    assert c.old["Valid to"] == "Dec 14 01:53:54 2026 GMT" and "Common Name" not in c.old


def test_forget_certificate_removes_only_that_server(tmp_path, monkeypatch):
    import hashlib

    from engavenrdp.backend.base import CertChange

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    store = tmp_path / "freerdp" / "server"
    store.mkdir(parents=True)
    (store / "srv.lab_3389.pem").write_text(_pem(b"old certificate"))
    (store / "other.lab_3389.pem").write_text(_pem(b"another server"))
    known = tmp_path / "freerdp" / "known_hosts2"
    known.write_text("srv.lab 3389 aa:bb subj iss\nsrv.lab 3390 cc:dd subj iss\nother.lab 3389 ee:ff subj iss\n")
    b = make(3)
    assert b.stored_fingerprint("srv.lab", 3389) == hashlib.sha256(b"old certificate").hexdigest()
    assert b.stored_fingerprint("nobody.lab", 3389) is None

    assert b.forget_certificate(CertChange("srv.lab", 3389))
    assert sorted(p.name for p in store.iterdir()) == ["other.lab_3389.pem"]
    assert known.read_text() == "srv.lab 3390 cc:dd subj iss\nother.lab 3389 ee:ff subj iss\n"
    assert not b.forget_certificate(CertChange("srv.lab", 3389))  # nothing left to forget


def test_accepted_certificate_overrides_reject_unknown():
    from engavenrdp.backend.base import CertChange

    s = spec()
    s.security = {**s.security, "cert": "deny"}
    assert "/cert:deny" in make(3).build_command(s).redacted
    s.trusted_cert = CertChange("srv.lab", 3390)
    assert "/cert:tofu" in make(3).build_command(s).redacted
    assert "/cert-tofu" in make(2).build_command(s).redacted
    s.security = {**s.security, "cert": "ignore"}
    assert "/cert:ignore" in make(3).build_command(s).redacted
