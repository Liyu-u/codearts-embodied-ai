# Isaac Sim `perception.v1` / `execution.v1` 联调设计

状态：已由 C 模块负责人吴昌庆确认总体方案，待书面复核后进入实施计划。

## 1. 背景

统一联调仓库以 `contracts/v1` 为模块间唯一协议，完整链路是：

```text
perception.v1 → task.v1 → strategy.v1 → execution.v1 → feedback.v1
```

C 模块负责人吴昌庆同时负责：

- `modules/perception/`：把 Isaac Sim 场景转换成 `perception.v1`；
- `modules/executor/`：执行 `strategy.v1`，输出 `execution.v1`；
- `integration/adapters/perception.py` 和 `integration/adapters/executor.py`：向统一流水线暴露 `run()` / `health()`。

目前 B 模块尚无真实 `strategy.v1` 输出。D 模块的 TraceCoder 分支已有一套可运行的策略样例和失败恢复语义，因此第一版以该分支实际使用的五个动作作为临时联调动作集。

Isaac Sim 运行环境有以下现实约束：

- 日常开发和测试在 Windows 的 `huawei` Conda 环境完成；
- Windows 的 `isaacsim` Conda 环境只用于本地 Kit 相关辅助工作；
- 真实 Isaac Sim 6.0.0 运行在校内 Linux 服务器的容器中；
- 服务器不能访问外网，也不允许在线安装或更新；
- 服务器只在需要时开启，不能作为日常 CI 依赖；
- 所有服务器输入必须先在 Windows 生成、校验并上传，结果再下载回本地；
- 当前服务器兼容性探针已通过镜像、GPU 0、资源和磁盘预检查，但因容器缓存挂载目录不可写而未完成真实渲染验收。

## 2. 目标

### 2.1 第一阶段：离线可运行的 Mock 契约闭环

在不启动 Isaac Sim、不连接服务器、不等待 A/B 模块完成的情况下：

1. 实现 `perception.v1` 适配器；
2. 实现只执行白名单动作的 `strategy.v1` 解释器；
3. 实现确定性的 Mock 执行后端；
4. 输出符合 `execution.v1` 的逐步执行证据；
5. 支持 TraceCoder 使用的步骤引用和 `on_failure` 恢复流程；
6. 增加正常、失败、引用错误和安全停止测试；
7. 用 `stacking_cubes` 场景跑通第一条联调用例。

### 2.2 第二阶段：离线 Isaac Sim 后端

在第一阶段接口保持不变的前提下：

1. 修复服务器容器缓存目录权限；
2. 完成 Isaac Sim 兼容性探针；
3. 将同一份 `strategy.v1` 作为离线作业输入；
4. 在服务器单次启动 Kit 后执行策略；
5. 下载并验证 `execution.v1`、轨迹、截图和日志；
6. 再扩展到杯子排列和颜色分类场景。

## 3. 非目标

第一版不做以下工作：

- 不执行 `strategy.v1.code` 中的任意 Python；
- 不接真实机械臂；
- 不要求 CI 或其他队友安装 Isaac Sim；
- 不恢复旧仓库的整个 `src/isaac` 目录；
- 不把服务器 IP、账号、端口、个人绝对路径或密码提交到 GitHub；
- 不替 A、B、D 重写其内部模块；
- 不收紧或破坏 `contracts/v1` 已有必填字段；
- 不把固定三场景验证脚本伪装成通用策略执行器。

## 4. 方案比较与决定

### 4.1 采用：适配器 + 可替换后端

```text
strategy.v1
    ↓
ExecutorAdapter
    ↓
安全策略解释器
    ↓
┌────────────────┬───────────────────────┐
│ MockBackend     │ OfflineIsaacBackend   │
│ 日常联调与 CI   │ 校内服务器真实仿真    │
└────────────────┴───────────────────────┘
    ↓
execution.v1
```

选择原因：接口稳定、可测试、服务器离线时仍可开发，并且不会让队友依赖 C 模块内部实现。

### 4.2 不采用：整体迁入旧 `src/isaac`

