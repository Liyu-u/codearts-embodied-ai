"""
Embodied Intent Reasoner — 动态全景压测工作台 v2.0

Language → Scene → Memory → Constraint → Universal Task IR v2.0
全量交互式注入 · 6 步决策轨迹可视化 · 参数证据链溯源

用法: python web_ui.py → http://localhost:7860
"""

from __future__ import annotations
import json, sys, time, copy
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

# ══════════════════ 设计令牌 ══════════════════
T = {
    "bg":"#F8FAFC","card":"#FFFFFF","th":"#F1F5F9","tr":"#F8FAFC","bd":"#E2E8F0",
    "sh":"0 1px 2px rgba(0,0,0,0.04)","t1":"#1E293B","t2":"#475569","t3":"#64748B","t4":"#94A3B8",
    "ac":"#3B82F6","r_bg":"#FEE2E2","r_t":"#991B1B","a_bg":"#FEF3C7","a_t":"#92400E",
    "b_bg":"#DBEAFE","b_t":"#1E40AF","g_bg":"#DCFCE7","g_t":"#14532D",
    "p_bg":"#F3E8FF","p_t":"#6B21A8","gy_bg":"#F1F5F9","gy_t":"#374151",
}

def _card(h, max_h="440px"):
    return f'<div style="background:{T["card"]};border-radius:8px;padding:16px;box-shadow:{T["sh"]};border:1px solid {T["bd"]};color:{T["t1"]};font-size:13px;line-height:1.6;max-height:{max_h};overflow-y:auto;">{h}</div>'

def _badge(t,bg,fg): return f'<span style="display:inline-block;padding:2px 8px;border-radius:5px;font-size:11px;font-weight:500;background:{bg};color:{fg};margin:1px 3px;">{t}</span>'

# ══════════════════ 记忆画像 ══════════════════
PROFILES = {
    "标准默认": {"力_N":5.0,"速_ms":0.15,"风格":"标准"},
    "老人轻柔 (1.8N)": {"力_N":1.8,"速_ms":0.08,"风格":"轻柔"},
    "工厂重载 (8N)": {"力_N":8.0,"速_ms":0.25,"风格":"强力"},
    "精密操作 (1.5N)": {"力_N":1.5,"速_ms":0.05,"风格":"精密"},
}

SCENE_TEMPLATE = json.dumps([
    {"name":"红色药瓶","x":0.15,"y":0.05,"z":0.03,"width":0.03,"height":0.08,"depth":0.03,"color":"red","material":"plastic","affordances":["graspable","fragile","movable"]},
    {"name":"玻璃水杯","x":0.08,"y":0.03,"z":0.06,"width":0.07,"height":0.12,"depth":0.07,"color":"transparent","material":"glass","affordances":["graspable","container","movable"]},
],indent=2,ensure_ascii=False)

