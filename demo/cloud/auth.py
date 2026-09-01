from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, replace
from enum import Enum
from typing import MutableMapping

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


class Role(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


_ROLE_ACTIONS: dict[Role, frozenset[str]] = {
    Role.VIEWER: frozenset({"read"}),
    Role.OPERATOR: frozenset({"read", "create_run", "cancel_run"}),
    Role.ADMIN: frozenset(
        {"read", "create_run", "cancel_run", "update_configuration", "manage_users"}
    ),
}


@dataclass(frozen=True, slots=True)
class SessionRecord:
    token_hash: bytes
    user_id: str
    role: Role
    issued_at_ms: int
    expires_at_ms: int
    revoked: bool = False


@dataclass(frozen=True, slots=True)
class IssuedSession:
    token: str
    record: SessionRecord
    cookie: dict[str, object]


def _role(value: Role | str) -> Role:
    try:
        return value if isinstance(value, Role) else Role(value)
    except (TypeError, ValueError) as exc:
        raise PermissionError("unknown browser role") from exc


def authorize(role: Role | str, action: str) -> bool:
    normalized = _role(role)
    if action not in _ROLE_ACTIONS[normalized]:
        raise PermissionError(f"role {normalized.value} cannot perform {action}")
    return True


def issue_session(
    user_id: str,
    role: Role | str,
    sessions: MutableMapping[bytes, SessionRecord],
    *,
    ttl_ms: int,
    now_ms: int,
    https: bool,
) -> IssuedSession:
    if not user_id or ttl_ms <= 0:
        raise ValueError("user_id and a positive ttl_ms are required")
    token = new_session_token()
    digest = session_token_hash(token)
    record = SessionRecord(
        token_hash=digest,
        user_id=user_id,
        role=_role(role),
        issued_at_ms=int(now_ms),
        expires_at_ms=int(now_ms) + int(ttl_ms),
    )
    sessions[digest] = record
    cookie: dict[str, object] = {
        "Name": "closed_loop_session",
        "Value": token,
        "HttpOnly": True,
        "SameSite": "Strict",
        "Secure": bool(https),
        "Path": "/",
        "Max-Age": max(1, int(ttl_ms) // 1000),
    }
    return IssuedSession(token=token, record=record, cookie=cookie)


def _find_session(
    token: str, sessions: MutableMapping[bytes, SessionRecord]
) -> tuple[bytes, SessionRecord]:
    candidate = session_token_hash(token)
    for stored_hash, record in sessions.items():
        if hmac.compare_digest(candidate, stored_hash):
            return stored_hash, record
    raise PermissionError("invalid browser session")


def validate_session(
    token: str,
    sessions: MutableMapping[bytes, SessionRecord],
    *,
    now_ms: int,
) -> SessionRecord:
    _, record = _find_session(token, sessions)
    if record.revoked:
        raise PermissionError("browser session is revoked")
    if int(now_ms) >= record.expires_at_ms:
        raise PermissionError("browser session has expired")
    return record


def revoke_session(
    token: str, sessions: MutableMapping[bytes, SessionRecord]
) -> SessionRecord:
    stored_hash, record = _find_session(token, sessions)
    revoked = replace(record, revoked=True)
    sessions[stored_hash] = revoked
    return revoked
