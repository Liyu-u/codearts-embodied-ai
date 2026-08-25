# Isaac Sim 仿真摄像头感知方案

## 1. 目标

将当前 C 模块的“USD/PhysX 直接读真值”感知，扩展为“从 Isaac Sim 摄像头图像获取环境信息”的感知链路：

```text
Isaac Sim Camera
  ├─ RGB 图像：颜色、外观、纹理
  ├─ Depth 图像：像素到三维坐标
  └─ Instance/Semantic 图像：仿真对象分割和稳定 ID 映射
          ↓
Camera Perception Provider
          ↓  perception_observation 1.0.0
已有 observation_normalizer
          ↓  perception.v1
A 意图理解 → B 策略 → C 执行 → D 反馈
```

摄像头感知只负责“看见什么、在哪里、长什么样”，不直接赋予对象可抓取、可放置等执行权限。执行权限仍由 C 的后端能力和安全策略决定。

## 2. 推荐相机布置

### 第一阶段：固定俯视 RGB-D 主相机

针对当前 `stacking_cubes` 场景，先使用一台固定相机即可：

| 参数 | 建议值 | 目的 |
|---|---:|---|
| 位置 | 工作台上方约 0.8–1.0 m | 覆盖绿色方块、红色方块和放置区域 |
| 朝向 | 光轴指向工作台中心 | 减少遮挡和透视变化 |
| RGB 分辨率 | 640×480 | 足够做颜色、形状和目标检测 |
| Depth 分辨率 | 640×480 | 支持三维位置估计 |
| 帧率 | 15–30 FPS | 满足抓取前观察和执行后复核 |
| 视场角 | 水平约 70° | 覆盖当前机械臂工作空间 |
| 传感器 | RGB + Depth + Instance Segmentation | 兼顾视觉信息和仿真稳定 ID |

建议路径：`/World/Sensors/overhead_rgbd`。

### 第二阶段：增加腕部相机

当俯视相机被机械臂或夹爪遮挡时，增加一台安装在末端附近的 RGB-D 相机：

- 路径：`/World/Robot/franka/panda_hand/wrist_camera`
- 分辨率：320×240 或 640×480
- 用途：近距离确认抓取、释放和目标遮挡情况
- 融合规则：俯视相机负责全局定位，腕部相机只在目标被遮挡或距离过近时补充观测

不要一开始就做双目融合。先把单俯视相机链路跑通，再加入腕部相机，可以减少坐标标定和时间同步问题。

## 3. 感知处理流程

### 3.1 采集

每个观测周期同时采集：

- RGB：颜色、外观和纹理候选；
- Depth：目标像素的深度；
- Instance/Semantic Segmentation：目标像素集合；
- Camera intrinsics：焦距、主点、图像尺寸；
- Camera extrinsics：相机坐标系到世界坐标系的变换；
- 时间戳：保证 RGB、Depth 和分割图属于同一帧。

### 3.2 对象分割和稳定 ID

仿真阶段可给每个 USD Prim 配置语义标签，并将分割结果映射到项目稳定 ID：

```text
/World/green_cube         → green_cube
/World/red_cube           → red_cube
/World/zone_unstack_target → zone_unstack_target
```

这一步只使用图像中的 instance mask，不读取对象的 USD 位姿。USD/PhysX 位姿只能作为离线评估真值，不能进入在线推理结果。

### 3.3 从深度反投影到世界坐标

对分割区域的像素中心或有效深度点，使用：

```text
p_camera = depth × K⁻¹ × [u, v, 1]ᵀ
p_world  = T_world_camera × p_camera
```

其中：

- `K` 是相机内参矩阵；
- `T_world_camera` 是外参变换；
- 过滤无效深度、超出工作空间和离群点；
- 用点云包围盒估计目标中心、宽、高、深；
- 立方体姿态第一阶段可输出单位四元数，后续再从边缘或点云拟合旋转。

### 3.4 跟踪和置信度

每个对象应保存：

- `object_id`：跨帧稳定；
- 类别、颜色、形状候选及分数；
- 三维位置和尺寸；
- `track_age_frames`；
- 速度和速度置信度；
- 感知置信度以及深度有效点比例。

若置信度低、目标被严重遮挡或深度无效，输出“需要澄清/重新观察”，不能猜测目标位置。

