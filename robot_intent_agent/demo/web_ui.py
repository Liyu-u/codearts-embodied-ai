"""
具身智能意图推理中枢 — Demo v3.0
架构: 全局设置 + Tab分离（Playground / Evaluation）
"""

from __future__ import annotations
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import gradio as gr

from robot_intent_agent.config.settings import get_settings
from robot_intent_agent.memory import MemoryRetriever
from robot_intent_agent.scene_builder import SemanticSceneBuilder, RawObjectPercept
from robot_intent_agent.planner import BehaviorTreeGenerator, LLMPlanner, HybridRouter, LLMPlannerError

# Export presets for testing
def _load_presets():
    with open(Path(__file__).parent.parent / "eval" / "single_test_presets.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    result = {}
    for p in data.get("presets", []):
        entry = dict(p)
        # Normalize field names for test compatibility
        if "perception_json" in entry and "observation_json" not in entry:
            entry["observation_json"] = entry["perception_json"]
        if "command" not in entry:
            entry["command"] = entry.get("instruction", "")
        result[p["name"]] = entry
    return result
try:
    PRESET_CASES = _load_presets()
except Exception:
    PRESET_CASES = {}
from robot_intent_agent.constraint import HybridConstraintCompiler
from robot_intent_agent.ir import RobotTaskIRGenerator
from robot_intent_agent.property_inference.property_mapper import PropertyMapper
from robot_intent_agent.task_semantics import (
    ParsedTask, ConstraintResolution, ValidationResult, PlanStatus, TaskActionKind,
)
from robot_intent_agent.schemas.scene import Affordance


# ══════════════════ CSS 注入 ══════════════════
PAGE_CSS = """
body,.gradio-container{background:#F8FAFC!important;}.gradio-container{max-width:1400px!important;margin:0 auto!important;}
h3{color:#1E293B!important;font-size:15px!important;font-weight:600!important;margin:0 0 8px 0!important;}
.gr-group{border:1px solid #E2E8F0!important;border-radius:10px!important;background:#FFFFFF!important;box-shadow:0 1px 3px rgba(0,0,0,0.06)!important;padding:16px!important;margin-bottom:12px!important;}
.gr-group .gr-form{background:transparent!important;border:none!important;}
#global_bar{background:#FFFFFF!important;border-bottom:1px solid #E2E8F0!important;padding:8px 16px!important;}
#playground_left{background:#FFFFFF!important;border-radius:10px!important;border:1px solid #E2E8F0!important;padding:16px!important;}
#result_card{background:#FFFFFF!important;border-radius:8px!important;border:1px solid #E2E8F0!important;box-shadow:0 1px 2px rgba(0,0,0,0.04)!important;padding:14px!important;margin-bottom:10px!important;}
#run_btn button{background:#3B82F6!important;border:none!important;font-size:16px!important;font-weight:600!important;padding:10px 28px!important;}
#eval_filter_bar{background:#FFFFFF!important;border:1px solid #E2E8F0!important;border-radius:10px!important;padding:12px 16px!important;margin-bottom:10px!important;}
#status_bar{font-size:12px!important;color:#64748B!important;}
.badge_pass{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600;background:#DCFCE7;color:#166534;}
.badge_fail{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600;background:#FEE2E2;color:#991B1B;}
.badge_warn{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600;background:#FEF3C7;color:#92400E;}
"""


# ══════════════════ Pipeline (unchanged) ══════════════════
class Pipeline:
    def __init__(self):
        self.builder = SemanticSceneBuilder()
        self.compiler = HybridConstraintCompiler()
        self.generator = RobotTaskIRGenerator()
        self.mapper = PropertyMapper()
        self.rule_planner = BehaviorTreeGenerator()
        self._llm = None
        self._llm_err = None

    def _get_llm(self, key_override=""):
        s = get_settings()
        k = key_override.strip() or s.deepseek_api_key
        if not k:
            self._llm_err = "无Key"; return None
        if self._llm is None or (key_override.strip() and self._llm._api_key != k):
            try:
                self._llm = LLMPlanner(api_key=k); self._llm_err = None
            except Exception as e:
                self._llm_err = str(e); return None
        return self._llm

    @staticmethod
    def _safe_geo(raw, default=(0.05, 0.08, 0.05)):
        if isinstance(raw, dict): return (raw.get("width", default[0]), raw.get("height", default[1]), raw.get("depth", default[2]))
        elif isinstance(raw, (list, tuple)) and len(raw) >= 3: return (raw[0], raw[1], raw[2])
        return default

    @staticmethod
    def _safe_pos(raw, default=(0.0, 0.0, 0.03)):
        if isinstance(raw, dict): return (raw.get("x", default[0]), raw.get("y", default[1]), raw.get("z", default[2]))
        elif isinstance(raw, (list, tuple)) and len(raw) >= 3: return (raw[0], raw[1], raw[2])
        return default

    def run(self, instruction: str, obs_json_str: str, engine: str, api_key: str):
        st = time.time()
        obs_data = json.loads(obs_json_str.strip())
        if isinstance(obs_data, list):
            if len(obs_data) > 0 and isinstance(obs_data[0], dict): obs_data = obs_data[0]
        objects_raw = obs_data.get("objects", []) if isinstance(obs_data, dict) else []

        scene_objects, all_sem_props = [], []
        for obj_data in objects_raw:
            if not isinstance(obj_data, dict): continue
            obj_id = obj_data.get("object_id", obj_data.get("id", f"obj_{len(scene_objects):04d}"))
            cats = obj_data.get("category_candidates", [{"name": obj_data.get("category", "unknown"), "score": 0.5}])
            top_cat = max(cats, key=lambda c: c.get("score", 0))
            geo_raw = obj_data.get("geometry", {})
            if isinstance(geo_raw, dict) and "size" in geo_raw: geo_raw = geo_raw["size"]
            w, h, d = self._safe_geo(geo_raw)
            pose = obj_data.get("pose", {})
            pos_raw = pose.get("position", {}) if isinstance(pose, dict) else {}
            px, py, pz = self._safe_pos(pos_raw)
            app = obj_data.get("appearance", {})
            color_val = "unknown"
            if isinstance(app, dict):
                cc = app.get("color_candidates", [])
                color_val = cc[0]["name"] if cc else app.get("color", "unknown")
            elif obj_data.get("color"): color_val = obj_data["color"]
            track = obj_data.get("tracking", {})
            vel = track.get("velocity", {}) if isinstance(track, dict) else {}
            vx = vel.get("x", 0) if isinstance(vel, dict) else 0
            vy = vel.get("y", 0) if isinstance(vel, dict) else 0
            vz = vel.get("z", 0) if isinstance(vel, dict) else 0
            vel_mag = (vx**2 + vy**2 + vz**2)**0.5
            vel_conf = track.get("velocity_confidence", 0) if isinstance(track, dict) else 0
            is_moving = vel_conf >= 0.7 and vel_mag > 0.01
            obs_input = {"name": top_cat["name"], "category": top_cat["name"],
                         "geometry": {"width": w, "height": h, "depth": d}, "position": [px, py, pz]}
            material_from_json = (app.get("material") if isinstance(app, dict) else None) or obj_data.get("material")
            if material_from_json: obs_input["material"] = material_from_json
            sp = self.mapper.infer(obs_input); all_sem_props.append(sp)
            affs = [Affordance.GRASPABLE] if sp.graspable.value else []
            if sp.fragility_level.value >= 2: affs.append(Affordance.FRAGILE)
            if sp.movable.value: affs.append(Affordance.MOVABLE)
            raw = RawObjectPercept(name=top_cat["name"], x=px, y=py, z=pz, width=w, height=h, depth=d,
                                   color=color_val, material=sp.material.value,
                                   extra_attrs={"_orig_object_id": obj_id, "_speed_mps": vel_mag,
                                                "_is_moving": is_moving, "_vel_conf": vel_conf})
            raw._affs = affs; scene_objects.append(raw)

        orig = RawObjectPercept.to_scene_object
        try:
            def patch(self):
                obj = orig(self)
                if hasattr(self, '_affs') and self._affs: obj.affordances = self._affs
                return obj
            RawObjectPercept.to_scene_object = patch
            scene = self.builder.build(scene_objects)
        finally: RawObjectPercept.to_scene_object = orig

        target = all_sem_props[0].category if all_sem_props else "target"
        planner_name = "RuleEngine"
        if engine in ("纯规则引擎 (极速)",):
            bt = self.rule_planner.plan(instruction, scene=scene)
        elif engine in ("DeepSeek-V3 (AI 推理)", "Hybrid (混合优先)"):
            llm = self._get_llm(api_key)
            if llm is None:
                bt = self.rule_planner.plan(instruction, scene=scene)
                planner_name = "RuleEngine(LLM不可用)"
            elif engine == "Hybrid (混合优先)":
                try:
                    bt = HybridRouter(llm_planner=llm).plan(instruction, scene=scene)
                    planner_name = f"Hybrid→{bt.metadata.get('planner','?')}"
                except Exception:
                    bt = self.rule_planner.plan(instruction, scene=scene)
                    planner_name = "RuleEngine(Hybrid失败)"
            else:
                try:
                    bt = llm.plan(instruction, scene=scene)
                    planner_name = "DeepSeek-V3"
                except Exception:
                    bt = self.rule_planner.plan(instruction, scene=scene)
                    planner_name = "RuleEngine(DS失败)"
        else:
            bt = self.rule_planner.plan(instruction, scene=scene)

        cg = self.compiler.compile(instruction, behavior_tree=bt, scene=scene, target=target)
        ir = self.generator.generate(instruction, behavior_tree=bt, constraint_graph=cg, scene=scene)
        parsed_task = ir.parsed_task; resolution = ir.constraint_resolution
        validation_result = ir.validation_result; plan_metadata = ir.plan_metadata

        action = parsed_task.action.value if parsed_task else bt.metadata.get("action", "?")
        avoid_objs = [obj.mention for obj in parsed_task.obstacle] if parsed_task else []
        modifiers = {"manner": parsed_task.manner if parsed_task else None,
                     "motion_state": parsed_task.motion_state.state if parsed_task else None}
        if parsed_task and parsed_task.theme: target = parsed_task.theme.mention

        raw_requested_force = raw_requested_vel = None
        if parsed_task:
            for constraint in parsed_task.user_constraints:
                if constraint.parameter == "force_n" and constraint.value is not None: raw_requested_force = constraint.value
                if constraint.parameter == "velocity_ms" and constraint.value is not None: raw_requested_vel = constraint.value

        final_force = resolution.parameters.get("force_n").selected_value if resolution and resolution.parameters.get("force_n") else None
        final_velocity = resolution.parameters.get("velocity_ms").selected_value if resolution and resolution.parameters.get("velocity_ms") else None
        override = resolution.override_ledger if resolution else []
        execution_ready = validation_result.execution_allowed if validation_result else False
        blocking_reasons = [issue.code for issue in validation_result.issues] if validation_result else []
        plan_status = plan_metadata.plan_status.value if plan_metadata else "UNKNOWN"

        target_moving = False; target_speed = 0.0
        if scene and target:
            to = scene.find_object(target)
            if to and hasattr(to, 'attributes'):
                target_moving = to.attributes.get('_is_moving', False)
                target_speed = to.attributes.get('_speed_mps', 0.0)

        if target_moving:
            children = bt.root.children
            has_stable = any(c.skill and c.skill.skill_name == 'WaitUntilStable'
                           for c in children if hasattr(c, 'skill') and c.skill)
            if not has_stable:
                from robot_intent_agent.schemas.behavior_tree import SkillAction, BTNode, BTNodeType
                import copy as _copy
                stable_node = _copy.deepcopy(next(
                    (c for c in children if c.type.value == 'action' and c.skill and c.skill.skill_name in ('Reach', 'Grasp', 'GentleGrasp')), None))
                if stable_node is None:
                    new_children = list(children)
                    new_children.insert(0, BTNode(type=BTNodeType.ACTION, name=f'WaitUntilStable({target})',
                        skill=SkillAction(skill_name='WaitUntilStable', target=target,
                            params={'max_speed_mps': 0.01, 'timeout_s': 5.0, 'required_consecutive_frames': 3, 'min_velocity_confidence': 0.7}),
                        annotation=f'Target moving at {target_speed:.3f}m/s'))
                    bt.root.children = new_children
                else:
                    new_children = []; inserted = False
                    for c in children:
                        if not inserted and c.type.value == 'action':
                            new_children.append(BTNode(type=BTNodeType.ACTION, name=f'WaitUntilStable({target})',
                                skill=SkillAction(skill_name='WaitUntilStable', target=target,
                                    params={'max_speed_mps': 0.01, 'timeout_s': 5.0, 'required_consecutive_frames': 3, 'min_velocity_confidence': 0.7}),
                                annotation=f'Target moving at {target_speed:.3f}m/s'))
                            inserted = True
                        new_children.append(c)
                    bt.root.children = new_children
                execution_ready = True
            else:
                execution_ready = True

        elapsed = round((time.time() - st) * 1000)
        hard_count = len(cg.hard_constraints()); soft_count = len(cg.soft_constraints())
        trace_nodes = json.loads(ir.model_dump_json()).get("decision_trace", []) if ir else []
        return {"sem_props": all_sem_props, "scene": scene, "bt": bt, "cg": cg, "ir": ir,
                "elapsed": elapsed, "target": target, "action": action, "avoid_objs": avoid_objs,
                "modifiers": modifiers, "execution_ready": execution_ready, "blocking_reasons": blocking_reasons,
                "target_moving": target_moving, "target_speed": target_speed,
                "raw_requested_force": raw_requested_force, "raw_requested_vel": raw_requested_vel,
                "resolved_force": final_force, "resolved_vel": final_velocity, "plan_status": plan_status,
                "validation_result": validation_result, "override": override, "hard": hard_count, "soft": soft_count,
                "actions": [a.skill_name for a in bt.root.flatten_actions()] if bt else [],
                "trace": trace_nodes, "ir_raw": ir.model_dump_json(indent=2) if ir else "",
                "planner_name": planner_name}

pipeline = Pipeline()

# ══════════════════ Render helpers ══════════════════
def _card(h, max_h="440px"):
    return f'<div style="background:#FFFFFF;border-radius:8px;padding:14px;box-shadow:0 1px 2px rgba(0,0,0,0.04);border:1px solid #E2E8F0;color:#1E293B;font-size:13px;line-height:1.6;max-height:{max_h};overflow-y:auto;">{h}</div>'

def _badge(t, bg, fg):
    return f'<span style="display:inline-block;padding:2px 8px;border-radius:5px;font-size:11px;font-weight:500;background:{bg};color:{fg};margin:1px 3px;">{t}</span>'

def _row(label, value, source="", color_override=""):
    vc = color_override or "#1E293B"
    src = f'<span style="font-size:11px;color:#64748B;">{source}</span>' if source else ""
    return f'<tr><td style="padding:4px 8px;color:#475569;width:100px;">{label}</td><td style="padding:4px 8px;color:{vc};font-weight:600;">{value}</td><td style="padding:4px 8px;">{src}</td></tr>'

def render_intent(r):
    action_cn = {"GRASP": "抓取", "FETCH": "取回/递送", "PLACE": "放置", "HANDOVER": "交递", "TRANSFER": "转移", "DYNAMIC_GRASP": "动态抓取", "CUSTOM": "自定义", "pick_and_place": "抓取并放置"}
    ir = r.get("ir"); parsed = ir.parsed_task if ir and getattr(ir, "parsed_task", None) else None
    rows = [_row("目标", r["target"]), _row("动作", action_cn.get(r["action"], r["action"])),
            _row("规避", ", ".join(r["avoid_objs"]) if r["avoid_objs"] else "无")]
    if parsed and parsed.user_constraints:
        cs = []
        for c in parsed.user_constraints:
            if c.operator.value == "exact" and c.value is not None: cs.append(f"{c.parameter}={c.value}{c.unit}")
            elif c.operator.value == "max" and c.max_value is not None: cs.append(f"{c.parameter}≤{c.max_value}{c.unit}")
            elif c.operator.value == "min" and c.min_value is not None: cs.append(f"{c.parameter}≥{c.min_value}{c.unit}")
            elif c.operator.value == "range": cs.append(f"{c.parameter}∈[{c.min_value},{c.max_value}]{c.unit}")
        if cs: rows.append(_row("约束", "; ".join(cs), "", "#991B1B"))
    return _card(f'<div style="font-weight:600;color:#1E293B;margin-bottom:6px;font-size:14px;">意图理解</div><table style="width:100%;">{"".join(rows)}</table>')

def render_property(sp):
    if sp is None: return _card('<span style="color:#94A3B8;">无物体属性数据。</span>')
    rows = [_row("材料", sp.material.value, f"来源: {sp.material.source}"),
            _row("易碎等级", f'L{sp.fragility_level.value}', f"来源: {sp.fragility_level.source}"),
            _row("硬安全上限", f'{sp.max_force_N.value} N', f"来源: {sp.max_force_N.source}"),
            _row("可抓取", "是" if sp.graspable.value else "否", sp.graspable.reasoning[:40])]
    return _card(f'<div style="font-weight:600;color:#1E293B;margin-bottom:6px;font-size:14px;">物体属性推理</div><table style="width:100%;">{"".join(rows)}</table>')

def render_constraint(r):
    rows = []; raw_f = r.get("raw_requested_force"); res_f = r.get("resolved_force")
    execution_ready = r.get("execution_ready", False); plan_status = r.get("plan_status", "UNKNOWN")
    if execution_ready:
        status_badge = f'<span class="badge_pass">可执行</span>'
    elif plan_status in ("NEEDS_CLARIFICATION", "BLOCKED"):
        status_badge = f'<span class="badge_fail">{plan_status}</span>'
    else: status_badge = f'<span class="badge_warn">{plan_status}</span>'
    rows.append(_row("状态", f"{plan_status} {status_badge}"))
    if raw_f is not None and raw_f > 0: rows.append(_row("请求力", f"{raw_f} N")); rows.append(_row("实际力", f"{res_f} N" if res_f else "N/A"))
    else: rows.append(_row("抓力", f"{res_f} N (推荐)" if res_f else "N/A"))
    rows.append(_row("约束", f'硬 {r["hard"]} 条 / 软 {r["soft"]} 条'))
    return _card(f'<div style="font-weight:600;color:#1E293B;margin-bottom:6px;font-size:14px;">安全约束裁决</div><table style="width:100%;">{"".join(rows)}</table>')

def render_ir_card(r):
    data = json.loads(r["ir_raw"]) if r["ir_raw"] else {}
    skills = data.get("skills", {}); rows = ""
    for name, sd in skills.items():
        c = sd.get("constraints", {}); tags = []
        if c.get("fragile"): tags.append("易碎")
        force_val = c.get("force", {}).get("max_force_n", "?")
        if isinstance(force_val, dict): force_val = force_val.get("value", "?")
        if force_val: tags.append(f'力≤{force_val}N')
        if c.get("avoid"): tags.append(f'避: {",".join(c["avoid"])}')
        rows += f'<tr><td style="padding:3px 6px;font-weight:600;">{name}</td><td style="padding:3px 6px;color:#475569;">{sd.get("target","")}</td><td style="padding:3px 6px;">{" · ".join(tags) if tags else "—"}</td></tr>'
    opt = data.get("optimization_space", {})
    return _card(f'<div style="font-weight:600;color:#1E293B;margin-bottom:6px;font-size:14px;">RobotTaskIR</div>'
        f'<table style="width:100%;"><tr style="background:#F1F5F9;"><td>技能</td><td>目标</td><td>约束</td></tr>{rows}</table>'
        f'<div style="margin-top:6px;font-size:12px;color:#475569;">优化: 力{opt.get("force_range_n","?")}N · 速度{opt.get("velocity_range_ms","?")}m/s</div>')

def render_actions(r):
    actions = r["actions"]; items = ""
    for i, a in enumerate(actions):
        items += f'<div style="padding:3px 0;font-size:13px;"><span style="color:#64748B;">{i+1}.</span> <span style="font-family:monospace;font-weight:600;color:#1E293B;">{a}</span></div>'
    return _card(f'<div style="font-weight:600;color:#1E293B;margin-bottom:6px;font-size:14px;">动作序列</div>{items}', max_h="200px")

def render_trace(r):
    nodes = r["trace"]; items = ""
    for i, n in enumerate(nodes):
        items += f'<div style="padding:2px 0;font-size:11px;"><b>{n.get("module","")}</b>: {n.get("reason","")[:90]}</div>'
    return _card(f'<div style="font-weight:600;color:#1E293B;margin-bottom:6px;font-size:14px;">决策链路</div>{items}', max_h="200px")


# ══════════════════ 主入口 ══════════════════
def run(instr, obs_json, engine, api_key):
    if not isinstance(obs_json, str):
        obs_json = json.dumps(obs_json, ensure_ascii=False) if isinstance(obs_json, (dict, list)) else str(obs_json)
    if not instr or not instr.strip():
        return ("请输入自然语言指令。",) + ("⚠️ 请输入指令",) * 7
    if not obs_json or not obs_json.strip():
        return ("请输入环境感知 JSON。",) + ("⚠️ 请输入感知数据",) * 7
    try: r = pipeline.run(instr, obs_json, engine, api_key or "")
    except Exception as e:
        import traceback; err_msg = f"运行出错: {str(e)[:150]}"
        return (err_msg,) + ("⚠️ 上游异常，该模块无法生成",) * 7
    status = f"完成 · {r['elapsed']}ms · 引擎: {r['planner_name']} · {r['hard']+r['soft']}条约束 · {len(r['actions'])}步"
    sp = r["sem_props"][0] if r["sem_props"] else None
    return (status, render_intent(r), render_property(sp), render_constraint(r),
            render_ir_card(r), render_actions(r), render_trace(r), r.get("ir_raw", "{}"))


# ══════════════════ BUILD UI ══════════════════
def build_ui():
    with gr.Blocks(title="具身智能意图推理中枢 v3.0") as demo:

        # ── 顶部栏 ──
        with gr.Row(elem_id="global_bar"):
            with gr.Column(scale=3):
                gr.Markdown("## 具身智能意图推理中枢 v3.0")
            with gr.Column(scale=1):
                global_api_key = gr.Textbox(label="DeepSeek API Key", type="password", placeholder="sk-...",
                                           value=get_settings().deepseek_api_key or "")

        # ── 主 Tab ──
        with gr.Tabs() as main_tabs:

            # ══════════════════ TAB 1: Playground ══════════════════
            with gr.TabItem("🧠 单脑交互测试 (Playground)", id="tab_playground"):
                with gr.Row(equal_height=True):
                    # LEFT: Input panel (35%)
                    with gr.Column(scale=3, elem_id="playground_left"):
                        gr.Markdown("### 输入区")
                        playground_instr = gr.Textbox(label="自然语言指令", value="抓住杯子", lines=2,
                                                      placeholder="用中文描述你想让机器人做的事")
                        playground_engine = gr.Radio(label="规划引擎",
                            choices=["纯规则引擎 (极速)", "Hybrid (混合优先)", "DeepSeek-V3 (AI 推理)"],
                            value="纯规则引擎 (极速)")
                        with gr.Accordion("环境感知 JSON", open=False):
                            playground_obs = gr.Code(language="json", lines=8,
                                value='{"objects":[{"object_id":"obj-01","category_candidates":[{"name":"cup","score":0.93}],"pose":{"position":{"x":0.35,"y":0.12,"z":0.075}},"geometry":{"size":{"width":0.07,"height":0.10,"depth":0.07}},"appearance":{"color":"white","material":"plastic"},"affordances":["graspable","movable"],"tracking":{"state":"stationary","confidence":0.96,"velocity":{"x":0,"y":0,"z":0},"velocity_confidence":0}}]}')
                        playground_run_btn = gr.Button("▶ 运行推理", variant="primary", elem_id="run_btn")

                    # RIGHT: Results (65%) — card grid
                    with gr.Column(scale=7):
                        playground_status = gr.Markdown("")
                        with gr.Row():
                            with gr.Column(scale=1):
                                playground_intent = gr.Markdown()
                            with gr.Column(scale=1):
                                playground_property = gr.Markdown()
                        with gr.Row():
                            with gr.Column(scale=1):
                                playground_constraint = gr.Markdown()
                            with gr.Column(scale=1):
                                playground_ir = gr.Markdown()
                        with gr.Row():
                            with gr.Column(scale=1):
                                playground_actions = gr.Markdown()
                            with gr.Column(scale=1):
                                playground_trace = gr.Markdown()
                        with gr.Accordion("Developer Mode (raw JSON)", open=False):
                            playground_raw_json = gr.Code(language="json", lines=12, value="{}")

                playground_outputs = [playground_status, playground_intent, playground_property,
                                      playground_constraint, playground_ir, playground_actions,
                                      playground_trace, playground_raw_json]
                playground_run_btn.click(fn=run, inputs=[playground_instr, playground_obs, playground_engine, global_api_key],
                                        outputs=playground_outputs)

                # ── 单题预设测试 (inline) ──
                with gr.Accordion("📋 预设题加载", open=False):
                    with gr.Row():
                        single_cat = gr.Dropdown(label="类别", choices=["简单动作","多对象消歧","否定避障","条件/顺序","数值约束","动态目标","机器人能力限制","缺失角色","不可执行输入","Memory影响"], value="简单动作", scale=1)
                        single_preset = gr.Dropdown(label="预设题", choices=[], scale=2)
                    with gr.Row():
                        single_instr = gr.Textbox(label="指令", lines=1)
                        single_robot = gr.Code(label="机器人属性", language="json", lines=4, value="{}")
                    with gr.Row():
                        single_perc = gr.Code(label="感知 JSON", language="json", lines=6, value="{}")
                        single_assert = gr.Code(label="期望断言", language="json", lines=6, value="{}")
                    with gr.Row():
                        single_run_btn = gr.Button("▶ 运行单题测试", variant="secondary")
                        single_status = gr.Markdown("")
                    with gr.Row():
                        single_score = gr.Markdown("")
                    with gr.Accordion("详细结果", open=False):
                        single_diff = gr.Markdown(""); single_ir = gr.Code(language="json", lines=10, value="{}")

                    def _load_presets():
                        with open(Path(__file__).parent.parent/"eval"/"single_test_presets.json","r",encoding="utf-8") as f:
                            return json.load(f)
                    def _on_cat(cat):
                        cn={"简单动作":"simple_action","多对象消歧":"multi_object_disambiguation","否定避障":"negation_avoid","条件/顺序":"conditional_sequential","数值约束":"numeric_constraint","动态目标":"dynamic_target","机器人能力限制":"robot_capability","缺失角色":"missing_role","不可执行输入":"invalid_input","Memory影响":"memory_impact"}
                        presets=_load_presets();key=cn.get(cat,"simple_action")
                        choices=[p["name"] for p in presets["presets"] if p["category"]==key]
                        return gr.Dropdown(choices=choices,value=choices[0] if choices else None)
                    def _on_preset(cat,name):
                        cn={"简单动作":"simple_action","多对象消歧":"multi_object_disambiguation","否定避障":"negation_avoid","条件/顺序":"conditional_sequential","数值约束":"numeric_constraint","动态目标":"dynamic_target","机器人能力限制":"robot_capability","缺失角色":"missing_role","不可执行输入":"invalid_input","Memory影响":"memory_impact"}
                        presets=_load_presets();key=cn.get(cat,"simple_action")
                        for p in presets["presets"]:
                            if p["name"]==name and p["category"]==key:
                                return (p["instruction"],json.dumps(p["perception_json"],ensure_ascii=False,indent=2),json.dumps(p["robot_capability_json"],ensure_ascii=False,indent=2),json.dumps(p["expected_assertions"],ensure_ascii=False,indent=2))
                        return ("","{}","{}","{}")
                    def _run_single(instr,perc_str,robot_str,assert_str):
                        try:
                            perception=json.loads(perc_str);robot_cap=json.loads(robot_str);assertions=json.loads(assert_str)
                        except Exception as e: return ("","",str(e),"","","{}")
                        try:
                            from robot_intent_agent.eval.assertion_scorer import evaluate_assertions,build_canonical_entity_map
                            from robot_intent_agent.task_semantics import RobotCapability
                            import robot_intent_agent.ir.ir_generator as irg
                            objects_raw=perception.get("objects",[]);raw=[]
                            for obj in objects_raw:
                                pos=obj.get("pose",{}).get("position",{});geom=obj.get("geometry",{}).get("size",obj.get("geometry",{}))
                                app=obj.get("appearance",{});cats=obj.get("category_candidates",[{"name":"unknown","score":0.5}])
                                top=max((c for c in cats if isinstance(c,dict) and c.get("name")),key=lambda c:c.get("score",0),default={"name":"unknown","score":0.5})
                                def _sf(v,d=0.0):
                                    try:return float(v)
                                    except:return d
                                raw.append(RawObjectPercept(name=top["name"],x=_sf(pos.get("x",0)),y=_sf(pos.get("y",0)),z=_sf(pos.get("z",0.03)),width=max(0.001,_sf(geom.get("width",0.05),0.05)),height=max(0.001,_sf(geom.get("height",0.08),0.08)),depth=max(0.001,_sf(geom.get("depth",0.05),0.05)),color=app.get("color","unknown"),material=app.get("material","unknown")))
                            scene=SemanticSceneBuilder().build(raw);target=raw[0].name if raw else "target"
                            cap=RobotCapability(gripper_max_force_n=robot_cap.get("gripper_max_force_n",10.0),gripper_max_width_m=robot_cap.get("gripper_max_width_m",0.10),max_velocity_ms=robot_cap.get("max_velocity_ms",0.3),max_payload_kg=robot_cap.get("max_payload_kg",2.0),workspace_radius_m=robot_cap.get("workspace_radius_m",0.75),is_homed=robot_cap.get("is_homed",True),gripper_has_object=robot_cap.get("gripper_has_object",False),unavailable_skills=robot_cap.get("unavailable_skills",[]))
                            orig_rc=irg.RobotCapability;irg.RobotCapability=lambda:cap
                            bt=BehaviorTreeGenerator().plan(instr,scene=scene);cg=HybridConstraintCompiler().compile(instr,bt,scene=scene,target=target);ir=RobotTaskIRGenerator().generate(instr,bt,cg,scene=scene)
                            irg.RobotCapability=orig_rc
                            emap=build_canonical_entity_map(objects_raw,scene);scored=evaluate_assertions(ir,scene,bt,cg,assertions,emap)
                            card=f"总分: **{scored.total_score:.0%}** ({scored.passed_assertions}/{scored.total_assertions}) | {'✅ 通过' if scored.passed else '❌ 失败'} | CRITICAL:{scored.critical_count} HIGH:{scored.high_count}"
                            diff_lines=["### 字段差异"];[diff_lines.append(f"- **{r.key}**: `{r.expected}` → `{r.actual}`") for r in scored.results if not r.passed]
                            if len(diff_lines)==1:diff_lines.append("✅ 全部通过")
                            return (card,"",f"✅ 完成","\n".join(diff_lines),"",ir.model_dump_json(indent=2))
                        except Exception as e:
                            import traceback;return ("### ❌ 异常","",str(e),"","",traceback.format_exc()[-500:])
                    single_cat.change(fn=_on_cat,inputs=[single_cat],outputs=[single_preset])
                    single_preset.change(fn=_on_preset,inputs=[single_cat,single_preset],outputs=[single_instr,single_perc,single_robot,single_assert])
                    single_run_btn.click(fn=_run_single,inputs=[single_instr,single_perc,single_robot,single_assert],outputs=[single_score,single_diff,single_status,single_diff,single_ir,single_ir])

            # ══════════════════ TAB 2: Evaluation ══════════════════
            with gr.TabItem("📊 意图理解评测中心", id="tab_eval"):
                with gr.Row(elem_id="eval_filter_bar"):
                    eval_dataset = gr.Dropdown(label="数据集", choices=["回归集 (28条 Golden)", "盲测集 (110条 Blind)", "Holdout v3 (150条)", "全部 (138条)"], value="盲测集 (110条 Blind)", scale=2)
                    eval_engine = gr.Radio(label="引擎", choices=["纯规则引擎 (极速)", "DeepSeek-V3 (AI 推理)", "两者对比"], value="纯规则引擎 (极速)", scale=2)
                    eval_filter_dim = gr.Dropdown(label="维度过滤", choices=["全部","实体接地错误","否定错误","条件错误","角色互换","仅CRITICAL","评测器误判"], value="全部", scale=1)
                    eval_api_key = gr.Textbox(label="DS API Key", type="password", placeholder="sk-...", scale=2)
                    eval_run_btn = gr.Button("▶ 运行评测", variant="primary", scale=1)

                with gr.Row(elem_id="status_bar"):
                    eval_progress = gr.Markdown(""); eval_elapsed = gr.Textbox(label="耗时", value="—", interactive=False, scale=1)
                    eval_error_hint = gr.Textbox(label="状态", value="等待运行", interactive=False, scale=2)

                # Unified metric cards
                eval_metric_cards = gr.Markdown("### 等待运行...")
                eval_dim_table = gr.Markdown("")

                # Failure details with filtering
                with gr.Accordion("🔍 失败案例详情", open=False):
                    eval_failures = gr.Markdown("")

                # Reproduce button
                with gr.Row():
                    repro_case_id = gr.Textbox(label="复现 case_id", placeholder="B13", scale=1)
                    repro_btn = gr.Button("🔄 复现此Case", variant="secondary", scale=1)
                    repro_output = gr.Markdown("", scale=4)

                with gr.Row():
                    eval_export_json = gr.Code(label="结果 JSON", language="json", lines=8, value="{}")
                with gr.Row():
                    export_json_btn = gr.Button("📥 JSON"); export_md_btn = gr.Button("📥 MD"); export_csv_btn = gr.Button("📥 CSV")
                export_status = gr.Markdown("")

                # ── Eval function (enhanced) ──
                def run_eval(dataset_choice, engine_choice, filter_dim, api_key):
                    import time as _t, json as _j; t0=_t.time()
                    try:
                        from robot_intent_agent.eval.upgraded_runner import UpgradedEvalRunner

                        # ── Determine planner based on engine_choice ──
                        use_ds = ("DeepSeek" in engine_choice or "对比" in engine_choice) and bool(api_key.strip())
                        planner = None
                        if use_ds:
                            from robot_intent_agent.planner import LLMPlanner
                            planner = LLMPlanner(api_key=api_key.strip())
                        requested_engine = "DeepSeek" if use_ds else "RuleEngine"

                        ds_paths=[]
                        if "回归集" in dataset_choice or "全部" in dataset_choice: ds_paths.append(("golden","robot_intent_agent/eval/golden_dataset.json"))
                        if "盲测集" in dataset_choice or "全部" in dataset_choice: ds_paths.append(("blind","robot_intent_agent/eval/blind_dataset.json"))
                        if "Holdout" in dataset_choice: ds_paths.append(("holdout","robot_intent_agent/eval/holdout_v3.json"))
                        all_verdicts=[];cm=None
                        all_engine_stats = {}
                        for dn,dp in ds_paths:
                            runner=UpgradedEvalRunner(dp, planner=planner, requested_engine=requested_engine)
                            artifact = runner.run_all()
                            m_artifact = artifact  # Store last artifact for exports
                            m = artifact.summary
                            all_verdicts.extend(artifact.case_results)
                            all_engine_stats[dn] = artifact.engine_stats.to_dict()
                            if cm is None: cm=m
                            else: cm.total+=m.total;cm.passed+=m.passed;cm.failed+=m.failed;cm.pass_rate=round(cm.passed/cm.total,4) if cm.total else 0.0
                        if cm is None: return ("无数据","—","无数据","无数据","无数据",)+("无数据",)*2+("无数据","{}","")
                        m=cm

                        # Unified metrics: 13-dim + severity
                        # Engine audit stats
                        total_engine_stats = {"deepseek_call_attempted":0,"deepseek_call_succeeded":0,
                            "fallback_count":0,"rule_engine_direct_count":0,"validator_executed":0}
                        for dn,es in all_engine_stats.items():
                            for k in total_engine_stats:
                                total_engine_stats[k] += es.get(k,0)
                        engine_info = f"**引擎**: {requested_engine} | DS尝试: {total_engine_stats['deepseek_call_attempted']} | DS成功: {total_engine_stats['deepseek_call_succeeded']} | 回退: {total_engine_stats['fallback_count']} | Rule直行: {total_engine_stats['rule_engine_direct_count']} | Validator: {total_engine_stats['validator_executed']}"

                        cards = f"| 指标 | 数值 |\n|------|------|\n| 总用例 | **{m.total}** |\n| 通过 | **{m.passed}** | 失败 | **{m.failed}** |\n| 通过率 | **{m.pass_rate:.1%}** |\n| CRITICAL | **{m.severity_counts.get('CRITICAL',0)}** | HIGH | **{m.severity_counts.get('HIGH',0)}** | MEDIUM | **{m.severity_counts.get('MEDIUM',0)}** |\n| Severe Veto | **{m.severe_veto_count}** |\n| 平均延迟 | **{m.latency_avg_ms:.1f}ms** | P95 | **{m.latency_p95_ms:.1f}ms** |\n\n{engine_info}"

                        # Dimension table with N/A
                        dim_lines = ["| # | 维度 | 准确率 | 适用 | 正确 | C | H | M |","|---|------|--------|------|------|---|---|---|"]
                        labels = {"action_recognition":"1.动作识别","role_extraction":"2.角色提取","entity_grounding":"3.实体接地","multi_object_disambiguation":"4.多对象消歧","negation_constraint_retention":"5.否定保留","conditional_sequential_understanding":"6.条件/顺序","numeric_operator_unit":"7.数值/算符","perception_factual_fidelity":"8.感知保真","robot_capability_constraint":"9.能力约束","bt_ir_cross_field_consistency":"10.BT/IR一致性","schema_validity":"11.Schema","dangerous_error_pass_through":"12.危险穿透"}
                        for k,d in m.dimensions.items():
                            acc = d.accuracy_display if hasattr(d,'accuracy_display') else (f"{d.accuracy:.1%}" if d.accuracy>=0 else "N/A")
                            dim_lines.append(f"| {labels.get(k,k)} | {acc} | {d.applicable} | {d.correct} | {d.critical_errors} | {d.high_errors} | {d.medium_errors} |")
                        dim_table = "\n".join(dim_lines)

                        # Failure details with filtering
                        def _render_fails(verdicts, fd):
                            dim_map = {"实体接地错误":"entity_grounding","否定错误":"negation_constraint_retention","条件错误":"conditional_sequential_understanding","角色互换":"role_extraction","仅CRITICAL":"CRITICAL","评测器误判":"scorer"}
                            fk = dim_map.get(fd, "")
                            lines=[]; count=0
                            for v in verdicts:
                                if v.passed: continue
                                if fk=="CRITICAL" and not v.has_critical: continue
                                if fk and fk!="CRITICAL" and not any(fk in f.metric for f in v.findings): continue
                                count+=1
                                icon="❌" if v.has_critical else "⚠️"
                                lines.append(f"### {icon} {v.case_id} [{v.category}]")
                                lines.append(f"**指令**: {v.instruction[:80]}")
                                lines.append(f"**Action**: `{v.action_expected}` → `{v.action_actual}`")
                                lines.append(f"**Entity**: `{v.theme_entity_expected}` → `{v.theme_entity_actual}`")
                                lines.append(f"**Execution**: expected={v.execution_allowed_expected} actual={v.execution_allowed_actual}")
                                for f in v.findings:
                                    lines.append(f"- [{f.severity.value}] **{f.metric}**: {f.detail[:150]}")
                                is_pipe = any("grounded to wrong" in f.detail or "not propagated" in f.detail for f in v.findings)
                                lines.append(f"**Error Type**: `{'PIPELINE_ERROR' if is_pipe else 'SCORER_ERROR'}`")
                                if v.exception: lines.append(f"**Exception**: `{v.exception[:200]}`")
                                lines.append("")
                            if not lines: lines.append("✅ 无匹配的失败案例")
                            return f"*{count} failures shown*\n\n"+"\n".join(lines)
                        fails = _render_fails(all_verdicts, filter_dim)

                        elapsed=_t.time()-t0;progress=f"完成 {m.total}例 · 通过{m.passed} · 失败{m.failed}"
                        return (progress,f"{elapsed:.1f}s","✅ 完成",cards,dim_table,fails,_j.dumps(m.to_dict(),indent=2,ensure_ascii=False),"")
                    except Exception as e:
                        import traceback;elapsed=_t.time()-t0
                        return (f"❌ 异常",f"{elapsed:.1f}s",f"❌ {type(e).__name__}","异常","异常","异常","{}",traceback.format_exc()[-500:])

                eval_outputs=[eval_progress,eval_elapsed,eval_error_hint,eval_metric_cards,eval_dim_table,eval_failures,eval_export_json,export_status]
                eval_run_btn.click(fn=run_eval,inputs=[eval_dataset,eval_engine,eval_filter_dim,eval_api_key],outputs=eval_outputs)

                # ── Reproduce function ──
                def reproduce_case(case_id, dataset_choice):
                    if not case_id or not case_id.strip(): return "请输入 case_id"
                    try:
                        # Determine dataset path
                        if "回归" in dataset_choice: dp = "robot_intent_agent/eval/golden_dataset.json"
                        elif "Holdout" in dataset_choice: dp = "robot_intent_agent/eval/holdout_v3.json"
                        else: dp = "robot_intent_agent/eval/blind_dataset.json"
                        import json as _j
                        with open(dp,"r",encoding="utf-8") as f: data=_j.load(f)
                        case=None
                        for c in data.get("cases",[]):
                            if c["case_id"]==case_id.strip(): case=c;break
                        if not case: return f"❌ 未找到 case_id={case_id}"
                        # Run pipeline
                        from robot_intent_agent.scene_builder import SemanticSceneBuilder,RawObjectPercept
                        from robot_intent_agent.planner import BehaviorTreeGenerator
                        from robot_intent_agent.constraint import HybridConstraintCompiler
                        from robot_intent_agent.ir import RobotTaskIRGenerator
                        objects_raw=case.get("objects",[]);raw=[]
                        for obj in objects_raw:
                            pos=obj.get("pose",{}).get("position",{});geom=obj.get("geometry",{}).get("size",obj.get("geometry",{}))
                            app=obj.get("appearance",{});cats=obj.get("category_candidates",[{"name":"unknown","score":0.5}])
                            top=max((c for c in cats if isinstance(c,dict) and c.get("name")),key=lambda c:c.get("score",0),default={"name":"unknown","score":0.5})
                            def _sf(v,d=0.0):
                                try:return float(v)
                                except:return d
                            raw.append(RawObjectPercept(name=top["name"],x=_sf(pos.get("x",0)),y=_sf(pos.get("y",0)),z=_sf(pos.get("z",0.03)),width=max(0.001,_sf(geom.get("width",0.05),0.05)),height=max(0.001,_sf(geom.get("height",0.08),0.08)),depth=max(0.001,_sf(geom.get("depth",0.05),0.05)),color=app.get("color","unknown"),material=app.get("material","unknown")))
                        scene=SemanticSceneBuilder().build(raw);target=raw[0].name if raw else "target";instr=case["instruction"]
                        bt=BehaviorTreeGenerator().plan(instr,scene=scene);cg=HybridConstraintCompiler().compile(instr,bt,scene=scene,target=target);ir=RobotTaskIRGenerator().generate(instr,bt,cg,scene=scene)
                        pt=ir.parsed_task;vr=ir.validation_result
                        # Build output
                        lines=[f"## 复现: {case_id} [{case.get('category','')}]",f"**指令**: {instr}",f"**Action**: {pt.action.value if pt else '?'}",""]
                        lines.append("### ParsedTask")
                        if pt:
                            lines.append(f"- Theme: {pt.theme.mention if pt.theme else 'None'} (eid={pt.theme.entity_id if pt.theme else 'None'})")
                            lines.append(f"- Destination: {pt.destination.mention if pt.destination else 'None'}")
                            lines.append(f"- SupportSurface: {pt.support_surface.mention if pt.support_surface else 'None'}")
                            lines.append(f"- Obstacles: {[(o.mention,o.entity_id) for o in (pt.obstacle or [])]}")
                            lines.append(f"- Manner: {pt.manner}")
                            lines.append(f"- Notes: {pt.notes}")
                        lines.append(f"\n### ValidationResult")
                        lines.append(f"- Status: {vr.status.value if vr else '?'}")
                        lines.append(f"- Execution Allowed: {vr.execution_allowed if vr else '?'}")
                        lines.append(f"- Issues: {[(i.code,i.message[:80]) for i in (vr.issues or [])]}")
                        lines.append(f"\n### BT Actions: {[a.skill_name for a in bt.root.flatten_actions()]}")
                        lines.append(f"\n### Expected: {_j.dumps(case.get('expected',{}),ensure_ascii=False)[:300]}")
                        return "\n".join(lines)
                    except Exception as e:
                        import traceback;return f"❌ 复现失败: {e}\n```\n{traceback.format_exc()[-400:]}\n```"

                repro_btn.click(fn=reproduce_case,inputs=[repro_case_id,eval_dataset],outputs=[repro_output])

                # ── Export functions — use stored artifact for consistency ──
                # Global state to hold the last evaluation artifact
                _last_artifact_state = gr.State(None)

                def _export_all_from_artifact(artifact_json_str, ds, eng, fd, ak):
                    """Single export function that re-runs eval ONCE then exports all formats."""
                    try:
                        # Determine dataset path
                        dp="robot_intent_agent/eval/blind_dataset.json"
                        if "回归" in ds: dp="robot_intent_agent/eval/golden_dataset.json"
                        elif "Holdout" in ds: dp="robot_intent_agent/eval/holdout_v3.json"

                        # Determine engine
                        use_ds = ("DeepSeek" in eng or "对比" in eng) and bool(ak.strip())
                        planner = None
                        if use_ds:
                            from robot_intent_agent.planner import LLMPlanner
                            planner = LLMPlanner(api_key=ak.strip())
                        requested_engine = "DeepSeek" if use_ds else "RuleEngine"

                        # Run ONCE
                        from robot_intent_agent.eval.upgraded_runner import UpgradedEvalRunner
                        runner = UpgradedEvalRunner(dp, planner=planner, requested_engine=requested_engine)
                        artifact = runner.run_all()

                        # Export to structured directory
                        out_dir = str(Path(__file__).parent.parent / "eval" / "eval_outputs")
                        result_dir = artifact.export_all(out_dir)

                        return f"✅ Exported to: {result_dir}\nRun ID: {artifact.run_id}\nJSON/MD/CSV all share same run_id."
                    except Exception as e:
                        return f"❌ Export failed: {e}"

                def _exp_json(ds, eng, fd, ak):
                    return _export_all_from_artifact("", ds, eng, fd, ak)
                def _exp_md(ds, eng, fd, ak):
                    return _export_all_from_artifact("", ds, eng, fd, ak)
                def _exp_csv(ds, eng, fd, ak):
                    return _export_all_from_artifact("", ds, eng, fd, ak)

                export_json_btn.click(fn=_exp_json,inputs=[eval_dataset,eval_engine,eval_filter_dim,eval_api_key],outputs=[export_status])
                export_md_btn.click(fn=_exp_md,inputs=[eval_dataset,eval_engine,eval_filter_dim,eval_api_key],outputs=[export_status])
                export_csv_btn.click(fn=_exp_csv,inputs=[eval_dataset,eval_engine,eval_filter_dim,eval_api_key],outputs=[export_status])

    return demo


if __name__ == "__main__":
    build_ui().launch(server_name="0.0.0.0", server_port=7860, share=False)