整体迁入虽然快，但会同时带入旧接口、固定场景脚本、Kit/Mock 混合逻辑和服务器工具，重新造成模块耦合，也与远端仓库本次“统一契约 + 适配器”的重构方向冲突。

### 4.3 不采用：通过个人路径调用外部项目

从统一仓库通过环境变量或绝对路径调用 `huaweijbgs` 会让其他成员和 CI 无法复现，并可能泄露个人目录结构。因此只允许在迁移期间作为人工参考，不作为正式实现。

## 5. 模块边界

### 5.1 `modules/perception`

职责：

- 接收后端提供的原始场景对象；
- 生成稳定的物体 ID；
- 统一坐标系和单位；
- 补充执行能力信息；
- 把预定义的安全目标区作为虚拟对象加入场景；
- 输出 `perception.v1`。

它不负责理解自然语言，也不选择要操作的目标。

### 5.2 `modules/executor`

职责：

- 校验 `strategy.v1` 的 `task_id`、步骤结构和动作白名单；
- 解析 `$step_id.field` 步骤引用；
- 调用 Mock 或 Isaac 后端；
- 执行 `on_failure` 恢复步骤；
- 触发失败或安全停止时保留完整证据；
- 输出 `execution.v1`。

它不生成策略，不静默修正未知动作，也不执行任意代码。

### 5.3 `integration/adapters`

适配器只做协议边界工作：

```python
run(input_json: dict) -> dict
health() -> dict
```

适配器负责：

- 输入 Schema 校验；
- 调用对应模块服务；
- 输出 Schema 校验；
- 把协议错误转换成明确、可追踪的失败。

适配器不重新实现机器人动作。

## 6. `perception.v1` 设计

### 6.1 坐标约定

- 长度单位：米；
- 默认坐标系：`world`；
- `coordinate_frame` 必须显式填写；
- 不允许 A/B 自己猜测坐标系；
- 真正切换到机械臂基坐标系时使用新的明确值，例如 `robot_base`，不能改变 `world` 的含义。

### 6.2 物体 ID

物理对象 ID 在同一个 `scene_id` 内稳定。第一版从场景定义中的英文稳定键生成，而不是使用中文显示名称或运行时数组序号。

示例：

```json
{
  "schema_version": "perception.v1",
  "scene_id": "stacking_cubes",
  "coordinate_frame": "world",
  "objects": [
    {
      "id": "green_cube",
      "category": "cube",
      "pose": {"x": 0.25, "y": 0.0, "z": 0.12},
      "dimensions": {"x": 0.04, "y": 0.04, "z": 0.04},
      "attributes": {
        "display_name": "绿色方块",
        "color": "green"
      },
      "execution": {
        "movable": true,
        "graspable": true
      }
    }
  ],
  "execution_context": {
    "backend": "mock",
    "scene_revision": "1"
  }
}
```

### 6.3 虚拟目标区

没有实体模型的安全放置位置也通过对象 ID 暴露：

```json
{
  "id": "zone_unstack_target",
  "category": "target_zone",
  "pose": {"x": 0.4, "y": 0.0, "z": 0.03},
  "attributes": {"purpose": "safe_placement"},
  "execution": {
    "movable": false,
    "graspable": false,
    "valid_destination": true
  }
}
```

这样 `task.v1.destination_id` 和 `strategy.v1` 只传稳定 ID，不需要上游生成裸坐标。

## 7. 第一版动作目录

第一版只支持以下五个动作。未知动作在执行前被拒绝。

### 7.1 `detect_object`

输入参数：

```json
{"object_id": "green_cube"}
```

兼容 TraceCoder 当前样例：

```json
{"object_name": "green_cube"}
```

规则：`object_id` 优先；`object_name` 只作为过渡别名。若名称匹配到多个对象，返回失败，不自动选第一个。

成功输出：

```json
{"status": "SUCCESS", "object_id": "green_cube"}
```

### 7.2 `move_to_object`

输入参数：

```json
{"object_id": "$detect_green.object_id"}
```

行为：解析对象 ID、检查可达性和工作空间，然后移动到对象上方的安全接近点。

### 7.3 `grasp`

输入参数：

