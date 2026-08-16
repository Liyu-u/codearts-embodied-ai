"""
策略代码生成器 — 同学 B (冯海) 负责
将意图 JSON 翻译为可执行 Python 控制脚本的核心模块。

职责:
1. 读取意图 JSON，根据 action 类型选择对应的策略模板
2. 填充模板参数（目标物体、目标位置、约束条件等）
3. 对生成的代码进行安全校验 (code_validator)
4. 校验通过后返回可执行代码字符串

双模式运行:
  - LLM 模式: 调用华为云 CodeArts / OpenAI 兼容 API 生成策略代码
  - 模板模式: 使用内置的 CaP 模板直接生成（无需 LLM，离线可用）
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 确保本模块目录在 path 中（兼容直接运行和包导入）
_MOD_DIR = str(Path(__file__).parent)
if _MOD_DIR not in sys.path:
    sys.path.insert(0, _MOD_DIR)

from code_validator import CodeValidator


# ============================================================
# 策略模板库 — CaP (Code-as-Policy) 内置模板
# ============================================================
STRATEGY_TEMPLATES = {
    "pick_and_place": '''def task_main():
    """抓取放置策略 — 由 strategy_generator 自动生成"""
    objects = get_scene_objects()
    target = None
    for obj in objects:
        if {target_object_literal} in obj.name:
            target = obj
            break
    if target is None:
        target = find_object(name_contains={target_object_literal})
    if not target:
        return {{"status": "failed", "reason": "未找到目标物体: " + str({target_object_literal})}}

    dest_x, dest_y, dest_z = {dest_x:.4f}, {dest_y:.4f}, {dest_z:.4f}
    result = pick_and_place(robot, target, dest_x, dest_y, dest_z)
    return result
''',

    "push": '''def task_main():
    """推物策略 — 由 strategy_generator 自动生成"""
    objects = get_scene_objects()
    target = None
    for obj in objects:
        if {target_object_literal} in obj.name:
            target = obj
            break
    if not target:
        target = find_object(name_contains={target_object_literal})
    if not target:
        return {{"status": "failed", "reason": "未找到目标物体: " + str({target_object_literal})}}

    dest_x, dest_y = {dest_x:.4f}, {dest_y:.4f}
    dx = dest_x - target.position[0]
    dy = dest_y - target.position[1]
    result = push(robot, target, dx, dy)
    return result
''',

    "stack": '''def task_main():
    """堆叠策略 — 由 strategy_generator 自动生成"""
    objects = get_scene_objects()
    top_obj = None
    bottom_obj = None
    for obj in objects:
        if {target_object_literal} in obj.name:
            top_obj = obj
        if {reference_object_literal} in obj.name:
            bottom_obj = obj
    if not top_obj:
        top_obj = find_object(name_contains={target_object_literal})
    if not bottom_obj:
        bottom_obj = find_object(name_contains={reference_object_literal})
    if not top_obj or not bottom_obj:
        return {{"status": "failed", "reason": "未找到堆叠目标物体"}}

    result = stack(robot, top_obj, bottom_obj)
    return result
''',

    "sort_by_color": '''def task_main():
    """颜色分类策略 — 由 strategy_generator 自动生成"""
    colors = {colors_list}
    zones = {zones_list}

    total_moved = 0
    for color, zone in zip(colors, zones):
        result = sort_by_color(robot, color, zone)
        if result["status"] != "success":
            return result
        total_moved += result.get("moved_count", 0)

    move_home(robot)
    return {{"status": "success", "total_moved": total_moved}}
''',

    "sort_by_size": '''def task_main():
    """尺寸排序策略 — 由 strategy_generator 自动生成"""
    objects = get_scene_objects()
    targets = [o for o in objects if any(name in o.name for name in {target_objects_list})]

    if not targets:
        return {{"status": "failed", "reason": "未找到目标物体"}}

    targets.sort(key=lambda o: o.bbox[0] * o.bbox[1] * o.bbox[2])

    pile_positions = [
        (0.3000, -0.1500, 0.0300),
        (0.3000,  0.0000, 0.0300),
        (0.3000,  0.1500, 0.0300),
    ]

    if len(targets) > len(pile_positions):
        return {{"status": "failed", "reason": "目标物体数量超过可用堆放位置"}}

    sorted_count = 0
    for i, target in enumerate(targets):
        px, py, pz = pile_positions[i]
        result = pick_and_place(robot, target, px, py, pz)
        if result["status"] != "success":
            return result
        sorted_count += 1

    move_home(robot)
    return {{"status": "success", "sorted_count": sorted_count}}
''',

    "filter_by_attribute": '''def task_main():
    """条件筛选策略 — 由 strategy_generator 自动生成"""
    objects = get_scene_objects()
    matching = []

    for obj in objects:
        name_matches = (not {target_objects_list}) or any(
            name in obj.name for name in {target_objects_list}
        )
        attribute_matches = (not {attributes_list}) or (
            obj.color and any(attr in obj.color.lower() for attr in {attributes_list})
        )
        if name_matches and attribute_matches:
            matching.append(obj)

    if not matching:
        return {{"status": "failed", "reason": "未找到符合条件的物体"}}

    dest_x, dest_y, dest_z = {dest_x:.4f}, {dest_y:.4f}, {dest_z:.4f}
    for i, target in enumerate(matching):
        offset_x = 0.03 * i
        result = pick_and_place(robot, target, dest_x + offset_x, dest_y, dest_z)
        if result["status"] != "success":
            return result

    move_home(robot)
    return {{"status": "success", "filtered_count": len(matching)}}
''',
}


# ============================================================
# 策略生成器
# ============================================================
class StrategyGenerator:
    """
    意图 JSON → Python 策略代码 编译器。

    支持两种生成模式:
      - 模板模式: 使用内置 CaP 模板，离线可用，速度快
      - LLM 模式: 调用 CodeArts/OpenAI API，处理复杂/未知任务
    """

    def __init__(
        self,
        llm_api_key: Optional[str] = None,
        llm_base_url: Optional[str] = None,
        llm_model: Optional[str] = None,
    ):
        # 兼容原有 OpenAI 变量，同时支持 README 中约定的 CodeArts 变量。
        self.llm_api_key = (
            llm_api_key
            or os.environ.get("CODEARTS_API_KEY")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        self.llm_base_url = (
            llm_base_url
            or os.environ.get("CODEARTS_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        )
        self.llm_model = (
            llm_model
            or os.environ.get("CODEARTS_MODEL")
            or os.environ.get("LLM_MODEL", "gpt-4o")
        )
        self.system_prompt = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        """加载 CodeArts 策略生成 System Prompt"""
        prompt_candidates = (
            Path(__file__).with_name("codearts_system_prompt.md"),
            Path(__file__).parent.parent.parent / "prompts" / "codearts_system_prompt.md",
        )
        for prompt_path in prompt_candidates:
            if prompt_path.exists():
                return prompt_path.read_text(encoding="utf-8")
        return "你是机器人控制策略编译器，将意图JSON翻译为Python代码。"

    def generate_from_template(self, intent: Dict[str, Any]) -> Tuple[bool, str, Optional[str]]:
        """
        使用内置 CaP 模板生成策略代码。

        Args:
            intent: 意图 JSON (符合 intent_schema_v1.json)

        Returns:
            (success, message, code)
        """
        action = intent.get("action", "")
        template = STRATEGY_TEMPLATES.get(action)

        if template is None:
            return False, f"无内置模板支持 action='{action}'，请使用 LLM 模式", None

        try:
            code = self._fill_template(action, template, intent)
        except Exception as e:
            return False, f"模板填充失败: {e}", None

        validation = CodeValidator.full_validation(code)
        if not validation["passed"]:
            return False, f"生成的代码未通过安全校验: {validation['summary']}", code

        return True, "[OK] 模板策略生成成功", code

    def generate_from_llm(self, intent: Dict[str, Any]) -> Tuple[bool, str, Optional[str]]:
        """
        使用 LLM (CodeArts/OpenAI) 生成策略代码。

        Args:
            intent: 意图 JSON

        Returns:
            (success, message, code)
        """
        if not self.llm_api_key:
            return False, "未配置 LLM API Key，无法使用 LLM 模式", None

        try:
            import http.client
            import json as _json

            user_message = _json.dumps(intent, ensure_ascii=False, indent=2)

            body = _json.dumps({
                "model": self.llm_model,
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": f"请将以下意图JSON翻译为Python策略代码:\n\n{user_message}"},
                ],
                "temperature": 0.1,
                "max_tokens": 2048,
            })

            from urllib.parse import urlparse
            parsed = urlparse(self.llm_base_url)
            host = parsed.hostname
            if parsed.scheme not in {"http", "https"} or not host:
                return False, f"LLM Base URL 无效: {self.llm_base_url}", None
            path = parsed.path.rstrip("/") + "/chat/completions"

            connection_cls = (
                http.client.HTTPSConnection
                if parsed.scheme == "https"
                else http.client.HTTPConnection
            )
            conn = connection_cls(host, parsed.port, timeout=30)
            conn.request(
                "POST", path, body,
                {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.llm_api_key}",
                },
            )
            response = conn.getresponse()
            response_body = response.read().decode("utf-8")
            conn.close()
            if response.status < 200 or response.status >= 300:
                return False, f"LLM API HTTP {response.status}: {response_body[:500]}", None
            data = _json.loads(response_body)

            if not isinstance(data.get("choices"), list) or not data["choices"]:
                return False, f"LLM API 返回异常: {data}", None

            raw_code = data["choices"][0].get("message", {}).get("content", "")
            code = self._extract_code_block(raw_code)

            if not code:
                return False, "LLM 未返回有效代码块", None

            validation = CodeValidator.full_validation(code)
            if not validation["passed"]:
                return False, f"LLM 生成的代码未通过安全校验: {validation['summary']}", code

            return True, "[OK] LLM 策略生成成功", code

        except Exception as e:
            return False, f"LLM 调用失败: {e}", None

    def generate(self, intent: Dict[str, Any], prefer_llm: bool = False) -> Dict[str, Any]:
        """
        智能生成策略代码：优先模板，失败则降级到 LLM。

        Args:
            intent: 意图 JSON
            prefer_llm: 是否优先使用 LLM

        Returns:
            {
                "success": bool,
                "message": str,
                "code": str | None,
                "mode": "template" | "llm",
                "validation": dict,
            }
        """
        if prefer_llm and self.llm_api_key:
            success, msg, code = self.generate_from_llm(intent)
            if success:
                return {
                    "success": True,
                    "message": msg,
                    "code": code,
                    "mode": "llm",
                    "validation": CodeValidator.full_validation(code) if code else {},
                }

        success, msg, code = self.generate_from_template(intent)
        if success:
            return {
                "success": True,
                "message": msg,
                "code": code,
                "mode": "template",
                "validation": CodeValidator.full_validation(code) if code else {},
            }

        if not prefer_llm and self.llm_api_key:
            success, msg, code = self.generate_from_llm(intent)
            if success:
                return {
                    "success": True,
                    "message": msg,
                    "code": code,
                    "mode": "llm",
                    "validation": CodeValidator.full_validation(code) if code else {},
                }

        return {
            "success": False,
            "message": msg,
            "code": code,
            "mode": "failed",
            "validation": CodeValidator.full_validation(code) if code else {},
        }

    # ============================================================
    # 模板填充
    # ============================================================
    def _fill_template(self, action: str, template: str, intent: Dict[str, Any]) -> str:
        """根据意图 JSON 填充策略模板参数"""
        dest = intent.get("destination") or {}
        dest_x = dest.get("x", 0.2)
        dest_y = dest.get("y", 0.0)
        dest_z = dest.get("z", 0.03)

        target_object = str(intent.get("target_object") or "")
        reference_object = str(intent.get("reference_object") or "")
        target_objects = list(intent.get("target_objects") or [])
        attributes = list(intent.get("attributes") or [])
        target_object_literal = repr(target_object)
        reference_object_literal = repr(reference_object)

        if action == "sort_by_color":
            colors = attributes if attributes else ["red", "blue", "green"]
            num_piles = intent.get("num_piles", len(colors))
            try:
                num_piles = max(1, int(num_piles))
            except (TypeError, ValueError):
                num_piles = len(colors)
            zones = []
            for i in range(len(colors)):
                y_offset = -0.15 + 0.15 * i
                zones.append((0.30, y_offset, 0.03))
            return template.format(
                colors_list=repr(colors),
                zones_list=repr(zones),
            )

        if action == "sort_by_size":
            return template.format(
                target_objects_list=repr(target_objects if target_objects else ([target_object] if target_object else [])),
            )

        if action == "filter_by_attribute":
            attr_lower = [a.lower() for a in attributes] if attributes else ["red"]
            return template.format(
                attributes_list=repr(attr_lower),
                target_objects_list=repr(target_objects if target_objects else ([target_object] if target_object else [])),
                dest_x=dest_x, dest_y=dest_y, dest_z=dest_z,
            )

        if action == "stack":
            return template.format(
                target_object_literal=target_object_literal,
                reference_object_literal=reference_object_literal,
            )

        return template.format(
            target_object_literal=target_object_literal,
            dest_x=dest_x, dest_y=dest_y, dest_z=dest_z,
        )

    @staticmethod
    def _extract_code_block(text: str) -> Optional[str]:
        """从 LLM 响应中提取 Python 代码块"""
        pattern = r'```python\s*\n(.*?)```'
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()

        pattern = r'```\s*\n(.*?)```'
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()

        if "def task_main" in text:
            return text.strip()

        return None


# ============================================================
# 快捷函数 (供 server.py 调用)
# ============================================================
def generate_strategy(intent: Dict[str, Any], prefer_llm: bool = False) -> Dict[str, Any]:
    """
    供后端 server.py 调用的策略生成入口。

    Args:
        intent: 意图 JSON (符合 intent_schema_v1.json)
        prefer_llm: 是否优先使用 LLM

    Returns:
        {"success": bool, "message": str, "code": str|None, "mode": str, "validation": dict}
    """
    generator = StrategyGenerator()
    return generator.generate(intent, prefer_llm=prefer_llm)


# ============================================================
# 自检 (独立运行)
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  策略代码生成器自检 — 同学 B (冯海)")
    print("=" * 60)

    generator = StrategyGenerator()

    test_intents = [
        {
            "intent_id": "test-001",
            "action": "pick_and_place",
            "target_object": "红色方块",
            "destination": {"x": 0.2000, "y": 0.0000, "z": 0.0300},
        },
        {
            "intent_id": "test-002",
            "action": "push",
            "target_object": "绿色圆柱",
            "destination": {"x": 0.4000, "y": -0.2000, "z": 0.0400},
        },
        {
            "intent_id": "test-003",
            "action": "stack",
            "target_object": "红色方块",
            "reference_object": "蓝色方块",
        },
        {
            "intent_id": "test-004",
            "action": "sort_by_color",
            "target_objects": ["红色方块", "蓝色方块", "绿色方块"],
            "attributes": ["red", "blue", "green"],
            "num_piles": 3,
        },
        {
            "intent_id": "test-005",
            "action": "filter_by_attribute",
            "target_objects": ["红色方块", "蓝色杯子"],
            "attributes": ["red"],
            "destination": {"x": -0.3000, "y": 0.1000, "z": 0.0300},
        },
    ]

    for intent in test_intents:
        print(f"\n--- {intent['intent_id']}: {intent['action']} ---")
        result = generator.generate(intent)
        print(f"  模式: {result['mode']}")
        print(f"  结果: {result['message']}")
        if result['code']:
            print(f"  代码前3行:")
            for line in result['code'].strip().split('\n')[:3]:
                print(f"    {line}")
            print(f"    ...")

    print("\n" + "=" * 60)
    print("  全部测试意图生成完成!")
    print("=" * 60)

