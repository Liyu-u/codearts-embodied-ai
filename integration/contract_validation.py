from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_FILES = {
    "perception_observation.1.0.0": "perception_observation.schema.json",
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


def _resolve_local_ref(root_schema: dict, ref: str) -> dict:
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported schema reference: {ref}")
    resolved: Any = root_schema
    for segment in ref[2:].split("/"):
        key = segment.replace("~1", "/").replace("~0", "~")
        resolved = resolved[key]
    if not isinstance(resolved, dict):
        raise ValueError(f"schema reference does not resolve to an object: {ref}")
    return resolved


def _validate(
    value: Any,
    schema: dict,
    path: str,
    errors: list[str],
    root_schema: dict | None = None,
) -> None:
    root_schema = root_schema or schema
    if "$ref" in schema:
        _validate(
            value,
            _resolve_local_ref(root_schema, schema["$ref"]),
            path,
            errors,
            root_schema,
        )
        return

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
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: number is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: number is above maximum {schema['maximum']}")

    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}.{name}: required property is missing")
        properties = schema.get("properties", {})
        for name, child in value.items():
            if name in properties:
                _validate(
                    child,
                    properties[name],
                    f"{path}.{name}",
                    errors,
                    root_schema,
                )
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{name}: additional property is not allowed")

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: array has fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: array has more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            fingerprints = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value]
            if len(fingerprints) != len(set(fingerprints)):
                errors.append(f"{path}: array items must be unique")
        for index, item in enumerate(value):
            _validate(item, schema["items"], f"{path}[{index}]", errors, root_schema)


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
