"""Headless smoke tests for the Qt UI (QT_QPA_PLATFORM=offscreen)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PySide6 = pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from engavenrdp.core.appstate import AppState  # noqa: E402
from engavenrdp.model import storage  # noqa: E402
from engavenrdp.model.nodes import FileNode, GroupNode, ServerNode, SmartGroupNode  # noqa: E402
from engavenrdp.model.resolve import resolve  # noqa: E402
from engavenrdp.model.secret import Secret  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from engavenrdp.ui import main_window, theme

    theme.apply(app, "light")
    f = FileNode(name="Demo")
    g = f.add(GroupNode(name="Web"))
    for i in range(3):
        g.add(ServerNode(name=f"web{i}", host=f"web{i}.lan"))
    f.add(SmartGroupNode(name="Ones", rules=[{"field": "name", "op": "ends", "value": "1"}]))
    p = tmp_path / "demo.erdp"
    storage.save(f, str(p))
    w = main_window.MainWindow(AppState(open_files=[str(p)], auto_save=False))
    w.restore_files([])
    yield w
    w.manager.disconnect_all()
    w.deleteLater()


def test_tree_and_grid(win):
    F = win.files[0]
    assert F.find_path("Web/web1")
    win._rebuild()
    web = F.find_path("Web")
    win.tree.select_node(web)
    assert win.client.currentWidget() is win.client.grid
    assert len(win.client.grid.tiles) == 3
    win.tree.select_node(F.find_path("Ones"))
    assert len(win.client.grid.tiles) == 1
    win.tree.select_node(F.find_path("Web/web0"))
    assert win.client.current is F.find_path("Web/web0")


def test_filter(win):
    win._rebuild()
    win.tree.apply_filter("web2")
    visible = [it.text(0) for it in win.tree._iter_items() if not it.isHidden()]
    assert "web2" in visible and "web0" not in visible


def test_properties_apply(win):
    from engavenrdp.ui.dialogs import PropertiesDialog

    web = win.files[0].find_path("Web")
    d = PropertiesDialog(web, [], win)
    conn = next(t for t in d.cat_tabs if t.cat.key == "connection")
    conn.inherit.setChecked(False)
    conn.widgets["port"].setValue(3390)
    logon = next(t for t in d.cat_tabs if t.cat.key == "logon")
    logon.inherit.setChecked(False)
    logon.widgets["username"].setText("bob")
    logon.widgets["password"].edit.setText("pw")
    d._ok()
    s = web.servers()[0]
    r = resolve(s)
    assert r["connection"]["port"] == 3390 and r["logon"]["username"] == "bob"
    assert r["logon"]["password"].plain == "pw"
    assert web.file.dirty


def test_bulk_dialog(win):
    from engavenrdp.ui.dialogs import BulkAddDialog

    d = BulkAddDialog(win._containers(), None, "app[1-3] {a,b}x")
    assert d.hosts == ["app1", "app2", "app3", "ax", "bx"]
    d.text.setPlainText("bad[1-")
    assert not d.ok.isEnabled()


def test_clone_paste_and_favorites(win):
    F = win.files[0]
    s = F.find_path("Web/web0")
    win.tree.select_node(s)
    win.clip("copy")
    win.tree.select_node(F.find_path("Web"))
    win.paste()
    assert len(F.find_path("Web").servers()) == 4
    win.state.set_favorite(F.path_on_disk, s.id, True)
    assert win.tree.virtual_sources["favorites"]() == [s]


def test_spec_uses_embedding_only_on_xcb(win):
    assert not win._can_embed()  # offscreen platform -> external windows


def test_credential_prompt_cached_and_remembered(win, monkeypatch):
    from engavenrdp.backend.base import BackendInfo, Command, RdpBackend
    from engavenrdp.ui import main_window

    class Stub(RdpBackend):
        name = "stub"
        specs = []

        def info(self):
            return BackendInfo("stub", True, "1", "/bin/sleep")

        def build_command(self, spec):
            Stub.specs.append(spec)
            return Command(["/bin/sleep", "30"], None, ["sleep"])

    win.backend = win.manager.backend = Stub()
    win.backend_info = win.backend.info()
    calls = []

    class FakeDlg:
        def __init__(self, server, user, domain, msg, parent=None):
            calls.append(server.name)
            from PySide6.QtWidgets import QCheckBox, QLineEdit
            self.user, self.domain, self.password = QLineEdit("alice"), QLineEdit("LAB"), QLineEdit("s3")
            self.save = QCheckBox()
            self.save.setChecked(True)

        def exec(self):
            return main_window.QDialog.DialogCode.Accepted

    monkeypatch.setattr(main_window, "CredentialDialog", FakeDlg)
    monkeypatch.setattr(main_window.MainWindow, "ensure_vault", lambda self, f: False)
    web = win.files[0].find_path("Web")
    for s in web.servers():
        assert win.connect_server(s)
    assert len(calls) == 1  # one prompt for the whole group (same credential source)
    assert all(sp.password == "s3" and sp.username == "alice" for sp in Stub.specs)
    # remembered on the file (where logon resolution ended)
    assert resolve(web.servers()[0])["logon"]["username"] == "alice"
    win.manager.disconnect_all()


def _wait(cond, ms=5000):
    from PySide6.QtTest import QTest

    for _ in range(ms // 20):
        if cond():
            return True
        QTest.qWait(20)
    return cond()


def test_rejected_typed_password_is_not_reused(win, monkeypatch):
    from engavenrdp.backend.base import BackendInfo, Command, RdpBackend
    from engavenrdp.core.session import State
    from engavenrdp.ui import main_window

    class Stub(RdpBackend):
        name = "stub"
        code = 132

        def info(self):
            return BackendInfo("stub", True, "1", "/bin/sh")

        def build_command(self, spec):
            return Command(["/bin/sh", "-c", f"exit {Stub.code}"], None, ["sh"])

        def is_auth_failure(self, code):
            return code == 132

    win.backend = win.manager.backend = Stub()
    win.backend_info = win.backend.info()
    calls = []

    class FakeDlg:
        def __init__(self, server, user, domain, msg, parent=None):
            calls.append(server.name)
            from PySide6.QtWidgets import QCheckBox, QLineEdit
            self.user, self.domain, self.password = QLineEdit("alice"), QLineEdit("LAB"), QLineEdit("typo")
            self.save = QCheckBox()

        def exec(self):
            return main_window.QDialog.DialogCode.Accepted

    monkeypatch.setattr(main_window, "CredentialDialog", FakeDlg)
    s = win.files[0].find_path("Web/web0")
    failed = lambda: win.manager.state(s) == State.ERROR  # noqa: E731

    assert win.connect_server(s) and _wait(failed)
    assert win.manager.session(s).auth_failed
    assert win.connect_server(s) and _wait(failed)
    assert len(calls) == 2  # asked again after the server rejected the password

    Stub.code = 131  # a network failure must not throw the typed password away
    assert win.connect_server(s) and _wait(failed)
    assert len(calls) == 3 and not win.manager.session(s).auth_failed
    assert win.connect_server(s) and _wait(failed)
    assert len(calls) == 3


def test_group_connect_does_not_stack_prompts(app):
    from engavenrdp.backend.freerdp import FreeRdpBackend
    from engavenrdp.core.manager import SessionManager

    m = SessionManager(FreeRdpBackend())
    servers = [ServerNode(name=f"s{i}", host=f"s{i}") for i in range(3)]
    extra = ServerNode(name="extra", host="extra")
    order, depth = [], [0]

    def connector(server):
        depth[0] += 1
        order.append((server.name, depth[0]))
        # a modal credential prompt runs an event loop: the queue timer and the
        # reconnect probes keep firing while it is open
        m._drain_queue()
        if server is servers[0]:
            m._connect(extra)  # e.g. a reconnect probe succeeding meanwhile
        depth[0] -= 1

    m.connector = connector
    m.connect_delay_ms = 1
    m.connect_many(servers)
    assert _wait(lambda: len(order) >= 4 and not m._queue)
    assert order == [("s0", 1), ("s1", 1), ("s2", 1), ("extra", 1)]  # one at a time, none lost
    m.cancel_queue()


def test_bad_gateway_password_is_reported_not_sent(app):
    from engavenrdp.backend.freerdp import FreeRdpBackend
    from engavenrdp.backend import SessionSpec
    from engavenrdp.core.session import Session, State

    b = FreeRdpBackend()
    b._major_override = 3
    sess = Session(ServerNode(name="s", host="s"), b)
    seen = []
    sess.state_changed.connect(seen.append)
    sess.start(SessionSpec(host="s", gateway={"enabled": True, "host": "gw"}, gateway_username="u",
                           gateway_password='a"b'))
    assert sess.state == State.ERROR and seen[-1] == State.ERROR and sess.proc is None
    assert "gateway password" in sess.last_error


def test_locked_global_profiles_are_never_rekeyed_silently(app, tmp_path, monkeypatch):
    import json

    from engavenrdp.core.profiles import GlobalProfiles
    from engavenrdp.model.settings import CredentialProfile
    from engavenrdp.security.crypto import Vault

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    gp = GlobalProfiles()
    gp.profiles = [CredentialProfile(name="a", username="alice", password=Secret(plain="one"), id="1"),
                   CredentialProfile(name="b", username="bob", password=Secret(plain="two"), id="2")]
    gp.vault = Vault.create_passphrase("right")
    gp.save()
    before = json.loads(gp.path.read_text())

    gp = GlobalProfiles()
    gp.load(lambda msg: None)  # passphrase prompt cancelled at start-up
    assert gp.locked and gp.vault is None
    gp.profiles[0].password = Secret(plain="changed")
    assert not gp.ensure_vault(lambda msg: None)  # would have made a new key before
    assert json.loads(gp.path.read_text()) == before

    answers = iter(["wrong", "right"])
    assert gp.ensure_vault(lambda msg: next(answers))  # wrong passphrase is asked again
    assert not gp.locked and gp.profiles[1].password.plain == "two"
    gp.save()
    gp = GlobalProfiles()
    gp.load(lambda msg: "right")
    assert [p.password.plain for p in gp.profiles] == ["changed", "two"]

    gp = GlobalProfiles()
    gp.load(lambda msg: None)
    gp.discard_locked()  # the explicit "remove the unreadable passwords" path
    assert all(p.password is None for p in gp.profiles) and not gp.header


def test_changed_certificate_can_be_trusted_from_the_session_card(win, tmp_path):
    from engavenrdp.backend.base import BackendInfo, CertChange, Command, RdpBackend
    from engavenrdp.core.session import State

    class Stub(RdpBackend):
        name = "stub"
        saved = "old"
        specs = []
        forgotten = []

        def info(self):
            return BackendInfo("stub", True, "1", "/bin/sh")

        def build_command(self, spec):
            Stub.specs.append(spec)
            if spec.trusted_cert is None:
                # like FreeRDP: prints the question and then waits for an answer for ever
                return Command(["/bin/sh", "-c", "echo CHANGED; sleep 30"], None, ["sh"])
            return Command(["/bin/sh", "-c", "echo connected to host; sleep 30"], None, ["sh"])

        def cert_change(self, lines):
            if "CHANGED" in lines:
                return CertChange("web0.lan", 3389, {"Thumbprint": "AA:BB", "Common Name": "<web0>"},
                                  {"Thumbprint": "11:22"})
            return None

        def forget_certificate(self, change):
            Stub.forgotten.append((change.host, change.port))
            return True

        def stored_fingerprint(self, host, port):
            return Stub.saved

    win.backend = win.manager.backend = Stub()
    win.backend_info = win.backend.info()
    s = win.files[0].find_path("Web/web0")
    s.set_record("logon", {"username": "alice", "password": Secret(plain="pw")})  # no credential prompt
    assert win.connect_server(s) and _wait(lambda: win.manager.state(s) == State.ERROR)
    sess = win.manager.session(s)
    sw = win.client.session_widget(s)
    assert sess.cert_change.new_fingerprint == "aabb" and "certificate has changed" in sess.last_error
    assert not sw.trust_btn.isHidden() and "AA:BB" in sw.card_msg.text() and "&lt;web0&gt;" in sw.card_msg.text()

    # the server shows yet another certificate on the retry: refuse and forget it again
    Stub.saved = "ccdd"
    sw.trust_btn.click()
    assert _wait(lambda: len(Stub.specs) == 2 and win.manager.state(s) == State.ERROR)
    assert Stub.specs[1].trusted_cert is sess.spec.trusted_cert and "another certificate" in sess.last_error
    assert Stub.forgotten == [("web0.lan", 3389)] * 2
    assert sw.trust_btn.isHidden()  # nothing to accept: the user has to connect again and look

    # normal case: the accepted certificate is the one that got saved
    assert win.connect_server(s) and _wait(lambda: win.manager.state(s) == State.ERROR)
    Stub.saved = "aabb"
    sw.trust_btn.click()
    assert _wait(lambda: win.manager.state(s) == State.CONNECTED)
    assert sess.cert_change is None and sw.trust_btn.isHidden()
