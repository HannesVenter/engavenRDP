import json
import pytest

from engavenrdp.model.nodes import FileNode, GroupNode, ServerNode, SmartGroupNode, TreeError, clone
from engavenrdp.model.patterns import expand, expand_many, parse_host_list, PatternError
from engavenrdp.model.resolve import resolve, effective_credentials
from engavenrdp.model.secret import Secret
from engavenrdp.model.settings import CredentialProfile
from engavenrdp.model import storage
from engavenrdp.security.crypto import Vault, KeyUnavailable


def build():
    f = FileNode(name="Lab")
    prod = f.add(GroupNode(name="Prod"))
    web = prod.add(GroupNode(name="Web"))
    s1 = web.add(ServerNode(name="web01", host="web01.lab"))
    s2 = web.add(ServerNode(name="web02", host="web02.lab", display_name="Web Two"))
    return f, prod, web, s1, s2


def test_container_rule():
    f, prod, web, s1, s2 = build()
    with pytest.raises(TreeError):
        prod.add(ServerNode(name="x"))
    with pytest.raises(TreeError):
        web.add(GroupNode(name="sub"))
    assert not web.can_accept(prod)  # cycle


def test_inheritance():
    f, prod, web, s1, s2 = build()
    f.set_record("connection", {"port": 3390})
    prod.set_record("logon", {"username": "admin", "domain": "LAB"})
    s2.set_record("logon", {"username": "other"})
    r1, r2 = resolve(s1), resolve(s2)
    assert r1["connection"]["port"] == 3390 and r1.sources["connection"] is f
    assert r1["logon"]["username"] == "admin" and r1.sources["logon"] is prod
    assert r2["logon"]["username"] == "other" and r2["logon"]["domain"] == ""
    assert resolve(s1).sources["display"] is None


def test_profiles():
    f, prod, web, s1, s2 = build()
    f.profiles.append(CredentialProfile("ops", "opsuser", "CORP", Secret(plain="pw")))
    prod.set_record("logon", {"profile": "ops"})
    c = effective_credentials(s1, resolve(s1)["logon"], [])
    assert (c.username, c.domain, c.password_text) == ("opsuser", "CORP", "pw")
    g = [CredentialProfile("glob", "g", "", None)]
    prod.set_record("logon", {"profile": "glob"})
    assert effective_credentials(s1, resolve(s1)["logon"], g).username == "g"
    prod.set_record("logon", {"profile": "nope"})
    assert effective_credentials(s1, resolve(s1)["logon"], g).missing_profile


def test_patterns():
    assert expand("web[01-03]") == ["web01", "web02", "web03"]
    assert expand("{prod,stage}-db[1-2]") == ["prod-db1", "prod-db2", "stage-db1", "stage-db2"]
    assert expand("n[a-c]") == ["na", "nb", "nc"]
    assert expand("h[1-2,5]") == ["h1", "h2", "h5"]
    assert expand_many("a[1-2], b {x,y}z") == ["a1", "a2", "b", "xz", "yz"]
    assert parse_host_list("# c\nsrv1  Server One\nsrv[2-3]\n") == [("srv1", "Server One"), ("srv2", ""), ("srv3", "")]
    with pytest.raises(PatternError):
        expand("x[1-99999]")
    with pytest.raises(PatternError):
        expand("x[1-2")


def test_smart_group():
    f, prod, web, s1, s2 = build()
    sg = f.add(SmartGroupNode(name="Twos", rules=[{"field": "display_name", "op": "contains", "value": "two"}]))
    assert sg.members() == [s2]
    sg.rules = [{"field": "group", "op": "contains", "value": "prod/web"}]
    assert sg.members() == [s1, s2]


