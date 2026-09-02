"""A-module safety gates for stable IDs, capabilities and task identity."""

import re
import unittest
from copy import deepcopy
from uuid import UUID

from integration.adapters import intent, perception
from modules.intent_understanding.adapter import _build_scene, _classify_intent_failure


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

    def test_unknown_color_target_is_not_replaced_by_visible_object(self):
        perception = _perception()
        result = intent.run({
            "instruction": "把紫色方块放到桌子上",
            "perception": perception,
        })

        self.assertEqual(result["status"], "NEEDS_CLARIFICATION", result)
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(result["target_ids"], [])
        self.assertNotEqual(result["target_ids"], ["obj-red"])

    def test_common_block_and_nominal_place_wording_ground_safely(self):
        scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
        cases = (
            ("请把绿块放到桌子上", "green_cube", "zone_unstack_target"),
            ("请帮我完成绿色方块的放置", "green_cube", "zone_unstack_target"),
            ("请帮我把绿色方块放好", "green_cube", "zone_unstack_target"),
        )
        for instruction, target_id, destination_id in cases:
            with self.subTest(instruction=instruction):
                result = intent.run({"instruction": instruction, "perception": scene})
                self.assertEqual(result["status"], "READY", result)
                self.assertTrue(result["execution_allowed"])
                self.assertEqual(result["target_ids"], [target_id])
                self.assertEqual(result["destination_id"], destination_id)

        sorting_scene = perception.run({"scene_id": "sorting_workcell", "backend": "mock"})
        result = intent.run({
            "instruction": "将绿色方块归位到绿色托盘",
            "perception": sorting_scene,
        })
        self.assertEqual(result["status"], "READY", result)
        self.assertEqual(result["target_ids"], ["green_sort_cube"])
        self.assertEqual(result["destination_id"], "middle_sort_tray")

    def test_explicit_object_destination_is_not_replaced_by_unique_table(self):
        scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
        result = intent.run({
            "instruction": "把红色方块放到绿色方块上",
            "perception": scene,
        })

        self.assertEqual(result["status"], "NEEDS_CLARIFICATION", result)
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(result["target_ids"], ["red_cube"])
        self.assertEqual(result["destination_id"], "green_cube")
        self.assertTrue(any("DESTINATION" in item for item in result["blocking_reasons"]))

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

    def test_execution_metadata_overrides_generic_scene_affordances(self):
        perception = {
            "schema_version": "perception.v1",
            "scene_id": "scene-execution-affordance",
            "objects": [
                {
                    "id": "zone-unstack-target",
                    "category": "放置区域",
                    "pose": {"x": 0.45, "y": 0.10, "z": 0.025},
                    "dimensions": {"x": 0.10, "y": 0.10, "z": 0.02},
                    "attributes": {
                        "display_name": "放置区域",
                        "purpose": "safe_placement",
                    },
                    "execution": {
                        "graspable": False,
                        "movable": False,
                        "valid_destination": True,
                    },
                },
                {
                    "id": "movable-stack-target",
                    "category": "红色方块",
                    "pose": {"x": 0.50, "y": -0.10, "z": 0.03},
                    "dimensions": {"x": 0.04, "y": 0.04, "z": 0.04},
                    "attributes": {"color": "red"},
                    "execution": {
                        "graspable": True,
                        "movable": True,
                        "valid_destination": True,
                    },
                },
            ],
        }

        scene = _build_scene(perception)

        zone = scene.find_object("zone-unstack-target")
        self.assertIsNotNone(zone)

        zone_affordances = {
            item.value if hasattr(item, "value") else str(item)
            for item in zone.affordances
        }

        self.assertIn("fixed", zone_affordances)
        self.assertNotIn("movable", zone_affordances)
        self.assertNotIn("graspable", zone_affordances)

        movable = scene.find_object("movable-stack-target")
        self.assertIsNotNone(movable)

        movable_affordances = {
            item.value if hasattr(item, "value") else str(item)
            for item in movable.affordances
        }

        self.assertIn("movable", movable_affordances)
        self.assertIn("graspable", movable_affordances)
        self.assertNotIn("fixed", movable_affordances)

    def test_provider_balance_failure_has_stable_diagnostic_class(self):
        class BalanceError(Exception):
            status_code = 402

        code, failure_class = _classify_intent_failure(BalanceError("Insufficient Balance"))

        self.assertEqual(code, "INTENT_PROVIDER_BALANCE")
        self.assertEqual(failure_class, "provider_balance")

    def test_degraded_camera_quality_blocks_execution_before_semantic_grounding(self):
        value = _perception()
        value["quality"] = {
            "status": "DEGRADED",
            "reasons": ["LOW_DEPTH_VALID_RATIO"],
        }

        result = self._run(value)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn(
            "PERCEPTION_QUALITY_DEGRADED:LOW_DEPTH_VALID_RATIO",
            result["blocking_reasons"],
        )


if __name__ == "__main__":
    unittest.main()
