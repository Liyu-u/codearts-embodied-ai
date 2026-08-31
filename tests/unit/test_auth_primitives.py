from __future__ import annotations

import pytest

from demo.cloud.auth import (
    hash_password,
    new_session_token,
    normalize_email,
    session_token_hash,
    validate_registration,
    verify_password,
)


def test_password_round_trip_and_unique_salts() -> None:
    salt1, digest1 = hash_password("Long-password-123!")
    salt2, digest2 = hash_password("Long-password-123!")
    assert salt1 != salt2
    assert digest1 != digest2
    assert verify_password("Long-password-123!", salt1, digest1)
    assert not verify_password("wrong", salt1, digest1)


def test_registration_validation_normalizes_email_and_rejects_short_password() -> None:
    assert normalize_email("  User@Example.COM ") == "user@example.com"
    with pytest.raises(ValueError, match="密码"):
        validate_registration("robot_user", "user@example.com", "short")


def test_session_tokens_are_url_safe_and_hashed_stably() -> None:
    token = new_session_token()
    assert token
    assert "." not in token
    assert "/" not in token
    assert "+" not in token
    assert session_token_hash(token) == session_token_hash(token)
    assert session_token_hash(token) != session_token_hash(new_session_token())
