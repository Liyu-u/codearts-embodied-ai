# 交接文档 · B 模块（CodeArts 策略生成）

> 收件人：B 负责人（冯海）
> 发送人：C 负责人（吴昌庆）
> 日期：2026-08-17
> 关联分支：`feature/executor-isaac-v1`（统一联调仓库）

---

## 一、你现在需要做的三件事

1. **真实接入 CodeArts**：完成账号、代金券、模型与 Agent 配置，把 `task.v1` 真实转换为 `strategy.v1`，禁止用本地规则策略冒充 CodeArts；
2. **建立统一 LLM Provider**（配置 model / base_url / api_key / timeout / retry / 结构化 JSON 输出），供 CodeArts 调用；
3. **只输出结构化原子策略**，`code` 字段恒为 `null`，不再给 C 传任意 Python 代码。

## 二、接口契约（必须遵守）

你的模块目录：`modules/strategy_generation/`；适配器：`integration/adapters/strategy.py`。

```text
run(input_json: dict) -> strategy_v1: dict
health() -> dict
```

输入：`task.v1`（`READY + pick_and_place`）。输出 `strategy.v1`，协议见 `contracts/v1/strategy.schema.json`。

**你只能生成这五个原子动作**（C 的白名单，其余动作会被执行前拒绝）：

| 动作 | 标准参数 | 兼容别名 | 成功后可引用字段 |
|---|---|---|---|
| `detect_object` | `{"object_id": "green_cube"}` | `object_name` | `object_id`、`pose` |
| `move_to_object` | `{"object_id": "green_cube"}` | 无 | `object_id` |
| `grasp` | `{"object_id": "green_cube"}` | 无 | `object_id` |
| `move_to_target` | `{"destination_id": "zone_unstack_target"}` | `target` | `destination_id` |
| `release` | `{}` | 无 | `object_id` |

**硬性规则：**

- `code` 只允许 `null` 或空字符串，任何非空代码 C 都会在执行前拒绝；
- `task_id` 原样沿用 A 的任务 ID；
- `step_id` 在主步骤和所有恢复步骤中全局唯一；
- `$step_id.field` 只能引用**已经执行**的步骤；
- 对象只传稳定 `object_id` / `destination_id`，显示名只用于 UI；
- `move_to_target` 的目标必须来自 perception 且 `valid_destination == true`。

五步策略样例见 `testdata/daily/stacking_strategy.json`：

```text
detect_object → move_to_object → grasp → move_to_target → release
```

## 三、老师对 CodeArts 的具体要求（P0）

1. **最小 CLI smoke test 先行**，再跑 `task.v1 → CodeArts → strategy.v1`；
2. 将 `CODEARTS_STRATEGY_MODE` 设为 `required` 做正式验收，**禁止本地策略冒充成功**；
3. 测试并记录这些真实问题：**输出不稳定、动作幻觉、引用错误、延迟**——这些配合 C 的约束校验与修复机制，本身就是本系统的贡献点；
4. 在 UI 明确展示：本次策略来源、模型、耗时、校验结果；
5. 验收至少保存 **3 条 CodeArts 真实成功记录** 和 **3 类失败处理记录**（超时 / 非法 JSON / 未知动作或错误实体 ID），并能重复运行。

## 四、你需要确认并回给 C 的问题

1. 公开适配器是否接受"五步原子动作 + `code=null`"作为正式输出？
2. 后续新增动作（push / stack）时，高层动作→原子动作的转换由谁负责、放哪个模块？
3. CodeArts 的输出字段里，哪些能稳定提供 `object_id / destination_id`？是否需要 C 提供 `capabilities` 声明来约束你的动作集合？

## 五、验收口径

- `run(task.v1)` 输出通过 `strategy.v1` schema 校验，且 `code == null`；
- 用 A 的 `READY + pick_and_place` 任务，CodeArts 能稳定产出五步策略；
- 超时 / 非法 JSON / 未知动作 / 错误实体 ID 各自有可复现的失败处理记录；
- 策略来源（CodeArts vs 本地规则）在 UI 可见，且有模型名、耗时、校验结果。

---

关联文档：`docs/Isaac执行器接口说明.md`（第 3 节"B 只能生成这些动作"）。
