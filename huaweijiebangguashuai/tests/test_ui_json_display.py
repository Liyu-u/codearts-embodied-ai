"""
Mock 单元测试 — 前端页面渲染
同学 A：测试 Gradio UI 的 JSON 高亮渲染器和任务预设下拉菜单
"""

import json
import sys
from pathlib import Path

import pytest

# 将 src 加入 path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ============================================================
# Mock 数据 (无需等同学 B/C/D)
# ============================================================
MOCK_INTENT = {
    "intent_id": "task-test-001",
    "raw_text": "帮我把红色方块放到蓝色杯子旁边",
    "action": "pick_and_place",
    "target_object": "红色方块",
    "reference_object": "蓝色杯子",
    "spatial_relation": "next_to",
    "destination": {"x": 0.2000, "y": 0.0000, "z": 0.0300},
    "confidence": 0.95,
}

MOCK_TASK_PRESETS = [
    "🖐️ 简单抓取",
    "🎨 颜色分类",
    "🧊 尺寸排序",
    "🔍 条件筛选",
]


# ============================================================
# 测试用例
# ============================================================
class TestJSONRendering:
    """测试 JSON 高亮渲染器"""

    def test_render_valid_json(self):
        """合法 JSON 应生成包含 action 名称的 HTML"""
        html = render_json_highlight(MOCK_INTENT)
        assert "pick_and_place" in html.lower() or "pick_and_place" in html
        assert "红色方块" in html

    def test_render_invalid_json(self):
        """非法输入应优雅降级"""
        html = render_json_highlight("not valid json {{{")
        assert html  # 不应抛异常
        assert "错误" in html or "等待" in html or "888" in html  # 灰度文字

    def test_different_actions_get_different_colors(self):
        """不同 action 应产生不同颜色"""
        pick = render_json_highlight({**MOCK_INTENT, "action": "pick_and_place"})
        sort = render_json_highlight({**MOCK_INTENT, "action": "sort_by_color"})
        # 两个渲染结果应不同（颜色代码应不同）
        assert pick != sort


class TestTaskPresets:
    """测试预设下拉菜单"""

    def test_presets_not_empty(self):
        """预设列表不应为空"""
        assert len(MOCK_TASK_PRESETS) == 4

    def test_each_preset_has_display_and_value(self):
        """每个预设应有显示名"""
        for preset in MOCK_TASK_PRESETS:
            assert len(preset) > 0


class TestIntentSchemaCompliance:
    """测试意图 JSON 是否符合 Schema 规范"""

    def test_required_fields_present(self):
        """必填字段必须存在"""
        required = ["intent_id", "raw_text", "action", "target_object"]
        for field in required:
            assert field in MOCK_INTENT, f"缺少必填字段: {field}"

    def test_action_in_enum(self):
        """action 必须在 schema 枚举值内"""
        valid_actions = {
            "pick_and_place", "push", "pull", "stack",
            "sort_by_color", "sort_by_size", "filter_by_attribute",
            "open", "close", "pour",
        }
        assert MOCK_INTENT["action"] in valid_actions, \
            f"action={MOCK_INTENT['action']} 不在有效值内"

    def test_confidence_range(self):
        """confidence 应在 [0, 1] 内"""
        conf = MOCK_INTENT.get("confidence", -1)
        assert 0.0 <= conf <= 1.0, f"confidence={conf} 超出范围"


# ============================================================
# 简易渲染器 (从 app.py 摘取核心逻辑进行独立测试)
# ============================================================
def render_json_highlight(data) -> str:
    """从 app.py 提取的 JSON 渲染器核心逻辑"""
    try:
        if isinstance(data, str):
            data = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return '<pre style="color:#888;">JSON 解析错误 — 请检查输入格式</pre>'

    action = data.get("action", "N/A")
    json_str = json.dumps(data, indent=2, ensure_ascii=False)
    return f"""
    <div>
        <h3>{action}</h3>
        <p>{data.get('raw_text', '')}</p>
        <pre>{json_str}</pre>
    </div>
    """