# ══════════════════ 管线处理器 ══════════════════
class PipelineProcessor:
    def __init__(self):
        self.rule_planner=BehaviorTreeGenerator(); self.compiler=HybridConstraintCompiler()
        self.generator=RobotTaskIRGenerator(); self.builder=SemanticSceneBuilder()
        self._llm=None; self._llm_err=None

    def _get_llm(self,key_override=""):
        s=get_settings();k=key_override.strip() or s.deepseek_api_key
        if not k:self._llm_err="无Key";return None
        if self._llm is None or (key_override.strip() and self._llm._api_key!=k):
            try:self._llm=LLMPlanner(api_key=k);self._llm_err=None
            except Exception as e:self._llm_err=str(e);return None
        return self._llm

    def run(self,instruction,objects,profile,force_n,vel_ms,engine,api_key):
        st=time.time()
        retriever=MemoryRetriever()
        p=PROFILES.get(profile,PROFILES["标准默认"]);style=p["风格"]
        if "轻柔" in style or "轻柔" in profile:retriever.add_user_preference("抓取风格","轻柔",画像=profile)
        if "老人" in profile:retriever.add_user_preference("接物手势","左手",画像=profile);retriever.add_user_preference("速度偏好","慢速",画像=profile)
        if "强力" in style:retriever.add_user_preference("抓取风格","强力",画像=profile)
        if "精密" in style:retriever.add_user_preference("速度偏好","极慢",画像=profile)
        retriever.add_skill_experience("标准抓取","目标物体",params={"抓力_N":float(force_n)},success=True)
        if float(force_n)<=3.0:retriever.add_skill_experience("轻柔抓取","目标物体",params={"抓力_N":float(force_n)},success=True)
        mem_items=[m.to_dict() for m in retriever.search(instruction,top_k=5)]
        scene=self.builder.build(objects)
        from robot_intent_agent.planner.behavior_tree_generator import RuleInstructionParser
        target=RuleInstructionParser.extract_target(instruction)
        pn="RuleEngine"
        if engine=="纯规则引擎 (0.3s极速)":bt=self.rule_planner.plan(instruction,scene=scene,memory_context=mem_items)
        elif engine in ("DeepSeek-V3 (AI推理)","Hybrid (混合优先)"):
            llm=self._get_llm(api_key)
            if llm is None:bt=self.rule_planner.plan(instruction,scene=scene,memory_context=mem_items);pn="RuleEngine(LLM不可用)"
            elif engine=="Hybrid (混合优先)":
                try:bt=HybridRouter(llm_planner=llm).plan(instruction,scene=scene,memory_context=mem_items);pn=f"Hybrid→{bt.metadata.get('planner','?')}"
                except Exception:bt=self.rule_planner.plan(instruction,scene=scene,memory_context=mem_items);pn="RuleEngine(Hybrid失败)"
            else:
                try:bt=llm.plan(instruction,scene=scene,memory_context=mem_items);pn="DeepSeek-V3"
                except LLMPlannerError:bt=self.rule_planner.plan(instruction,scene=scene,memory_context=mem_items);pn="RuleEngine(DS失败)"
        else:bt=self.rule_planner.plan(instruction,scene=scene,memory_context=mem_items)
        cg=self.compiler.compile(instruction,behavior_tree=bt,scene=scene,memory_context=mem_items,target=target)
        ir=self.generator.generate(instruction,behavior_tree=bt,constraint_graph=cg,scene=scene,memory_context=mem_items)
        elapsed=round((time.time()-st)*1000)
        return {"instruction":instruction,"memory_items":mem_items,"scene":scene,"behavior_tree":bt,"constraint_graph":cg,"ir":ir,"planner_name":pn,"elapsed_ms":elapsed}

processor=PipelineProcessor()

# ══════════════════ 渲染: 决策轨迹 ══════════════════
def _render_trace(trace_nodes:list)->str:
    if not trace_nodes:return _card('<span style="color:#94A3B8;">无决策轨迹数据。</span>')
    mc={"NL_PARSE":"#DBEAFE","SCENE_GROUNDING":"#DCFCE7","MEMORY_RETRIEVAL":"#FEF3C7","CONSTRAINT_REASONING":"#FEE2E2","CONFLICT_RESOLUTION":"#F3E8FF","TASK_COMPILATION":"#E0E7FF"}
    mt={"NL_PARSE":"#1E40AF","SCENE_GROUNDING":"#14532D","MEMORY_RETRIEVAL":"#92400E","CONSTRAINT_REASONING":"#991B1B","CONFLICT_RESOLUTION":"#6B21A8","TASK_COMPILATION":"#3730A3"}
    ml={"NL_PARSE":"自然语言解析","SCENE_GROUNDING":"场景实体接地","MEMORY_RETRIEVAL":"记忆检索","CONSTRAINT_REASONING":"约束推理","CONFLICT_RESOLUTION":"冲突裁决","TASK_COMPILATION":"任务编译"}
    rows=""
    for i,n in enumerate(trace_nodes):
        m=n.get("module","");c=n.get("confidence",1)
        conf_badge=f'<span style="font-weight:700;color:{"#D97706" if c<0.7 else T["g_t"]};">{c:.0%}</span>'
        deps=" → ".join(n.get("depends_on",[]) or ["—"])
        rows+=(
            f'<tr style="border-bottom:1px solid {T["bd"]};">'
            f'<td style="padding:8px 10px;text-align:center;font-size:16px;">{"📌" if i==0 else "⬇️" if i<len(trace_nodes)-1 else "✅"}</td>'
            f'<td style="padding:8px 10px;">{_badge(ml.get(m,m),mc.get(m,T["gy_bg"]),mt.get(m,T["gy_t"]))}</td>'
            f'<td style="padding:8px 10px;font-size:12px;color:{T["t2"]};max-width:240px;">{(n.get("reason","") or "")[:120]}</td>'
            f'<td style="padding:8px 10px;font-size:11px;color:{T["t3"]};">{deps}</td>'
            f'<td style="padding:8px 10px;text-align:center;">{conf_badge}</td>'
            f'<td style="padding:8px 10px;font-family:monospace;font-size:11px;color:{T["t3"]};text-align:right;">{n.get("latency_ms",0):.1f}ms</td></tr>')
    return _card(
        '<table style="width:100%;border-collapse:collapse;">'
        f'<tr style="background:{T["th"]};color:{T["t2"]};font-weight:600;font-size:12px;"><td style="padding:8px 10px;width:28px;"></td><td style="padding:8px 10px;">模块</td><td style="padding:8px 10px;">裁决理由</td><td style="padding:8px 10px;">依赖链</td><td style="padding:8px 10px;text-align:center;">置信度</td><td style="padding:8px 10px;text-align:right;">耗时</td></tr>'
        +rows+'</table>',max_h="500px")

