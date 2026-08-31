from __future__ import annotations

import pytest
from unittest.mock import patch

from demo.cloud.credentials import CredentialCipher


def test_credential_cipher_round_trip_from_env() -> None:
    key = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
    with patch.dict("os.environ", {"CLOUD_CREDENTIALS_KEY": key}, clear=False):
        cipher = CredentialCipher.from_env(production=True)

        encrypted = cipher.encrypt("super-secret")

        assert encrypted != b"super-secret"
        assert cipher.decrypt(encrypted) == "super-secret"


def test_credential_cipher_requires_production_key() -> None:
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(RuntimeError, match="CLOUD_CREDENTIALS_KEY"):
            CredentialCipher.from_env(production=True)
