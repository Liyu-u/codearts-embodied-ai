# 变更日志

> 基于 Git 提交历史维护
> 格式基于 [Keep a Changelog](https://keepachangelog.com/)

---

## [Unreleased]

### Added
- [2026-07-20] `src/isaac/scene_builder.py` — 三标准测试场景构建器 (Kit/Mock 双模式)
- [2026-07-20] `src/isaac/scenes/stacking_cubes.usda` — 方块堆叠场景 (3 物体 + PhysX)
- [2026-07-20] `src/isaac/scenes/cup_lineup.usda` — 杯子排列场景 (5 物体 + PhysX)
- [2026-07-20] `src/isaac/scenes/color_sorting.usda` — 颜色分类场景 (6 物体 + PhysX + 分类区)
- [2026-07-20] `src/isaac/action_library.py` — 12 个常见动作封装 (pick_up, stack, sort_by_color 等)
- [2026-07-20] `docs/samples/` — 4 个 JSON 样例文件 (scene_state, intent, code_execute_response, error_report)
- [2026-07-17] `src/isaac/code_loader.py` — 策略代码动态加载器
- [2026-07-17] `src/isaac/run_simulation.py` — Isaac Sim 仿真入口脚本 (3 个示例任务)
- [2026-07-17] `demo_api_usage.py` — 元 API 使用演示脚本
- [2026-07-17] `docs/API_MANUAL.md` — 元 API 使用手册 v1.0
- [2026-07-17] `docs/ARCHITECTURE.md`, `PROGRESS.md`, `DECISIONS.md`, `ISSUES.md`, `CHANGELOG.md`, `LOG.md` — 项目记忆系统
- [2026-07-14] 项目仓库结构初始化

### Changed
- [2026-07-20] `src/isaac/get_scene_json.py` — SceneObject 升级为 perception_observation v1.0.0 (四元数/置信度/追踪/真值)
- [2026-07-20] `src/backend/server.py` — /api/scene/current 返回 perception_observation 格式
- [2026-07-20] `src/isaac/code_loader.py` — 注入 action_library 到策略执行命名空间
- [2026-07-20] `src/isaac/run_simulation.py` — 接入 scene_builder + 新增 4 个场景任务
- [2026-07-20] `docs/API_MANUAL.md` — 完整重写队友 B 章节 + 12 个动作库文档
- [2026-07-17] `src/isaac/exec_wrapper.py` — 重写为双模式架构 (Kit/Mock), 集成 Isaac Sim 6.0.1 真实 API
- [2026-07-17] `src/isaac/get_scene_json.py` — 重写为双模式, 真实 USD Stage 遍历 + 语义标签
- [2026-07-17] `src/agent/code_validator.py` — GBK 编码兼容修复 + 对象方法调用白名单
- [2026-07-17] `src/monitor/trace_probe.py` — 修复 error_report 缺少 status 字段

### Fixed
- [2026-07-20] exec_wrapper Mock 模式夹爪状态跟踪 (close_gripper → verify_grasp 链路)
- [2026-07-17] GBK 编码错误: emoji → ASCII
- [2026-07-17] code_validator 白名单误报: 对象方法调用
- [2026-07-17] trace_probe KeyError: status 字段缺失
