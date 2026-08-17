# A-C 观察消息与 B-C 正式执行接口设计

## 目标

在不要求 A 修改既有 `perception_observation 1.0.0` JSON 的前提下，让 C 能校验并转换该消息；同时把 B→C 的五步策略参数收敛为稳定 ID：`object_id` 和 `destination_id`。

## 已确认的 A-C 外部格式

A 向 C 提供的观察消息保持：

- `schema_version="1.0.0"`
- `message_type="perception_observation"`
- `observation_id`、`scene_id`、毫秒时间戳和时钟域
- `coordinate_system`
- `source.module/pipeline_version/sensor_ids`
- `objects[].object_id`
- `category_candidates`
- `pose.position` 和 `pose.orientation={x,y,z,w}`
- `geometry.type/size`
- `appearance` 三类候选
- `tracking`
- 可选 `simulation_metadata`

该格式是模块间 wire contract。C 在边界处把它转换为仓库现有内部 `perception.v1`，不要求 A 改成内部格式。

## C 的转换规则

- `object_id` → 内部 `id`，不得重新编号。
- `coordinate_system` → `coordinate_frame`。
- category/color/shape/texture 候选按最高 score 选择主值，同时保留全部候选。
- `pose.position` → 内部平铺 `pose.x/y/z`；四元数原样保存在对象的 `orientation` 字段，顺序明确为 `xyzw`。
- `geometry.size` → 内部 `dimensions.width/height/depth`。
- tracking、source、observation_id、timestamp 和 simulation_metadata 保存在内部元数据中。
- `simulation_metadata.evaluation_only=true` 时不得用于真实运行的语义推断或安全决策。
- A 消息不决定物体是否可抓取或目标区是否安全；这些由 C 的 Mock/Isaac/real backend 根据本地场景能力判断。

## B-C 正式字段

第一阶段动作保持：

1. `detect_object {"object_id": "<stable-id>"}`
2. `move_to_object {"object_id": "$<detect-step>.object_id"}`
3. `grasp {"object_id": "$<detect-step>.object_id"}`
4. `move_to_target {"destination_id": "<stable-id>"}`
5. `release {}`

正式接口不再输出或接受 `object_name` 和 `target`。`strategy.code` 继续必须为空。

## 本轮修改边界

- 修改 C 的 contract、perception adapter、normalizer、动作参数校验和 MockBackend。
- 修改 B 的公开 strategy adapter，使其输出正式字段。
- 不修改 A 和 D 的业务代码。
- 为 A、B、D 分别输出修改需求文档。
- D 尚未升级时，TraceCoder 内部轻量仿真可能不能解释新字段；真实 `execution.v1` 仍是最终事实，D 负责人必须按需求文档升级。

## 验收

- 用户给出的 A JSON 通过新 wire schema。
- C 转换后对象 ID、主类别、颜色、位姿、尺寸和元数据正确。
- B 只输出 `object_id` 与 `destination_id`。
- C 拒绝遗留 `object_name` 与 `target`。
- B→C Mock pick-and-place 继续成功。
- 全仓测试通过。
