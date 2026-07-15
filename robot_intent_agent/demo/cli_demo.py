#!/usr/bin/env python3
"""
CLI Demo — 端到端管线演示

展示:
    Instruction → Memory → Scene → BT → Constraints → IR

用法:
    python cli_demo.py                          # 交互模式
    python cli_demo.py --task medicine          # 预设任务
    python cli_demo.py --task all               # 全部预设
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Parent path
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from robot_intent_agent.memory import MemoryRetriever
from robot_intent_agent.scene_builder import SemanticSceneBuilder, RawObjectPercept
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.constraint import HybridConstraintCompiler
from robot_intent_agent.ir import RobotTaskIRGenerator


# ============================================================
# 预设任务
# ============================================================

@dataclass
class PresetTask:
    name: str
    instruction: str
    objects: List[RawObjectPercept]
    memory_setup: callable
    description: str


PRESET_TASKS: Dict[str, PresetTask] = {}


def _build_presets():
    global PRESET_TASKS

    def elderly_memory(r: MemoryRetriever):
        r.add_user_preference("grip_style", "gentle", user="elderly")
        r.add_user_preference("hand_preference", "left", user="elderly")
        r.add_skill_experience("gentle_grasp", "红色药瓶",
                               params={"force_n": 2.5}, success=True)

    PRESET_TASKS["medicine"] = PresetTask(
        name="medicine_delivery",
        instruction="请把桌上的红色药瓶递到我手上，动作轻一点，千万别碰倒旁边的玻璃水杯",
        objects=[
            RawObjectPercept(name="红色药瓶", x=0.15, y=0.05, z=0.03,
                             width=0.03, height=0.08, depth=0.03,
                             color="red", material="plastic"),
            RawObjectPercept(name="玻璃水杯", x=0.08, y=0.03, z=0.06,
                             width=0.07, height=0.12, depth=0.07,
                             color="transparent", material="glass"),
        ],
        memory_setup=elderly_memory,
        description="老人递药 — 轻抓取 + 避开水杯",
    )

    def water_memory(r: MemoryRetriever):
        r.add_user_preference("speed_preference", "slow", user="elderly")

    PRESET_TASKS["water"] = PresetTask(
        name="water_delivery",
        instruction="帮我把桌上的水杯拿给老人，慢一点，桌上有药瓶别碰倒了",
        objects=[
            RawObjectPercept(name="水杯", x=0.10, y=0.00, z=0.06,
                             width=0.07, height=0.12, depth=0.07,
                             color="white", material="ceramic"),
            RawObjectPercept(name="药瓶", x=0.05, y=0.02, z=0.04,
                             width=0.03, height=0.08, depth=0.03,
                             color="orange", material="plastic"),
        ],
        memory_setup=water_memory,
        description="老人喝水 — 慢速递水 + 不碰药瓶",
    )

    def fragile_memory(r: MemoryRetriever):
        r.add_user_preference("grip_style", "gentle")
        r.add_skill_experience("gentle_grasp", "花瓶",
                               params={"force_n": 2.0}, success=True)

    PRESET_TASKS["fragile"] = PresetTask(
        name="fragile_transport",
        instruction="帮我把那个古董花瓶搬到展示台上，轻一点，别碰周围的东西",
        objects=[
            RawObjectPercept(name="古董花瓶", x=0.10, y=0.00, z=0.05,
                             width=0.08, height=0.20, depth=0.08,
                             color="blue", material="ceramic",
                             extra_attrs={"fragile": True, "heavy": False}),
            RawObjectPercept(name="障碍物", x=0.05, y=0.02, z=0.06,
                             width=0.04, height=0.10, depth=0.04),
        ],
        memory_setup=fragile_memory,
        description="脆弱物品 — 超轻力 + 绕行障碍",
    )


_build_presets()


# ============================================================
# Pipeline Runner
# ============================================================

class PipelineRunner:
    """端到端管线执行器"""

    def __init__(self):
        self.planner = BehaviorTreeGenerator()
        self.compiler = HybridConstraintCompiler()
        self.generator = RobotTaskIRGenerator()
        self.builder = SemanticSceneBuilder()

    def run(self, task: PresetTask, verbose: bool = True) -> Dict[str, Any]:
        """执行完整管线"""

        # Step 3: Memory
        retriever = MemoryRetriever()
        task.memory_setup(retriever)
        memory_results = retriever.search(task.instruction, top_k=5)
        memory_items = [item.to_dict() for item in memory_results]

        # Step 4: Scene
        scene = self.builder.build(task.objects)

        # Extract target
        from robot_intent_agent.planner.behavior_tree_generator import RuleInstructionParser
        target = RuleInstructionParser.extract_target(task.instruction)

        # Step 5: BT
        bt = self.planner.plan(
            task.instruction, scene=scene, memory_context=memory_items,
        )

        # Step 6: Constraints
        cg = self.compiler.compile(
            task.instruction, behavior_tree=bt, scene=scene,
            memory_context=memory_items, target=target,
        )

        # Step 7: IR
        ir = self.generator.generate(
            task.instruction, behavior_tree=bt, constraint_graph=cg,
            scene=scene, memory_context=memory_items,
        )

        result = {
            "instruction": task.instruction,
            "memory_count": len(memory_items),
            "scene_objects": [o.name for o in scene.objects],
            "scene_relations": len(scene.relations),
            "bt_actions": [a.skill_name for a in bt.root.flatten_actions()],
            "constraints_total": len(cg.nodes),
            "constraints_hard": len(cg.hard_constraints()),
            "ir_json": ir.model_dump_json(indent=2),
            "ir_summary": ir.summary(),
        }

        if verbose:
            self._print_result(result)

        return result

    def _print_result(self, result: Dict[str, Any]):
        print()
        print("╔" + "═" * 58 + "╗")
        print("║  Robot Intent Agent — End-to-End Pipeline" + " " * 11 + "║")
        print("╠" + "═" * 58 + "╣")
        print(f"║  Instruction: {result['instruction'][:42]}...{' ' * (15 - min(42, len(result['instruction']))) if len(result['instruction']) > 42 else ' ' * (57 - 10 - len(result['instruction']))}║")
        print("╠" + "═" * 58 + "╣")
        print(f"║  Memory:     {result['memory_count']} items retrieved" + " " * 26 + "║")
        objs = ", ".join(result["scene_objects"][:3])
        print(f"║  Scene:      {len(result['scene_objects'])} objects ({objs})" + " " * max(0, 38 - len(objs)) + "║")
        print(f"║  Relations:  {result['scene_relations']} spatial relations" + " " * 23 + "║")
        actions = " → ".join(result["bt_actions"])
        print(f"║  BT:         {actions}" + " " * max(0, 45 - len(actions)) + "║")
        print(f"║  Constraints: {result['constraints_total']} total ({result['constraints_hard']} hard)" + " " * 20 + "║")
        print("╠" + "═" * 58 + "╣")
        print(f"║  IR:         Generated (see below)" + " " * 28 + "║")
        print("╚" + "═" * 58 + "╝")

        # Print IR snippet
        data = json.loads(result["ir_json"])
        print("\n  ── IR Skills ──")
        for skill_name, skill_data in data.get("skills", {}).items():
            c = skill_data.get("constraints", {})
            force = c.get("force", {}).get("max_force_n", "")
            fragile = "FRAGILE" if c.get("fragile") else ""
            avoid = ", ".join(c.get("avoid", []))
            extras = []
            if force: extras.append(f"force≤{force}N")
            if fragile: extras.append(fragile)
            if avoid: extras.append(f"avoid=[{avoid}]")
            extra_str = f"  [{', '.join(extras)}]" if extras else ""
            print(f"    {skill_name}({skill_data.get('target', '')}){extra_str}")

        opt = data.get("optimization_space", {})
        print(f"\n  ── Optimization ──")
        print(f"    force_range:  {opt.get('force_range_n', '')} N")
        print(f"    velocity_range: {opt.get('velocity_range_ms', '')} m/s")
        print(f"    targets:      {opt.get('targets', '')}")
        print()


# ============================================================
# Main
# ============================================================

def main():
    runner = PipelineRunner()

    # Process args
    task_key = "medicine"
    if len(sys.argv) > 2 and sys.argv[1] == "--task":
        task_key = sys.argv[2]

    if task_key == "all":
        for key in PRESET_TASKS:
            print(f"\n{'='*60}")
            print(f"  Task: {key} — {PRESET_TASKS[key].description}")
            print(f"{'='*60}")
            runner.run(PRESET_TASKS[key])
    elif task_key in PRESET_TASKS:
        runner.run(PRESET_TASKS[task_key])
    else:
        print(f"Unknown task: {task_key}")
        print(f"Available: {list(PRESET_TASKS.keys())} | all")


if __name__ == "__main__":
    main()
