"""Dialogs: properties (generic, schema-driven), bulk add, credentials, profiles, preferences."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
                               QTabWidget, QTableWidget, QTableWidgetItem, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget, QHeaderView)

from ..model.nodes import ContainerNode, FileNode, Node, ServerNode, SmartGroupNode
from ..model.patterns import PatternError, expand_many
from ..model.resolve import resolve, resolve_category
from ..model.secret import Secret
from ..model.settings import CATEGORIES, Category, CredentialProfile, FieldSpec
from ..model.smartgroups import FIELDS as SG_FIELDS, OPERATORS as SG_OPS


# ======================================================================
# secret line edit
# ======================================================================
class SecretEdit(QWidget):
    """Password field that knows about locked / unchanged values."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.eye = QToolButton(text="👁", checkable=True)
        self.eye.setToolTip("Show password")
        self.eye.toggled.connect(lambda on: self.edit.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        self.clear_btn = QToolButton(text="✕")
        self.clear_btn.setToolTip("Clear stored password")
        self.clear_btn.clicked.connect(self._clear)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.eye)
        lay.addWidget(self.clear_btn)
        self._orig: Secret | None = None
        self._cleared = False

    def set_secret(self, s: Secret | None) -> None:
        self._orig, self._cleared = s, False
        self.edit.clear()
        if s is None or not s:
            self.edit.setPlaceholderText("(none)")
        elif s.locked:
            self.edit.setPlaceholderText("(stored, but locked on this computer)")
        else:
            self.edit.setText(s.plain or "")
            self.edit.setPlaceholderText("")

    def _clear(self) -> None:
        self._cleared = True
        self.edit.clear()
        self.edit.setPlaceholderText("(none)")

    def secret(self) -> Secret | None:
        txt = self.edit.text()
        if self._cleared and not txt:
            return None
        if self._orig is not None and self._orig.locked and not txt:
            return self._orig  # untouched locked value
        if self._orig is not None and txt == (self._orig.plain or ""):
            return self._orig
        return Secret(plain=txt) if txt else None


