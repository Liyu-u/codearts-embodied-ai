# 交接文档 · A 模块（意图理解与实体绑定）

> 收件人：A 负责人（王翊航 / 郭家腾）
> 发送人：C 负责人（吴昌庆）
> 日期：2026-08-17
> 关联分支：`feature/executor-isaac-v1`（统一联调仓库）

---

## 一、你现在需要做的三件事

1. **把 `modules/intent_understanding/` 从空目录补齐**，实现统一适配器 `run()/health()`，输出合法的 `task.v1`；
2. **把"歧义"做成一个可以量化的研究问题**（老师特别强调，见第四节）；
3. **前端不要再用 Mock**，明确展示 Mock / CodeArts / Isaac / 真机四种来源，避免评委把 Mock 当成真实执行。

## 二、接口契约（必须遵守）

你的模块目录：`modules/intent_understanding/`；适配器：`integration/adapters/intent.py`。

```text
run(input_json: dict) -> task_v1: dict
health() -> dict
```

输入：`{"instruction": "把绿色方块放到桌子上", "perception": <perception.v1>}`

输出 `task.v1`，协议见 `contracts/v1/task.schema.json`：

| 字段 | 必填 | 说明 |
|---|---|---|
| `schema_version` | 是 | 固定 `task.v1` |
| `task_id` | 是 | 每次任务唯一，**不要**复用 observation_id / scene_id |
| `action` | 是 | 第一阶段只支持 `pick_and_place` |
| `target_ids` | 否 | 稳定对象 ID 数组 |
| `destination_id` | 否 | 稳定目标区 ID |
| `constraints` | 否 | 转成可执行参数（速度/容差/抓取方式），不要只写中文 |
| `status` | 是 | `READY` / `NEEDS_CLARIFICATION` / `BLOCKED` |
| `blocking_reasons` | 否 | 阻断时的结构化原因 |

**硬性规则：**

- 只把 perception 里的 `objects[].id` 当作主键，**禁止**用中文 `display_name` 做主键。
- 抓取目标必须满足 `execution.graspable == true`；目的地只能选 `execution.valid_destination == true`。
- 不要在 task 里塞 C 未声明的坐标；C 只认稳定 ID。
- 绑定不到唯一目标时，返回 `NEEDS_CLARIFICATION`（多候选）或 `BLOCKED`（信息不足），**不要猜**。

## 三、C 会给你什么

C 的感知适配器输出 `perception.v1`（样例见 `testdata/daily/stacking_scene.json`）：

```json
{
  "id": "green_cube",
  "category": "cube",
  "pose": {"x": 0.25, "y": 0.0, "z": 0.12},
  "dimensions": {"x": 0.04, "y": 0.04, "z": 0.04},
  "attributes": {"display_name": "绿色方块", "color": "green"},
  "execution": {"movable": true, "graspable": true}
}
```

`zone_unstack_target` 是虚拟目标区（`execution.valid_destination: true`），不是可抓实体。

## 四、把"歧义"做成核心研究问题（老师重点）

老师原话方向：**"语言表达有歧义，但最终可执行动作可能仍然唯一。"** 建议把歧义分三类：

1. **语言看似歧义，但结合场景后只有一个可执行目标** → 自动消歧，正常 `READY`；
2. **存在多个可执行候选** → 必须向用户澄清，返回 `NEEDS_CLARIFICATION`；
3. **信息不足，系统无法安全判断** → 必须阻断，返回 `BLOCKED`，**禁止猜测**。

为此建立**成对测试集**，并汇报四个指标：

- 实体绑定准确率；
- 歧义识别 F1；
- 漏澄清率（该澄清却直接执行了）；
- **危险误执行率**（该阻断却执行了，这个最关键）。

这比单纯展示"系统听懂一句话"更有研究深度，也直接服务于最终演示的"歧义→唯一消歧 / 多候选→澄清 / 信息不足→阻断"故事线。

## 五、你需要确认并回给 C 的问题

1. 第一阶段是否确认只验收 `READY + pick_and_place`？
2. perception 里 `category / attributes` 的最低字段要求是什么？（C 好补齐场景定义）
3. 稳定 ID 方案是否接受沿用 C 的 `green_cube / zone_unstack_target`，显示名只给人看？
4. 歧义测试集由谁牵头维护，放哪个目录？

## 六、验收口径

- `run()` 输出的 `task.v1` 通过 `contracts/v1/task.schema.json` 校验；
- 用 `testdata/daily/stacking_scene.json` 作感知输入，指令"把绿色方块放到桌子上"能稳定输出 `READY + green_cube + zone_unstack_target`；
- 多候选 / 信息不足样例分别返回 `NEEDS_CLARIFICATION` / `BLOCKED`；
- 前端区分 Mock 与真实执行来源（见老师 P1"前端展示升级"）。

---

关联文档：`docs/Isaac执行器接口说明.md`（第 2 节"A 应该如何使用"）。
