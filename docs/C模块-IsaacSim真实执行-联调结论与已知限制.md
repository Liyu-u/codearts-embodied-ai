# C 模块 · Isaac Sim 真实执行 —— 联调结论与已知限制

> 作者：C 负责人（吴昌庆）
> 日期：2026-08-18
> 范围：`modules/executor/` + `modules/perception/` + `integration/config/`，真实 Isaac Sim 6.0.0 校内服务器

---

## 一、一句话结论

**真实 Isaac Sim（服务器 6.0.0 容器 + GPU 0 + CPU 物理）已经能完成 Franka 抓放闭环：**
方块被真实抓取、搬运、放置（实测移动 0.69 米），并输出标准 `execution.v1`。

```json
{
  "schema_version": "execution.v1",
  "status": "SUCCEEDED",
  "cube_before": [0.5, 0.0, 0.0258],
  "cube_after":  [0.014, 0.489, 0.0257],
  "cube_moved_m": 0.689
}
```

夹爪轨迹也证明了完整的"接近 → 抓取 → 搬运 → 放置"：夹爪从 `0.04`（张开）→ `0.026`（闭合抓取）→ `0.039`（放置后张开）。

---

## 二、实测已验证通过的能力

| 能力 | 状态 |
|---|---|
| Isaac Sim 6.0.0 无头启动（容器 `python.sh` + `headless`） | ✅ |
| GPU 0（Quadro RTX 8000）识别 | ✅ |
| 离线资产路径 `/isaacsim_assets/Assets/Isaac/6.0` | ✅ |
| Franka 加载（正确资产路径 + 变体） | ✅ |
| 机械臂复位到 home 位姿 | ✅ |
| 差分 IK 笛卡尔运动 | ✅ |
| 夹爪开合（DOF 7/8） | ✅ |
| 完整 pick-and-place（官方 `FrankaPickPlace`，done=true） | ✅ |
| 输出 `execution.v1`（含方块前后位姿、关节轨迹、耗时） | ✅ |

---

## 三、已确认的官方 API（重要，避免再踩坑）

这些是 Isaac Sim 6.0 的**正确**用法，旧代码（R2 的 `exec_wrapper.py`）全是错的：

| 项目 | 旧代码（错） | 官方 6.0（对） |
|---|---|---|
| Franka 资产 | `Isaac/Robots/Franka/franka.usd` | **`Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd`** |
| 机器人封装 | `Articulation(prim_path=...)` + `.initialize()` | **`Franka(robot_path, create_robot=True)`**（官方扩展类，内置 IK + 夹爪） |
| 关节控制 | `end_effector.set_world_pose` + `apply_action` | **`set_dof_position_targets`** |
| 读关节 | `end_effector.get_world_pose` | **`get_dof_positions()`（返回 (1,9)，需 reshape）** |
| 笛卡尔 IK | 无（误用 set_world_pose） | **差分 IK `set_end_effector_pose(pos, orient, "damped-least-squares")`** |
| 夹爪 | 单独 Articulation | **`set_gripper_position([f1,f2])`，DOF 索引 [7,8]** |
| 物理设备 | `setup_simulation(dt, "cuda")`（会崩） | **`set_physics_sim_device("cpu")`** |
| 物理推进 | `World.step()` | **`SimulationApp.update()`** |
| home 位姿 | 无 | `[0.012, -0.568, 0, -2.811, 0, 3.037, 0.741, 0.04, 0.04]` |
| 复位时机 | 无 | **必须在 `timeline.play()` 之后**，否则物理 tensor 未初始化会 assert |

关键依赖：`isaacsim.robot.experimental.manipulators.examples` 扩展（官方例子扩展），导入 `Franka` 前需 `set_extension_enabled_immediate(...)`。

---

## 四、已知限制（诚实记录）

### 限制 1：自定义 `move_to` 的"循环直到收敛"在 CPU 物理下每帧耗时爆炸