def test_roundtrip_and_encryption(tmp_path):
    f, prod, web, s1, s2 = build()
    prod.set_record("logon", {"username": "admin", "password": Secret(plain="s3cret")})
    f.vault = Vault.create_passphrase("hunter2")
    p = tmp_path / "lab.erdp"
    storage.save(f, str(p))
    raw = p.read_text()
    assert "s3cret" not in raw and "enc:v1:" in raw
    g = storage.load(str(p), lambda msg: "hunter2")
    assert not g.locked
    srv = g.find_path("Prod/Web/web02")
    assert srv.display_name == "Web Two"
    assert resolve(srv)["logon"]["password"].plain == "s3cret"
    # wrong machine / no passphrase -> locked but preserved
    h = storage.load(str(p), lambda msg: None)
    assert h.locked
    h.find_path("Prod").comment = "edited"
    h.vault = None
    storage.save(h, str(p))
    again = storage.load(str(p), lambda m: "hunter2")
    assert resolve(again.find_path("Prod/Web/web01"))["logon"]["password"].plain == "s3cret"
    assert again.find_path("Prod").comment == "edited"


def test_wrong_passphrase_retry(tmp_path):
    f, *_ = build()
    f.profiles.append(CredentialProfile("x", "u", "", Secret(plain="pw")))
    f.vault = Vault.create_passphrase("right")
    p = tmp_path / "a.erdp"
    storage.save(f, str(p))
    answers = iter(["wrong", "right"])
    g = storage.load(str(p), lambda m: next(answers))
    assert g.profiles[0].password.plain == "pw"


def test_plain_needs_vault(tmp_path):
    f, prod, *_ = build()
    prod.set_record("logon", {"password": Secret(plain="x")})
    with pytest.raises(KeyUnavailable):
        storage.save(f, str(tmp_path / "x.erdp"))


def test_clone():
    f, prod, web, s1, s2 = build()
    c = clone(web)
    assert c.id != web.id and c.children[0].id != s1.id and c.parent is None
    assert c.children[0].parent is c


RDG = """<?xml version="1.0" encoding="utf-8"?>
<RDCMan programVersion="2.7" schemaVersion="3">
  <file>
    <credentialsProfiles><credentialsProfile inherit="None"><profileName scope="Local">ops</profileName><userName>op</userName><password>AQAAANCM</password><domain>CORP</domain></credentialsProfile></credentialsProfiles>
    <properties><expanded>True</expanded><name>Imported</name></properties>
    <logonCredentials inherit="None"><profileName scope="Local">ops</profileName></logonCredentials>
    <group>
      <properties><expanded>False</expanded><name>Web</name></properties>
      <connectionSettings inherit="None"><connectToConsole>True</connectToConsole><startProgram /><workingDir /><port>3390</port></connectionSettings>
      <server><properties><displayName>Web 1</displayName><name>web1.corp</name></properties></server>
      <server><properties><name>web2.corp</name></properties></server>
    </group>
    <smartGroup><properties><name>Webs</name></properties><ruleGroup operator="All"><rule><property>DisplayName</property><operator>Matches</operator><value>web</value></rule></ruleGroup></smartGroup>
  </file>
</RDCMan>"""


def test_rdg_import(tmp_path):
    p = tmp_path / "x.rdg"
    p.write_text(RDG)
    f = storage.load(str(p))
    assert f.name == "Imported" and f.profiles[0].username == "op"
    web = f.find_path("Web")
    assert [s.host for s in web.servers()] == ["web1.corp", "web2.corp"]
    r = resolve(web.children[0])
    assert r["connection"]["port"] == 3390 and r["connection"]["console"]
    assert r["logon"]["profile"] == "ops"
    assert "DPAPI" in f.lock_reason
    assert len(f.find_path("Webs").members()) == 2


def test_newer_file_format_is_refused(tmp_path):
    f, *_ = build()
    d = storage.to_dict(f)
    d["version"] = storage.VERSION + 1
    with pytest.raises(storage.FormatError):
        storage.from_dict(d)
    d["version"] = storage.VERSION
    assert storage.from_dict(d).name == "Lab"
