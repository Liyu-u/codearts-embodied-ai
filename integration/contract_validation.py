from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_FILES = {
    "perception.v1": "perception.schema.json",
    "task.v1": "task.schema.json",
    "strategy.v1": "strategy.schema.json",
    "execution.v1": "execution.schema.json",
    "feedback.v1": "feedback.schema.json",
}


class ContractValidationError(ValueError):
    pass


@lru_cache(maxsize=None)
def load_contract(schema_version: str) -> dict:
    filename = CONTRACT_FILES.get(schema_version)
    if filename is None:
        raise ValueError(f"unsupported schema version: {schema_version}")
    path = ROOT / "contracts" / "v1" / filename
    return json.loads(path.read_text(encoding="utf-8"))


def _matches_type(value: Any, expected: str) -> bool:
    checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "number": lambda item: isinstance(item, (int, float))
        and not isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    return checks[expected](value)


def _validate(value: Any, schema: dict, path: str, errors: list[str]) -> None:
    expected_type = schema.get("type")
    if expected_type is not None:
        allowed = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_matches_type(value, item) for item in allowed):
            rendered = " or ".join(allowed)
            errors.append(f"{path}: expected type {rendered}")
            return

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: expected one of {schema['enum']!r}")
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        errors.append(f"{path}: string is shorter than {schema['minLength']}")

    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}.{name}: required property is missing")
        properties = schema.get("properties", {})
        for name, child in value.items():
            if name in properties:
                _validate(child, properties[name], f"{path}.{name}", errors)
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{name}: additional property is not allowed")

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            _validate(item, schema["items"], f"{path}[{index}]", errors)


def validate_contract(value: object, schema_version: str) -> list[str]:
    errors: list[str] = []
    _validate(value, load_contract(schema_version), "$", errors)
    return errors


def assert_contract(value: object, schema_version: str) -> None:
    errors = validate_contract(value, schema_version)
    if errors:
        raise ContractValidationError(
            f"{schema_version} validation failed: " + "; ".join(errors)
        )