# ══════════════════ 渲染: 行为树 + 场景联动 ══════════════════
def _render_bt_scene(bt,scene)->str:
    if not bt:return _card('<span style="color:#94A3B8;">未生成行为树。</span>')
    # 收集阻挡关系
    blocking_names=set()
    if scene:
        for r in scene.relations:
            if r.predicate.value=="blocking":
                for o in scene.objects:
                    if o.id in (r.subject,r.object):blocking_names.add(o.name)
    ts={"sequence":(T["b_bg"],T["b_t"]),"fallback":(T["p_bg"],T["p_t"]),"action":(T["g_bg"],T["g_t"]),"condition":(T["a_bg"],T["a_t"]),"decorator":(T["p_bg"],T["p_t"]),"parallel":(T["r_bg"],T["r_t"])}
    tl={"sequence":"顺序","fallback":"选择","action":"动作","condition":"条件","decorator":"修饰","parallel":"并行"}
    def nd(node,d=0):
        ml=20 if d>0 else 0;pad=8+d*22
        bg,tx=ts.get(node.type.value,(T["gy_bg"],T["gy_t"]));label=tl.get(node.type.value,node.type.value)
        border=f'border-left:4px solid {T["ac"]};' if d==0 else (f'border-left:2px solid {T["bd"]};' if d==1 else 'border-left:1px solid transparent;')
        # 高亮 Avoid 节点
        is_avoid=node.skill and node.skill.skill_name=="Avoid"
        row_bg='background:#FEF2F2;' if is_avoid else ''
        out=f'<div style="padding:6px 0 6px {pad}px;margin-left:{ml}px;{border}{row_bg}">{_badge(label,bg,tx)} '
        out+=f'<span style="color:{T["t1"]};font-weight:{"600" if d==0 else "500"};font-size:14px;">{node.name[:55]}</span>'
        if node.skill:
            t=node.skill.target or ""
            out+=f' <span style="font-family:Consolas,monospace;color:{T["t1"]};font-weight:700;font-size:13px;">{node.skill.skill_name}</span>'
            out+=f'<span style="color:{T["t2"]};font-size:13px;">({t})</span>'
            if is_avoid:out+=f' <span style="background:#FEE2E2;color:#991B1B;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:700;">BLOCKING</span>'
            ps=[]
            for k,v in list(node.skill.params.items())[:3]:
                if k=="force_n":ps.append(f"抓力={v}N")
                elif k=="velocity_ms":ps.append(f"速度={v}m/s")
            if ps:out+=f' <span style="color:{T["t3"]};font-size:12px;">· {", ".join(ps)}</span>'
        if node.condition:
            cl={"is_gripper_empty":"夹爪为空?","target_in_view":"目标可见?","path_clear":"路径畅通?"}
            out+=f' <span style="color:{T["a_t"]};font-size:13px;">? {cl.get(node.condition.condition,node.condition.condition)}</span>'
        out+="</div>"
        for c in node.children:out+=nd(c,d+1)
        return out
    html=nd(bt.root)
    # 底部汇总阻挡关系
    block_warning=""
    if blocking_names:
        block_warning=f'<div style="margin-top:8px;padding:6px 10px;background:#FEF2F2;border-radius:6px;font-size:12px;color:#991B1B;">⚠️ 空间干涉：{"、".join(blocking_names)} 阻挡目标路径 → 系统自动前置 Avoid 节点</div>'
    return _card(
        f'<div style="font-family:Consolas,monospace;font-size:14px;line-height:2.2;">{html}</div>'
        f'{block_warning}'
        f'<div style="margin-top:8px;color:{T["t3"]};font-size:12px;">共 {bt.root.action_count()} 个动作节点</div>',max_h="520px")

