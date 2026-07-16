"""
具身智能意图理解系统 — 动态注入交互式压测工作台 v3.0

零硬编码：所有场景、记忆、指令数据均从 UI 实时注入管线。

用法: python web_ui.py → http://localhost:7860
"""

from __future__ import annotations
import json, sys, copy
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import gradio as gr
from robot_intent_agent.config.settings import get_settings
from robot_intent_agent.memory import MemoryRetriever
from robot_intent_agent.scene_builder import SemanticSceneBuilder, RawObjectPercept
from robot_intent_agent.planner import (
    BehaviorTreeGenerator, LLMPlanner, HybridRouter, LLMPlannerError,
)
from robot_intent_agent.constraint import HybridConstraintCompiler
from robot_intent_agent.ir import RobotTaskIRGenerator

# ═══════════════════════════════════
# Design tokens
# ═══════════════════════════════════
TK = {
    "card":"#fff","th":"#E5E7EB","tr":"#F8FAFC","bd":"#E2E8F0","sh":"0 1px 3px rgba(0,0,0,0.06)",
    "t1":"#1E293B","t2":"#475569","t3":"#64748B","t4":"#94A3B8",
    "r_bg":"#FEE2E2","r_t":"#991B1B","a_bg":"#FEF3C7","a_t":"#92400E",
    "b_bg":"#DBEAFE","b_t":"#1E40AF","g_bg":"#DCFCE7","g_t":"#14532D",
    "p_bg":"#F3E8FF","p_t":"#6B21A8","gy_bg":"#F3F4F6","gy_t":"#374151",
    "ac":"#3B82F6",
}

def _card(h, max_h="440px"):
    return f'<div style="background:{TK["card"]};border-radius:10px;padding:18px;box-shadow:{TK["sh"]};border:1px solid {TK["bd"]};color:{TK["t1"]};font-size:14px;line-height:1.65;max-height:{max_h};overflow-y:auto;">{h}</div>'

def _badge(t, bg, fg):
    return f'<span style="display:inline-block;padding:3px 9px;border-radius:6px;font-size:12px;font-weight:500;background:{bg};color:{fg};margin:1px 2px;">{t}</span>'

# ═══════════════════════════════════
# 记忆画像预设
# ═══════════════════════════════════
PROFILES = {
    "标准默认": {"抓取力_N":5.0,"速度_ms":0.15,"风格":"标准","描述":"通用默认参数"},
    "老人轻柔模式": {"抓取力_N":2.5,"速度_ms":0.10,"风格":"轻柔","描述":"低力慢速,左手偏好"},
    "工厂重载模式": {"抓取力_N":8.0,"速度_ms":0.25,"风格":"强力","描述":"高力高速,效率优先"},
    "精密操作模式": {"抓取力_N":1.5,"速度_ms":0.05,"风格":"精密","描述":"超低力超慢速"},
}

# ═══════════════════════════════════
# 场景 JSON 模板
# ═══════════════════════════════════
SCENE_TEMPLATE = json.dumps([
    {"name":"红色药瓶","x":0.15,"y":0.05,"z":0.03,"width":0.03,"height":0.08,"depth":0.03,"color":"red","material":"plastic","affordances":["graspable","fragile","movable"]},
    {"name":"玻璃水杯","x":0.08,"y":0.03,"z":0.06,"width":0.07,"height":0.12,"depth":0.07,"color":"transparent","material":"glass","affordances":["graspable","container","movable"]},
], indent=2, ensure_ascii=False)

