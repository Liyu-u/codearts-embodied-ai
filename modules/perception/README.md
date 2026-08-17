# 感知模块（C 角色）

## 作用

感知适配器同时支持两种入口：原有确定性 Mock 请求，以及 A 的正式 `perception_observation 1.0.0` 消息。外部消息在 C 的边界转换为内部 `perception.v1`，对象稳定 ID 保持不变。

## 调用

```python
from integration.adapters import perception

scene = perception.run({
    "scene_id": "stacking_cubes",
    "backend": "mock",
})
```

A 的正式消息可直接传给同一入口：

```python
scene = perception.run(perception_observation_1_0_0)
```

完整 wire 样例见 `testdata/integration/a_perception_observation_v1.json`。

返回值包含：

- `schema_version="perception.v1"`
- `scene_id` 和 `coordinate_frame`
- `objects[].id`：跨 A、B、C、D 保持不变的对象 ID
- `objects[].category`：供 A 进行语义绑定
- `objects[].pose`：世界坐标系位置
- Mock 对象的 `objects[].execution.graspable`：是否可抓取
- Mock 对象的 `objects[].execution.valid_destination`：是否允许作为放置目标

Mock 场景中的 `green_cube` 与 `zone_unstack_target` 分别使用中文类别“绿色方块”和“桌子”，因此 A 可把“把绿色方块放到桌子上”绑定为稳定 ID；C 后端仍只按 ID 执行。

## 当前边界

- 请求式场景的 `backend` 当前只能是 `mock`；A 的正式观察消息不使用该请求字段。
- 不从网络、服务器或 Isaac Sim 隐式读取状态。
- A 消息不携带 C 的可信执行能力；C 不根据 `simulation_metadata` 推导可抓取性或安全目标区。
- 接入 Isaac Sim 时新增后端，不改变 `perception.v1` 和稳定 ID 规则。

## 测试

在 `huawei` Conda 环境中运行：

```bash
python -m pytest tests/contract/test_perception_observation_schema.py tests/unit/test_perception_observation_normalizer.py tests/contract/test_perception_adapter.py -q
```
