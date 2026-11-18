"""A password value that may or may not be decryptable on this machine."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Secret:
    """Holds a plaintext value, an opaque ciphertext, or both.

    * ``plain`` is set when the user typed it, or when we managed to decrypt.
    * ``cipher`` is the ``enc:v1:...`` string read from disk. If the key is not
      available it is kept untouched and written back on save, so opening a file
      on the "wrong" machine never destroys credentials.
    """

    plain: str | None = None
    cipher: str | None = None

    @property
    def available(self) -> bool:
        return self.plain is not None

    @property
    def locked(self) -> bool:
        return self.plain is None and self.cipher is not None

    def __bool__(self) -> bool:  # truthy if anything is stored
        return bool(self.plain) or bool(self.cipher)

    def __repr__(self) -> str:  # never leak secrets into logs
        state = "plain" if self.plain is not None else ("locked" if self.cipher else "empty")
        return f"Secret(<{state}>)"

    @staticmethod
    def of(value: str | None) -> "Secret | None":
        return Secret(plain=value) if value else None
