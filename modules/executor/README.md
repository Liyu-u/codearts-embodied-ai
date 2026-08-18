# Executor v1 适配器

本模块由 C 模块负责人维护，接收经过协议校验的 `strategy.v1`，解释有限动作并输出 `execution.v1`。后端可替换：

- `MockBackend`（`modules/executor/mock_backend.py`）：确定性内存状态机，用于本地开发与 CI；
- `IsaacSimBackend`（`modules/executor/isaac_backend.py`）：校内服务器 Isaac Sim 6.0 真实仿真；
- `RealRobotBackend`（`modules/executor/real_backend.py`）：真机小范围低速测试（强制人工确认）。

三者实现相同的 `execute / safe_stop / trajectory_points / snapshot` 接口，适配器调用方式不变。

## 构造与调用

Executor 必须绑定到生成策略时所依据的同一份 perception 状态：

```python
from integration.adapters import perception
from integration.adapters.executor import ExecutorAdapter
from modules.executor.mock_backend import MockBackend

scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
executor = ExecutorAdapter(MockBackend.from_perception(scene))
execution = executor.run(strategy_v1)
health = executor.health()
```

## 第一阶段动作白名单

| 动作 | 标准参数 | 兼容别名 | 成功结果中的关键字段 |
|---|---|---|---|
| `detect_object` | `{"object_id": "green_cube"}` | 可用 `object_name` 代替 `object_id`，两者不能同时出现 | `object_id`、`pose` |
| `move_to_object` | `{"object_id": "green_cube"}` | 无 | `object_id` |
| `grasp` | `{"object_id": "green_cube"}` | 无 | `object_id` |
| `move_to_target` | `{"destination_id": "zone_unstack_target"}` | 可用 `target` 代替 `destination_id`，两者不能同时出现 | `destination_id` |
| `release` | `{}` | 无 | `object_id` |

不在表中的动作会在调用后端前被拒绝。`move_to_target` 的目标必须来自当前 perception，且具有 `execution.valid_destination: true`。

## 步骤引用

B 可以在后续步骤参数中引用已经执行成功的结果：

```json
{
  "step_id": "approach_green",
  "action": "move_to_object",
  "arguments": {"object_id": "$detect_green.object_id"}
}
```

规则如下：

- 格式为 `$step_id.field`，可继续访问更深的字典字段。
- 只能引用此前已经执行的步骤，不能引用未来步骤。
- 所有主步骤和恢复步骤的 `step_id` 必须唯一。
- 引用不存在时，该步骤为 `FAILED`，原因以 `UNRESOLVED_REFERENCE` 开头，后续主步骤为 `SKIPPED`。

## 失败恢复与安全停止

```json
{
  "step_id": "grasp_green",
  "action": "grasp",
  "arguments": {"object_id": "$detect_green.object_id"},
  "on_failure": {
    "max_attempts": 1,
    "steps": [
      {
        "step_id": "retry_grasp_green",
        "action": "grasp",
        "arguments": {"object_id": "$detect_green.object_id"}
      }
    ],
    "on_exhausted": "stop"
  }
}
```

- `max_attempts` 只能是 1–3。
- 每个恢复块最多 10 个步骤；总后端动作调用最多 100 次；主步骤最多 50 个。
- 恢复成功后继续下一个主步骤，不再次运行原失败动作。
- 恢复耗尽或动作调用超限时，executor 进入安全停止，顶层状态为 `SAFE_STOP`，并产生 `safety_events`。
- 主步骤失败但没有 `on_failure` 时，顶层状态为 `FAILED`，剩余主步骤为 `SKIPPED`。
- 第一阶段 `on_exhausted` 只允许 `stop`。

## 状态不要混用

| 位置 | 可用状态 | 含义 |
|---|---|---|
| `execution.status` | `SUCCEEDED`、`FAILED`、`SAFE_STOP` | 整个任务的最终状态 |
| `execution.steps[].status` | `SUCCESS`、`FAILED`、`SKIPPED` | 单个动作记录的状态 |
| `execution.steps[].phase` | `main`、`recovery_1`…`recovery_3`、`safe_stop` | 动作属于主流程、哪轮恢复或安全停止 |