# ======================================================================
# category tab (schema driven)
# ======================================================================
class CategoryTab(QWidget):
    def __init__(self, node: Node, cat: Category, profile_names: list[str], parent=None) -> None:
        super().__init__(parent)
        self.node, self.cat = node, cat
        lay = QVBoxLayout(self)
        self.inherit = QCheckBox("Inherit from parent")
        self.source = QLabel(objectName="subtitle")
        top = QHBoxLayout()
        top.addWidget(self.inherit)
        top.addStretch(1)
        top.addWidget(self.source)
        lay.addLayout(top)
        if isinstance(node, FileNode):
            self.inherit.setText("Use built-in defaults")
        self.form = QFormLayout()
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        box = QGroupBox(cat.label)
        box.setLayout(self.form)
        lay.addWidget(box)
        lay.addStretch(1)
        self.widgets: dict[str, QWidget] = {}
        self.labels: dict[str, QLabel] = {}
        for f in cat.fields:
            w = self._make(f, profile_names)
            lbl = QLabel(f.label if f.kind != "bool" else "")
            if f.help:
                w.setToolTip(f.help)
            self.widgets[f.key] = w
            self.labels[f.key] = lbl
            self.form.addRow(lbl, w)
        rec = node.record(cat.key)
        inherited, src = resolve_category(node, cat.key, include_self=False)
        self._inherited = inherited
        src_label = "built-in defaults" if src is None else f"{src.kind} “{src.label}”"
        self.source.setText(f"Parent value from: {src_label}")
        self.inherit.setChecked(rec.get("inherit", True))
        self._load(inherited if rec.get("inherit", True) else {**cat.defaults(), **rec})
        self.inherit.toggled.connect(self._on_inherit)
        self._update_enabled()

    def _make(self, f: FieldSpec, profile_names: list[str]) -> QWidget:
        if f.kind == "bool":
            w = QCheckBox(f.label)
            w.toggled.connect(self._update_enabled)
        elif f.kind == "int":
            w = QSpinBox()
            w.setRange(f.minimum, f.maximum)
        elif f.kind == "choice":
            w = QComboBox()
            for val, label in f.choices:
                w.addItem(label, val)
            w.currentIndexChanged.connect(self._update_enabled)
        elif f.kind == "secret":
            w = SecretEdit()
        elif f.kind == "profile":
            w = QComboBox()
            w.addItem("(none — use the fields below)", "")
            for n in profile_names:
                w.addItem(n, n)
            w.currentIndexChanged.connect(self._update_enabled)
        elif f.kind == "paths":
            w = QPlainTextEdit()
            w.setFixedHeight(70)
        else:
            w = QLineEdit()
        return w

    def _load(self, vals: dict[str, Any]) -> None:
        for f in self.cat.fields:
            w, v = self.widgets[f.key], vals.get(f.key, f.default)
            if f.kind == "bool":
                w.setChecked(bool(v))
            elif f.kind == "int":
                w.setValue(int(v or 0))
            elif f.kind in ("choice", "profile"):
                i = w.findData(str(v) if v is not None else "")
                if i < 0 and f.kind == "profile" and v:
                    w.addItem(f"{v} (missing)", v)
                    i = w.count() - 1
                w.setCurrentIndex(max(0, i))
            elif f.kind == "secret":
                w.set_secret(v)
            elif f.kind == "paths":
                w.setPlainText(v or "")
            else:
                w.setText(str(v or ""))

    def values(self) -> dict[str, Any]:
        out = {}
        for f in self.cat.fields:
            w = self.widgets[f.key]
            if f.kind == "bool":
                out[f.key] = w.isChecked()
            elif f.kind == "int":
                out[f.key] = w.value()
            elif f.kind in ("choice", "profile"):
                out[f.key] = w.currentData()
            elif f.kind == "secret":
                out[f.key] = w.secret()
            elif f.kind == "paths":
                out[f.key] = w.toPlainText()
            else:
                out[f.key] = w.text().strip()
        return out

    def _on_inherit(self, on: bool) -> None:
        if on:
            self._load(self._inherited)
        self._update_enabled()

    def _update_enabled(self) -> None:
        editable = not self.inherit.isChecked()
        cur = {}
        for f in self.cat.fields:
            w = self.widgets[f.key]
            if f.kind == "bool":
                cur[f.key] = w.isChecked()
            elif f.kind in ("choice", "profile"):
                cur[f.key] = w.currentData()
        using_profile = bool(cur.get("profile"))
        for f in self.cat.fields:
            en = editable
            if f.enabled_if:
                k, val = f.enabled_if
                en = en and cur.get(k) == val
                # chained: gateway user fields also need 'enabled'
                if k == "use_logon":
                    en = en and cur.get("enabled", True)
            if self.cat.key == "logon" and f.key in ("username", "domain", "password") and using_profile:
                en = False
            self.widgets[f.key].setEnabled(en)
            self.labels[f.key].setEnabled(en)

    def apply(self) -> None:
        if self.inherit.isChecked():
            self.node.set_record(self.cat.key, None)
        else:
            self.node.set_record(self.cat.key, self.values())


# ======================================================================
# smart group rules
# ======================================================================
class RulesTab(QWidget):
    def __init__(self, node: SmartGroupNode, parent=None) -> None:
        super().__init__(parent)
        self.node = node
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Show servers that match"))
        self.match = QComboBox()
        self.match.addItem("all rules", "all")
        self.match.addItem("any rule", "any")
        self.match.setCurrentIndex(0 if node.match == "all" else 1)
        top.addWidget(self.match)
        top.addStretch(1)
        lay.addLayout(top)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Field", "Operator", "Value"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table, 1)
        btns = QHBoxLayout()
        add = QPushButton("Add rule")
        add.clicked.connect(lambda: self._add({"field": "name", "op": "contains", "value": ""}))
        rem = QPushButton("Remove rule")
        rem.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()))
        btns.addWidget(add)
        btns.addWidget(rem)
        btns.addStretch(1)
        self.preview = QLabel(objectName="subtitle")
        btns.addWidget(self.preview)
        lay.addLayout(btns)
        for r in node.rules:
            self._add(r)
        if not node.rules:
            self._add({"field": "name", "op": "contains", "value": ""})

    def _add(self, rule: dict[str, str]) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        f = QComboBox()
        for k, label in SG_FIELDS:
            f.addItem(label, k)
        f.setCurrentIndex(max(0, f.findData(rule.get("field", "name"))))
        o = QComboBox()
        for k, label in SG_OPS:
            o.addItem(label, k)
        o.setCurrentIndex(max(0, o.findData(rule.get("op", "contains"))))
        self.table.setCellWidget(row, 0, f)
        self.table.setCellWidget(row, 1, o)
        self.table.setItem(row, 2, QTableWidgetItem(rule.get("value", "")))

    def rules(self) -> list[dict[str, str]]:
        out = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 2)
            out.append({"field": self.table.cellWidget(r, 0).currentData(),
                        "op": self.table.cellWidget(r, 1).currentData(),
                        "value": item.text() if item else ""})
        return [r for r in out if r["value"]]

    def apply(self) -> None:
        self.node.rules = self.rules()
        self.node.match = self.match.currentData()


