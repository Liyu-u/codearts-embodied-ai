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
├── docs/             # 📑 [文档] JSON规范、API说明、项目进度
│   ├── intent_schema_v1.json         # [A]结构化需求 JSON 模板规范 (T001-T010)
│   ├── robot_meta_api_whitepaper.md   # [C/B] 机器人元 API 说明书
│   ├── sprint1_plan.md               # [组长] 本周规划与阶段目标纪要
│   └── weekly_reports/               # 各周进度汇报与文献调研备份
│
├── prompts/          # 💬 [提示词库] CodeArts与意图解析的System Prompt与Few-shot
│   ├── intent_parser_prompt.md       # [A] 口语→JSON 解析 (内置转换样例)
│   └── codearts_system_prompt.md     # [B]策略生成 (内置代码样例)
│
├── src/              # 💻 [核心源代码] 按模块解耦的工程代码
│   ├── ui/           # 🎨 前端交互界面 (A)
│   │   └── app.py                    # Gradio 应用 (输入框+预设下拉+JSON渲染器)
│   ├── agent/        # 🧠 大模型调用与安全校验 (B)
│   │   └── code_validator.py         # 代码沙盒校验器 (黑/白名单 + 安全断言检查)
│   ├── isaac/        # 🤖 Isaac Sim 物理仿真与元API (C)
│   │   ├── exec_wrapper.py           # 【核心】元 API 底层驱动 + IK + 防撞断言
│   │   ├── get_scene_json.py         # 场景感知脚本 (物体 3D 坐标 + BBox)
│   │   └── scenes/                   # Isaac Sim 场景存档文件 (.usd)
│   ├── backend/      # 🔗 后端中央中转转发服务 (D)
│   │   └── server.py                 # FastAPI 中转服务器 (HTTP + Socket)
│   └── monitor/      # 🚨 运行态探针与异常监听 (D)
│       └── trace_probe.py            # 【闭环亮点】探针 + error_report 生成器
│
├── tests/            # 🧪 [测试脚本] 各角色的独立 Mock 测试用例
│   ├── test_ui_json_display.py       # [A] 前端 JSON 渲染测试
│   ├── test_codearts_generation.py   # [B] 策略生成 + 安全校验测试
│   ├── test_isaac_api_motion.py      # [C] 机械臂运动 + 安全断言测试
│   └── test_probe_interception.py    # [D] 探针异常拦截 + 报告生成测试
│
├── logs/             # 📊 [运行日志] 自动生成的感知JSON与报错JSON ("黑匣子")
│   ├── scene_state.json              # (自动生成) 实体坐标快照
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

## 👥 团队分工与职责

| 角色 | 姓名 | 职责定位 | 模块 | 本周核心产出 |
|------|------|----------|------|-------------|
| **A** | 王翊航 / 郭家腾 | 意图解析与交互 | `src/ui/` + `prompts/` + `docs/` | `app.py`, `intent_parser_prompt.md`, `intent_schema_v1.json` |
| **B** | 冯海 | CodeArts 智能体与策略代码 | `src/agent/` + `prompts/` | `code_validator.py`, `codearts_system_prompt.md` |
| **C** | 吴昌庆 | 物理仿真与环境感知 | `src/isaac/` + `docs/` | `exec_wrapper.py`, `get_scene_json.py`, `robot_meta_api_whitepaper.md` |
| **D** | 王翊航 / 郭家腾 | 闭环纠错 | `src/backend/` + `src/monitor/` | `server.py`, `trace_probe.py` |

---

## 📋 第一周阶段性成果

1. 建立了最前沿的 **Isaac Sim 6.0.1 + Franka Panda 7-DOF** GPU 物理仿真环境；高质量完成了 `T001-T010` 共 10 个典型任务的结构化 JSON Schema 契约初稿。
2. 深度学习了《Code as Policies》《SayCan》《TraceCoder》《Voyager》等 8 篇 CCF A/B 类顶会方法论，将"排序、条件筛选、形状匹配"等复杂逻辑融入赛题要求，确立了系统的三阶段处理架构。

---

## 🎯 第二周核心目标

实现**自然语言 → Isaac Sim 仿真机器人的闭环控制**。

### 里程碑

| 里程碑 | 目标 | 说明 |
|--------|------|------|
| **M1** | 接口契约定稿 | 确定《需求 JSON 规范》、《机器人元 API 说明书》、《诊断日志 JSON 规范》的格式和字段接口 |
| **M2** | 四模块独立跑通 | 四个模块各自独立通过 Mock 测试 |
| **M3** | MVP 单链路贯通 | 在交互界面输入自然语言 → Isaac Sim 中 Franka Panda 成功完成物理动作 |

### 各角色重点目标

- **A (王翊航/郭家腾)**：打通口语转 JSON，搭建可视化交互网页
- **B (冯海)**：调教 CodeArts，把 JSON 翻译为带几何计算的 Python 控制脚本
- **C (吴昌庆)**：维护 Isaac Sim，输出感知坐标，把底层运动封装成元 API
- **D (王翊航/郭家腾)**：打通前后端数据链，植入底层监控探针

> 📖 详细开发说明见 [开发说明.md](开发说明.md)

---

## 📄 许可证

[待定]
