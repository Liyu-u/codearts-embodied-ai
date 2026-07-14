"""
测试大模型解析 JSON 的准确率
同学 A：验证 intent_parser 输出是否符合 schema
"""

import json
import pytest
from pathlib import Path


MOCK_DIR = Path(__file__).parent.parent / "mock"


def test_intent_schema_compliance():
    """验证 mock_intent_output.json 符合 intent_schema_v1.json 规范"""
    schema_path = Path(__file__).parent.parent / "docs" / "intent_schema_v1.json"
    output_path = MOCK_DIR / "mock_intent_output.json"

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    with open(output_path, "r", encoding="utf-8") as f:
        mock_output = json.load(f)

    # 基本字段校验
    required_fields = schema.get("required", [])
    for sample in mock_output.get("samples", []):
        for field in required_fields:
            assert field in sample, f"缺少必填字段: {field}"

        # action 必须在枚举范围内
        valid_actions = schema["properties"]["action"].get("enum", [])
        if valid_actions:
            assert (
                sample["action"] in valid_actions
            ), f"action='{sample['action']}' 不在有效值内: {valid_actions}"


@pytest.mark.parametrize(
    "nl_input,expected_action",
    [
        ("帮我把那个红色的方块拿过来", "pick_and_place"),
        ("把桌上所有积木按颜色分成三堆", "sort"),
        ("帮我把绿色的东西推到桌子左边去", "push"),
    ],
)
def test_action_classification(nl_input: str, expected_action: str):
    """测试自然语言 → 动作类型分类"""
    # 这里 mock 了 LLM 调用，实际测试应接入 intent_parser 模块
    # TODO: 接入 intent_parser 模块后替换为真实调用
    mock_mapping = {
        "抓取": "pick",
        "拿": "pick_and_place",
        "推": "push",
        "分": "sort",
        "分类": "sort",
    }
    for keyword, action in mock_mapping.items():
        if keyword in nl_input:
            # 这是一个占位测试，实际应该用 intent_parser 解析
            pass
    pytest.skip("待接入 intent_parser 模块后实现完整测试")
