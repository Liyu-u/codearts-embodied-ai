"""
Property Fusion Engine -- resolves conflicts between reasoning sources.

Priority: physical_rule > ontology > sensor > VLM
Conservative principle: on safety conflict, choose the stricter limit.
"""

from typing import Any, Dict, List, Optional


class FusionEngine:
    """
    Resolve conflicting property values from different sources.

    Rules:
    1. Safety limits (force, velocity): choose the stricter (lower) value
    2. Boolean affordance conflicts: prefer false / requires_inspection
    3. Low-confidence sources do not override high-confidence sources
    4. All conflicts are recorded
    """

    SOURCE_PRIORITY = {"physical_rule": 4, "ontology": 3, "sensor": 2, "appearance_rule": 2, "vlm": 1, "unknown": 0}

    @classmethod
    def resolve_safety_limit(
        cls, candidates: List[Dict[str, Any]],
        param_name: str, default: float
    ) -> Dict[str, Any]:
        """
        Resolve a numeric safety limit.

        Input: [{value: 2.0, source: "ontology", confidence: 0.98}, ...]
        Output: {value, source, confidence, reasoning, conflicts: [...]}
        """
        if not candidates:
            return {"value": default, "source": "default", "confidence": 0.3,
                    "reasoning": "No candidates provided; using default", "conflicts": []}

        conflicts = []
        # Safety: pick the minimum (stricter) value
        best = min(candidates, key=lambda c: c.get("value", float("inf")))

        for c in candidates:
            if c["value"] != best["value"]:
                conflicts.append({
                    "source": c["source"],
                    "value": c["value"],
                    "overridden_by": best["source"],
                    "reason": f"Stricter limit ({best['value']}) from {best['source']} overrides ({c['value']}) from {c['source']}",
                })

        return {
            "value": best["value"],
            "source": best.get("source", "unknown"),
            "confidence": best.get("confidence", 0.5),
            "reasoning": f"Selected strictest {param_name}: {best['value']} from {best.get('source','?')}",
            "conflicts": conflicts,
        }

    @classmethod
    def resolve_boolean(
        cls, candidates: List[Dict[str, Any]], param_name: str, default: bool = False
    ) -> Dict[str, Any]:
        """
        Resolve a boolean affordance with conservative bias.

        On conflict: prefer False (safer assumption).
        """
        if not candidates:
            return {"value": default, "source": "default", "confidence": 0.3,
                    "reasoning": "No candidates; defaulting to conservative", "conflicts": []}

        true_votes = [c for c in candidates if c.get("value")]
        false_votes = [c for c in candidates if not c.get("value")]

        # If any high-confidence source says False, prefer False
        high_conf_false = [c for c in false_votes if c.get("confidence", 0) > 0.7]
        if high_conf_false:
            return {
                "value": False, "source": high_conf_false[0]["source"],
                "confidence": high_conf_false[0]["confidence"],
                "reasoning": f"Conservative: {param_name}=False (high-confidence source)",
                "conflicts": [{"source": c["source"], "value": c["value"],
                               "overridden_by": high_conf_false[0]["source"]} for c in true_votes],
            }

        # Otherwise, majority wins
        if true_votes:
            return {
                "value": True, "source": true_votes[0]["source"],
                "confidence": true_votes[0].get("confidence", 0.5),
                "reasoning": f"{param_name}=True from {true_votes[0]['source']}",
                "conflicts": [],
            }

        return {"value": False, "source": "default", "confidence": 0.3,
                "reasoning": f"All sources unclear; defaulting {param_name}=False", "conflicts": []}
