from __future__ import annotations

import hashlib
import hmac
import re
import secrets

_PBKDF2_ITERATIONS = 310_000
_SALT_BYTES = 16
_SESSION_TOKEN_BYTES = 32
_PASSWORD_MIN_LENGTH = 8
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")


def hash_password(password: str) -> tuple[bytes, bytes]:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
    )
    return salt, digest


def verify_password(password: str, salt: bytes, digest: bytes) -> bool:
    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
    )
    return hmac.compare_digest(candidate, digest)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_registration(username: str, email: str, password: str) -> None:
    normalized_username = username.strip()
    normalized_email = normalize_email(email)

    if not _USERNAME_PATTERN.fullmatch(normalized_username):
        raise ValueError("用户名必须是 3 到 64 个字符，且只包含字母、数字、下划线、点或连字符")
    if "@" not in normalized_email or normalized_email.startswith("@") or normalized_email.endswith("@"):
        raise ValueError("邮箱格式无效")
    if len(password) < _PASSWORD_MIN_LENGTH:
        raise ValueError("密码至少需要 8 个字符")


def new_session_token() -> str:
    return secrets.token_urlsafe(_SESSION_TOKEN_BYTES)


def session_token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()