## 4. 与现有协议的接入

摄像头层输出正式观测消息，不直接改动 C 的内部协议：

```json
{
  "schema_version": "1.0.0",
  "message_type": "perception_observation",
  "observation_id": "isaac-camera-<frame>",
  "scene_id": "stacking_cubes",
  "timestamp": 0,
  "clock_domain": "isaac_sim",
  "coordinate_system": "world",
  "source": {
    "module": "isaac_sim_camera",
    "pipeline_version": "camera-perception.v1",
    "sensor_ids": ["overhead_rgbd"]
  },
  "objects": [
    {
      "object_id": "green_cube",
      "category_candidates": [{"name": "绿色方块", "score": 0.98}],
      "pose": {
        "position": {"x": 0.50, "y": 0.00, "z": 0.026},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
      },
      "geometry": {
        "type": "box",
        "size": {"width": 0.052, "height": 0.052, "depth": 0.052}
      },
      "appearance": {
        "color_candidates": [{"name": "green", "score": 0.99}],
        "shape_candidates": [{"name": "cube", "score": 0.97}],
        "texture_candidates": [{"name": "smooth", "score": 0.90}]
      },
      "tracking": {
        "track_age_frames": 1,
        "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
        "velocity_confidence": 0.90
      }
    }
  ],
  "simulation_metadata": {
    "evaluation_only": true,
    "ground_truth_objects": []
  }
}
```

该消息直接复用现有 `modules/perception/observation_normalizer.py`，转换为 `perception.v1` 后再交给 A/B/C/D。`simulation_metadata` 只用于离线评估，不能被 C 当作执行能力或真实位姿来源。

## 5. 建议的软件接口

新增一个与 `IsaacGroundTruthProvider` 并列的 provider：

```python
class IsaacCameraObservationProvider:
    backend = "isaac_camera"

    def capture(self) -> dict:
        """采集 RGB、Depth、分割图和相机标定信息。"""

    def observe(self) -> dict:
        """输出 perception_observation 1.0.0。"""

    def health(self) -> dict:
        """报告相机帧率、深度有效率、跟踪数量和最近时间戳。"""
```

原有 provider 继续保留，用于对比评估：

```text
camera_observation → perception.v1  （在线推理）
USD/PhysX truth    → perception.v1  （离线对照，不进入推理）
```

## 6. 分阶段实现和验收

### 阶段 1：相机链路打通

1. 在 Isaac Sim 场景中创建固定俯视 RGB-D 相机；
2. 输出 RGB、Depth、Instance Segmentation 和相机标定；
3. 完成 `green_cube`、`red_cube`、`zone_unstack_target` 的稳定 ID 映射；
4. 生成并校验 `perception_observation 1.0.0`；
5. 通过现有 normalizer 接入 A。

### 阶段 2：与 Ground Truth 对比

使用 USD/PhysX 只做评估真值，建议指标：

- 目标检测召回率：≥ 95%；
- 稳定 ID 准确率：100%；
- 三维位置误差：≤ 2 cm；
- 立方体尺寸误差：≤ 5 mm；
- 深度有效率：≥ 98%；
- 单帧感知延迟：≤ 100 ms；
- 连续 100 帧无重复 ID、无 NaN、无坐标系跳变。

### 阶段 3：困难场景

依次加入：机械臂遮挡、光照变化、材质变化、深度噪声、相机抖动、目标部分出视野。
每种困难场景都必须验证：低置信度时阻断，而不是输出猜测位置。

### 阶段 4：重新跑 A→B→C→D

至少验证三类结果：

1. 摄像头正确识别绿色方块并完成放置；
2. 摄像头看不到目标时 A 要求澄清，C 不得启动；
3. 摄像头观测和 USD/PhysX 真值偏差超过阈值时，系统报告感知失败，不伪造执行成功。

## 7. 关键结论

当前最合适的路线是“单固定俯视 RGB-D + 仿真 instance segmentation + 深度反投影”，先验证图像感知链路，再增加腕部相机和纯 RGB 检测。这样既能复用现有协议和 A/B/C/D 代码，又能明确区分“摄像头看到的结果”和“仿真真值”，避免把 Ground Truth 误当成真实视觉能力。
