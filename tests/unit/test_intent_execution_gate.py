"""A-module safety gates for stable IDs, capabilities and task identity."""

import re
import unittest
from copy import deepcopy
from uuid import UUID

from integration.adapters import intent


def _perception(*, target_execution=None, destination_execution=None, dimensions=None):
    target_execution = {"graspable": True} if target_execution is None else target_execution
    destination_execution = {"valid_destination": True} if destination_execution is None else destination_execution
    return {
        "schema_version": "perception.v1",
        "scene_id": "scene-a-gate",
        "objects": [
            {
                "id": "obj-red",
                "category": "红色方块",
                "pose": {"x": 0.1, "y": 0.0, "z": 0.03},
                "dimensions": dimensions or {"x": 0.04, "y": 0.04, "z": 0.04},
                "attributes": {"color": "red"},
                "execution": target_execution,
            },
            {
                "id": "surface-table",
                "category": "桌子",
                "pose": {"x": 0.3, "y": 0.0, "z": 0.03},
                "dimensions": {"width": 0.5, "height": 0.05, "depth": 0.5},
                "attributes": {"support_surface": True},
                "execution": destination_execution,
            },
        ],
    }


class IntentExecutionGateTests(unittest.TestCase):
    def _run(self, perception):
        return intent.run({
            "instruction": "把红色方块放到桌子上",
            "perception": perception,
        })

    def test_task_id_is_uuid_and_not_scene_id(self):
        first = self._run(_perception())
        second = self._run(_perception())

        UUID(first["task_id"])
        UUID(second["task_id"])
        self.assertNotEqual(first["task_id"], second["task_id"])
        self.assertNotEqual(first["task_id"], "scene-a-gate")

    def test_missing_target_capability_blocks_before_ready(self):
        result = self._run(_perception(target_execution={"graspable": False}))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["execution_allowed"])
        self.assertTrue(any("TARGET_NOT_GRASPABLE" in item for item in result["blocking_reasons"]))

    def test_missing_destination_capability_blocks_before_ready(self):
        result = self._run(_perception(destination_execution={}))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["execution_allowed"])
        self.assertTrue(any("DESTINATION_VALIDITY_UNKNOWN" in item for item in result["blocking_reasons"]))

    def test_missing_or_duplicate_ids_never_get_synthetic_ids(self):
        missing = _perception()
        del missing["objects"][0]["id"]
        result = self._run(missing)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("MISSING_OBJECT_ID", result["diagnostics"]["message"])
        self.assertFalse(any(re.fullmatch(r"obj-\d+", item) for item in result["target_ids"]))

        duplicate = _perception()
        duplicate["objects"].append(deepcopy(duplicate["objects"][0]))
        result = self._run(duplicate)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("DUPLICATE_OBJECT_ID", result["diagnostics"]["message"])

    def test_dimensions_accept_both_aliases_but_do_not_default(self):
        result = self._run(_perception(dimensions={"x": 0.04, "y": 0.04, "z": 0.04}))
        self.assertNotIn("MISSING_DIMENSIONS", result["diagnostics"].get("message", ""))

        missing = _perception(dimensions={"width": 0.04, "height": 0.04})
        result = self._run(missing)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("MISSING_DIMENSIONS", result["diagnostics"]["message"])

    def test_task_public_output_has_id_not_destination_coordinates(self):
        result = self._run(_perception())
        self.assertNotIn("destination", result)
        self.assertIn("destination_id", result)


if __name__ == "__main__":
    unittest.main()