# ======================================================================
# properties
# ======================================================================
class PropertiesDialog(QDialog):
    def __init__(self, node: Node, profile_names: list[str], parent=None, start_tab: str | None = None) -> None:
        super().__init__(parent)
        self.node = node
        kind = {"server": "Server", "group": "Group", "smartgroup": "Smart group", "file": "File"}[node.kind]
        self.setWindowTitle(f"{kind} properties — {node.label}")
        self.resize(820, 600)
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)

        # general
        gen = QWidget()
        form = QFormLayout(gen)
        if isinstance(node, ServerNode):
            self.host = QLineEdit(node.host)
            self.host.setPlaceholderText("host name or IP address")
            self.display = QLineEdit(node.display_name)
            self.display.setPlaceholderText("optional friendly name")
            form.addRow("Server name", self.host)
            form.addRow("Display name", self.display)
        else:
            self.name = QLineEdit(node.name)
            form.addRow("Name", self.name)
        self.comment = QPlainTextEdit(node.comment)
        self.comment.setFixedHeight(90)
        form.addRow("Comment", self.comment)
        if isinstance(node, FileNode):
            enc = node.encryption.get("mode", "none") if node.encryption else "none"
            info = {"keyring": "Desktop keyring (Secret Service)", "passphrase": "Passphrase",
                    "none": "Not set up yet (created on first save with a password)"}.get(enc, enc)
            form.addRow("Location", QLabel(node.path_on_disk or "(not saved yet)"))
            form.addRow("Credential encryption", QLabel(info))
            if node.locked:
                warn = QLabel(f"⚠ Credentials are locked: {node.lock_reason}")
                warn.setWordWrap(True)
                form.addRow(warn)
        self.tabs.addTab(gen, "General")

        self.rules_tab = None
        if isinstance(node, SmartGroupNode):
            self.rules_tab = RulesTab(node)
            self.tabs.addTab(self.rules_tab, "Rules")

        self.cat_tabs: list[CategoryTab] = []
        if not isinstance(node, SmartGroupNode):
            for cat in CATEGORIES:
                t = CategoryTab(node, cat, profile_names)
                self.cat_tabs.append(t)
                self.tabs.addTab(t, cat.label)
                if start_tab == cat.key:
                    self.tabs.setCurrentWidget(t)

        if isinstance(node, ServerNode):
            self.tabs.addTab(self._effective(node), "Effective settings")

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _effective(self, node: ServerNode) -> QWidget:
        t = QTreeWidget()
        t.setHeaderLabels(["Setting", "Value", "From"])
        t.setColumnWidth(0, 220)
        t.setColumnWidth(1, 180)
        r = resolve(node)
        for cat in CATEGORIES:
            top = QTreeWidgetItem([cat.label, "", r.source_label(cat.key)])
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            for fs in cat.fields:
                v = r[cat.key].get(fs.key)
                if fs.kind == "secret":
                    v = "(locked)" if isinstance(v, Secret) and v.locked else ("••••••" if v else "")
                elif fs.kind == "choice":
                    v = dict(fs.choices).get(str(v), v)
                elif fs.kind == "bool":
                    v = "Yes" if v else "No"
                top.addChild(QTreeWidgetItem([fs.label, str(v if v is not None else ""), ""]))
            t.addTopLevelItem(top)
            top.setExpanded(True)
        return t

    def _ok(self) -> None:
        n = self.node
        if isinstance(n, ServerNode):
            host = self.host.text().strip()
            if not host:
                QMessageBox.warning(self, "Server name", "Enter a host name or IP address.")
                return
            n.host = n.name = host
            n.display_name = self.display.text().strip()
        else:
            name = self.name.text().strip()
            if not name:
                QMessageBox.warning(self, "Name", "Enter a name.")
                return
            n.name = name
        n.comment = self.comment.toPlainText().strip()
        if self.rules_tab:
            self.rules_tab.apply()
        for t in self.cat_tabs:
            t.apply()
        n.mark_dirty()
        self.accept()