```json
{"object_id": "$detect_green.object_id"}
```

行为：从安全接近点下降、闭合夹爪、验证抓取并抬升。若当前没有先接近该对象，返回失败。

### 7.4 `move_to_target`

规范输入参数：

```json
{"destination_id": "zone_unstack_target"}
```

兼容 TraceCoder 当前样例：

```json
{"target": "zone_unstack_target"}
```

规则：目标必须存在于本次 `perception.v1`，并且 `execution.valid_destination` 为 `true`。第一版不允许上游直接提交任意目标坐标。

### 7.5 `release`

输入参数：

```json
{}
```

行为：确认夹爪持有对象，在目标区释放，然后安全撤离。没有持有对象时返回失败。

## 8. 步骤引用

解释器支持 TraceCoder 已使用的引用格式：

```text
$<step_id>.<result_path>
```

例如：

```json
{"object_id": "$detect_green.object_id"}
```

解析规则：

1. 被引用步骤必须已经执行；
2. `step_id` 必须唯一；
3. 路径必须存在；
4. 不允许引用未来步骤；
5. 引用失败时该步骤返回 `FAILED`，原因以 `UNRESOLVED_REFERENCE` 开头；
6. 引用失败不能被静默替换为原字符串。

## 9. 失败恢复与安全停止

支持 TraceCoder 的 `on_failure` 结构：

```json
{
  "max_attempts": 2,
  "steps": [
    {
      "step_id": "retry_grasp",
      "action": "grasp",
      "arguments": {"object_id": "$detect_green.object_id"}
    }
  ],
  "on_exhausted": "stop"
}
```

语义：

1. 主步骤失败后才进入恢复；
2. 每轮按顺序执行恢复步骤；
3. `max_attempts` 第一版限制为 1–3；
4. 恢复步骤仍受同一动作白名单和安全检查约束；
5. 任意恢复步骤失败，则本轮恢复失败；
6. 恢复耗尽且 `on_exhausted == "stop"` 时执行内部安全停止；
7. 第一版只接受 `stop`，其他取值在执行前拒绝；
8. 安全停止后不再执行剩余主步骤；
9. 主步骤失败且没有 `on_failure` 时立即停止主流程，顶层状态为 `FAILED`，剩余主步骤记录为 `SKIPPED`；
10. 恢复成功时继续下一个主步骤，不重复执行已由恢复步骤替代的失败动作；
11. 主步骤、恢复步骤和安全停止都进入 `execution.v1.steps`。

为避免策略造成无限工作量，第一版同时限制：

- 主步骤最多 50 个；
- 单步恢复步骤最多 10 个；
- 恢复最多 3 轮；
- 总动作调用最多 100 次；
- 超过限制直接 `SAFE_STOP`。

## 10. `execution.v1` 设计

### 10.1 顶层状态

- `SUCCEEDED`：所有必要主步骤完成，未触发安全停止；
- `FAILED`：发生业务失败或输入引用错误，但没有安全门禁事件；
- `SAFE_STOP`：策略不安全、恢复耗尽、超限或后端要求停止。

### 10.2 步骤记录

每个动作调用至少记录：

```json
{
  "step_id": "grasp_green",
  "phase": "main",
  "action": "grasp",
  "arguments": {"object_id": "green_cube"},
  "status": "SUCCESS",
  "reason": null,
  "duration_ms": 120
}
```

`phase` 可取：

- `main`；
- `recovery_1`、`recovery_2`、`recovery_3`；
- `safe_stop`。

步骤状态使用 `SUCCESS` / `FAILED` / `SKIPPED`。顶层状态继续使用现有 Schema 规定的 `SUCCEEDED` / `FAILED` / `SAFE_STOP`。

### 10.3 完整输出示例

```json
{
  "schema_version": "execution.v1",
  "task_id": "stacking-demo-001",
  "status": "SUCCEEDED",
  "steps": [],
  "trajectory_points": [
    {
      "timestamp_ms": 0,
      "coordinate_frame": "world",
      "position": {"x": 0.0, "y": 0.0, "z": 0.35}
    }
  ],
  "total_duration_ms": 1200,
  "safety_events": []
}
```