# ══════════════════ 渲染: 证据链 ══════════════════
def _render_evidence(ir,cg)->str:
    """提取 skills 约束 + clamping 元数据 → 参数溯源表"""
    if not ir:return _card('<span style="color:#94A3B8;">无 IR 数据。</span>')
    data=json.loads(ir.model_dump_json())
    skills=data.get("skills",{})
    force_clamp=cg.metadata.get("force_clamping",{}) if cg else {}
    vel_clamp=cg.metadata.get("velocity_clamping",{}) if cg else {}
    rows=""
    # 从 skills 提取约束参数
    for sname,sd in skills.items():
        c=sd.get("constraints",{})
        if c.get("force"):
            fv_raw=c["force"].get("max_force_n","?")
            # Handle both flat value and ParamValue dict
            if isinstance(fv_raw,dict):
                fv=fv_raw.get("value","?");src=", ".join(fv_raw.get("source",["rule"]));ev=", ".join(fv_raw.get("evidence",["—"]))
            else:
                fv=fv_raw;src=", ".join(force_clamp.get("sources",["rule"]));ev=", ".join(force_clamp.get("evidence",["—"]))
            rows+=f'<tr><td style="padding:6px 10px;font-family:monospace;color:{T["t1"]};">{sname}.force_n</td><td style="padding:6px 10px;color:{T["t1"]};font-weight:700;">{fv} N</td><td style="padding:6px 10px;">{_badge(src[:30],T["b_bg"],T["b_t"])}</td><td style="padding:6px 10px;font-size:11px;color:{T["t2"]};">{ev[:80]}</td></tr>'
        if c.get("velocity"):
            vv_raw=c["velocity"].get("max_linear_ms","?")
            if isinstance(vv_raw,dict):
                vv=vv_raw.get("value","?");src=", ".join(vv_raw.get("source",["rule"]));ev=", ".join(vv_raw.get("evidence",["—"]))
            else:
                vv=vv_raw;src=", ".join(vel_clamp.get("sources",["rule"]));ev=", ".join(vel_clamp.get("evidence",["—"]))
            rows+=f'<tr><td style="padding:6px 10px;font-family:monospace;color:{T["t1"]};">{sname}.velocity_ms</td><td style="padding:6px 10px;color:{T["t1"]};font-weight:700;">{vv} m/s</td><td style="padding:6px 10px;">{_badge(src[:30],T["b_bg"],T["b_t"])}</td><td style="padding:6px 10px;font-size:11px;color:{T["t2"]};">{ev[:80]}</td></tr>'
        if c.get("fragile"):
            rows+=f'<tr><td style="padding:6px 10px;font-family:monospace;color:{T["t1"]};">{sname}.fragile</td><td style="padding:6px 10px;color:{T["t1"]};font-weight:700;">true</td><td style="padding:6px 10px;">{_badge("affordance",T["r_bg"],T["r_t"])}</td><td style="padding:6px 10px;font-size:11px;color:{T["t2"]};">物体可供性: FRAGILE</td></tr>'
    if not rows:rows=f'<tr><td colspan="4" style="padding:12px;color:{T["t3"]};">无可溯源参数（本任务无跨模块裁决）</td></tr>'
    return _card(
        '<table style="width:100%;border-collapse:collapse;">'
        f'<tr style="background:{T["th"]};color:{T["t2"]};font-weight:600;font-size:12px;"><td style="padding:8px 10px;">参数名</td><td style="padding:8px 10px;">最终值</td><td style="padding:8px 10px;">裁决源 (source)</td><td style="padding:8px 10px;">证据说明 (evidence)</td></tr>'
        +rows+'</table>',max_h="360px")