# ======================================================================
# add server / bulk add
# ======================================================================
class AddServerDialog(QDialog):
    def __init__(self, groups: list[tuple[str, ContainerNode]], current: ContainerNode | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add server")
        self.resize(460, 0)
        form = QFormLayout(self)
        self.host = QLineEdit()
        self.host.setPlaceholderText("e.g. web01.corp.local or 10.0.0.5")
        self.display = QLineEdit()
        self.display.setPlaceholderText("optional")
        self.comment = QLineEdit()
        self.group = QComboBox()
        for label, g in groups:
            self.group.addItem(label, g)
            if g is current:
                self.group.setCurrentIndex(self.group.count() - 1)
        form.addRow("Server name", self.host)
        form.addRow("Display name", self.display)
        form.addRow("Comment", self.comment)
        form.addRow("Add to", self.group)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _ok(self) -> None:
        if not self.host.text().strip():
            self.host.setFocus()
            return
        self.accept()

    def result_server(self) -> tuple[ServerNode, ContainerNode]:
        h = self.host.text().strip()
        return (ServerNode(name=h, host=h, display_name=self.display.text().strip(),
                           comment=self.comment.text().strip()), self.group.currentData())


class BulkAddDialog(QDialog):
    def __init__(self, groups: list[tuple[str, ContainerNode]], current: ContainerNode | None,
                 text: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add servers")
        self.resize(620, 480)
        lay = QVBoxLayout(self)
        help_ = QLabel("One host or pattern per line (or separated by spaces/commas).<br>"
                       "<code>web[01-10]</code> · <code>node[a-f]</code> · "
                       "<code>{prod,stage}-db[1-5]</code> · <code>10.0.0.[1-20]</code>")
        help_.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(help_)
        row = QHBoxLayout()
        self.text = QPlainTextEdit(text)
        self.text.setPlaceholderText("web[01-10]\n{prod,stage}-db[1-5]")
        self.preview = QListWidget()
        self.preview.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        row.addWidget(self.text, 3)
        row.addWidget(self.preview, 2)
        lay.addLayout(row, 1)
        self.status = QLabel(objectName="subtitle")
        lay.addWidget(self.status)
        form = QFormLayout()
        self.group = QComboBox()
        for label, g in groups:
            self.group.addItem(label, g)
            if g is current:
                self.group.setCurrentIndex(self.group.count() - 1)
        self.skip = QCheckBox("Skip servers that already exist in the target group")
        self.skip.setChecked(True)
        form.addRow("Add to", self.group)
        form.addRow("", self.skip)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = bb.button(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.text.textChanged.connect(self._update)
        self.hosts: list[str] = []
        self._update()

    def _update(self) -> None:
        self.preview.clear()
        try:
            self.hosts = expand_many(self.text.toPlainText())
            self.status.setText(f"{len(self.hosts)} server(s)")
            self.ok.setEnabled(bool(self.hosts))
        except PatternError as e:
            self.hosts = []
            self.status.setText(f"⚠ {e}")
            self.ok.setEnabled(False)
        for h in self.hosts[:500]:
            QListWidgetItem(h, self.preview)
        if len(self.hosts) > 500:
            QListWidgetItem(f"… and {len(self.hosts) - 500} more", self.preview)

    def target(self) -> ContainerNode:
        return self.group.currentData()


# ======================================================================
# credentials prompt
# ======================================================================
class CredentialDialog(QDialog):
    def __init__(self, server: ServerNode, username: str, domain: str, message: str = "",
                 can_save: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Connect to {server.label}")
        form = QFormLayout(self)
        if message:
            m = QLabel(message)
            m.setWordWrap(True)
            form.addRow(m)
        self.user = QLineEdit(username)
        self.domain = QLineEdit(domain)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("User name", self.user)
        form.addRow("Domain", self.domain)
        form.addRow("Password", self.password)
        self.save = QCheckBox("Remember these credentials (encrypted)")
        self.save.setVisible(can_save)
        form.addRow("", self.save)
        bb = QDialogButtonBox()
        bb.addButton("Connect", QDialogButtonBox.ButtonRole.AcceptRole).setObjectName("primary")
        bb.addButton(QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        (self.password if username else self.user).setFocus()


class PassphraseDialog(QDialog):
    def __init__(self, message: str, confirm: bool = False, parent=None, title="Passphrase") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        form = QFormLayout(self)
        lbl = QLabel(message)
        lbl.setWordWrap(True)
        form.addRow(lbl)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Passphrase", self.pw)
        self.pw2 = None
        if confirm:
            self.pw2 = QLineEdit()
            self.pw2.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow("Confirm", self.pw2)
        self.err = QLabel(objectName="subtitle")
        form.addRow(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.resize(420, 0)

    def _ok(self) -> None:
        if not self.pw.text():
            self.err.setText("Enter a passphrase.")
            return
        if self.pw2 is not None:
            if len(self.pw.text()) < 8:
                self.err.setText("Use at least 8 characters.")
                return
            if self.pw.text() != self.pw2.text():
                self.err.setText("The passphrases do not match.")
                return
        self.accept()

    @staticmethod
    def ask(parent, message: str, confirm: bool = False, title="Passphrase") -> str | None:
        d = PassphraseDialog(message, confirm, parent, title)
        return d.pw.text() if d.exec() == QDialog.DialogCode.Accepted else None


# ======================================================================
# credential profiles
# ======================================================================
class ProfileEditor(QDialog):
    def __init__(self, profile: CredentialProfile | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Credential profile")
        form = QFormLayout(self)
        self.name = QLineEdit(profile.name if profile else "")
        self.user = QLineEdit(profile.username if profile else "")
        self.domain = QLineEdit(profile.domain if profile else "")
        self.pw = SecretEdit()
        self.pw.set_secret(profile.password if profile else None)
        form.addRow("Profile name", self.name)
        form.addRow("User name", self.user)
        form.addRow("Domain", self.domain)
        form.addRow("Password", self.pw)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(lambda: self.name.text().strip() and self.accept())
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.resize(420, 0)

    def apply(self, profile: CredentialProfile) -> None:
        profile.name = self.name.text().strip()
        profile.username = self.user.text().strip()
        profile.domain = self.domain.text().strip()
        profile.password = self.pw.secret()


class ProfilesDialog(QDialog):
    """Manage profiles of one file and the global store."""

    def __init__(self, scopes: list[tuple[str, list[CredentialProfile]]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Credential profiles")
        self.resize(560, 420)
        self.scopes = scopes
        self.changed: set[int] = set()
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Profiles are named credentials you can pick in any Logon Credentials tab."))
        self.tabs = QTabWidget()
        self.lists: list[QListWidget] = []
        for label, profs in scopes:
            w = QWidget()
            v = QHBoxLayout(w)
            lst = QListWidget()
            v.addWidget(lst, 1)
            col = QVBoxLayout()
            for text, fn in (("Add…", self._add), ("Edit…", self._edit), ("Remove", self._remove)):
                b = QPushButton(text)
                b.clicked.connect(fn)
                col.addWidget(b)
            col.addStretch(1)
            v.addLayout(col)
            lst.itemDoubleClicked.connect(lambda _i: self._edit())
            self.lists.append(lst)
            self.tabs.addTab(w, label)
        lay.addWidget(self.tabs)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.accept)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)
        self._refresh()

    def _refresh(self) -> None:
        for (label, profs), lst in zip(self.scopes, self.lists):
            lst.clear()
            for p in profs:
                who = f"{p.domain}\\{p.username}" if p.domain else p.username
                QListWidgetItem(f"{p.name}    —  {who}" + ("   🔒" if p.password and p.password.locked else ""), lst)

    def _cur(self):
        i = self.tabs.currentIndex()
        return i, self.scopes[i][1], self.lists[i].currentRow()

    def _add(self) -> None:
        i, profs, _ = self._cur()
        d = ProfileEditor(None, self)
        if d.exec():
            p = CredentialProfile(name="")
            d.apply(p)
            import uuid

            p.id = uuid.uuid4().hex
            profs.append(p)
            self.changed.add(i)
            self._refresh()

    def _edit(self) -> None:
        i, profs, row = self._cur()
        if row < 0:
            return
        d = ProfileEditor(profs[row], self)
        if d.exec():
            d.apply(profs[row])
            self.changed.add(i)
            self._refresh()

    def _remove(self) -> None:
        i, profs, row = self._cur()
        if row < 0:
            return
        if QMessageBox.question(self, "Remove profile", f"Remove profile “{profs[row].name}”?") \
                == QMessageBox.StandardButton.Yes:
            del profs[row]
            self.changed.add(i)
            self._refresh()


# ======================================================================
# preferences
# ======================================================================
class PreferencesDialog(QDialog):
    def __init__(self, state, backend_info, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.state = state
        form = QFormLayout(self)
        self.auto_save = QCheckBox("Save files automatically after every change")
        self.auto_save.setChecked(state.auto_save)
        self.theme = QComboBox()
        for k, l in (("system", "Follow system"), ("light", "Light"), ("dark", "Dark")):
            self.theme.addItem(l, k)
        self.theme.setCurrentIndex(max(0, self.theme.findData(state.theme)))
        self.cmd = QLineEdit(state.freerdp_command)
        self.cmd.setPlaceholderText("auto-detect (xfreerdp3, then xfreerdp)")
        self.retry = QSpinBox()
        self.retry.setRange(5, 600)
        self.retry.setSuffix(" s")
        self.retry.setValue(state.reconnect_interval)
        self.delay = QSpinBox()
        self.delay.setRange(0, 10000)
        self.delay.setSingleStep(100)
        self.delay.setSuffix(" ms")
        self.delay.setValue(state.group_connect_delay_ms)
        self.confirm = QCheckBox("Ask before disconnecting a group")
        self.confirm.setChecked(state.confirm_disconnect)
        self.virtual = QCheckBox("Show Connected / Favorites / Recent / Reconnect groups")
        self.virtual.setChecked(state.show_virtual_groups)
        form.addRow("", self.auto_save)
        form.addRow("Theme", self.theme)
        form.addRow("FreeRDP command", self.cmd)
        info = QLabel(("✔ " if backend_info.available else "⚠ ") +
                      (f"{backend_info.message} — {backend_info.path}" if backend_info.available
                       else backend_info.message))
        info.setWordWrap(True)
        info.setObjectName("subtitle")
        form.addRow("", info)
        form.addRow("Reconnect: retry every", self.retry)
        form.addRow("Group connect: delay between servers", self.delay)
        form.addRow("", self.confirm)
        form.addRow("", self.virtual)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.resize(560, 0)

    def apply(self) -> None:
        s = self.state
        s.auto_save = self.auto_save.isChecked()
        s.theme = self.theme.currentData()
        s.freerdp_command = self.cmd.text().strip()
        s.reconnect_interval = self.retry.value()
        s.group_connect_delay_ms = self.delay.value()
        s.confirm_disconnect = self.confirm.isChecked()
        s.show_virtual_groups = self.virtual.isChecked()


SHORTCUTS_HTML = """
<table cellpadding=4>
<tr><td><b>Enter</b> / <b>Ctrl+Enter</b></td><td>Connect</td></tr>
<tr><td><b>Ctrl+Shift+D</b></td><td>Disconnect</td></tr>
<tr><td><b>F5</b></td><td>Reconnect</td></tr>
<tr><td><b>Alt+Enter</b></td><td>Properties</td></tr>
<tr><td><b>Ctrl+N</b> / <b>Ctrl+G</b> / <b>Ctrl+Shift+G</b></td><td>Add server / group / smart group</td></tr>
<tr><td><b>Ctrl+B</b></td><td>Add servers in bulk</td></tr>
<tr><td><b>F2</b> / <b>Del</b></td><td>Rename / Delete</td></tr>
<tr><td><b>Ctrl+F</b></td><td>Search the tree</td></tr>
<tr><td><b>F11</b></td><td>Full screen</td></tr>
<tr><td><b>Ctrl+K</b></td><td>Toggle favorite</td></tr>
<tr><td><b>Ctrl+Shift+N</b> / <b>Ctrl+O</b> / <b>Ctrl+S</b></td><td>New / Open / Save file</td></tr>
<tr><td><b>Ctrl+X / C / V</b></td><td>Cut / copy / paste servers and groups</td></tr>
<tr><td><b>Ctrl+wheel</b></td><td>Resize thumbnails</td></tr>
</table>
<p>While the remote session has the keyboard, local shortcuts go to the remote computer.
Click the tree (or use the full-screen bar) to get them back.</p>
"""