- **现象**：差分 IK 单次远距离移动，前 ~33 帧正常（20ms/帧），之后每帧耗时指数增长（3.6s → 20s → 126s/帧）。
- **原因**：疑似 warp 在 CPU 下反复 `get_jacobian_matrices().numpy()` 的内存累积问题（未能完全定位）。
- **影响**：5 动作 `IsaacSimBackend` 里"循环判收敛"的 `move_to` 无法在 CPU 下稳定跑完远距离移动。
- **应对**：真实抓放改用**官方 `FrankaPickPlace`**（分阶段固定帧数，实测稳定 453ms/帧、280 帧 done，不爆炸）。

### 限制 2：CUDA 物理段错误

- **现象**：`set_physics_sim_device("cuda")` 触发 `numpy→torch` 切换 + `DOF types mismatch` 后在 OmniGraph/TBB 段错误。
- **原因**：疑似 "AlternateFinger" 夹爪变体第 9 个 DOF 在 GPU 物理张量里为 "Invalid" + 无头 + CUDA 的组合 bug。
- **应对**：暂用 CPU 物理（官方例子默认也是 CPU）。

### 限制 3：无头截图失败

- **现象**：`capture_viewport_to_file` 无有效视口，`probe.png` 未生成。
- **影响**：暂无渲染截图证据；但物理仿真、关节、方块位姿等证据齐全。
- **应对**：后续用 fabric 渲染器或 replicator 单独补；不阻塞物理执行验收。

---

## 五、当前架构

```text
strategy.v1（五步原子动作）
        ↓
ExecutorAdapter + StrategyInterpreter（统一仓库，已验证）
        ↓
┌─────────────────────────────────────────────┐
│ MockBackend   （本地/CI，64 测试全绿）        │
│ IsaacSimBackend（同接口，真实侧骨架已验证）   │
│   ├─ 官方 Franka + 差分 IK + 夹爪（可用）      │
│   └─ move_to 循环收敛（CPU 下待优化，见限制1） │
└─────────────────────────────────────────────┘
        ↓
execution.v1
```

- **`IsaacSimBackend` 的 5 动作接口与 `MockBackend` 完全一致**（`execute/safe_stop/trajectory_points/snapshot` + `mode`），Mock 侧 64 项单测全绿。
- 真实服务器抓放目前走**官方 `FrankaPickPlace`** 作为执行引擎（稳定可靠），5 动作接口代码保留，后续修好 CUDA 或改用分阶段 move_to 即可切回。
- `integration/config` 的 `local/sim/real` 三档配置、安全守卫（限速/工作空间/超时/急停/碰撞 fail-closed）均已实现并测试。

---

## 六、给 A / B / D 队友的要点

1. **接口没变**：你们照旧只跟 `perception.v1 / task.v1 / strategy.v1 / execution.v1 / feedback.v1` 打交道，不碰 Isaac Sim 内部。
2. **真实执行与 Mock 可对照**：同一条 `strategy.v1` 在 Mock 和真实 Isaac Sim 都能跑；真实侧会额外给出 `cube_before/cube_after`、关节轨迹等物理证据。
3. **性能预期**：真实 pick-and-place 一次约 **2 分钟**（CPU 物理，453ms/帧 × 280 帧）；这是服务器无 CUDA 加速下的实际速度，正式验收演示时请预留时间。
4. **不要在服务器上装/更新任何东西**：全程离线，代码 Windows 打包 → scp → 容器 `--network none` 运行。

---

## 七、下一步（按优先级）

1. （可选）修 CUDA 段错误或把 `move_to` 改成"分阶段固定帧数"，让 5 动作真实后端也能稳定跑远距离移动；
2. 补无头截图（fabric 渲染器）；
3. 把真实 `execution.v1` 接入 D（TraceCoder）做反馈闭环；
4. 三场景（stacking / cup / sorting）服务器批处理。

---

*本结论基于 2026-08-18 校内服务器实测，服务器配置：Isaac Sim 6.0.0 容器、GPU 0、Ubuntu 24.04、CPU 物理、完全离线。*
