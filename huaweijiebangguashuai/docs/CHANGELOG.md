# 变更日志

> 基于 Git 提交历史维护
> 格式基于 [Keep a Changelog](https://keepachangelog.com/)

---

## [Unreleased]

### Added
- [2026-07-17] `src/isaac/code_loader.py` — 策略代码动态加载器
- [2026-07-17] `src/isaac/run_simulation.py` — Isaac Sim 仿真入口脚本 (3 个示例任务)
- [2026-07-17] `demo_api_usage.py` — 元 API 使用演示脚本
- [2026-07-17] `docs/API_MANUAL.md` — 元 API 使用手册 v1.0
- [2026-07-17] `docs/ARCHITECTURE.md`, `PROGRESS.md`, `DECISIONS.md`, `ISSUES.md`, `CHANGELOG.md`, `LOG.md` — 项目记忆系统
- [2026-07-14] 项目仓库结构初始化

### Changed
- [2026-07-17] `src/isaac/exec_wrapper.py` — 重写为双模式架构 (Kit/Mock), 集成 Isaac Sim 6.0.1 真实 API
- [2026-07-17] `src/isaac/get_scene_json.py` — 重写为双模式, 真实 USD Stage 遍历 + 语义标签
- [2026-07-17] `src/agent/code_validator.py` — GBK 编码兼容修复 + 对象方法调用白名单
- [2026-07-17] `src/backend/server.py` — 接入真实 code_loader, 新增 `/api/code/execute` 端点 (v0.2.0)
- [2026-07-17] `src/monitor/trace_probe.py` — 修复 error_report 缺少 status 字段

### Fixed
- [2026-07-17] GBK 编码错误: emoji → ASCII
- [2026-07-17] code_validator 白名单误报: 对象方法调用
- [2026-07-17] trace_probe KeyError: status 字段缺失
