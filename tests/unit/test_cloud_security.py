from __future__ import annotations

import json
import unittest

from integration.contract_validation import ContractValidationError

from demo.cloud.security import (
    ALLOWED_ARTIFACTS,
    MAX_JSON_BYTES,
    read_json_body,
    require_bearer,
    validate_artifact,
)


class CloudSecurityTests(unittest.TestCase):
    def test_require_bearer_honors_token_and_rejects_bad_headers(self) -> None:
        with self.assertRaises(PermissionError):
            require_bearer(None, "relay-token")
        with self.assertRaises(PermissionError):
            require_bearer("Bearer wrong-token", "relay-token")
        with self.assertRaises(RuntimeError):
            require_bearer("Bearer relay-token", "")
        self.assertEqual(
            require_bearer("Bearer relay-token", "relay-token"),
            "relay-token",
        )

    def test_read_json_body_rejects_oversized_body(self) -> None:
        oversized = ("x" * (MAX_JSON_BYTES + 1)).encode("utf-8")
        with self.assertRaises(ValueError):
            read_json_body(oversized)

        self.assertEqual(read_json_body(json.dumps({"ok": True}).encode("utf-8")), {"ok": True})

    def test_validate_artifact_enforces_allowlist_and_schema(self) -> None:
        self.assertIn("perception.json", ALLOWED_ARTIFACTS)
        validate_artifact(
            "perception.json",
            {
                "schema_version": "perception.v1",
                "scene_id": "stacking_cubes",
                "objects": [
                    {
                        "id": "green_cube",
                        "category": "block",
                        "pose": {"x": 0.0, "y": 0.0, "z": 0.1},
                    }
                ],
            },
        )
        with self.assertRaises(ContractValidationError):
            validate_artifact("perception.json", {"schema_version": "perception.v1"})
        with self.assertRaises(ValueError):
            validate_artifact("../perception.json", {"schema_version": "perception.v1"})
        with self.assertRaises(ValueError):
            validate_artifact("not-allowed.json", {"schema_version": "perception.v1"})


if __name__ == "__main__":
    unittest.main()
