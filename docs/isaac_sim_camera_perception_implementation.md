# Isaac Sim 真实摄像头感知实现

当前实现已经把相机观测接入现有感知契约：

```text
Isaac Sim 6 RtxCamera + CameraSensor
  -> RGB + distance_to_image_plane + instance_id_segmentation
  -> USD 路径标签归一化、RTX 宽高布局归一化
  -> 深度反投影到 world 坐标
  -> perception_observation 1.0.0
  -> perception.v1
```

关键入口：

- `modules/perception/isaac_camera.py`：通用 RGB-D/分割观测 provider。
- `modules/perception/isaac_camera_real.py`：Isaac Sim 6 实际运行包装，处理 RTX 数组布局和 `/World/...` 标签。
- `integration/adapters/isaac_camera_perception.py`：把外部相机观测规范化为内部 `perception.v1`。
- `tools/run_isaac_camera_perception_real.py`：真实 Isaac Sim 容器入口。

容器内运行示例：

```bash
./python.sh /workspace/tools/run_isaac_camera_perception_real.py \
  --result-dir /workspace/results --frames 120 --/app/headless=true
```

输出文件为 `camera_observation.json`、`perception.json`、`camera_metrics.json` 和 `rgb.png`。
在线位姿来自 RGB-D 深度反投影；USD/PhysX 真值不进入在线观测，只用于场景创建和离线评估。

本次真实冒烟结果：RGB、深度和实例分割均产生有效数据，识别出 `red_cube`、`green_cube` 和 `zone_unstack_target` 三个对象；两个 JSON 契约均通过校验。正式控制闭环前仍应使用目标相机的标定内参替换示例 pinhole 参数，并增加遮挡、光照和运动目标场景。
