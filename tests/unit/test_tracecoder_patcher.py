"""Safety checks for malformed D/TraceCoder patch responses."""

from __future__ import annotations

import unittest

from modules.evaluator.tracecoder.patcher import validate_patch


class TraceCoderPatcherTests(unittest.TestCase):
    def test_non_object_change_is_rejected_without_exception(self):
        result = validate_patch({"changes": ["allowed patch change"]})

        self.assertFalse(result["passed"])
        self.assertIn("必须是包含 operation 的 JSON 对象", result["issues"][0])

    def test_non_object_patch_is_rejected_without_exception(self):
        result = validate_patch("not a patch")

        self.assertFalse(result["passed"])
        self.assertEqual(result["issues"], ["修改结果必须是 JSON 对象。"])


if __name__ == "__main__":
    unittest.main()
