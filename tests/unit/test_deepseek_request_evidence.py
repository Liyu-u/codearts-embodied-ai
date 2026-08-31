import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = ROOT / "modules" / "intent_understanding"
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from robot_intent_agent.planner.llm_planner import LLMPlanner


class DeepSeekRequestEvidenceTests(unittest.TestCase):
    def test_provider_response_id_is_recorded(self):
        response = SimpleNamespace(
            id="deepseek-response-123",
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"schema_version":"semantic-candidate-1.0","candidates":[]}'))],
        )

        class Completions:
            @staticmethod
            def create(**kwargs):
                return response

        planner = LLMPlanner(api_key="not-a-real-key")
        planner._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        planner._call_api("test")

        self.assertEqual(planner.last_call_metadata["request_id"], "deepseek-response-123")
        self.assertEqual(planner.last_call_metadata["request_id_source"], "provider_response")


if __name__ == "__main__":
    unittest.main()
