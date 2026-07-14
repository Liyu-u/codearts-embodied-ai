"""
页面可视化组件
同学 A：JSON 渲染器、状态卡片、场景可视化组件
"""

from typing import Any, Dict


def render_intent_card(intent_json: Dict[str, Any]) -> str:
    """将意图 JSON 渲染为可视化的状态卡片 HTML"""
    action_colors = {
        "pick": "#4CAF50",
        "place": "#2196F3",
        "push": "#FF9800",
        "pull": "#9C27B0",
    }

    action = intent_json.get("action", "unknown")
    color = action_colors.get(action, "#757575")

    return f"""
    <div style="border: 2px solid {color}; border-radius: 12px; padding: 16px; margin: 8px;">
        <h3 style="color: {color};">🎯 动作: {action.upper()}</h3>
        <p><b>目标物体:</b> {intent_json.get('target_object', 'N/A')}</p>
        <p><b>原始指令:</b> {intent_json.get('raw_text', '')}</p>
    </div>
    """


def render_scene_overlay(scene_json: Dict[str, Any]) -> Dict[str, Any]:
    """将场景 JSON 转为 3D 可视化标注数据"""
    objects = scene_json.get("objects", [])
    annotations = []

    for obj in objects:
        bbox = obj.get("bbox", {})
        annotations.append(
            {
                "label": obj.get("name", "unknown"),
                "position": obj.get("position", {}),
                "size": {
                    "w": bbox.get("width", 0),
                    "h": bbox.get("height", 0),
                    "d": bbox.get("depth", 0),
                },
            }
        )

    return {"objects": objects, "annotations": annotations}
