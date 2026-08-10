# 项目架构

> 最后更新：2026-07-17
> 同学 C（吴昌庆）维护

## 系统概览

华为揭榜挂帅 — 具身智能机械臂操作系统。用户说一句话 → LLM 解析意图 → CodeArts 生成策略代码 → Isaac Sim 执行 Franka Panda 物理仿真 → 探针监控闭环。

## 技术栈

| 层级 | 技术 | 用途 |
|---|---|---|
| 前端 UI | Gradio | 任务输入/JSON 可视化 |
| 意图解析 (A) | LLM + intent_parser_prompt | 口语→结构化 JSON |
| 策略生成 (B) | CodeArts LLM + codearts_system_prompt | JSON→Python 策略代码 |
| **物理仿真 (C)** | **Isaac Sim 6.0.1 + Franka Panda** | **机械臂物理执行+场景感知** |
| 代码校验 (B→C) | AST + 正则黑白名单 | 拦截危险代码 |
| 中转服务 (D) | FastAPI | 全链路 HTTP 调度 |
| 探针监控 (D) | TraceProbe 旁路监听 | 异常捕获+反思闭环 |

## 目录结构

```
src/
  agent/               # 同学 B: CodeArts 策略生成
    code_validator.py    # 代码安全校验器（三层防护）
  isaac/               # 同学 C (吴昌庆): Isaac Sim 仿真
    exec_wrapper.py      # Franka Panda 执行包装器 (双模式: Kit/Mock)
    get_scene_json.py    # 场景感知 (双模式)
    code_loader.py       # 策略代码动态加载器
    run_simulation.py    # Isaac Sim 仿真入口脚本
    scripts/             # 测试/调试脚本
  backend/             # 同学 D: 中转服务
    server.py            # FastAPI 服务器 v0.2.0
  monitor/             # 同学 D: 运行态监控
    trace_probe.py       # 运行时探针
  ui/                  # 同学 A: 前端界面
    app.py
prompts/               # LLM 提示词
docs/                  # 文档
  API_MANUAL.md          # 元 API 使用手册 v1.0
  architecture_design.md # 系统架构设计
  robot_meta_api_whitepaper.md  # 元 API 白皮书
  intent_schema_v1.json # 意图 Schema
  sprint1_plan.md       # Sprint 1 计划
tests/                 # 单元测试 (55 cases)
```

## 核心模块关系

```
UI (Gradio) ──→ server.py (FastAPI) ──→ code_validator.py ──→ code_loader.py
                    │        │                                      │
                    │        └── get_scene_json.py ←── Isaac Sim USD Stage
                    │                                               │
                    └── trace_probe.py ←── exec_wrapper.py ←──┘
                         (旁路监听)       (机械臂执行)
```

## Isaac Sim 双模式架构

| 模式 | 触发条件 | 用途 |
|---|---|---|
| **Kit 模式** | `isaacsim.exe --exec` 内运行 | 真实物理仿真, IK 求解, GPU 渲染 |
| **Mock 模式** | 普通 Python 运行 | 单元测试, CI/CD, 策略代码调试 |

模式自动检测：`_KIT_MODE` 标志位（尝试 import `isaacsim.core.api` 的结果）。
