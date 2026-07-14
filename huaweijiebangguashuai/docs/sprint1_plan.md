# 📋 Sprint 1 冲刺规划与阶段目标纪要

> **上传人**: 组长 | **日期**: 2026-07-14 | **周期**: 第 1 周

---

## 🎯 Sprint 目标

完成系统 MVP 闭环搭建：**"用户说一句话 → 机械臂在仿真中完成动作"** 的端到端流程贯通。

---

## 📌 本周阶段里程碑 (Phase Gates)

| 阶段 | 目标 | 完成标准 | 负责人 | 截止日 |
|---|---|---|---|---|
| **P0: 规范定稿** | 确定数据契约 | `intent_schema_v1.json` + `robot_meta_api_whitepaper.md` 经全组评审通过 | A + C/B | Day 2 |
| **P1: 意图解析** | 口语→JSON | LLM 能把 5 条口语指令正确转为 Schema JSON（准确率 >90%） | A | Day 3 |
| **P2: 策略生成** | JSON→可执行代码 | CodeArts 能生成含元 API 调用的 Python 策略（4 个标准样例全通过） | B | Day 4 |
| **P3: 仿真执行** | 代码→物理动作 | Isaac Sim 中 Panda 机械臂真实走位抓取成功 | C | Day 5 |
| **P4: 闭环监控** | 执行→反馈 | 探针成功捕获 1 次模拟异常并生成 `error_report.json` | D | Day 6 |
| **P5: 系统联调** | 全链路贯通 | 前端输入→后端路由→策略生成→仿真执行→结果返回 | 全员 | Day 7 |

---

## 👥 本周分工

| 角色 | 姓名 | 负责模块 | 本周必传物 |
|---|---|---|---|
| 同学 A | — | `src/ui/` + `prompts/intent_parser_prompt.md` + `docs/intent_schema_v1.json` | `app.py` + prompt + schema |
| 同学 B | — | `src/agent/` + `prompts/codearts_system_prompt.md` | `code_validator.py` + CaP prompt |
| 同学 C | 吴昌庆 | `src/isaac/` + `docs/robot_meta_api_whitepaper.md` | `exec_wrapper.py` + `get_scene_json.py` + 白皮书 |
| 同学 D | — | `src/backend/` + `src/monitor/` | `server.py` + `trace_probe.py` |
| 组长 | — | `docs/sprint1_plan.md` + 总体协调 | 本文件 |

---

## ⚠️ 风险与应对

| 风险 | 概率 | 影响 | 应对策略 |
|---|---|---|---|
| CodeArts API 调用超时 | 中 | 高 | 同学 B 准备 Mock 数据降级方案 |
| Isaac Sim IK 求解失败 | 中 | 中 | 同学 C 预设 5 个安全可达位姿作为 fallback |
| LLM 输出格式不稳定 | 高 | 中 | 同学 A 的 prompt 加强约束 + 同学 B 的 validator 做兜底 |
| 各模块接口不兼容 | 低 | 高 | 同学 D 的 `schemas.py` 作为全组唯一数据契约 |

---

## 📊 每日站会议程

1. 昨天完成了什么？（每人 30 秒）
2. 今天计划做什么？（每人 30 秒）
3. 遇到什么阻塞？（需要帮助的举手）

---

## ✅ 本周产出清单 (Deliverables)

- [ ] `docs/intent_schema_v1.json` — 结构化需求 JSON 模板
- [ ] `docs/robot_meta_api_whitepaper.md` — 元 API 白皮书
- [ ] `prompts/intent_parser_prompt.md` — 意图解析提示词（含 3 个样例）
- [ ] `prompts/codearts_system_prompt.md` — CaP 策略生成提示词（含 4 个样例）
- [ ] `src/ui/app.py` — Gradio/Streamlit 前端界面
- [ ] `src/agent/code_validator.py` — 代码安全校验器
- [ ] `src/isaac/exec_wrapper.py` — 机械臂底层执行包装器
- [ ] `src/isaac/get_scene_json.py` — 场景感知脚本
- [ ] `src/backend/server.py` — FastAPI/Flask 中转服务器
- [ ] `src/monitor/trace_probe.py` — 运行态探针
- [ ] 4 个独立 Mock 测试脚本 (`tests/`)
