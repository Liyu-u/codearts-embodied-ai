# 会话日志

> 每次会话结束前追加一条记录

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
