# B-C 联调接口说明（第一阶段）

## 1. 当前已经打通的链路

```text
C Mock 感知
  -> perception.v1
A 意图理解
  -> task.v1 (READY, pick_and_place, 稳定对象 ID)
B 策略适配器
  -> strategy.v1 (五步原子动作, code=null)
C Mock 执行器
  -> execution.v1
D TraceCoder
  -> feedback.v1
```

这一链路不需要 Isaac Sim 或校园服务器，适合日常开发和接口回归。服务器可用时，只替换 C 的感知/执行后端，A/B/D 协议不变。

## 2. A 给 B 的输入

第一阶段可执行任务必须同时满足：

```json
{
  "schema_version": "task.v1",
  "task_id": "stacking_cubes",
  "action": "pick_and_place",
  "target_ids": ["green_cube"],
  "destination_id": "zone_unstack_target",
  "status": "READY",
  "blocking_reasons": []
}
```

要求：

- `status` 必须为 `READY`；
- `action` 必须来自当前共同开放动作：`pick`、`grasp`、`pick_and_place`、`place`、`transfer`、`fetch`、`stack`；
- `pick/grasp` 必须且只能有一个稳定 `target_ids`，不需要目的地；
- `pick_and_place/place/transfer/fetch/stack` 必须且只能有一个稳定 `target_ids`，并提供非空稳定 `destination_id`；
- `stack` 的目标物和底座不能是同一对象，C 还要求底座声明 `execution.stackable_destination=true`。

## 3. B 给 C 的输出

```json
{
  "schema_version": "strategy.v1",
  "task_id": "stacking_cubes",
  "steps": [
    {
      "step_id": "stacking_cubes-detect",
      "action": "detect_object",
      "arguments": {"object_id": "green_cube"}
    },
    {
      "step_id": "stacking_cubes-approach",
      "action": "move_to_object",
      "arguments": {"object_id": "$stacking_cubes-detect.object_id"}
    },
    {
      "step_id": "stacking_cubes-grasp",
      "action": "grasp",
      "arguments": {"object_id": "$stacking_cubes-detect.object_id"},
      "on_failure": {
        "max_attempts": 1,
        "steps": [
          {
            "step_id": "stacking_cubes-retry-grasp",
            "action": "grasp",
            "arguments": {"object_id": "$stacking_cubes-detect.object_id"}
          }
        ],
        "on_exhausted": "stop"
      }
    },
    {
      "step_id": "stacking_cubes-move-target",
      "action": "move_to_target",
      "arguments": {"destination_id": "zone_unstack_target"}
    },
    {
      "step_id": "stacking_cubes-release",
      "action": "release",
      "arguments": {}
    }
  ],
  "code": null,
  "success": true,
  "blocked": false,
  "mode": "primitive_plan"
}
```

正式接口只允许 `object_id` 和 `destination_id`。旧字段 `object_name`、`target` 已停止兼容，C 会在执行前以 `INVALID_ARGUMENT` 拒绝。D 必须同步迁移，具体见 `docs/interfaces/C对D修改需求-2026-08-17.md`。

## 4. C 的输出

```json
{
  "schema_version": "execution.v1",
  "task_id": "stacking_cubes",
  "status": "SUCCEEDED",
  "steps": [
    {
      "step_id": "stacking_cubes-detect",
      "phase": "main",
      "action": "detect_object",
      "arguments": {"object_id": "green_cube"},
      "status": "SUCCESS",
      "reason": null,
      "duration_ms": 10
    }
  ],
  "trajectory_points": [],
  "total_duration_ms": 460,
  "safety_events": []
}
```

`steps` 示例只展示第一条，真实输出记录所有主步骤和实际发生的恢复步骤。顶层状态只有：

- `SUCCEEDED`：全部完成；
- `FAILED`：普通失败且没有可用恢复；
- `SAFE_STOP`：恢复耗尽、动作上限或安全条件触发。

## 5. D 的输出

D 接收 `{task, strategy, execution}`，输出 `feedback.v1`：

```json
{
  "schema_version": "feedback.v1",
  "task_id": "stacking_cubes",
  "diagnosis": "{...}",
  "retryable": false,
  "patch": {
    "schema_version": "strategy.v1",
    "task_id": "stacking_cubes",
    "steps": [
      {
        "step_id": "stacking_cubes-detect",
        "action": "detect_object",
        "arguments": {"object_id": "green_cube"}
      }
    ]
  }
}
```

上面的 `patch.steps` 为缩写示例，真实成功输出会保留完整五步策略。
`diagnosis` 是 JSON 序列化后的字符串；需要结构化读取时使用
`json.loads(feedback["diagnosis"])`。其中 `final_passed` 和
`execution_passed` 以 C 的真实 `execution.v1` 为准。

## 6. 环境和运行方式

本项目固定使用两个 Conda 环境：

- `huawei`：运行仓库代码、A/B/C/D、Mock、契约测试和离线联调；
- `isaacsim`：运行 Isaac Sim/Kit 与真实仿真后端。

本地离线联调：

```bash
conda activate huawei
python -m unittest tests.e2e.test_abcd_pick_and_place_e2e -v
python -m unittest discover -s tests -t . -v
```

校园服务器不能访问外网，也不允许在线更新。Isaac Sim 依赖和项目包必须先在 Windows 下载、校验，再传到 `/data/stu_01`；服务器容器内通过 Isaac Sim 自带的 `/isaac-sim/python.sh` 运行，不依赖 Windows 的 Conda 环境。

## 7. 当前限制

1. 当前验收 `READY + pick/grasp`、`pick_and_place/place`、`transfer`、有明确目标区的 `fetch` 和 `stack`。
2. `push`、`dynamic_grasp`、`handover`、`pour`、`wait`、`custom` 仍由 A 澄清或 B 阻断，不会交给 C。
3. 公共 Pipeline 在 A 非 READY 或 B 返回阻断时都会提前结束；只有形成合法 `strategy.v1` 后才交给 C。
4. 本轮通过的是确定性 Mock 闭环；Isaac Sim 后端、USD 场景、控制器和服务器实测仍是下一阶段。
5. 任何 B 生成的 Python 代码都不属于联调协议，C 会拒绝非空 `strategy.code`。
6. D 的轻量仿真会给推导对象生成内部 ID，而任务目标使用 perception 稳定 ID；真实 `execution.v1` 是最终事实来源，`stack` 通过 `object_on` 复核并由 C 的快照提供尺寸落点证据。
7. 正式 B→C 策略统一使用 `object_id` 与 `destination_id`；旧字段仅在 MockBackend 的直接调用兼容层保留，不属于正式策略接口。
