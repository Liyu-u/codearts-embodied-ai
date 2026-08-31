from __future__ import annotations

import os
from dataclasses import dataclass

from cryptography.fernet import Fernet


@dataclass(frozen=True, slots=True)
class CredentialCipher:
    _fernet: Fernet

    @classmethod
    def from_env(cls, production: bool = True) -> "CredentialCipher":
        key = os.environ.get("CLOUD_CREDENTIALS_KEY", "").strip()
        if not key:
            if production:
                raise RuntimeError("CLOUD_CREDENTIALS_KEY is required in production")
            key = Fernet.generate_key().decode("ascii")
        return cls(Fernet(key.encode("ascii")))

    def encrypt(self, value: str) -> bytes:
        return self._fernet.encrypt(value.encode("utf-8"))

    def decrypt(self, value: bytes) -> str:
        return self._fernet.decrypt(value).decode("utf-8")
