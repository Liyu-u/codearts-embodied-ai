# B-C Isaac v1 联调设计

## 目标

在不修改 A、D 和公共 `integration/pipeline.py` 业务逻辑的前提下，打通第一阶段 `READY + pick_and_place` 链路：

```text
C 感知 -> A 意图 -> B 五步原子策略 -> C Mock/Isaac 执行 -> D 反馈
```

第一阶段以 Mock 执行器作为离线验收依据；校园服务器可用时，再把同一份 `strategy.v1` 交给 Isaac Sim 后端验证。

## 修改边界

- B：只修改公开适配器 `integration/adapters/strategy.py` 及 B 的测试、说明文档；不改 B 内部代码生成器。
- C：迁入现有感知、执行器、契约校验、测试与说明；允许调整 C Mock 场景的语义标签以兼容 A。
- A：不修改。
- D：不修改。
- 公共 Pipeline：不修改；第一阶段不验收 B 阻断后的提前返回。
- 不上传分支，直到吴昌庆检查并明确同意。

## B 对外策略格式

B 仅把 `READY`、单目标、具有稳定 `target_ids[0]` 和 `destination_id` 的 `pick_and_place` 任务变成可执行策略。公开输出中的 `code` 始终为 `null`，C 永不执行 B 生成的 Python 代码。

步骤固定为：

1. `detect_object`：`{"object_name": "<target_id>"}`
2. `move_to_object`：引用检测结果 `{"object_id": "$<detect_step>.object_id"}`
3. `grasp`：引用检测结果，并配置一次相同抓取重试
4. `move_to_target`：`{"target": "<destination_id>"}`
5. `release`：空参数

`object_name` 和 `target` 字段中承载的是稳定感知 ID。这是兼容 D 当前轻量仿真字段名、同时被 C 动作目录接受的过渡格式。

抓取恢复策略固定为：

```json
{
  "max_attempts": 1,
  "steps": [
    {
      "step_id": "<task_id>-retry-grasp",
      "action": "grasp",
      "arguments": {"object_id": "$<task_id>-detect.object_id"}
    }
  ],
  "on_exhausted": "stop"
}
```

其他 READY 动作、多目标任务、缺少稳定目标 ID 或目标区域 ID 的任务一律输出：`blocked=true`、`success=false`、`steps=[]`、`code=null`。

## C 行为

C 只接受动作白名单：`detect_object`、`move_to_object`、`grasp`、`move_to_target`、`release`。执行前校验 `strategy.v1`、拒绝非空 `code`、解析 `$step_id.field` 引用，并限制主步骤数、恢复步骤数和重试次数。恢复失败或动作上限触发时进入安全停止并输出 `execution.v1`。

C Mock 感知场景必须同时满足：

- 对外对象 ID 稳定；
- A 能通过中文类别完成目标与目标区域绑定；
- C 后端能从 `execution.graspable` 和 `execution.valid_destination` 判断执行能力。

## 验收

- 最新主线原有测试保持通过。
- C 原有契约、单元、集成及 Mock E2E 测试通过。
- B 输出精确的五动作序列、标准恢复结构和 `code=null`。
- 真实 `C 感知 -> A -> B -> C 执行 -> D` 测试返回：任务 `READY`、执行 `SUCCEEDED`、反馈 `feedback.v1`。
- 不要求本轮连接校园服务器或运行 Isaac Sim GUI。

## 环境约定

- `huawei` Conda 环境：仓库代码、契约、Mock 和 A/B/C/D 联调测试。
- `isaacsim` Conda 环境：只运行 Isaac Sim/Kit 相关程序和真实仿真后端。
- 校园服务器离线，不在服务器上在线安装或更新软件；依赖先在 Windows 准备后再传输。
