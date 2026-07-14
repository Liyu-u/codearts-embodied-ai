# 🤖 codearts-embodied-ai

## 《言出必行：基于 CodeArts 代码智能体的具身智能指令生成系统》

在工业制造、仓储物流等具身智能场景中，机器人完成抓取、搬运等任务依赖工程师手工编写控制指令，需求转化效率低、调试成本高，且大模型应用多停留在"生成代码"的单点工具层面，缺乏全流程闭环。

本系统以**"言出必行"**为目标：操作者用一句自然语言下达任务，系统即自动理解需求、生成驱动机器人的可执行指令，并在仿真中真实完成动作——**说得出，就做得到**。系统基于华为云码道 (CodeArts) 代码智能体全流程开发，贯通"需求理解 → 多智能体协同生成指令 → 仿真执行 → 反馈纠错"闭环，以执行过程为可信判据，确保指令必能执行、执行必准，为具身智能工程化落地提供可行方案。

---

## 🏗️ 项目结构

```
huaweijiebangguashuai/
├── docs/                               # [文档与设计契约]
│   ├── intent_schema_v1.json           # 结构化需求 JSON 模版规范 (T001-T010)
│   ├── robot_meta_api_whitepaper.md    # 机器人元 API 白皮书与函数说明书
│   └── weekly_reports/                 # 各周进度汇报与文献调研备份
│
├── prompts/                            # [提示词工程库]
│   ├── intent_parser_prompt.md         # 口语转规范 JSON 的 System Prompt
│   ├── codearts_system_prompt.md       # CodeArts 策略生成 System Prompt (含CaP)
│   └── reflexion_prompt.md             # 反思自愈闭环提示词模板
│
├── src/                                # [核心源代码]
│   ├── ui/                             # 前端页面模块 (同学 A)
│   │   ├── app.py                      # Gradio / Streamlit 交互主程序
│   │   └── components.py               # 页面可视化组件
│   ├── agent/                          # 大模型中枢与策略编译 (同学 B)
│   │   ├── codearts_client.py          # 华为云 CodeArts API 调用封装
│   │   └── code_validator.py           # 代码安全校验与沙盒拦截器
│   ├── isaac/                          # 物理仿真与元 API 底层 (同学 C)
│   │   ├── exec_wrapper.py             # 机械臂底层执行包装器
│   │   ├── get_scene_json.py           # 场景感知脚本
│   │   └── scenes/                     # Isaac Sim 场景存档文件
│   ├── backend/                        # 中央数据中转与路由 (同学 D)
│   │   ├── server.py                   # FastAPI 中转服务器主入口
│   │   └── schemas.py                  # Pydantic 数据类型验证
│   └── monitor/                        # 运行态探针与监控 (同学 D)
│       ├── trace_probe.py              # 异常捕获旁路探针
│       └── error_handler.py            # 报告生成器 (error_report.json)
│
├── mock/                               # [假数据测试库]
│   ├── mock_nl_input.json              # 模拟用户自然语言指令
│   ├── mock_scene_state.json           # 模拟 Isaac Sim 场景坐标
│   ├── mock_intent_output.json         # 模拟解析出的标准需求 JSON
│   ├── mock_generated_policy.py        # 模拟生成的 Python 策略脚本
│   └── mock_error_report.json          # 模拟碰撞诊断日志
│
├── tests/                              # [自动化测试用例]
│   ├── test_intent_parser.py           # 测试大模型解析 JSON 的准确率
│   ├── test_code_validator.py          # 测试沙盒安全拦截能力
│   └── test_meta_api_ik.py             # 测试底层逆运动学求解
│
├── .gitignore                          # Git 忽略配置
├── .env.example                        # 环境变量模版
├── requirements.txt                    # Python 依赖包清单
├── environment.yml                     # Conda 环境配置清单
└── README.md                           # 仓库门面
```

---

## 🚀 快速启动

### 环境准备

```bash
# 克隆仓库
git clone https://github.com/Liyu-u/codearts-embodied-ai.git
cd codearts-embodied-ai/huaweijiebangguashuai

# 方式一：使用 pip
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 方式二：使用 Conda
conda env create -f environment.yml
conda activate embodied-ai
```

### 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入你的 API Keys
```

### 启动服务

```bash
# 启动后端 API 服务器
cd src/backend
python server.py

# 启动前端界面 (新终端)
cd src/ui
python app.py
```

### 运行测试

```bash
pytest tests/ -v
```

---

## 🔄 系统闭环流程

```
用户自然语言 → [意图解析器] → 规范 JSON
                              ↓
                    [CodeArts 策略生成] → 控制代码
                              ↓
                      [代码安全校验] → 通过/拦截
                              ↓
                  [Isaac Sim 物理执行] → 成功/失败
                              ↓ (失败时)
                    [探针监控 + 反思修复] → 重试
```

---

## 👥 团队分工

| 角色 | 模块 | 职责 |
|------|------|------|
| 同学 A | `src/ui/` + `prompts/` + `docs/` | 前端交互 + 意图解析 Schema + 提示词 |
| 同学 B | `src/agent/` + `prompts/` | CodeArts 策略生成 + 代码安全校验 |
| 同学 C (昌庆) | `src/isaac/` + `docs/` | Isaac Sim 元 API + 场景感知 + 物理执行 |
| 同学 D | `src/backend/` + `src/monitor/` | 数据中转路由 + 异常探针 + 反思闭环 |

---

## 📹 展示视频

[待添加]

---

## 📄 许可证

[待定]
