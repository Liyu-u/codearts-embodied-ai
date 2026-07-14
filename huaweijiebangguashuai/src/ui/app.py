"""
Gradio / Streamlit 交互主程序
同学 A：前端页面入口，提供自然语言输入框与可视化反馈
"""

import gradio as gr


def build_interface():
    """构建 Gradio Web 交互界面"""

    with gr.Blocks(title="具身智能机械臂操作系统") as demo:
        gr.Markdown("# 🤖 具身智能机械臂操作系统")
        gr.Markdown("输入自然语言指令，让机械臂为你完成任务！")

        with gr.Row():
            with gr.Column(scale=2):
                nl_input = gr.Textbox(
                    label="📝 自然语言指令",
                    placeholder="例如：帮我把红色方块放到蓝色杯子旁边",
                    lines=3,
                )
                submit_btn = gr.Button("🚀 执行任务", variant="primary")

            with gr.Column(scale=1):
                status_output = gr.JSON(label="📊 任务状态")
                scene_output = gr.Image(label="📷 场景画面")

        submit_btn.click(
            fn=lambda x: {"status": "parsing", "raw": x},
            inputs=[nl_input],
            outputs=[status_output],
        )

    return demo


if __name__ == "__main__":
    demo = build_interface()
    demo.launch(server_name="0.0.0.0", server_port=7860)