# ═══════════════════════════════════
# 管线处理器
# ═══════════════════════════════════
class PipelineProcessor:
    def __init__(self):
        self.rule_planner = BehaviorTreeGenerator()
        self.compiler = HybridConstraintCompiler()
        self.generator = RobotTaskIRGenerator()
        self.builder = SemanticSceneBuilder()
        self._llm = None; self._llm_err = None

    def _get_llm(self, key_override=""):
        s = get_settings(); k = key_override.strip() or s.deepseek_api_key
        if not k: self._llm_err="无API Key"; return None
        if self._llm is None or (key_override.strip() and self._llm._api_key!=k):
            try: self._llm=LLMPlanner(api_key=k); self._llm_err=None
            except Exception as e: self._llm_err=str(e); return None
        return self._llm

    def run(self, instruction, objects, memory_params, engine, api_key):
        # 构建 Memory
        retriever = MemoryRetriever()
        force_n = float(memory_params.get("抓取力_N",5.0))
        vel_ms = float(memory_params.get("速度_ms",0.15))
        style = memory_params.get("风格","标准")
        profile = memory_params.get("画像","标准默认")

        if style=="轻柔" or "轻柔" in profile:
            retriever.add_user_preference("抓取风格","轻柔",用户=profile)
        if style=="强力" or "重载" in profile:
            retriever.add_user_preference("抓取风格","强力",用户=profile)
        if "老人" in profile:
            retriever.add_user_preference("接物手势","左手",用户=profile)
            retriever.add_user_preference("速度偏好","慢速",用户=profile)
        if "精密" in profile:
            retriever.add_user_preference("速度偏好","极慢",用户=profile)

        retriever.add_skill_experience("标准抓取","目标物体",params={"抓力_N":force_n},success=True)
        if force_n<=3.0:
            retriever.add_skill_experience("轻柔抓取","目标物体",params={"抓力_N":force_n},success=True)

        mem_items = [m.to_dict() for m in retriever.search(instruction,top_k=5)]
        scene = self.builder.build(objects)
        from robot_intent_agent.planner.behavior_tree_generator import RuleInstructionParser
        target = RuleInstructionParser.extract_target(instruction)

        planner_name = "Rule Engine"
        bt = None

        if engine=="纯规则引擎 (极速)":
            bt = self.rule_planner.plan(instruction,scene=scene,memory_context=mem_items)
        elif engine in ("DeepSeek-V3 (AI 规划)","Hybrid (规则优先+LLM兜底)"):
            llm = self._get_llm(api_key)
            if llm is None:
                bt = self.rule_planner.plan(instruction,scene=scene,memory_context=mem_items)
                planner_name = f"Rule Engine (LLM不可用)"
            else:
                if engine=="Hybrid (规则优先+LLM兜底)":
                    router = HybridRouter(llm_planner=llm)
                    try:
                        bt = router.plan(instruction,scene=scene,memory_context=mem_items)
                        planner_name = f"Hybrid → {bt.metadata.get('planner','?')}"
                    except Exception:
                        bt = self.rule_planner.plan(instruction,scene=scene,memory_context=mem_items)
                        planner_name = "Rule Engine (Hybrid 失败)"
                else:
                    try:
                        bt = llm.plan(instruction,scene=scene,memory_context=mem_items)
                        planner_name = "DeepSeek-V3"
                    except LLMPlannerError:
                        bt = self.rule_planner.plan(instruction,scene=scene,memory_context=mem_items)
                        planner_name = "Rule Engine (DeepSeek 失败)"
        else:
            bt = self.rule_planner.plan(instruction,scene=scene,memory_context=mem_items)

        cg = self.compiler.compile(instruction,behavior_tree=bt,scene=scene,memory_context=mem_items,target=target)
        ir = self.generator.generate(instruction,behavior_tree=bt,constraint_graph=cg,scene=scene,memory_context=mem_items)
        return {"instruction":instruction,"memory_items":mem_items,"scene":scene,"behavior_tree":bt,
                "constraint_graph":cg,"ir":ir,"planner_name":planner_name}

processor = PipelineProcessor()

# ═══════════════════════════════════
# 渲染函数 (同前, 略精简)
# ═══════════════════════════════════
def _render_memory(items):
    if not items: return _card('<span style="color:#94A3B8;">未检索到记忆条目。</span>')
    tl = {"user_preference":"👤 用户偏好","skill_experience":"🔧 技能经验","environment_prior":"🏠 环境先验"}
    pm = {"high":(TK["r_bg"],TK["r_t"],"高"),"medium":(TK["a_bg"],TK["a_t"],"中"),"low":(TK["gy_bg"],TK["gy_t"],"低")}
    rows = []
    for i,item in enumerate(items[:8]):
        bg = TK["tr"] if i%2==0 else "transparent"
        pri = item.get("priority","medium"); pb,pf,pl = pm.get(pri,pm["low"])
        val = json.dumps(item.get("value",""),ensure_ascii=False)
        rows.append(f'<tr style="background:{bg};height:38px;"><td style="padding:10px 12px;color:{TK["t1"]};font-size:14px;">{tl.get(item.get("memory_type",""),"📌")}</td><td style="padding:10px 12px;color:{TK["t1"]};font-weight:500;font-size:14px;">{item.get("key","?")}</td><td style="padding:10px 12px;color:{TK["t2"]};font-size:13px;">{val[:50]}</td><td style="padding:10px 12px;">{_badge(pl,pb,pf)}</td></tr>')
    return _card('<table style="width:100%;border-collapse:collapse;">'+f'<tr style="background:{TK["th"]};color:{TK["t2"]};font-weight:600;font-size:14px;"><td style="padding:10px 12px;border-radius:6px 0 0 6px;">类型</td><td style="padding:10px 12px;">键</td><td style="padding:10px 12px;">值</td><td style="padding:10px 12px;border-radius:0 6px 6px 0;">优先级</td></tr>'+"".join(rows)+'</table>')

