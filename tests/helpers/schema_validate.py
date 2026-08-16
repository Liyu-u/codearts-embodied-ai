"""零依赖的最小 JSON Schema 校验器（覆盖 contracts/v1 用到的 draft-2020-12 子集）。

统一联调仓库的契约测试需要校验模块输出是否符合 contracts/v1 里的 schema。
为不引入 jsonschema 第三方依赖（联调环境可能离线），这里实现 schema 用到
的最小能力子集：
  - type / required / properties / items（递归）
  - enum / const / minLength
  - additionalProperties（contracts/v1 中均为 true，遇到 false 会报错）

用法::

    from tests.helpers.schema_validate import validate
    errors = validate(instance, schema)
    assert not errors, errors
"""

from __future__ import annotations

from typing import Any


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"  # bool 是 int 的子类，必须优先判断
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _check_type(value: Any, expected: Any, path: str) -> list[str]:
    if isinstance(expected, list):  # type: ["string", "null"]
        return [] if _type_name(value) in expected else [
            f"{path}: 类型应为 {expected}，实际为 {_type_name(value)}"
        ]
    if expected == "number" and isinstance(value, bool):
        return [f"{path}: 布尔值不是有效 number"]
    # integer 允许 int；number 允许 int/float
    if expected == "number":
        return [] if isinstance(value, (int, float)) and not isinstance(value, bool) else [
            f"{path}: 类型应为 number，实际为 {_type_name(value)}"
        ]
    if expected == "integer":
        return [] if isinstance(value, int) and not isinstance(value, bool) else [
            f"{path}: 类型应为 integer，实际为 {_type_name(value)}"
        ]
    if _type_name(value) != expected:
        return [f"{path}: 类型应为 {expected}，实际为 {_type_name(value)}"]
    return []


def validate(instance: Any, schema: dict, path: str = "$") -> list[str]:
    """校验 instance 是否符合 schema，返回错误列表（空 = 通过）。"""
    errors: list[str] = []

    if "const" in schema:
        if instance != schema["const"]:
            errors.append(f"{path}: 必须等于 {schema['const']!r}，实际为 {instance!r}")
        return errors

    if "type" in schema:
        errors.extend(_check_type(instance, schema["type"], path))

    if "enum" in schema:
        if instance not in schema["enum"]:
            errors.append(f"{path}: 取值必须在 {schema['enum']} 内，实际为 {instance!r}")

    if isinstance(instance, str) and "minLength" in schema:
        if len(instance) < schema["minLength"]:
            errors.append(f"{path}: 长度必须 >= {schema['minLength']}")

    if isinstance(instance, dict):
        for required in schema.get("required", []):
            if required not in instance:
                errors.append(f"{path}: 缺少必填字段 {required!r}")
        for key, value in instance.items():
            prop_schema = schema.get("properties", {}).get(key)
            if prop_schema is not None:
                errors.extend(validate(value, prop_schema, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: 不允许额外字段 {key!r}")

    if isinstance(instance, list):
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for index, item in enumerate(instance):
                errors.extend(validate(item, items_schema, f"{path}[{index}]"))

    return errors


def load_schema(relative_path: str) -> dict:
    """从仓库 contracts/v1 读取 schema（相对仓库根）。"""
    import json
    from pathlib import Path

    # 本文件位于 tests/helpers/ 下：parents[0]=helpers, [1]=tests, [2]=仓库根
    root = Path(__file__).resolve().parents[2]
    schema_path = root / "contracts" / "v1" / relative_path
    return json.loads(schema_path.read_text(encoding="utf-8"))