# ══════════════════ 主管线入口 ══════════════════
def run_pipeline(instruction,engine,api_key,profile,force_n,vel_ms,
    scene_mode,scene_json_text,
    o1n,o1x,o1y,o1z,o1w,o1h,o1d,o1c,o1m,o1_g,o1_f,o1_mv,o1_ct,
    o2n,o2x,o2y,o2z,o2w,o2h,o2d,o2c,o2m,o2_g,o2_f,o2_mv,o2_ct):
    # 动态构建物体
    objects=[]
    if scene_mode=="JSON直填":
        if scene_json_text.strip():
            try:
                raw=json.loads(scene_json_text.strip())
                for item in raw:
                    from robot_intent_agent.schemas.scene import Affordance
                    affs=[]
                    for a in item.get("affordances",["graspable","movable"]):
                        try:affs.append(Affordance(a))
                        except ValueError:pass
                    objects.append(RawObjectPercept(name=item.get("name","物体"),x=float(item.get("x",0)),y=float(item.get("y",0)),z=float(item.get("z",0.03)),width=float(item.get("width",0.04)),height=float(item.get("height",0.08)),depth=float(item.get("depth",0.04)),color=item.get("color","?"),material=item.get("material","?"),extra_attrs={"affs_override":affs} if affs else {}))
            except Exception as e:return (f"JSON解析失败:{e}",)+("",)*6
    else:
        from robot_intent_agent.schemas.scene import Affordance
        orig=RawObjectPercept.to_scene_object
        def _make(n,x,y,z,w,h,d,color,mat,grasp,frag,mov,cont):
            if not n.strip():return None
            affs=[];attrs={}
            if grasp:affs.append(Affordance.GRASPABLE)
            if frag:affs.append(Affordance.FRAGILE);attrs["fragile"]=True
            if mov:affs.append(Affordance.MOVABLE)
            if cont:affs.append(Affordance.CONTAINER)
            if not affs:affs=[Affordance.GRASPABLE,Affordance.MOVABLE]
            o=RawObjectPercept(name=n.strip(),x=float(x),y=float(y),z=float(z),width=float(w),height=float(h),depth=float(d),color=color.strip() or "?",material=mat.strip() or "?",extra_attrs=attrs)
            o._affs=affs;return o
        try:
            def patch(self):
                obj=orig(self)
                if hasattr(self,'_affs') and self._affs:obj.affordances=self._affs
                return obj
            RawObjectPercept.to_scene_object=patch
            for args in [(o1n,o1x,o1y,o1z,o1w,o1h,o1d,o1c,o1m,o1_g,o1_f,o1_mv,o1_ct),(o2n,o2x,o2y,o2z,o2w,o2h,o2d,o2c,o2m,o2_g,o2_f,o2_mv,o2_ct)]:
                o=_make(*args)
                if o:objects.append(o)
        finally:RawObjectPercept.to_scene_object=orig
    if not objects:return ("⚠️ 至少配置一个物体。",)+("",)*6

    r=processor.run(instruction,objects,profile,force_n,vel_ms,engine,api_key or "")
    ir=r["ir"];ir_raw=ir.model_dump_json(indent=2) if ir else""
    trace_data=json.loads(ir_raw).get("decision_trace",[]) if ir_raw else[]
    conf=getattr(ir,'overall_confidence',0.95)

    status=(f"✅ 推理完成 | 引擎: {r['planner_name']} | 总置信度: {conf:.3f} | 决策轨迹: {len(trace_data)}步 | 耗时: {r['elapsed_ms']}ms")

    return (status,
            _render_trace(trace_data),
            _render_bt_scene(r["behavior_tree"],r["scene"]),
            _render_evidence(ir,r["constraint_graph"]),
            ir_raw,ir_raw,
            # 记忆+场景摘要 (供内部查看)
            _card(f'记忆命中: {len(r["memory_items"])}条 | 场景物体: {len(r["scene"].objects)}个 | 空间关系: {len(r["scene"].relations)}条 | 约束: {len(r["constraint_graph"].nodes)}条'))