def _render_scene(scene):
    if not scene or not scene.objects: return _card('<span style="color:#94A3B8;">无场景物体。</span>')
    al = {"graspable":"可抓取","fragile":"易碎","movable":"可移动","container":"容器","stackable":"可堆叠","fixed":"固定","pushable":"可推"}
    ab_map = {"graspable":(TK["b_bg"],TK["b_t"]),"fragile":(TK["r_bg"],TK["r_t"]),"movable":(TK["g_bg"],TK["g_t"]),"container":(TK["a_bg"],TK["a_t"]),"stackable":(TK["p_bg"],TK["p_t"]),"fixed":(TK["gy_bg"],TK["gy_t"])}
    o_rows=[]
    for i,o in enumerate(scene.objects):
        bg = TK["tr"] if i%2==0 else "transparent"
        tags = " ".join(_badge(al.get(a.value,a.value),*ab_map.get(a.value,(TK["gy_bg"],TK["gy_t"]))) for a in o.affordances[:4])
        o_rows.append(f'<tr style="background:{bg};height:38px;"><td style="padding:10px 12px;color:{TK["t1"]};font-weight:500;font-size:14px;">{o.name}</td><td style="padding:10px 12px;color:{TK["t2"]};font-size:13px;">{o.label or "—"}</td><td style="padding:10px 12px;color:{TK["t2"]};font-size:13px;font-family:monospace;">({o.position.x:.2f},{o.position.y:.2f},{o.position.z:.2f})</td><td style="padding:10px 12px;color:{TK["t2"]};font-size:13px;font-family:monospace;">{o.bbox.width:.2f}x{o.bbox.height:.2f}x{o.bbox.depth:.2f}</td><td style="padding:10px 12px;">{tags}</td></tr>')
    rl = {"left_of":"左","right_of":"右","above":"上","below":"下","in_front_of":"前","behind":"后","near":"近","blocking":"阻挡","supporting":"支撑","inside":"内"}
    rt=""
    for r in scene.relations:
        l=rl.get(r.predicate.value,r.predicate.value); is_b=r.predicate.value=="blocking"
        rt+=_badge(l,TK["r_bg"] if is_b else TK["b_bg"],TK["r_t"] if is_b else TK["b_t"])+" "
    return _card(
        f'<div style="font-weight:600;color:{TK["t1"]};margin-bottom:10px;font-size:14px;">📦 物体列表</div>'
        '<table style="width:100%;border-collapse:collapse;">'+f'<tr style="background:{TK["th"]};color:{TK["t2"]};font-weight:600;font-size:14px;"><td style="padding:10px 12px;border-radius:6px 0 0 6px;">名称</td><td style="padding:10px 12px;">语义</td><td style="padding:10px 12px;">位置(m)</td><td style="padding:10px 12px;">包围盒(m)</td><td style="padding:10px 12px;border-radius:0 6px 6px 0;">可供性</td></tr>'
        +"".join(o_rows)+'</table>'
        +f'<div style="font-weight:600;color:{TK["t1"]};margin:16px 0 10px;font-size:14px;">🔗 空间关系（{len(scene.relations)}条）</div><div style="line-height:2.8;">{rt}</div>')

