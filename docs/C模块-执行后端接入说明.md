# C 模块 · 执行后端接入说明（新 main）

> 作者：C 负责人（吴昌庆）
> 日期：2026-08-19
> 分支：`feature/executor-isaac-v2`（基于 `main@ddcb20d`，已推送 GitHub）

本文说明 C 模块（`modules/executor` + `modules/perception` + `integration/config`）在
A/B/D 接入 DeepSeek 之后的 **新 main** 上是如何接入、如何被调用的，供 A/B/D 队友与队长对接。

---

## 一、一句话结论

`main` 已经前进了 13 个提交（A 意图理解、B CodeArts、D TraceCoder 接 DeepSeek + C1 语义门加固），
**但没有 Isaac Sim 执行后端**。本分支把 C 的 Isaac Sim 执行后端、物理安全守卫和
`local/sim/real` 三档环境配置重新集成到新 main，且保持了执行后端的公开接口不变。

---

## 二、C 模块现在的文件结构

| 路径 | 说明 |
|---|---|
| `modules/executor/models.py` | `ExecutorBackend` Protocol + `ExecutionLimits`（main 原有，未改） |
| `modules/executor/mock_backend.py` | Mock 后端（main 重做版，支持 `placement_mode`） |
| `modules/executor/strategy_interpreter.py` | 解释器（main 版 + 本分支补执行证据归一化） |
| `modules/executor/action_catalog.py` | 薄封装，重导出 `integration/strategy_policy.py` |
| `modules/executor/safety.py` | **新增**：纯 Python 物理安全层（工作空间/限速/力/超时/急停） |
| `modules/executor/isaac_driver.py` | **新增**：Isaac Sim 驱动（官方 Franka + 差分 IK + 夹爪） |
| `modules/executor/robot_backend.py` | **新增**：受安全守卫的机器人后端基类（fail-closed） |
| `modules/executor/isaac_backend.py` | **新增**：`IsaacSimBackend`（mode=`isaac`） |
| `modules/executor/real_backend.py` | **新增**：`RealRobotBackend`（mode=`real`，强制人工确认） |
| `integration/config/` | **新增**：`local/sim/real` 三档 TOML + `loader.py`/`models.py` |
| `integration/strategy_policy.py` | 语义门（main 已有，B/C/D 共用） |
| `integration/adapters/executor.py` | 适配器（main 版 + 本分支补 `from_profile`） |

---

## 三、后端接口（未变，队友无需改动）

三个后端共享同一接口，上层 `StrategyInterpreter` 不感知后端差异：

```text
execute(action, arguments) -> dict   # {status, reason, duration_ms, ...}
safe_stop(reason) -> dict
trajectory_points() -> list[dict]
snapshot() -> dict
mode -> "mock" | "isaac" | "real"
```

同一份 `strategy.v1` 在 Mock 与 Isaac Sim / 真机下都能执行，满足「同一指令、结果可比」。

---

## 四、三档环境配置

| profile | 后端 | 人工确认 | 限速 | 工作空间 |
|---|---|---|---|---|
| `local` | `mock` | 否 | 0.30 m/s | x/y ±0.5，z 0–0.6 |
| `sim` | `isaac` | 否 | 0.30 m/s | x/y ±0.5，z 0–0.6 |
| `real` | `real` | **是** | 0.05 m/s | x/y ±0.3，z 0.02–0.45 |

- `load_profile(name)` 读 TOML + 叠加环境变量覆盖（`RIA_*` 限速/力、`EXECUTOR_BACKEND`）。
- `build_backend(profile, perception, driver=...)` 映射为具体后端。
- `ExecutorAdapter.from_profile(profile, perception, driver=...)` 一键装配。

---

## 五、契约变化（队友要留意的点）

1. **`move_to_target` 新增 `placement_mode`**（`direct` / `stack_on`，默认 `direct`）。
   - Mock 后端两种都支持；**Isaac/真机后端目前只支持 `direct`**，传 `stack_on` 会明确失败
     （`PLACEMENT_MODE_UNSUPPORTED`），绝不静默降级。`stack_on` 已标 `TODO(executor)` 待补。
2. **`execution.v1` 新增 `provenance`**：`ExecutorAdapter.run()` 会追加
   `{source, backend, agent:"executor", validation}`，便于 D 侧溯源。
3. **动作校验上移到 `integration/strategy_policy.py`**：`ALLOWED_ACTIONS` /
   `validate_action_arguments` / `validate_strategy` 统一在这里，B/C/D 共用同一语义门。

---

## 六、执行证据归一化

`strategy_interpreter` 会把后端动作结果中的 **`pose` / `velocity_m_s` / `collisions` /
`grasp_force_n`** 写入 `execution.v1` 的步骤记录；后端在运动期间产生的碰撞/越界/超时等
**安全事件按时间序汇入 `execution.v1.safety_events`**；安全门触发时解释器直接进入
`SAFE_STOP`，不再进入有限恢复。

---

## 七、如何接入（给 Pipeline / 队友）

```python
from integration.adapters import perception
from integration.adapters.executor import ExecutorAdapter
from integration.config.loader import load_profile

scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})

# local(mock) 不需要 driver；sim/real 传入 Isaac/真机驱动
profile = load_profile("sim")
adapter = ExecutorAdapter.from_profile(profile, scene, driver=OmniDriver(...))

execution = adapter.run(strategy_v1)   # 返回 execution.v1（含 provenance + 证据）
caps = adapter.capabilities()          # {"allowed_actions": [...], "max_recovery_attempts":3, "max_retries":2}
```

`integration/pipeline.py` 通过 `adapters["executor"].run(strategy)` 与
`adapters["executor"].capabilities()` 调用，无需改动即可沿用。

---

## 八、待办（按优先级）

1. `stack_on` 堆叠放置：对齐 `MockBackend._stack_pose`，补 Isaac/真机后端实现。
2. 服务器 Isaac Sim / 真机完整联调 + 验收（`stu_01@10.16.0.40:5122`，离线容器）。
3. （可选）真实 `execution.v1` 接入 D（TraceCoder）反馈闭环。

---

## 九、测试情况

- 本分支 C 模块单元测试 **34 项全绿**（config / safety / isaac_backend / real_backend / enrichment）。
- 全套单测 78 通过 + 1 个 import 错误（`pydantic_settings`，A 模块依赖，本地环境未装，与 C 无关）。
- 端到端 mock sanity check 通过：`SUCCEEDED` + `provenance` + 证据字段。
