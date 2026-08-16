# Perception v1 Mock 适配器

本模块由 C 模块负责人维护，把场景中的对象、位姿、能力信息和预定义安全目标区转换为统一的 `perception.v1`。它不理解自然语言、不决定任务目标，也不生成动作策略。

## 当前入口

```python
from integration.adapters import perception

result = perception.run({
    "scene_id": "stacking_cubes",
    "backend": "mock",
})
health = perception.health()
```

请求字段：

| 字段 | 必填 | 当前取值 | 说明 |
|---|---:|---|---|
| `scene_id` | 是 | `stacking_cubes` | 选择确定性的测试场景 |
| `backend` | 否 | `mock` | 第一阶段仅允许 Mock，省略时默认为 Mock |

## `perception.v1` 输出

| 路径 | 类型 | 含义 |
|---|---|---|
| `schema_version` | string | 固定为 `perception.v1` |
| `scene_id` | string | 本次场景的稳定标识 |
| `coordinate_frame` | string | 第一阶段固定为 `world` |
| `objects` | array | 场景对象和虚拟目标区 |
| `objects[].id` | string | 跨模块使用的稳定 ID |
| `objects[].category` | string | 物体类别，例如 `cube`、`target_zone` |
| `objects[].pose` | object | 米制世界坐标，包含 `x`、`y`、`z` |
| `objects[].dimensions` | object | 可选尺寸，单位为米 |
| `objects[].attributes` | object | 展示名、颜色、用途等描述信息 |
| `objects[].execution.movable` | boolean | 该对象能否移动 |
| `objects[].execution.graspable` | boolean | 该对象能否抓取 |
| `objects[].execution.valid_destination` | boolean | 该对象能否作为安全放置目标 |
| `execution_context.backend` | string | 当前感知来源，第一阶段为 `mock` |
| `execution_context.scene_revision` | string | 场景数据修订号 |

## ID 与目标区规则

- A、B、C、D 模块之间只传 `objects[].id`，不要用中文展示名作为主键。
- 同一份 perception 中的对象 ID 必须唯一。
- `zone_unstack_target` 是虚拟目标区，不是可抓取实体。
- B 生成 `move_to_target` 时，只能选择当前 perception 中 `execution.valid_destination == true` 的 ID。
- 上游不能绕过目标区，直接向 C 提交任意坐标。

完整样例见 [`../../testdata/daily/stacking_scene.json`](../../testdata/daily/stacking_scene.json)，协议见 [`../../contracts/v1/perception.schema.json`](../../contracts/v1/perception.schema.json)。

## 当前限制

第一阶段只有确定性 Mock 场景，不读取相机，也不启动 Isaac Sim。它证明 JSON 契约和模块联调方式可用，不证明真实识别精度、碰撞安全或物理运动正确。后续 Isaac 后端仍须输出相同的 `perception.v1`，这样 A/B/D 无需修改调用接口。