def _render_bt(bt):
    if not bt: return _card('<span style="color:#94A3B8;">未生成行为树。</span>')
    ts={"sequence":(TK["b_bg"],TK["b_t"]),"fallback":(TK["p_bg"],TK["p_t"]),"action":(TK["g_bg"],TK["g_t"]),"condition":(TK["a_bg"],TK["a_t"]),"decorator":(TK["p_bg"],TK["p_t"]),"parallel":(TK["r_bg"],TK["r_t"])}
    tl={"sequence":"顺序","fallback":"选择","action":"动作","condition":"条件","decorator":"修饰","parallel":"并行"}
    def nd(node,d=0):
        ml=20 if d>0 else 0; pad=8+d*22
        bg,tx=ts.get(node.type.value,(TK["gy_bg"],TK["gy_t"])); label=tl.get(node.type.value,node.type.value)
        border=f'border-left:4px solid {TK["ac"]};' if d==0 else (f'border-left:2px solid {TK["bd"]};' if d==1 else 'border-left:1px solid transparent;')
        out=f'<div style="padding:6px 0 6px {pad}px;margin-left:{ml}px;{border}">{_badge(label,bg,tx)} '
        out+=f'<span style="color:{TK["t1"]};font-weight:{"600" if d==0 else "500"};font-size:14px;">{node.name[:55]}</span>'
        if node.skill:
            t=node.skill.target or ""
            out+=f' <span style="font-family:Consolas,monospace;color:{TK["t1"]};font-weight:700;font-size:13px;">{node.skill.skill_name}</span>'
            out+=f'<span style="color:{TK["t2"]};font-size:13px;">({t})</span>'
            ps=[]
            for k,v in list(node.skill.params.items())[:3]:
                if k=="force_n":ps.append(f"抓力={v}N")
                elif k=="velocity_ms":ps.append(f"速度={v}m/s")
            if ps:out+=f' <span style="color:{TK["t3"]};font-size:12px;">{" · ".join(ps)}</span>'
        if node.condition:
            cl={"is_gripper_empty":"夹爪是否为空?","target_in_view":"目标是否可见?","path_clear":"路径是否畅通?"}
            out+=f' <span style="color:{TK["a_t"]};font-size:13px;">? {cl.get(node.condition.condition,node.condition.condition)}</span>'
        out+="</div>"
        for c in node.children:out+=nd(c,d+1)
        return out
    return _card(f'<div style="font-family:Consolas,monospace;font-size:14px;line-height:2.2;">{nd(bt.root)}</div><div style="margin-top:12px;color:{TK["t3"]};font-size:12px;">共 {bt.root.action_count()} 个动作节点</div>',max_h="520px")

def _render_constraint(cg):
    if not cg or not cg.nodes: return _card('<span style="color:#94A3B8;">未生成约束。</span>')
    cd={"safety":"#EF4444","physical":"#3B82F6","spatial":"#10B981","interaction":"#F59E0B","temporal":"#8B5CF6"}
    cl={"safety":"安全","physical":"物理","spatial":"空间","interaction":"交互","temporal":"时序"}
    def grp(nodes,title,bb,bf):
        if not nodes:return ""
        rows=""
        for n in nodes:
            rows+=f'<tr><td style="padding:10px 12px;width:16px;"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{cd.get(n.category.value,"#999")};"></span></td><td style="padding:10px 12px;font-family:Consolas,monospace;font-size:15px;color:{TK["t1"]};font-weight:500;">{n.expression}</td><td style="padding:10px 12px;">{_badge(cl.get(n.category.value,n.category.value),TK["gy_bg"],TK["gy_t"])}</td><td style="padding:10px 12px;color:{TK["t2"]};font-size:13px;font-weight:500;">{n.applies_to_skill or "全局"}</td></tr>'
        return f'<div style="margin-bottom:14px;"><span style="display:inline-block;padding:4px 12px;border-radius:10px;font-size:13px;font-weight:bold;background:{bb};color:{bf};">{title} · {len(nodes)}条</span><table style="width:100%;border-collapse:collapse;margin-top:8px;">{rows}</table></div>'
    bindings=cg.bind_to_skills()
    tags="".join(_badge(f"{s}：{len(ns)}条",TK["b_bg"],TK["b_t"])+" " for s,ns in bindings.items() if s!="_global")
    return _card(grp(cg.hard_constraints(),"🔴 硬约束 — 不可违反",TK["r_bg"],TK["r_t"])+grp(cg.soft_constraints(),"🟡 软约束 — 尽量满足",TK["a_bg"],TK["a_t"])+f'<div style="margin-top:10px;color:{TK["t2"]};font-size:13px;font-weight:500;">约束总数：{len(cg.nodes)} 条</div><div style="margin-top:8px;">{tags}</div>')