### 10.4 安全事件

安全事件至少包含：

```json
{
  "type": "RECOVERY_EXHAUSTED",
  "severity": "error",
  "step_id": "grasp_green",
  "message": "grasp recovery exhausted; executor entered safe stop"
}
```

第一版标准事件类型：

- `UNKNOWN_ACTION`；
- `INVALID_ARGUMENT`；
- `UNRESOLVED_REFERENCE`；
- `OBJECT_NOT_FOUND`；
- `OBJECT_NOT_REACHABLE`；
- `INVALID_DESTINATION`；
- `WORKSPACE_VIOLATION`；
- `RECOVERY_EXHAUSTED`；
- `ACTION_LIMIT_EXCEEDED`；
- `BACKEND_ERROR`。

## 11. 第一个端到端样例

场景：`stacking_cubes`。

任务：把绿色方块移动到安全目标区。

```json
{
  "schema_version": "strategy.v1",
  "task_id": "stacking-demo-001",
  "steps": [
    {
      "step_id": "detect_green",
      "action": "detect_object",
      "arguments": {"object_id": "green_cube"}
    },
    {
      "step_id": "approach_green",
      "action": "move_to_object",
      "arguments": {"object_id": "$detect_green.object_id"}
    },
    {
      "step_id": "grasp_green",
      "action": "grasp",
      "arguments": {"object_id": "$detect_green.object_id"},
      "on_failure": {
        "max_attempts": 1,
        "steps": [
          {
            "step_id": "retry_grasp_green",
            "action": "grasp",
            "arguments": {"object_id": "$detect_green.object_id"}
          }
        ],
        "on_exhausted": "stop"
      }
    },
    {
      "step_id": "move_target",
      "action": "move_to_target",
      "arguments": {"destination_id": "zone_unstack_target"}
    },
    {
      "step_id": "release_green",
      "action": "release",
      "arguments": {}
    }
  ],
  "code": null
}
```

Mock 后端执行成功后必须把 `green_cube` 的位置更新为目标区位置，并通过最终状态断言，而不是只因为动作函数返回成功就判定任务成功。

## 12. Mock 后端

Mock 后端维护一个确定性的内存状态：

- 对象位置；
- 当前接近对象；
- 当前夹持对象；
- 末端执行器位置；
- 轨迹点；
- 可注入的单次或持续失败。

它必须执行真实状态迁移，并拒绝不合法顺序，例如：

- 未接近对象直接抓取；
- 未抓取对象直接释放；
- 抓取 A 后却移动 B；
- 把物体移动到非目标区；
- 引用不存在步骤；
- 恢复耗尽后继续执行。

Mock 的意义是验证协议、状态机和失败恢复，不用于宣称真实机械臂运动已经通过。

## 13. 离线 Isaac 后端

第二阶段保持适配器签名不变，将执行分成三部分：

1. Windows 生成离线作业：源代码、`perception.json`、`strategy.json`、清单和 SHA-256；
2. Linux 服务器容器执行：网络关闭、GPU 0、Isaac Sim 6.0.0、只读源代码与资源、可写结果目录；
3. Windows 下载结果：校验 `execution.json`、截图、轨迹和日志。

统一仓库只提交通用配置模板，不提交真实服务器地址、账号、SSH 端口或密码。

当前固定三场景批处理继续作为 Isaac 运行时验证工具；通用 `strategy.v1` 执行将使用独立入口，不能修改固定场景结果来冒充动态策略执行。

## 14. 当前服务器探针问题

已确认：

- 源包 SHA-256 正确；
- Isaac Sim 6.0.0 镜像存在；
- GPU 0 可用；
- 离线资产存在；
- `/data` 空间充足；
- Kit 已进入扩展启动阶段。

失败边界：绑定到 `/isaac-sim/.cache`、`Documents`、`.local/share/ov/data` 和日志目录的宿主机目录不可由容器运行用户写入，导致 Shader Cache、Derived Data Cache、用户配置和 RTX texture cache 创建失败。

