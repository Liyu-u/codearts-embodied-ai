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
- `action` 必须为 `pick_and_place`；
- `target_ids` 必须且只能有一个稳定 ID；
- `destination_id` 必须是非空稳定 ID。

## 3. B 给 C 的输出

```json
{
  "schema_version": "strategy.v1",
  "task_id": "stacking_cubes",
  "steps": [
    {
      "step_id": "stacking_cubes-detect",
      "action": "detect_object",
      "arguments": {"object_name": "green_cube"}
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
      "arguments": {"target": "zone_unstack_target"}
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

兼容说明：D 当前读取 `object_name` 和 `target`，C 同时接受它们及 `object_id`/`destination_id`。这里的 `object_name` 和 `target` 实际承载稳定 ID，不做模糊名称搜索。

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
      "arguments": {"object_name": "green_cube"},
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
        "arguments": {"object_name": "green_cube"}
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

校园服务器不能访问外网，也不允许在线更新。Isaac Sim 依赖和项目包必须先在 Windows 下载、校验，再传到 `/data/stu_01`；服务器端使用 `isaacsim` 环境运行。

## 7. 当前限制

1. 第一阶段只验收 `READY + pick_and_place`。
2. B 的其他动作全部阻断，不会交给 C。
3. 公共 Pipeline 目前只在 A 非 READY 时提前返回；若 B 返回阻断结果，Pipeline 仍会调用 C。因此阻断链路尚未纳入端到端验收，后续需要公共 Pipeline 负责人修改。
4. 本轮通过的是确定性 Mock 闭环；Isaac Sim 后端、USD 场景、控制器和服务器实测仍是下一阶段。
5. 任何 B 生成的 Python 代码都不属于联调协议，C 会拒绝非空 `strategy.code`。
6. D 当前的轻量仿真会给推导对象生成内部 ID，而任务目标使用 perception 稳定 ID，因此诊断中的 `simulation_final_passed` 可能为 `false`；D 已把真实 `execution.v1` 设为最终事实来源，所以执行成功时 `final_passed=true`。彻底消除这项模拟差异需要 D 负责人后续统一对象 ID，本阶段不修改 D。