def _render_ir(ir):
    if not ir: return _card('<span style="color:#94A3B8;">未生成 IR。</span>')
    data=json.loads(ir.model_dump_json())
    rows=""
    for i,(name,sd) in enumerate(data.get("skills",{}).items()):
        c=sd.get("constraints",{});tags=""
        if c.get("fragile"):tags+=_badge("易碎",TK["r_bg"],TK["r_t"])+" "
        if c.get("force"):tags+=_badge(f'抓力≤{c["force"].get("max_force_n","?")}N',TK["b_bg"],TK["b_t"])+" "
        if c.get("avoid"):tags+=_badge(f'避让=[{",".join(c["avoid"])}]',TK["a_bg"],TK["a_t"])+" "
        if c.get("velocity"):tags+=_badge(f'速度≤{c["velocity"].get("max_linear_ms","?")}m/s',TK["g_bg"],TK["g_t"])+" "
        bg=TK["tr"] if i%2==0 else "transparent"
        rows+=f'<tr style="background:{bg};border-bottom:1px solid {TK["bd"]};height:42px;"><td style="padding:10px 12px;color:{TK["t1"]};font-weight:600;font-size:14px;">{name}</td><td style="padding:10px 12px;color:{TK["t2"]};font-size:14px;">{sd.get("target","")}</td><td style="padding:10px 12px;line-height:2.4;">{tags}</td></tr>'
    opt=data.get("optimization_space",{})
    fv=str(opt.get("force_range_n","?"));vv=str(opt.get("velocity_range_ms","?"));tv=", ".join(opt.get("targets",[]))
    return _card(
        f'<div style="font-weight:600;color:{TK["t1"]};margin-bottom:10px;font-size:14px;">🔧 技能与约束</div>'
        '<table style="width:100%;border-collapse:collapse;">'+f'<tr style="background:{TK["th"]};color:{TK["t2"]};font-weight:600;font-size:14px;"><td style="padding:10px 12px;border-radius:6px 0 0 6px;">技能</td><td style="padding:10px 12px;">目标</td><td style="padding:10px 12px;border-radius:0 6px 6px 0;">约束标签</td></tr>'+rows+'</table>'
        +f'<div style="font-weight:600;color:{TK["t1"]};margin:20px 0 12px;font-size:14px;">🎛️ 优化空间</div>'
        '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;">'
        +f'<div style="background:{TK["tr"]};border-radius:10px;padding:20px 16px;text-align:center;border:1px solid {TK["bd"]};"><div style="color:{TK["t3"]};font-size:11px;margin-bottom:6px;">抓力范围</div><div style="color:{TK["t1"]};font-size:22px;font-weight:700;font-family:Consolas,monospace;">{fv} N</div></div>'
        +f'<div style="background:{TK["tr"]};border-radius:10px;padding:20px 16px;text-align:center;border:1px solid {TK["bd"]};"><div style="color:{TK["t3"]};font-size:11px;margin-bottom:6px;">速度范围</div><div style="color:{TK["t1"]};font-size:22px;font-weight:700;font-family:Consolas,monospace;">{vv} m/s</div></div>'
        +f'<div style="background:{TK["tr"]};border-radius:10px;padding:20px 16px;text-align:center;border:1px solid {TK["bd"]};"><div style="color:{TK["t3"]};font-size:11px;margin-bottom:6px;">优化目标</div><div style="color:{TK["t1"]};font-size:18px;font-weight:700;">{tv}</div></div></div>')

