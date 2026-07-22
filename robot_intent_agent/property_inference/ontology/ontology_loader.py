"""
Ontology Loader v2.0 -- enhanced query with match_type, confidence, reasoning.
"""
from pathlib import Path
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_ONTOLOGY_PATH = Path(__file__).parent / "physics_ontology.json"


@dataclass
class OntologyResult:
    matched_category: str
    properties: Dict[str, Any]
    match_type: str          # "exact" | "alias" | "fuzzy" | "none"
    confidence: float        # 1.0 for exact, lower for fuzzy
    source: str = "ontology"
    match_reason: str = ""


class OntologyLoader:
    """Load and query the physics ontology with match type tracking."""

    def __init__(self):
        with open(_ONTOLOGY_PATH, "r", encoding="utf-8") as f:
            self._data = json.load(f)
        self._entries: Dict[str, Dict] = self._data.get("entries", {})
        self._alias_map: Dict[str, str] = {}
        self._build_alias_index()

    def _build_alias_index(self):
        for key, entry in self._entries.items():
            for alias in entry.get("aliases", []):
                self._alias_map[alias.lower()] = key

    def _normalize(self, text: str) -> str:
        return re.sub(r"[_\- ]+", "_", text.strip().lower())

    def query(self, category: str) -> OntologyResult:
        """
        Query ontology with match type tracking.

        Returns OntologyResult with:
        - match_type: "exact" | "alias" | "fuzzy" | "none"
        - confidence: 1.0 (exact), 0.95 (alias), 0.6-0.8 (fuzzy), 0.0 (none)
        - match_reason: human-readable explanation
        """
        norm = self._normalize(category)

        # 1. Exact match
        if norm in self._entries:
            return OntologyResult(
                matched_category=norm,
                properties=dict(self._entries[norm]),
                match_type="exact",
                confidence=1.0,
                match_reason=f"Exact ontology match: '{norm}'",
            )

        # 2. Alias match
        if norm in self._alias_map:
            target = self._alias_map[norm]
            return OntologyResult(
                matched_category=target,
                properties=dict(self._entries[target]),
                match_type="alias",
                confidence=0.95,
                match_reason=f"Alias match: '{norm}' -> '{target}'",
            )

        # 3. Fuzzy match (substring-based, with threshold safeguard)
        best_key = None
        best_score = 0.0
        for key in self._entries:
            if norm in key or key in norm:
                score = len(set(norm) & set(key)) / max(len(norm), len(key))
                if score > best_score:
                    best_score = score
                    best_key = key

        if best_key and best_score >= 0.4:
            conf = round(0.6 + 0.2 * best_score, 2)
            return OntologyResult(
                matched_category=best_key,
                properties=dict(self._entries[best_key]),
                match_type="fuzzy",
                confidence=conf,
                match_reason=f"Fuzzy match: '{norm}' ~ '{best_key}' (score={best_score:.2f})",
            )

        # 4. No match
        return OntologyResult(
            matched_category="unknown",
            properties={},
            match_type="none",
            confidence=0.0,
            match_reason=f"No ontology entry for '{category}' or its aliases",
        )

    def list_categories(self) -> list:
        return list(self._entries.keys())