未经确认不得执行 `strategy.code`。当前实现只接受 `null` 或空字符串，任何非空内容都会在后端调用前被拒绝，不会执行 Python、Shell 或其他任意代码。

完整策略样例见 [`../../testdata/daily/stacking_strategy.json`](../../testdata/daily/stacking_strategy.json)，协议见 [`../../contracts/v1/strategy.schema.json`](../../contracts/v1/strategy.schema.json) 和 [`../../contracts/v1/execution.schema.json`](../../contracts/v1/execution.schema.json)。

## Mock 与 Isaac / 真机的边界

Mock 后端会真实维护“接近、夹持、移动、释放”内存状态，并支持确定性失败注入，适合服务器不可用时开发 A/B/D 联调。它不启动 Isaac Sim，也不能证明碰撞、动力学、渲染或真实机械臂控制正确。

`IsaacSimBackend` / `RealRobotBackend` 在 Mock 之外增加了完整的安全守卫（见下），并通过 `MotionDriver` 抽象调用真实运动原语：

- `modules/executor/safety.py`：工作空间、限速、超时、抓取验证等纯安全守卫；
- `modules/executor/isaac_driver.py`：`MotionDriver` 协议 + 惰性导入的 `OmniDriver`；
- `modules/executor/robot_backend.py`：受安全守卫的后端基类。

### 用 local/sim/real 配置选择后端

```python
from integration.adapters import perception
from integration.adapters.executor import ExecutorAdapter
from integration.config.loader import load_profile

scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
profile = load_profile("sim")                 # local | sim | real
executor = ExecutorAdapter.from_profile(profile, scene)
execution = executor.run(strategy_v1)
```

`local` 映射到 `MockBackend`，`sim` 映射到 `IsaacSimBackend`，`real` 映射到
`RealRobotBackend`；后端安全参数（工作空间、限速、限力、超时、人工确认等）由
`integration/config/profiles/*.toml` 与环境变量 `RIA_*` 决定。

### 安全守卫（Isaac / 真机）

每次运动前，后端强制检查并 fail-closed：

| 守卫 | 触发结果 |
|---|---|
| 工作空间越界 | `WORKSPACE_VIOLATION` → 安全停止 |
| 碰撞风险 | `COLLISION_DETECTED` → 安全停止 |
| 碰撞查询异常 | `COLLISION_CHECK_ERROR` → 安全停止（绝不假设安全） |
| 线速度超限 / 上限非正 | `SPEED_LIMIT_EXCEEDED` → 安全停止 |
| 动作超时 | `ACTION_TIMEOUT` → 安全停止 |
| 真机未人工确认 | `HUMAN_CONFIRMATION_REQUIRED`（真机模式） |
| 急停 | `E_STOP_TRIGGERED`（`emergency_stop()`） |
| 驱动异常 | `BACKEND_ERROR` → 安全停止 |

这些后端安全事件会按时间顺序并入 `execution.v1.safety_events`，动作的
`pose` / `collisions` / `grasp_force_n` 会写入对应步骤记录。

### 实机验证清单（必须在 isaacsim 环境完成，CI 不覆盖）

`OmniDriver` 中的具体控制器 / IK / PhysX 调用点尚未经过真实环境验证。上线前按顺序：

1. `isaacsim` 环境跑通 `OmniDriver.connect()`，确认 Franka 与夹爪 prim 路径；
2. 单原语验证：`move_to`、`gripper_open`、`gripper_close`、`collision_free`；
3. 确认 `end_effector.set_world_pose` 的 IK 返回语义与关节限位读取；
4. 五动作 `detect → move_to_object → grasp → move_to_target → release` 顺序实测；
5. 注入“IK 不可达 / 碰撞 / 超时 / 抓空”验证 fail-closed 路径；
6. 真机必须先确认、限速（≤0.05 m/s）、限工作空间，再做小范围低速测试。