# ═══════════════════════════════════
# 主管线入口 — 全量动态注入
# ═══════════════════════════════════
def run_pipeline(
    instruction,
    engine, api_key,
    # Tab B interactive
    obj1_name, obj1_x, obj1_y, obj1_z, obj1_w, obj1_h, obj1_d, obj1_color, obj1_mat,
    obj1_grasp, obj1_frag, obj1_mov, obj1_cont,
    obj2_name, obj2_x, obj2_y, obj2_z, obj2_w, obj2_h, obj2_d, obj2_color, obj2_mat,
    obj2_grasp, obj2_frag, obj2_mov, obj2_cont,
    # Tab B JSON
    scene_json_text, scene_mode,
    # Tab C
    profile, force_override, vel_override,
):
    # ═══ 动态构建场景物体 ═══
    objects = []

    if scene_mode == "JSON 直填":
        if scene_json_text.strip():
            try:
                raw = json.loads(scene_json_text.strip())
                for item in raw:
                    affordances = []
                    for a in item.get("affordances",["graspable","movable"]):
                        try:
                            from robot_intent_agent.schemas.scene import Affordance
                            affordances.append(Affordance(a))
                        except ValueError:
                            pass
                    objects.append(RawObjectPercept(
                        name=item.get("name","物体"),
                        x=float(item.get("x",0)), y=float(item.get("y",0)), z=float(item.get("z",0.03)),
                        width=float(item.get("width",0.04)), height=float(item.get("height",0.08)), depth=float(item.get("depth",0.04)),
                        color=item.get("color","unknown"), material=item.get("material","unknown"),
                        extra_attrs={"affordances_override": affordances} if affordances else {},
                    ))
            except Exception as e:
                return (f"JSON 解析失败: {e}",)+("",)*7
    else:
        # 交互式模式：从 UI 控件组装
        def _make_obj(name, x, y, z, w, h, d, color, mat, grasp, frag, mov, cont):
            if not name.strip(): return None
            attrs = {}
            from robot_intent_agent.schemas.scene import Affordance
            affordances = []
            if grasp: affordances.append(Affordance.GRASPABLE)
            if frag: affordances.append(Affordance.FRAGILE); attrs["fragile"]=True
            if mov: affordances.append(Affordance.MOVABLE)
            if cont: affordances.append(Affordance.CONTAINER)
            if not affordances:
                affordances = [Affordance.GRASPABLE, Affordance.MOVABLE]
            obj = RawObjectPercept(
                name=name.strip(), x=float(x), y=float(y), z=float(z),
                width=float(w), height=float(h), depth=float(d),
                color=color.strip() or "unknown", material=mat.strip() or "unknown",
                extra_attrs=attrs,
            )
            # patch affordances onto the resulting SceneObject
            obj._affordances_override = affordances
            return obj

        # Monkey-patch RawObjectPercept.to_scene_object to use our affordances
        orig_to_scene = RawObjectPercept.to_scene_object
        def patched_to_scene(self):
            obj = orig_to_scene(self)
            if hasattr(self, '_affordances_override') and self._affordances_override:
                obj.affordances = self._affordances_override
            return obj
        RawObjectPercept.to_scene_object = patched_to_scene

        try:
            for args in [
                (obj1_name,obj1_x,obj1_y,obj1_z,obj1_w,obj1_h,obj1_d,obj1_color,obj1_mat,obj1_grasp,obj1_frag,obj1_mov,obj1_cont),
                (obj2_name,obj2_x,obj2_y,obj2_z,obj2_w,obj2_h,obj2_d,obj2_color,obj2_mat,obj2_grasp,obj2_frag,obj2_mov,obj2_cont),
            ]:
                o = _make_obj(*args)
                if o: objects.append(o)
        finally:
            RawObjectPercept.to_scene_object = orig_to_scene

    if not objects:
        return ("⚠️ 请至少配置一个物体。",)+("",)*7

    # ═══ 动态构建记忆参数 ═══
    memory_params = {
        "抓取力_N": float(force_override),
        "速度_ms": float(vel_override),
        "风格": PROFILES.get(profile,{}).get("风格","标准"),
        "画像": profile,
    }

    # ═══ 跑管线 ═══
    r = processor.run(instruction, objects, memory_params, engine, api_key or "")

    status = (
        f"✅ 动态注入成功 | 引擎: {r['planner_name']} | "
        f"场景: {len(r['scene'].objects)}物体 · {len(r['scene'].relations)}条关系 | "
        f"记忆: {len(r['memory_items'])}条 | "
        f"动作: {r['behavior_tree'].root.action_count()}个 | "
        f"约束: {len(r['constraint_graph'].nodes)}条"
    )

    ir_raw = r["ir"].model_dump_json(indent=2) if r["ir"] else ""
    return (status,
            _render_memory(r["memory_items"]),
            _render_scene(r["scene"]),
            _render_bt(r["behavior_tree"]),
            _render_constraint(r["constraint_graph"]),
            _render_ir(r["ir"]),
            ir_raw, ir_raw)

# ═══════════════════════════════════
# Profile change → update sliders
# ═══════════════════════════════════
def on_profile_change(profile):
    p = PROFILES.get(profile, PROFILES["标准默认"])
    return p.get("抓取力_N",5.0), p.get("速度_ms",0.15)

# ═══════════════════════════════════
# UI
# ═══════════════════════════════════
PAGE_CSS = """
body,.gradio-container{background:#F1F4F9!important;}
.gradio-container{max-width:1300px!important;margin:0 auto!important;}
h4{color:#334155!important;font-size:14px!important;font-weight:600!important;margin-bottom:6px!important;}
.tab-nav{font-size:14px!important;font-weight:500!important;}
#ir_json_box .cm-editor{background:#0F172A!important;}
#ir_json_box .cm-editor .cm-content{color:#38BDF8!important;font-family:'JetBrains Mono',Consolas,monospace!important;}
#ir_json_box .cm-editor .cm-gutters{background:#1E293B!important;color:#64748B!important;border-right:1px solid #334155!important;}
"""