修复前先用只读命令确认镜像默认用户 UID/GID 与宿主机目录属主。修复只调整本项目专属缓存目录，不改变 `/data` 其他目录权限，不使用递归 `chmod 777`。修复后必须重新生成新 run ID 并完整重跑兼容性探针。

## 15. 代码迁移原则

从现有 C 模块实现中按能力迁移，不整目录复制：

- 从 `get_scene_json.py` 复用场景枚举和物体属性提取思路；
- 从 `exec_wrapper.py` 复用元动作、轨迹和安全断言；
- 从 `action_library.py` 复用安全接近、抓取、释放的运动组合；
- 从批处理代码复用原子结果写入、基础设施错误分类和单 Kit 生命周期；
- 服务器上传脚本继续留在 C 的开发项目，联调仓库只接收经过脱敏和通用化的离线接口实现。

## 16. 测试设计

### 16.1 契约测试

- perception 输出符合 `perception.schema.json`；
- executor 输入符合 `strategy.schema.json`；
- execution 输出符合 `execution.schema.json`；
- `task_id` 原样贯穿；
- 示例 JSON 可解析且通过 Schema。

### 16.2 单元测试

- 五个动作的参数校验；
- 步骤引用成功和失败；
- 重复 `step_id`；
- 未知动作；
- 非空 `strategy.code` 被拒绝；
- 恢复成功；
- 恢复耗尽后安全停止；
- 动作次数上限；
- 虚拟目标区校验。

### 16.3 集成测试

- `stacking_cubes` Mock 成功闭环；
- 目标不存在返回 `FAILED`；
- 抓取持续失败返回 `SAFE_STOP`；
- 输出可直接作为 TraceCoder 的 `execution.v1` 输入；
- 没有 TraceCoder 适配器时流水线仍能返回 execution。

### 16.4 真实 Isaac 验收

- 兼容性探针全部通过；
- 截图存在、非空且可见；
- GPU 0 被实际使用；
- 真实策略执行生成 `execution.v1`；
- Mock 与 Isaac 对同一输入保持相同顶层语义；
- 服务器全程不访问外网。

## 17. 预期仓库变更

```text
modules/perception/
├── __init__.py
├── mock_scene.py
├── service.py
└── README.md

modules/executor/
├── __init__.py
├── action_catalog.py
├── models.py
├── strategy_interpreter.py
├── mock_backend.py
└── README.md

integration/adapters/
├── perception.py
└── executor.py

testdata/daily/
├── stacking_scene.json
└── stacking_strategy.json

tests/contract/
├── test_perception_adapter.py
└── test_execution_adapter.py

tests/integration/
└── test_mock_isaac_pipeline.py

docs/
└── Isaac执行器接口说明.md
```

第一阶段不修改公共流水线的调用顺序，不依赖尚未合并的 TraceCoder 分支。

## 18. 验收标准

### 第一阶段完成

- 五个白名单动作均有明确参数和返回值；
- Mock 场景输出合法 `perception.v1`；
- Mock 执行器输出合法 `execution.v1`；
- 步骤引用、恢复流程和安全停止有自动化测试；
- 第一个 `stacking_cubes` 用例重复执行结果一致；
- 文档能让 A、B、D 明确各自需要传什么、会收到什么；
- 不依赖 Isaac Sim、服务器、网络或 API Key。

### 第二阶段完成

- 服务器缓存权限问题通过最小权限修复解决；
- 兼容性探针产生成功报告和可见截图；
- 同一份策略在真实 Isaac 后端产生 `execution.v1`；
- 日志证明只启动一次 Kit、只使用 GPU 0、没有访问外网；
- 结果下载并保存在本地验收目录。

## 19. 协作与上传门禁

实施基于远端最新 `main` 的独立分支 `feature/executor-isaac-v1`，不覆盖旧工作区的未提交内容。

顺序固定为：

1. 用户复核本设计；
2. 编写详细实施计划；
3. 测试先行完成第一阶段；
4. 展示代码差异、接口样例和测试结果；
5. 用户确认理解且同意上传；
6. 才执行 Git 提交、推送并准备 Pull Request 说明。

未经第 5 步明确确认，不向 GitHub 上传代码或文档。
