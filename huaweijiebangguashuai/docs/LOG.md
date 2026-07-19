# 会话日志

> 每次会话结束前追加一条记录

---

## 2026-07-20 会话

**做了什么**：
- SceneObject 数据结构升级为 perception_observation v1.0.0（四元数朝向、置信度候选、追踪、真值元数据）
- 新增 `src/isaac/action_library.py` — 12 个常见动作封装（pick_up, place_at, pick_and_place, stack, push, sort_by_color 等）
- 新增 `src/isaac/scene_builder.py` — 三标准测试场景构建器（Kit/Mock 双模式）
- 创建 3 个标准 .usda 场景文件（stacking_cubes 3物体, cup_lineup 5杯子, color_sorting 6方块）
- run_simulation.py 重构 — 接入 scene_builder，新增 4 个场景匹配的示例任务
- code_loader.py 注入动作库到策略执行命名空间
- server.py `/api/scene/current` 返回完整 perception_observation 格式
- 创建 4 个 JSON 样例文件到 docs/samples/
- 修复 exec_wrapper Mock 模式夹爪状态跟踪
- API_MANUAL.md 完整重写队友 B 章节
- 55/55 单元测试 + 11 烟雾测试全部通过
- Git 提交: `5bd52b3` feat: perception_observation v1.0.0 + 12 动作封装库

**下次继续**：
- Isaac Sim headless 端到端测试
- 与队友 A/B/D 联调
- 全链路 MVP 贯通

---

## 2026-07-17 会话

**做了什么**：
- Isaac Sim 6.0.1 环境验证：确认 API 路径 (`isaacsim.core.*`), GPU 识别, headless 模式
- 重写 `src/isaac/exec_wrapper.py`：双模式架构 (Kit/Mock), 集成真实 Isaac Sim API
- 重写 `src/isaac/get_scene_json.py`：双模式, 真实 USD Stage 遍历 + 语义标签
- 新增 `src/isaac/code_loader.py`：策略代码安全执行器 (校验→注入→exec→task_main)
- 新增 `src/isaac/run_simulation.py`：仿真入口脚本 (3 示例任务 + CLI + 场景搭建)
- 修复 `src/agent/code_validator.py`：GBK 编码兼容 + 对象方法调用白名单
- 修复 `src/monitor/trace_probe.py`：error_report 缺少 status 字段
- 升级 `src/backend/server.py` v0.2.0：接入真实 code_loader, 新增 `/api/code/execute`
- 创建 `demo_api_usage.py`：4 项演示 (场景感知/运动控制/安全断言/策略执行)
- 创建 `docs/API_MANUAL.md`：元 API 使用手册 v1.0
- 初始化项目记忆系统：ARCHITECTURE.md, PROGRESS.md, DECISIONS.md, ISSUES.md, CHANGELOG.md, LOG.md
- 55/55 单元测试全部通过
- Git 提交: `759eac3` feat: Isaac Sim 6.0.1 真实 API 集成 + 双模式架构 (Mock/Kit)

**下次继续**：
- Isaac Sim headless 端到端测试 (冷启动 ~10min)
- 三标准场景 .usd 文件创建
- 与队友 B 联调 CodeArts 策略代码
- 与队友 D 联调探针挂载和闭环反馈
