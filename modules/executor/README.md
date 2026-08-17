# 执行器模块（C 角色）

## 作用

执行器消费 B 输出的 `strategy.v1`，通过受控后端执行原子动作，并返回 `execution.v1`。第一阶段的 `MockBackend` 用于无需服务器的离线联调；后续 Isaac Sim 后端实现同一个后端接口。

## 安全边界

- 只允许 `detect_object`、`move_to_object`、`grasp`、`move_to_target`、`release`。
- `detect_object`、`move_to_object`、`grasp` 只接受 `object_id`；`move_to_target` 只接受 `destination_id`；`release` 参数必须为空。
- 遗留字段 `object_name` 和 `target` 不再兼容，会在执行前返回 `INVALID_ARGUMENT`。
- `strategy.code` 必须为 `null` 或空字符串；任何非空代码都会在执行前被拒绝。
- 所有动作及恢复动作在执行前统一校验。
- 主步骤最多 50，单个恢复最多 10 步，恢复尝试最多 3 次，总动作调用最多 100 次。
- 恢复耗尽或超过动作上限后调用后端安全停止，输出 `SAFE_STOP` 和安全事件。

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