# ══════════════════ Profile → 滑块联动 ══════════════════
def on_profile_change(profile):
    p=PROFILES.get(profile,PROFILES["标准默认"])
    return p["力_N"],p["速_ms"]

# ══════════════════ UI ══════════════════
PAGE_CSS="""
body,.gradio-container{background:#F8FAFC!important;}.gradio-container{max-width:1340px!important;margin:0 auto!important;}
h4{color:#334155!important;font-size:13px!important;font-weight:600!important;margin-bottom:4px!important;}
.tab-nav button{font-size:14px!important;}
#ir_json_box .cm-editor{background:#0F172A!important;border-radius:8px!important;}
#ir_json_box .cm-editor .cm-content{color:#38BDF8!important;font-family:'JetBrains Mono',Consolas,monospace!important;font-size:13px!important;line-height:1.7!important;}
#ir_json_box .cm-editor .cm-gutters{background:#1E293B!important;color:#64748B!important;border-right:1px solid #334155!important;}
"""

def build_ui():
    s=get_settings();has_key=s.has_deepseek_key()
    with gr.Blocks(title="Embodied Intent Reasoner v2.0",head=gr.HTML(f"<style>{PAGE_CSS}</style>")) as demo:
        gr.Markdown("# 🧠 Embodied Intent Reasoner (具身意图推理器) v2.0\n**Language → Scene → Memory → Constraint → Universal Task IR v2.0 · 全景动态压测工作台**")

        with gr.Tabs():
            # ═══ TAB 1: 指令+引擎 ═══
            with gr.TabItem("📝 自然语言与引擎调度"):
                with gr.Row():
                    instr=gr.Textbox(label="自然语言指令（支持任意复杂中文口语）",value="请把桌上的红色药瓶递到我手上，动作轻一点，千万别碰倒旁边的玻璃水杯",lines=2,scale=3,placeholder="例如：把桌上不是红色的东西推到左边，速度快一点，别碰到中间的水杯")
                with gr.Row():
                    engine=gr.Radio(label="🧠 规划引擎",choices=["纯规则引擎 (0.3s极速)","Hybrid (混合优先)","DeepSeek-V3 (AI推理)"],value="纯规则引擎 (0.3s极速)",scale=2)
                    api_key=gr.Textbox(label="🔑 DeepSeek API Key",value=s.deepseek_api_key if has_key else"",type="password",placeholder="sk-..." if not has_key else"已加载环境变量",scale=1)

            # ═══ TAB 2: 场景编辑器 ═══
            with gr.TabItem("🎯 3D 物理场景编辑器"):
                scene_mode=gr.Radio(label="编辑模式",choices=["交互式滑块","JSON直填"],value="交互式滑块")
                with gr.Column(visible=True) as slider_panel:
                    gr.Markdown("### 物体 1 — 操作目标")
                    with gr.Row():
                        o1n=gr.Textbox(label="名称",value="红色药瓶",scale=2);o1c=gr.Textbox(label="颜色",value="red",scale=1);o1m=gr.Textbox(label="材质",value="plastic",scale=1)
                    with gr.Row():o1x=gr.Slider(-0.5,0.5,0.15,0.01,label="X(m)");o1y=gr.Slider(-0.5,0.5,0.05,0.01,label="Y(m)");o1z=gr.Slider(0.0,0.3,0.03,0.01,label="Z(m)")
                    with gr.Row():o1w=gr.Slider(0.01,0.2,0.03,0.01,label="宽(m)");o1h=gr.Slider(0.01,0.3,0.08,0.01,label="高(m)");o1d=gr.Slider(0.01,0.2,0.03,0.01,label="深(m)")
                    with gr.Row():o1_g=gr.Checkbox(label="✋可抓取",value=True);o1_f=gr.Checkbox(label="💔易碎",value=True);o1_mv=gr.Checkbox(label="↔️可移动",value=True);o1_ct=gr.Checkbox(label="📦容器",value=False)
                    gr.Markdown("---");gr.Markdown("### 物体 2 — 障碍物")
                    with gr.Row():
                        o2n=gr.Textbox(label="名称",value="玻璃水杯",scale=2);o2c=gr.Textbox(label="颜色",value="transparent",scale=1);o2m=gr.Textbox(label="材质",value="glass",scale=1)
                    with gr.Row():o2x=gr.Slider(-0.5,0.5,0.08,0.01,label="X(m)");o2y=gr.Slider(-0.5,0.5,0.03,0.01,label="Y(m)");o2z=gr.Slider(0.0,0.3,0.06,0.01,label="Z(m)")
                    with gr.Row():o2w=gr.Slider(0.01,0.2,0.07,0.01,label="宽(m)");o2h=gr.Slider(0.01,0.3,0.12,0.01,label="高(m)");o2d=gr.Slider(0.01,0.2,0.07,0.01,label="深(m)")
                    with gr.Row():o2_g=gr.Checkbox(label="✋可抓取",value=True);o2_f=gr.Checkbox(label="💔易碎",value=False);o2_mv=gr.Checkbox(label="↔️可移动",value=True);o2_ct=gr.Checkbox(label="📦容器",value=True)
                with gr.Column(visible=False) as json_panel:
                    scene_json=gr.Code(label="Raw Perception JSON（粘贴任意复杂场景）",language="json",value=SCENE_TEMPLATE,lines=14)
                scene_mode.change(fn=lambda m:(gr.update(visible=m=="交互式滑块"),gr.update(visible=m=="JSON直填")),inputs=[scene_mode],outputs=[slider_panel,json_panel])

            # ═══ TAB 3: 记忆编辑器 ═══
            with gr.TabItem("🧠 记忆与约束底线编辑器"):
                with gr.Row():
                    profile=gr.Dropdown(label="👤 记忆画像",choices=list(PROFILES.keys()),value="标准默认",scale=1)
                with gr.Row():
                    force_n=gr.Slider(0.1,50.0,5.0,0.1,label="最高抓取力红线 max_force_n (N)")
                    vel_ms=gr.Slider(0.01,1.0,0.15,0.01,label="最高线性速度红线 max_velocity_ms (m/s)")
                profile.change(fn=on_profile_change,inputs=[profile],outputs=[force_n,vel_ms])

        # ═══ 运行按钮 ═══
        with gr.Row():btn=gr.Button("🚀 运行管线",variant="primary",size="lg");status=gr.Markdown("")
        gr.Markdown("---")

        # ═══ 输出面板 ═══
        gr.Markdown("## 📊 输出控制台")

        # 视图 A: 决策轨迹
        gr.Markdown("### 🧠 6 步决策推理轨迹 (Decision Trace DAG)")
        trace_out=gr.HTML()

        # 视图 B: 行为树 + 场景联动
        gr.Markdown("### 🌳 行为树与空间干涉联动")
        bt_out=gr.HTML()

        # 视图 C: 证据链
        gr.Markdown("### 🔗 参数溯源与证据链 (Evidence Chain)")
        ev_out=gr.HTML()

        # 视图 D: IR JSON (dark)
        with gr.Accordion("📦 Universal Task IR v2.0 终态预览 (VSCode Dark)",open=False):
            ir_json_out=gr.Code(language="json",lines=24,elem_id="ir_json_box")
        ir_dl=gr.Textbox(visible=False)
        # 摘要
        summary_out=gr.HTML()

        all_in=[instr,engine,api_key,profile,force_n,vel_ms,scene_mode,scene_json,
                o1n,o1x,o1y,o1z,o1w,o1h,o1d,o1c,o1m,o1_g,o1_f,o1_mv,o1_ct,
                o2n,o2x,o2y,o2z,o2w,o2h,o2d,o2c,o2m,o2_g,o2_f,o2_mv,o2_ct]
        all_out=[status,trace_out,bt_out,ev_out,ir_json_out,ir_dl,summary_out]
        btn.click(fn=run_pipeline,inputs=all_in,outputs=all_out)
        demo.load(fn=run_pipeline,inputs=all_in,outputs=all_out)
    return demo

if __name__=="__main__":
    build_ui().launch(server_name="0.0.0.0",server_port=7860,share=False)
