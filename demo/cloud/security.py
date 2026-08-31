from __future__ import annotations

import hmac
import json
from pathlib import PurePosixPath
from typing import Any

from integration.contract_validation import assert_contract


MAX_JSON_BYTES = 2_000_000
ALLOWED_ARTIFACTS: dict[str, str | None] = {
    "perception.json": "perception.v1",
    "execution.json": "execution.v1",
    "final_pose.json": None,
    "progress.jsonl": None,
    "container_log_summary.json": None,
}


def require_bearer(
    authorization_header: str | None,
    relay_token: str,
    *,
    production: bool = True,
) -> str:
    if production and not relay_token:
        raise RuntimeError("production relay token must not be empty")
    if not authorization_header:
        raise PermissionError("missing bearer token")
    prefix = "Bearer "
    if not authorization_header.startswith(prefix):
        raise PermissionError("missing bearer token")
    presented = authorization_header[len(prefix) :]
    if not hmac.compare_digest(presented, relay_token):
        raise PermissionError("invalid bearer token")
    return presented


def read_json_body(body: bytes | bytearray | memoryview | str, *, max_bytes: int = MAX_JSON_BYTES) -> Any:
    if isinstance(body, str):
        encoded = body.encode("utf-8")
    else:
        encoded = bytes(body)
    if len(encoded) > max_bytes:
        raise ValueError(f"json body exceeds {max_bytes} bytes")
    return json.loads(encoded.decode("utf-8"))


def _is_safe_artifact_name(artifact_name: str) -> bool:
    if not artifact_name or artifact_name in {".", ".."}:
        return False
    if "/" in artifact_name or "\\" in artifact_name:
        return False
    return PurePosixPath(artifact_name).name == artifact_name


def validate_artifact(artifact_name: str, value: object) -> None:
    if not _is_safe_artifact_name(artifact_name):
        raise ValueError(f"invalid artifact path: {artifact_name}")
    schema_version = ALLOWED_ARTIFACTS.get(artifact_name)
    if schema_version is None:
        if artifact_name not in ALLOWED_ARTIFACTS:
            raise ValueError(f"artifact not allowlisted: {artifact_name}")
        return
    assert_contract(value, schema_version)
