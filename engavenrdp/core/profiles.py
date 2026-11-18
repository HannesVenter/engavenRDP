"""Global credential profiles (~/.config/engavenrdp/profiles.json)."""
from __future__ import annotations

import json
import os

from ..model.secret import Secret
from ..model.settings import CredentialProfile
from ..model.storage import profiles_in, profiles_out
from ..security.crypto import KeyUnavailable, Vault, WrongPassphrase
from .appstate import config_dir


class GlobalProfiles:
    def __init__(self) -> None:
        self.path = config_dir() / "profiles.json"
        self.profiles: list[CredentialProfile] = []
        self.header: dict = {}
        self.vault: Vault | None = None
        self.locked = False
        self.message = ""

    def load(self, passphrase_cb=None) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except Exception as e:
            self.message = f"Could not read global profiles: {e}"
            return
        self.header = data.get("encryption", {})
        self.profiles = profiles_in(data.get("profiles", []))
        if self.header:
            self.unlock(passphrase_cb)

    def _locked_secrets(self) -> list[Secret]:
        return [p.password for p in self.profiles if isinstance(p.password, Secret) and p.password.locked]

    def unlock(self, passphrase_cb=None) -> bool:
        """Open the existing vault and decrypt the saved passwords. A wrong
        passphrase is asked for again; cancelling leaves the profiles locked."""
        if self.vault is not None and self.vault.unlocked:
            return True
        if not self.header:
            return False
        msg = "Passphrase for your global credential profiles"
        try:
            while True:
                pw = None
                if self.header.get("mode") == "passphrase":
                    pw = passphrase_cb(msg) if passphrase_cb else None
                    if pw is None:
                        raise KeyUnavailable("Passphrase not entered")
                try:
                    vault = Vault.open(self.header, pw)
                    break
                except WrongPassphrase:
                    if passphrase_cb is None:
                        raise
                    msg = "Wrong passphrase — try again."
            for s in self._locked_secrets():
                s.plain = vault.decrypt(s.cipher)
        except KeyUnavailable as e:
            self.locked = bool(self._locked_secrets())
            self.message = str(e)
            return False
        self.vault = vault
        self.locked = False
        self.message = ""
        return True

    def discard_locked(self) -> None:
        """Give up on saved passwords that cannot be decrypted, so a new key can be set up."""
        for p in self.profiles:
            if isinstance(p.password, Secret) and p.password.locked:
                p.password = None
        self.header = {}
        self.locked = False
        self.message = ""

    def ensure_vault(self, passphrase_cb=None) -> bool:
        if self.vault is not None and self.vault.unlocked:
            return True
        if self.header and self.unlock(passphrase_cb):
            return True
        if self._locked_secrets():
            # A new key would be written over the old header and orphan these
            # passwords for good. The caller must discard_locked() first.
            return False
        try:
            self.vault = Vault.create_default()
            return True
        except KeyUnavailable:
            pw = passphrase_cb("No desktop keyring found. Choose a passphrase to protect "
                               "your global credential profiles:") if passphrase_cb else None
            if not pw:
                return False
            self.vault = Vault.create_passphrase(pw)
            return True

    def save(self) -> None:
        data = {"format": "engavenrdp-profiles", "version": 1,
                "profiles": profiles_out(self.profiles, self.vault)}
        if self.vault is not None:
            data["encryption"] = self.vault.header
        elif self.header:
            data["encryption"] = self.header
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # never world-readable
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
