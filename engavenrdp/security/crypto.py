"""Encryption of secrets at rest.

Two key sources:

* ``keyring``   – a random Fernet key stored in the desktop Secret Service
                  (GNOME Keyring / KWallet via libsecret). Uses the ``keyring``
                  package if importable, otherwise the ``secret-tool`` CLI.
* ``passphrase``– key derived with scrypt from a user passphrase + random salt.

Values are stored as ``enc:v1:<fernet token>``.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

PREFIX = "enc:v1:"
SERVICE = "engavenrdp"
_CHECK_PLAINTEXT = b"engavenrdp-key-check"


class KeyUnavailable(Exception):
    """The key for a file cannot be obtained on this machine."""


class WrongPassphrase(KeyUnavailable):
    pass


def derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


# ---------------------------------------------------------------- keyring
class KeyringStore:
    """Tiny abstraction over python-keyring and secret-tool."""

    def __init__(self) -> None:
        self._kr = None
        try:
            import keyring  # type: ignore
            from keyring.backends import fail  # type: ignore

            if not isinstance(keyring.get_keyring(), fail.Keyring):
                self._kr = keyring
        except Exception:
            self._kr = None
        self._tool = shutil.which("secret-tool")

    @property
    def available(self) -> bool:
        return self._kr is not None or self._tool is not None

    @property
    def backend_name(self) -> str:
        if self._kr is not None:
            return "keyring"
        if self._tool:
            return "secret-tool"
        return "none"

    def get(self, key_id: str) -> str | None:
        if self._kr is not None:
            try:
                return self._kr.get_password(SERVICE, key_id)
            except Exception:
                return None
        if self._tool:
            try:
                res = subprocess.run([self._tool, "lookup", "application", SERVICE, "key-id", key_id],
                                     capture_output=True, text=True, timeout=20)
                if res.returncode != 0:
                    return None
                return res.stdout.strip() or None
            except Exception:
                return None
        return None

    def set(self, key_id: str, value: str, label: str = "engavenRDP file key") -> None:
        if self._kr is not None:
            self._kr.set_password(SERVICE, key_id, value)
            return
        if self._tool:
            res = subprocess.run([self._tool, "store", f"--label={label}", "application", SERVICE,
                                  "key-id", key_id], input=value, text=True, capture_output=True, timeout=30)
            if res.returncode != 0:
                raise KeyUnavailable(res.stderr.strip() or "secret-tool store failed")
            return
        raise KeyUnavailable("No Secret Service (keyring) is available on this system")


_store: KeyringStore | None = None


def keyring_store() -> KeyringStore:
    global _store
    if _store is None:
        _store = KeyringStore()
    return _store


# ---------------------------------------------------------------- vault
class Vault:
    """Encrypts/decrypts values for one file (or the global profile store)."""

    def __init__(self, key: bytes | None, header: dict) -> None:
        self._fernet = Fernet(key) if key else None
        self.header = header

    @property
    def unlocked(self) -> bool:
        return self._fernet is not None

    # -- creation ---------------------------------------------------------
    @classmethod
    def create_keyring(cls) -> "Vault":
        import uuid

        key = Fernet.generate_key()
        key_id = uuid.uuid4().hex
        keyring_store().set(key_id, key.decode())
        header = {"mode": "keyring", "key_id": key_id}
        v = cls(key, header)
        header["check"] = v._encrypt_raw(_CHECK_PLAINTEXT)
        return v

    @classmethod
    def create_passphrase(cls, passphrase: str) -> "Vault":
        salt = os.urandom(16)
        key = derive_key(passphrase, salt)
        header = {"mode": "passphrase", "salt": base64.b64encode(salt).decode(), "kdf": "scrypt-n15-r8-p1"}
        v = cls(key, header)
        header["check"] = v._encrypt_raw(_CHECK_PLAINTEXT)
        return v

    @classmethod
    def create_default(cls) -> "Vault":
        if keyring_store().available:
            try:
                return cls.create_keyring()
            except KeyUnavailable:
                pass
        raise KeyUnavailable("no-keyring")

    # -- opening ----------------------------------------------------------
    @classmethod
    def open(cls, header: dict, passphrase: str | None = None) -> "Vault":
        mode = header.get("mode")
        if mode == "keyring":
            key = keyring_store().get(header.get("key_id", ""))
            if not key:
                raise KeyUnavailable("The key for this file is not in this computer's keyring "
                                     "(the file was probably created on another computer or user account).")
            v = cls(key.encode(), header)
        elif mode == "passphrase":
            if passphrase is None:
                raise KeyUnavailable("A passphrase is required to decrypt the credentials in this file.")
            salt = base64.b64decode(header["salt"])
            v = cls(derive_key(passphrase, salt), header)
        else:
            raise KeyUnavailable(f"Unknown encryption mode “{mode}”")
        if "check" in header:
            try:
                v._decrypt_raw(header["check"])
            except InvalidToken:
                if mode == "passphrase":
                    raise WrongPassphrase("Wrong passphrase.")
                raise KeyUnavailable("The keyring key does not match this file.")
        return v

    # -- primitives -------------------------------------------------------
    def _encrypt_raw(self, data: bytes) -> str:
        assert self._fernet is not None
        return self._fernet.encrypt(data).decode()

    def _decrypt_raw(self, token: str) -> bytes:
        assert self._fernet is not None
        return self._fernet.decrypt(token.encode())

    def encrypt(self, plaintext: str) -> str:
        if self._fernet is None:
            raise KeyUnavailable("Vault is locked")
        return PREFIX + self._encrypt_raw(plaintext.encode("utf-8"))

    def decrypt(self, value: str) -> str:
        if self._fernet is None:
            raise KeyUnavailable("Vault is locked")
        if not value.startswith(PREFIX):
            raise ValueError("Not an encrypted value")
        try:
            return self._decrypt_raw(value[len(PREFIX):]).decode("utf-8")
        except InvalidToken as e:
            raise KeyUnavailable("Value was encrypted with a different key") from e
