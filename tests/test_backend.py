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
