"""
前端交互界面 — Gradio Web 应用
同学 A 上传：指令输入框 + 任务预设下拉菜单 + JSON 高亮渲染区
"""

import json
import gradio as gr

# ============================================================
# 任务预设下拉菜单配置
# ============================================================
TASK_PRESETS = {
    "🖐️ 简单抓取 — 抓红色方块放蓝杯子旁": "帮我把那个红色的方块拿过来放到蓝杯子旁边",
    "🎨 颜色分类 — 所有积木按颜色分三堆": "把桌面上所有积木按颜色分成三堆，红色左边、蓝色中间、绿色右边",
    "🧊 尺寸排序 — 方块从小到大排列": "把桌上所有方块按大小从小到大在桌面右边排成一排",
    "🔍 条件筛选 — 只拿红色的东西": "把桌面上所有红色的东西挑出来放到左边角落",
    "🥚 精细操作 — 轻拿易碎品": "小心地把最上面那个易碎品拿下来，轻拿轻放平放到桌面中间",
    "📦 堆叠 — 把红方块摞到蓝方块上面": "把红色方块摞到蓝色方块上面",
    "🚪 推物 — 把绿色圆柱推到远处": "帮我把绿色圆柱推到桌子右边去",
}


def render_json_highlight(intent_json_str: str) -> str:
    """将 JSON 渲染为带语法高亮的 HTML 卡片"""
    try:
        data = json.loads(intent_json_str) if isinstance(intent_json_str, str) else intent_json_str
    except (json.JSONDecodeError, TypeError):
        return "<pre style='color:#888;'>等待解析结果...</pre>"

    action = data.get("action", "N/A")
    action_colors = {
        "pick_and_place": "#4CAF50",
        "push": "#FF9800",
        "stack": "#9C27B0",
        "sort_by_color": "#2196F3",
        "sort_by_size": "#00BCD4",
        "filter_by_attribute": "#E91E63",
    }
    color = action_colors.get(action, "#757575")

    json_str = json.dumps(data, indent=2, ensure_ascii=False)

    return f"""
    <div style="border-left: 4px solid {color}; border-radius: 8px;
                padding: 16px; background: #1e1e1e; font-family: monospace; font-size: 13px;
                max-height: 400px; overflow-y: auto;">
        <h3 style="color: {color}; margin-top: 0;">🎯 动作: {action}</h3>
        <p style="color: #ccc;"><b>原始指令:</b> {data.get('raw_text', 'N/A')}</p>
        <pre style="color: #9cdcfe; white-space: pre-wrap;">{json_str}</pre>
    </div>
    """


def process_command(nl_input: str, preset_choice: str) -> tuple:
    """
    核心处理函数 (当前返回模拟数据，后续接入后端 API)
    """
    text = nl_input.strip() or preset_choice
    if not text:
        return None, "<pre style='color:#888;'>请输入指令或选择预设任务...</pre>"

    # TODO: 对接 backend server.py 的全链路流水线
    mock_result = {
        "intent_id": "task-mock",
        "raw_text": text,
        "action": "pick_and_place",
        "target_object": "红色方块",
        "destination": {"x": 0.2000, "y": 0.0000, "z": 0.0300},
        "confidence": 0.95,
    }

    status = {
        "status": "✅ 解析成功",
        "action": mock_result["action"],
        "confidence": mock_result["confidence"],
        "target": mock_result["target_object"],
    }

    return json.dumps(status, ensure_ascii=False), render_json_highlight(mock_result)


# ============================================================
# Gradio 界面构建
# ============================================================
with gr.Blocks(
    title="具身智能机械臂操作系统",
    theme=gr.themes.Soft(),
    css="""
        .preset-dropdown { margin-bottom: 12px; }
        .json-panel { min-height: 200px; }
    """,
) as demo:
    gr.Markdown(
        """
        # 🤖 言出必行：具身智能机械臂操作系统
        ### 华为揭榜挂帅 · 基于 CodeArts 代码智能体的具身指令生成系统
        ---
        """
    )

    with gr.Row():
        # --- 左栏: 输入区 ---
        with gr.Column(scale=1):
            gr.Markdown("### 📝 指令输入")
            nl_input = gr.Textbox(
                label="自然语言指令",
                placeholder="例如：帮我把红色方块放到蓝色杯子旁边",
                lines=3,
            )
            preset_dropdown = gr.Dropdown(
                label="📋 任务预设 (快速选择)",
                choices=list(TASK_PRESETS.keys()),
                value=None,
                interactive=True,
                elem_classes=["preset-dropdown"],
            )
            submit_btn = gr.Button("🚀 解析执行", variant="primary", size="lg")

        # --- 右栏: 结果区 ---
        with gr.Column(scale=1):
            gr.Markdown("### 📊 解析状态")
            status_output = gr.JSON(label="任务状态卡片")
            gr.Markdown("### 🎨 规范化 JSON")
            json_display = gr.HTML(label="意图 JSON 渲染器", elem_classes=["json-panel"])

    # 事件绑定
    submit_btn.click(
        fn=process_command,
        inputs=[nl_input, preset_dropdown],
        outputs=[status_output, json_display],
    )

    # 预设选择时自动填入输入框
    def fill_preset(choice):
        return TASK_PRESETS.get(choice, "") if choice else ""

    preset_dropdown.change(fn=fill_preset, inputs=[preset_dropdown], outputs=[nl_input])

    gr.Markdown("---\n*📌 Sprint 1 MVP — 前端演示版*")


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
