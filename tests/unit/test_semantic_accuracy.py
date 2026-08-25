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
from robot_intent_agent.schemas.scene import (
    Affordance,
    BoundingBox,
    Position,
    SceneObject,
    SemanticSceneGraph,
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
    def test_llm_cannot_substitute_an_explicit_missing_target(self):
        """Provider redirection must keep an explicit purple target blocked."""
        scene = SemanticSceneGraph(objects=[
            SceneObject(
                id="red_cube",
                name="红色方块",
                specific_class="block",
                parent_class="object",
                parent_classes=["block", "object"],
                position=Position(x=0.0, y=0.0, z=0.02),
                bbox=BoundingBox(width=0.04, height=0.04, depth=0.04),
                attributes={"color": "red"},
                affordances=[Affordance.GRASPABLE, Affordance.MOVABLE],
            ),
            SceneObject(
                id="table",
                name="桌子",
                specific_class="table",
                parent_class="support_surface",
                parent_classes=["table", "support_surface"],
                position=Position(x=0.2, y=0.0, z=0.02),
                bbox=BoundingBox(width=0.5, height=0.05, depth=0.5),
                attributes={},
                affordances=[Affordance.FIXED],
            ),
        ])

        class IncorrectProvider:
            is_available = True
            last_call_metadata = {}

            @staticmethod
            def semantic_candidates(instruction, scene=None, memory_context=None):
                del scene, memory_context
                graph = SemanticTaskGraph(
                    instruction=instruction,
                    entities=[
                        SemanticEntity(
                            local_ref="provider-target",
                            mention="红色方块",
                            category="block",
                            attributes={"color": "red"},
                        ),
                        SemanticEntity(
                            local_ref="provider-destination",
                            mention="桌子",
                            category="table",
                        ),
                    ],
                    events=[SemanticEvent(
                        event_id="event-1",
                        action="PLACE",
                        theme_ref="provider-target",
                        destination_ref="provider-destination",
                        evidence_span=instruction,
                    )],
                )
                return [SemanticCandidate.from_graph(graph, confidence=0.9, source="llm")]

        result = SemanticCompiler(IncorrectProvider()).compile(
            "请把紫色方块放到桌子上", scene=scene, mode="llm"
        )

        self.assertTrue(result.engine_trace["fallback_used"])
        self.assertIn("EXPLICIT_RULE_CONSTRAINT_CHANGED", result.engine_trace["fallback_reason"])
        self.assertIsNone(next(
            entity for entity in result.graph.entities
            if entity.attributes.get("color") == "purple"
        ).entity_id)

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