def build_ui():
    settings = get_settings()
    has_key = settings.has_deepseek_key()

    with gr.Blocks(title="具身智能意图理解系统 — 动态压测工作台", head=gr.HTML(f"<style>{PAGE_CSS}</style>")) as demo:
        gr.Markdown("# 🤖 具身智能意图理解 — 动态注入压测工作台\n**零硬编码 · 全量 UI 驱动 · 实时管线响应**")

        # ═══════════════════ TABS ═══════════════════
        with gr.Tabs():
            # ═══ TAB A: 指令与引擎 ═══
            with gr.TabItem("📝 指令与引擎", id="tab_a"):
                with gr.Row():
                    instr = gr.Textbox(
                        label="自然语言指令（支持任意复杂中文口语）",
                        value="请把桌上的红色药瓶递到我手上，动作轻一点，千万别碰倒旁边的玻璃水杯",
                        lines=2, scale=3,
                        placeholder="例如：把桌上不是红色的东西推到左边，速度快一点，别碰到中间的水杯",
                    )
                with gr.Row():
                    engine = gr.Radio(
                        label="🧠 规划引擎",
                        choices=["纯规则引擎 (极速)","Hybrid (规则优先+LLM兜底)","DeepSeek-V3 (AI 规划)"],
                        value="纯规则引擎 (极速)",
                        scale=2,
                    )
                    with gr.Column(scale=1):
                        api_key = gr.Textbox(
                            label="🔑 DeepSeek API Key",
                            value=settings.deepseek_api_key if has_key else "",
                            type="password",
                            placeholder="sk-..." if not has_key else "已加载环境变量",
                        )

            # ═══ TAB B: 场景编辑器 ═══
            with gr.TabItem("🎯 场景编辑器", id="tab_b"):
                scene_mode = gr.Radio(
                    label="编辑模式",
                    choices=["交互式滑块","JSON 直填"],
                    value="交互式滑块",
                )

                with gr.Column(visible=True) as slider_panel:
                    gr.Markdown("### 物体 1 — 操作目标")
                    with gr.Row():
                        obj1_name = gr.Textbox(label="名称", value="红色药瓶", scale=2)
                        obj1_color = gr.Textbox(label="颜色", value="red", scale=1)
                        obj1_mat = gr.Textbox(label="材质", value="plastic", scale=1)
                    with gr.Row():
                        obj1_x = gr.Slider(-0.5, 0.5, 0.15, step=0.01, label="X (m)")
                        obj1_y = gr.Slider(-0.5, 0.5, 0.05, step=0.01, label="Y (m)")
                        obj1_z = gr.Slider(0.0, 0.3, 0.03, step=0.01, label="Z (m)")
                    with gr.Row():
                        obj1_w = gr.Slider(0.01, 0.2, 0.03, step=0.01, label="宽 (m)")
                        obj1_h = gr.Slider(0.01, 0.3, 0.08, step=0.01, label="高 (m)")
                        obj1_d = gr.Slider(0.01, 0.2, 0.03, step=0.01, label="深 (m)")
                    with gr.Row():
                        obj1_grasp = gr.Checkbox(label="✋可抓取", value=True)
                        obj1_frag = gr.Checkbox(label="💔易碎", value=True)
                        obj1_mov = gr.Checkbox(label="↔️可移动", value=True)
                        obj1_cont = gr.Checkbox(label="📦容器", value=False)

                    gr.Markdown("---")
                    gr.Markdown("### 物体 2 — 障碍物")
                    with gr.Row():
                        obj2_name = gr.Textbox(label="名称", value="玻璃水杯", scale=2)
                        obj2_color = gr.Textbox(label="颜色", value="transparent", scale=1)
                        obj2_mat = gr.Textbox(label="材质", value="glass", scale=1)
                    with gr.Row():
                        obj2_x = gr.Slider(-0.5, 0.5, 0.08, step=0.01, label="X (m)")
                        obj2_y = gr.Slider(-0.5, 0.5, 0.03, step=0.01, label="Y (m)")
                        obj2_z = gr.Slider(0.0, 0.3, 0.06, step=0.01, label="Z (m)")
                    with gr.Row():
                        obj2_w = gr.Slider(0.01, 0.2, 0.07, step=0.01, label="宽 (m)")
                        obj2_h = gr.Slider(0.01, 0.3, 0.12, step=0.01, label="高 (m)")
                        obj2_d = gr.Slider(0.01, 0.2, 0.07, step=0.01, label="深 (m)")
                    with gr.Row():
                        obj2_grasp = gr.Checkbox(label="✋可抓取", value=True)
                        obj2_frag = gr.Checkbox(label="💔易碎", value=False)
                        obj2_mov = gr.Checkbox(label="↔️可移动", value=True)
                        obj2_cont = gr.Checkbox(label="📦容器", value=True)

                with gr.Column(visible=False) as json_panel:
                    scene_json = gr.Code(
                        label="Raw Perception JSON（粘贴任意复杂场景）",
                        language="json",
                        value=SCENE_TEMPLATE,
                        lines=14,
                    )

                def toggle_scene_mode(mode):
                    return (
                        gr.update(visible=(mode=="交互式滑块")),
                        gr.update(visible=(mode=="JSON 直填")),
                    )
                scene_mode.change(fn=toggle_scene_mode, inputs=[scene_mode], outputs=[slider_panel, json_panel])

            # ═══ TAB C: 记忆编辑器 ═══
            with gr.TabItem("🧠 记忆编辑器", id="tab_c"):
                with gr.Row():
                    profile = gr.Dropdown(
                        label="👤 用户画像",
                        choices=list(PROFILES.keys()),
                        value="标准默认",
                        scale=1,
                    )
                    with gr.Column(scale=2):
                        gr.Markdown("")  # spacer
                with gr.Row():
                    force_override = gr.Slider(
                        0.1, 50.0, 5.0, step=0.1,
                        label="最大抓取力 (N) — 越小越轻柔",
                    )
                    vel_override = gr.Slider(
                        0.01, 1.0, 0.15, step=0.01,
                        label="最大速度 (m/s) — 越小越慢",
                    )

                profile.change(fn=on_profile_change, inputs=[profile], outputs=[force_override, vel_override])

        # ═══════════════════ RUN ═══════════════════
        with gr.Row():
            btn = gr.Button("🚀 运行管线（动态注入全部参数）", variant="primary", size="lg")
            status = gr.Markdown("")

        gr.Markdown("---")
        gr.Markdown(
            '<div style="text-align:center;font-size:15px;color:#475569;margin-bottom:20px;">'
            '📝 <b style="color:#1E293B;">输入</b> → '
            '🧠 <b style="color:#1E293B;">记忆检索</b> → '
            '👁️ <b style="color:#1E293B;">场景构建</b> → '
            '🌳 <b style="color:#1E293B;">行为树</b> → '
            '🔒 <b style="color:#1E293B;">约束编译</b> → '
            '📦 <b style="color:#1E293B;">任务 IR</b></div>')

        # ═══════════════════ OUTPUTS ═══════════════════
        with gr.Row(equal_height=True):
            with gr.Column(scale=1): gr.Markdown("#### 🧠 记忆检索（Step 3）"); mem_out = gr.HTML()
            with gr.Column(scale=1): gr.Markdown("#### 👁️ 语义场景（Step 4）"); scn_out = gr.HTML()
        gr.Markdown("#### 🌳 行为树（Step 5）"); bt_out = gr.HTML()
        gr.Markdown("#### 🔒 约束图（Step 6）"); cg_out = gr.HTML()
        gr.Markdown("#### 📦 任务中间表示（Step 7）"); ir_out = gr.HTML()
        with gr.Accordion("🔍 完整 RobotTaskIR JSON", open=False):
            ir_json_out = gr.Code(language="json", lines=22, elem_id="ir_json_box")
        ir_dl = gr.Textbox(visible=False)

        # ═══════════════════ EVENT WIRING ═══════════════════
        all_inputs = [
            instr, engine, api_key,
            obj1_name,obj1_x,obj1_y,obj1_z,obj1_w,obj1_h,obj1_d,obj1_color,obj1_mat,
            obj1_grasp,obj1_frag,obj1_mov,obj1_cont,
            obj2_name,obj2_x,obj2_y,obj2_z,obj2_w,obj2_h,obj2_d,obj2_color,obj2_mat,
            obj2_grasp,obj2_frag,obj2_mov,obj2_cont,
            scene_json, scene_mode,
            profile, force_override, vel_override,
        ]
        all_outputs = [status,mem_out,scn_out,bt_out,cg_out,ir_out,ir_json_out,ir_dl]

        btn.click(fn=run_pipeline, inputs=all_inputs, outputs=all_outputs)
        demo.load(fn=run_pipeline, inputs=all_inputs, outputs=all_outputs)

    return demo

if __name__ == "__main__":
    build_ui().launch(server_name="0.0.0.0", server_port=7860, share=False)
