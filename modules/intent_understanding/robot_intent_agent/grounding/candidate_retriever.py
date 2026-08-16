"""Candidate retrieval: broad recall only, never final binding."""

from __future__ import annotations

from typing import Any, Iterable, List, Optional


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


class CandidateRetriever:
    def retrieve(self, scene: Any, category: Optional[str] = None, attributes: Optional[dict] = None,
                 mention: Optional[str] = None,
                 exclude_ids: Optional[set[str]] = None) -> List[Any]:
        attributes = attributes or {}
        excluded = exclude_ids or set()
        objects = list(getattr(scene, "objects", []) or []) if scene is not None else []
        if mention:
            mention_text = str(mention).strip().lower()
            mention_aliases = {
                "杯子": "cup", "水杯": "cup", "玻璃杯": "glass",
                "托盘": "tray", "盒子": "box", "箱子": "box",
                "方块": "block", "积木": "block", "小球": "ball",
                "桌子": "table", "桌": "table", "花瓶": "vase",
                "水壶": "hot_kettle", "热水壶": "hot_kettle",
                "书": "book", "轴承": "bearing", "齿轮": "gear",
                "组件": "component", "零件": "part", "工件": "workpiece",
                "收纳箱": "bin", "柜子": "cabinet", "焊接区": "welding_zone",
            }
            canonical_mention = mention_aliases.get(mention_text, mention_text)
            mention_matches = [obj for obj in objects if any(
                value and (
                    mention_text in str(value).lower() or str(value).lower() in mention_text or
                    canonical_mention in str(value).lower() or str(value).lower() in canonical_mention
                )
                for value in (getattr(obj, "name", ""), getattr(obj, "original_mention", ""),
                              getattr(obj, "label", ""), getattr(obj, "specific_class", ""))
            )]
            if mention_matches:
                objects = mention_matches
        result = []
        for obj in objects:
            obj_id = getattr(obj, "id", "")
            if obj_id in excluded:
                continue
            actual = getattr(obj, "specific_class", None) or getattr(obj, "label", None) or getattr(obj, "name", "")
            obj_attrs = getattr(obj, "attributes", {}) or {}
            actual_values = {
                str(actual or "").lower(),
                str(getattr(obj, "parent_class", None) or "").lower(),
                str(getattr(obj, "label", None) or "").lower(),
                str(getattr(obj, "name", None) or "").lower(),
            }
            affordances = {
                str(item.value if hasattr(item, "value") else item).lower()
                for item in (getattr(obj, "affordances", []) or [])
            }
            affordances.update(str(item).lower() for item in (obj_attrs.get("_upstream_affordances", []) or []))
            # Retrieval is deliberately broad.  Role/action feasibility and
            # joint scoring decide the final binding; category filtering here
            # must not erase valid candidates for generic mentions such as
            # "object", "container" or "support surface".
            if category:
                category_key = str(category).lower()
                generic = {"object", "item", "unknown", "entity", "container", "material"}
                category_match = category_key in actual_values or category_key in affordances
                if category_key == "support_surface":
                    category_match = category_match or bool({"fixed", "support_surface"} & affordances)
                if category_key in {"operator", "human", "recipient"}:
                    category_match = category_match or bool({"recipient", "reachable"} & affordances)
                if category_key in {"operator", "human", "recipient"}:
                    category_match = category_match or bool({"recipient", "reachable"} & affordances)
                if category_key not in generic and not category_match:
                    attrs_text = str(obj_attrs).lower()
                    if category_key not in attrs_text:
                        continue
            # Relative size/shape/spatial wording is ranked by the scorer;
            # it is not an exact perception attribute and must not erase all
            # candidates at retrieval time.
            if any(key not in {"size", "shape", "spatial_relation"}
                   and value is not None
                   and _canonical_attribute(obj_attrs.get(key, ""))
                       != _canonical_attribute(value)
                   for key, value in attributes.items() if value is not None):
                continue
            result.append(obj)
        return result
