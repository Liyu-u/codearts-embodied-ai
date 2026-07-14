# 🤖 言出必行：基于 CodeArts 代码智能体的具身智能指令生成系统

> **华为揭榜挂帅 · 具身智能机械臂操作系统**
>
> 说得出，就做得到 —— 操作者说一句话，机械臂在仿真中真实完成动作。

---

在工业制造、仓储物流等具身智能场景中，机器人完成抓取、搬运等任务依赖工程师手工编写控制指令，需求转化效率低、调试成本高，且大模型应用多停留在"生成代码"的单点工具层面，缺乏全流程闭环。

本系统以**"言出必行"**为目标：操作者用一句自然语言下达任务，系统即自动理解需求、生成驱动机器人的可执行指令，并在仿真中真实完成动作——**说得出，就做得到**。系统基于华为云码道 (CodeArts) 代码智能体全流程开发，贯通 **"需求理解 → 多智能体协同生成指令 → 仿真执行 → 反馈纠错"** 闭环，以执行过程为可信判据，确保指令必能执行、执行必准。

---

## 🌳 仓库全局目录树

```
huaweijiebangguashuai/
├── docs/             # 📑 [文档与契约] Schema规范、API白皮书、项目进度
│   ├── intent_schema_v1.json         # [同学 A] 结构化需求 JSON 模板规范 (T001-T010)
│   ├── robot_meta_api_whitepaper.md   # [同学 C/B] 机器人元 API 白皮书 (6-8 核心动词)
│   ├── sprint1_plan.md               # [组长] 本周冲刺规划与阶段目标纪要
│   └── weekly_reports/               # 各周进度汇报与文献调研备份
│
├── prompts/          # 💬 [提示词库] CodeArts与意图解析的System Prompt与Few-shot
│   ├── intent_parser_prompt.md       # [同学 A] 口语→JSON 解析 (内置 3 个转换样例)
│   └── codearts_system_prompt.md     # [同学 B] CaP 策略生成 (内置 4 个代码样例)
│
├── src/              # 💻 [核心源代码] 按模块解耦的工程代码
│   ├── ui/           # 🎨 前端交互界面 (同学 A)
│   │   └── app.py                    # Gradio 应用 (输入框+预设下拉+JSON渲染器)
│   ├── agent/        # 🧠 大模型调用与安全校验 (同学 B)
│   │   └── code_validator.py         # 代码沙盒校验器 (黑/白名单 + 安全断言检查)
│   ├── isaac/        # 🤖 Isaac Sim 物理仿真与元API (同学 C/吴昌庆)
│   │   ├── exec_wrapper.py           # 【核心】元 API 底层驱动 + IK + 防撞断言
│   │   ├── get_scene_json.py         # 场景感知脚本 (物体 3D 坐标 + BBox)
│   │   └── scenes/                   # Isaac Sim 场景存档文件 (.usd)
│   ├── backend/      # 🔗 后端中央中转转发服务 (同学 D)
│   │   └── server.py                 # FastAPI 中转服务器 (HTTP + Socket)
│   └── monitor/      # 🚨 运行态探针与异常监听 (同学 D)
│       └── trace_probe.py            # 【闭环亮点】探针 + error_report 生成器
│
├── tests/            # 🧪 [测试脚本] 各角色的独立 Mock 测试用例
│   ├── test_ui_json_display.py       # [同学 A] 前端 JSON 渲染测试
│   ├── test_codearts_generation.py   # [同学 B] 策略生成 + 安全校验测试
│   ├── test_isaac_api_motion.py      # [同学 C] 机械臂运动 + 安全断言测试
│   └── test_probe_interception.py    # [同学 D] 探针异常拦截 + 报告生成测试
│
├── logs/             # 📊 [运行日志] 自动生成的感知JSON与报错JSON ("黑匣子")
│   ├── scene_state.json              # (自动生成) 战场实体坐标快照
│   └── error_report_*.json           # (自动生成) 异常现场报告 → 触发反思闭环
│
├── .gitignore
├── .env.example
├── requirements.txt
├── environment.yml
└── README.md         # 📖 项目全局说明文档
```

---

## 🔄 系统闭环流程

```
用户自然语言
    ↓
[意图解析器]  (prompts/intent_parser_prompt.md + LLM)
    ↓  规范 JSON
[CodeArts 策略生成]  (prompts/codearts_system_prompt.md + CaP)
    ↓  控制代码
[代码安全校验]  (src/agent/code_validator.py)
    ↓  通过
[Isaac Sim 物理执行]  (src/isaac/exec_wrapper.py + Franka Panda)
    ↓
    ├─ 成功 → ✅ 返回结果给前端
    │
    └─ 失败 → 🚨 探针截获 (src/monitor/trace_probe.py)
                ↓  error_report.json → logs/
                ↓  触发 Reflexion 反思闭环
                ↓  重新生成修正策略 → 重试 (最多 3 次)
```

---

## 🚀 快速启动

```bash
# 1. 克隆
git clone https://github.com/Liyu-u/codearts-embodied-ai.git
cd codearts-embodied-ai/huaweijiebangguashuai

# 2. 安装依赖
pip install -r requirements.txt
# 或: conda env create -f environment.yml

# 3. 配置
cp .env.example .env
# 编辑 .env 填入 API Keys

# 4. 启动后端
cd src/backend && python server.py &

# 5. 启动前端
cd src/ui && python app.py
# 打开 http://localhost:7860
```

---

## 👥 团队分工

| 角色 | 模块 | 本周必传物 |
|------|------|-----------|
| **同学 A** | `src/ui/` + `prompts/` + `docs/` | `app.py`, `intent_parser_prompt.md`, `intent_schema_v1.json` |
| **同学 B** | `src/agent/` + `prompts/` | `code_validator.py`, `codearts_system_prompt.md` |
| **同学 C (吴昌庆)** | `src/isaac/` + `docs/` | `exec_wrapper.py`, `get_scene_json.py`, `robot_meta_api_whitepaper.md` |
| **同学 D** | `src/backend/` + `src/monitor/` | `server.py`, `trace_probe.py` |
| **组长** | `docs/sprint1_plan.md` + 协调 | 冲刺规划 |

---

## 📄 许可证

[待定]
