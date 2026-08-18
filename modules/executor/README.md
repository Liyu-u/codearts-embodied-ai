# 执行器模块（C 角色）

## 作用

执行器消费 B 输出的 `strategy.v1`，通过受控后端执行原子动作，并返回 `execution.v1`。第一阶段的 `MockBackend` 用于无需服务器的离线联调；后续 Isaac Sim 后端实现同一个后端接口。

## 安全边界

- 只允许 `detect_object`、`move_to_object`、`grasp`、`move_to_target`、`release`。
- `move_to_target` 默认是直接放置；堆叠必须显式传 `placement_mode: "stack_on"`，且目标对象必须声明 `execution.stackable_destination=true`。
- `strategy.code` 必须为 `null` 或空字符串；任何非空代码都会在执行前被拒绝。
- 所有动作及恢复动作在执行前统一校验。
- 主步骤最多 50，单个恢复最多 10 步，恢复尝试最多 3 次，总动作调用最多 100 次。
- 恢复耗尽或超过动作上限后调用后端安全停止，输出 `SAFE_STOP` 和安全事件。

## 用户级动作的落地方式

B 的 `pick/grasp` 只生成检测、接近、抓取三步；`pick_and_place/place`、`transfer`、`fetch` 复用五步抓取搬运策略；`stack` 仍复用五步策略，但 C 会依据底座和被抓物尺寸计算底座上方的中心落点。

## 引用与恢复

后续步骤可引用前序结果：

```json
{"object_id": "$task-001-detect.object_id"}
```

抓取恢复示例：

```json
{
  "max_attempts": 1,
  "steps": [
    {
      "step_id": "task-001-retry-grasp",
      "action": "grasp",
      "arguments": {"object_id": "$task-001-detect.object_id"}
    }
  ],
  "on_exhausted": "stop"
}
```

## 调用

```python
from integration.adapters.executor import ExecutorAdapter
from modules.executor.mock_backend import MockBackend

backend = MockBackend.from_perception(perception_v1)
execution_v1 = ExecutorAdapter(backend).run(strategy_v1)
```

Isaac Sim 后端需要实现：

```python
execute(action: str, arguments: dict) -> dict
safe_stop(reason: str) -> dict
trajectory_points() -> list[dict]
snapshot() -> dict
```

## 测试

在 `huawei` Conda 环境中运行：

```bash
python -m unittest tests.contract.test_execution_adapter tests.unit.test_mock_executor_backend tests.unit.test_strategy_interpreter tests.unit.test_strategy_recovery -v
```
