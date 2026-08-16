"""Explainable candidate scoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


_ATTRIBUTE_ALIASES = {
    "红": "red", "红色": "red", "蓝": "blue", "蓝色": "blue",
    "绿": "green", "绿色": "green", "黄": "yellow", "黄色": "yellow",
    "白": "white", "白色": "white", "黑": "black", "黑色": "black",
    "透明": "transparent", "玻璃": "glass", "塑料": "plastic",
    "金属": "metal", "木质": "wood", "橡胶": "rubber",
}


def _canonical_attribute(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _ATTRIBUTE_ALIASES.get(text, text)


@dataclass
class GroundingScore:
    entity_id: str
    score: float
    evidence: List[str] = field(default_factory=list)


class GroundingScorer:
    def score(self, candidate: Any, category: str | None = None, attributes: Dict[str, Any] | None = None,
              required_affordances: List[str] | None = None, mention: str | None = None,
              peers: List[Any] | None = None) -> GroundingScore:
        attributes = attributes or {}
        required_affordances = required_affordances or []
        actual = getattr(candidate, "specific_class", None) or getattr(candidate, "label", None)
        score = 0.0
        evidence: List[str] = []
        if category and actual == category:
            score += 0.45; evidence.append(f"category={category}")
        elif category and category in str(getattr(candidate, "name", "")):
            score += 0.25; evidence.append(f"category_mention={category}")
        obj_attrs = getattr(candidate, "attributes", {}) or {}
        for key, value in attributes.items():
            if value is None:
                continue
            if _canonical_attribute(obj_attrs.get(key, "")) == _canonical_attribute(value):
                score += 0.25; evidence.append(f"{key}={value}")
            else:
                score -= 0.35
        affordances = {str(a.value if hasattr(a, "value") else a).lower() for a in (getattr(candidate, "affordances", []) or [])}
        affordances.update(str(item).lower() for item in (obj_attrs.get("_upstream_affordances", []) or []))
        for affordance in required_affordances:
            if str(affordance).lower() in affordances:
                score += 0.2; evidence.append(f"affordance={affordance}")
            else:
                score -= 0.25
        # Relative descriptions are deliberately interpreted here, after
        # broad category/attribute retrieval. This keeps language variation
        # separate from scene identity and supports descriptions such as
        # "中间偏后的偏小的蓝色瓶子" without requiring a fixed phrase rule.
        peers = list(peers or [])
        text = str(mention or "")
        bbox = getattr(candidate, "bbox", None)
        volume = (getattr(bbox, "width", 0.0) * getattr(bbox, "height", 0.0) *
                  getattr(bbox, "depth", 0.0)) if bbox is not None else 0.0
        peer_volumes = []
        for item in peers:
            box = getattr(item, "bbox", None)
            if box is not None:
                peer_volumes.append(getattr(box, "width", 0.0) * getattr(box, "height", 0.0) * getattr(box, "depth", 0.0))
        asks_small = any(token in text for token in ("偏小", "较小", "小型", "small")) or (
            "小的" in text and "大小的" not in text
        )
        asks_large = any(token in text for token in ("偏大", "较大", "大型", "大的", "large"))
        if volume and peer_volumes and asks_small:
            if volume < max(peer_volumes) - 1e-9:
                score += 0.30; evidence.append("relative_size=small")
            else:
                # An explicit comparative descriptor is evidence, not a
                # soft hint.  If the candidate is not among the smallest
                # objects, penalize it so a spatial cue cannot silently make
                # an otherwise contradictory object executable.
                score -= 0.30; evidence.append("relative_size!=small")
        if volume and peer_volumes and asks_large:
            if volume > min(peer_volumes) + 1e-9:
                score += 0.30; evidence.append("relative_size=large")
            else:
                score -= 0.30; evidence.append("relative_size!=large")
        positions = [getattr(item, "position", None) for item in peers]
        position = getattr(candidate, "position", None)
        if position is not None and positions:
            ys = [float(getattr(item, "y", 0.0)) for item in positions]
            xs = [float(getattr(item, "x", 0.0)) for item in positions]
            cy, cx = float(getattr(position, "y", 0.0)), float(getattr(position, "x", 0.0))
            # Perception adapters do not all expose the same horizontal axis.
            # Prefer the conventional y axis when it contains a real spread;
            # fall back to x only when y is constant.  This makes a spatial
            # cue useful without hard-coding one simulator's coordinate
            # convention, and leaves identical candidates ambiguous.
            horizontal_values = ys if max(ys) - min(ys) > 1e-9 else xs
            horizontal_value = cy if horizontal_values is ys else cx
            if any(token in text for token in ("左侧", "左边", "靠近左", "left")) and horizontal_value <= min(horizontal_values) + 1e-9:
                score += 0.22; evidence.append("relative_position=left")
            if any(token in text for token in ("右侧", "右边", "靠近右", "right")) and horizontal_value >= max(horizontal_values) - 1e-9:
                score += 0.22; evidence.append("relative_position=right")
            depth_values = xs if max(xs) - min(xs) > 1e-9 else ys
            depth_value = cx if depth_values is xs else cy
            if any(token in text for token in ("前方", "前面", "front")) and depth_value <= min(depth_values) + 1e-9:
                score += 0.22; evidence.append("relative_position=front")
            if any(token in text for token in ("后方", "后面", "behind")) and depth_value >= max(depth_values) - 1e-9:
                score += 0.22; evidence.append("relative_position=behind")
            if any(token in text for token in ("中间", "middle")) and len(xs) >= 3:
                median_x = sorted(xs)[len(xs) // 2]
                if abs(cx - median_x) <= max(0.01, (max(xs) - min(xs)) * 0.35):
                    score += 0.18; evidence.append("relative_position=middle")
        if bbox is not None and any(token in text for token in ("细长", "长条", "elongated")):
            dims = [float(getattr(bbox, key, 0.0)) for key in ("width", "height", "depth")]
            if min(dims) > 0 and max(dims) / min(dims) >= 1.35:
                score += 0.20; evidence.append("relative_shape=elongated")
        if bbox is not None and any(token in text for token in ("矮胖", "短粗", "compact")):
            dims = [float(getattr(bbox, key, 0.0)) for key in ("width", "height", "depth")]
            if min(dims) > 0 and max(dims) / min(dims) < 2.0:
                score += 0.20; evidence.append("relative_shape=compact")
        return GroundingScore(str(getattr(candidate, "id", "")), max(0.0, score), evidence)
