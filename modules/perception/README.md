# 感知模块（C 角色）

## 作用

感知适配器为 A 提供 `perception.v1`，并为 C 执行后端保留稳定对象 ID、位姿和执行能力标记。第一阶段使用确定性的 Mock 场景；后续 Isaac Sim 后端必须输出相同格式。

## 调用

```python
from integration.adapters import perception

scene = perception.run({
    "scene_id": "stacking_cubes",
    "backend": "mock",
})
```

当前 Mock 还提供一个综合分拣工位：`sorting_workcell`。它包含三个待分拣方块
（红、绿、蓝）和三个有颜色标识的托盘，可用于在同一个环境中测试多条自然语言
`pick_and_place` 指令：

```python
scene = perception.run({
    "scene_id": "sorting_workcell",
    "backend": "mock",
})
```

分拣场景的稳定 ID 为：`red_sort_cube`、`green_sort_cube`、`blue_sort_cube`，
以及 `left_sort_tray`、`middle_sort_tray`、`right_sort_tray`。托盘使用红、绿、
蓝颜色属性来帮助 A 在多个同类目标区中完成唯一绑定。

返回值包含：

- `schema_version="perception.v1"`
- `scene_id` 和 `coordinate_frame`
- `objects[].id`：跨 A、B、C、D 保持不变的对象 ID
- `objects[].category`：供 A 进行语义绑定
- `objects[].pose`：世界坐标系位置
- `objects[].execution.graspable`：是否可抓取
- `objects[].execution.valid_destination`：是否允许作为放置目标

Mock 场景中的 `green_cube` 与 `zone_unstack_target` 分别使用中文类别“绿色方块”和“桌子”，因此 A 可把“把绿色方块放到桌子上”绑定为稳定 ID；C 后端仍只按 ID 执行。

## 当前边界

- `backend` 只能是 `mock`。
- 不从网络、服务器或 Isaac Sim 隐式读取状态。
- 接入 Isaac Sim 时新增后端，不改变 `perception.v1` 和稳定 ID 规则。

## 测试

在 `huawei` Conda 环境中运行：

```bash
python -m unittest tests.contract.test_perception_adapter tests.unit.test_perception_service -v
```
