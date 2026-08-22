# Isaac Sim Ground Truth 感知

`modules/perception/isaac_ground_truth.py` 是第一阶段的仿真真值适配器。它不使用摄像头或视觉模型，而是：

1. 场景由 Isaac Sim 创建，物体拥有稳定的 prim 路径 `/World/{object_id}`。
2. 语义 manifest 提供对象类别、尺寸和可执行能力；它不提供运行时位姿。
3. `MotionDriver.read_object_pose()` 从当前 USD/PhysX 状态读取每个物体的真实位姿。
4. 适配器补充空间关系，输出既有的 `perception.v1`，并在 `execution_context` 标明 `backend=isaac_ground_truth`。
5. C 的 `IsaacSimBackend` 直接消费这份快照，执行证据同时保存 `perception.json` 和 `execution.json`。

## 远程验收

校园 VPN 连通后，在仓库根目录运行：

```powershell
.\tools\run_remote_ground_truth_acceptance.ps1 -User stu_01 -Device cuda
```

脚本不会上传任何密钥文件，只上传代码和 `reports/live_chain_ab.json` 策略。成功后本地 `reports/gt-*/` 至少包含：

- `perception.json`：从 Isaac Sim live USD/PhysX 读取的 `perception.v1`；
- `execution.json`：C 后端的 `execution.v1` 及物理位姿前后对比；
- `progress.jsonl`：场景、感知、执行阶段证据。

这一步是“仿真真值闭环”的初版，不等同于摄像头感知。后续替换为相机/检测模型时，只需新增同契约的视觉 provider，不改变 A/B/C/D 接口。
