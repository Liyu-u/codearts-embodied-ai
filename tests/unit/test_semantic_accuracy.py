"""Regression tests for A-module semantic accuracy boundaries."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "modules" / "intent_understanding"))

from robot_intent_agent.schemas.semantic_task_graph import (
    SemanticCandidate,
    SemanticEntity,
    SemanticEvent,
    SemanticTaskGraph,
)
from robot_intent_agent.semantic_compiler import SemanticCompiler
from robot_intent_agent.semantic_reasoner.semantic_fusion import SemanticFusion


def _event(action: str, evidence: str, *, theme_ref: str | None = "obj") -> SemanticEvent:
    return SemanticEvent(
        event_id="event-1",
        action=action,
        theme_ref=theme_ref,
        evidence_span=evidence,
    )


def _graph(event: SemanticEvent) -> SemanticTaskGraph:
    return SemanticTaskGraph(
        instruction=event.evidence_span,
        entities=[SemanticEntity(local_ref="obj", mention="红色方块")],
        events=[event],
    )


class SemanticAccuracyTests(unittest.TestCase):
    def test_generic_custom_evidence_cannot_be_upgraded_to_grasp(self):
        current = _event("CUSTOM", "处理一下红色方块")
        incoming = _event("GRASP", "处理一下红色方块")

        self.assertFalse(SemanticFusion._allow_action_correction(
            current, incoming, _graph(current), "处理一下红色方块"
        ))

    def test_explicit_action_evidence_can_correct_custom(self):
        current = _event("CUSTOM", "拿起红色方块")
        incoming = _event("GRASP", "拿起红色方块")

        self.assertTrue(SemanticFusion._allow_action_correction(
            current, incoming, _graph(current), "拿起红色方块"
        ))

    def test_provider_candidates_are_ranked_by_confidence(self):
        instruction = "抓取红色方块"
        low_graph = _graph(_event("GRASP", "抓取红色方块"))
        high_graph = _graph(_event("GRASP", "抓取红色方块"))
        low = SemanticCandidate.from_graph(low_graph, confidence=0.41, source="llm")
        high = SemanticCandidate.from_graph(high_graph, confidence=0.92, source="llm")

        selected, ordered, trace = SemanticCompiler._select_llm_candidate(
            [low, high], instruction
        )

        self.assertIs(selected, high)
        self.assertIs(ordered[0], high)
        self.assertTrue(next(item for item in trace if item["index"] == 1)["selected"])
        self.assertFalse(next(item for item in trace if item["index"] == 0)["selected"])


if __name__ == "__main__":
    unittest.main()
