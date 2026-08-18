# 闭环系统前端 Demo

这是一个不依赖前端构建工具的本地演示页。它通过 `demo/server.py` 调用仓库现有的感知、意图、策略、C Mock 执行器和 TraceCoder 反馈适配器；页面只负责把每个协议环节按顺序展示出来。HTTP 服务层是标准库，但真实适配器依赖根目录 `requirements.txt`。

## 启动

在仓库根目录执行：

```bash
python -m pip install -r requirements.txt
python demo/server.py
```

然后打开 <http://127.0.0.1:8765/>。如果端口冲突，可以设置 `DEMO_PORT`。

## 页面能演示什么

- 预设题目：单独抓取、普通放置、搬运、堆叠、目标歧义、目标不存在、缺少目标区、当前仍不支持的推送动作、C 一次抓取失败后恢复、C 持续失败安全停止、目标区不可放置，以及多色方块分拣工作站；每个场景都显示二维预览和对应模块重点。
- “多色方块分拣工作站”同一环境提供三条成功指令和三条安全/能力边界指令，可用自然语言按钮快速切换，也可以直接编辑指令。
- 自然语言输入：可编辑指令，也可以点击当前场景的快捷指令。
- 模块看板：上层展示预设环境、策略生成、TraceCoder 反馈；下层展示环境感知 JSON、自然语言输入和 C Mock 仿真执行，中间用箭头表示信息流转。
- 摘要优先：每个模块表面只显示状态、数量、耗时等关键指标；点击“查看详情”后才展开完整协议字段、动作列表或反馈诊断。
- C 模块可视化：表面展示物体/目标区、夹爪位置和轨迹；展开后查看动作状态、耗时、持物状态和安全事件。
- 故障演示：选择“TraceCoder 修复后重试（成功）”可看到无动作级恢复的初始策略、C 首次抓取失败、D 生成恢复 patch、C 第二次执行成功；选择“抓取失败后恢复”可看到 B 已提供的 `recovery_1` 在同一次 C 执行内成功；选择“抓取持续失败”可看到 `SAFE_STOP` 和 `RECOVERY_EXHAUSTED`。D 卡片会展示每次 C 尝试、诊断轮次、patch 和重试决策。
- 阻断场景：意图无法唯一绑定或缺少目的地时停在 A，不会伪造 C 的执行结果。

## 页面中的英文标识

页面保留协议和动作的原始英文编号，旁边同步显示中文解释，便于演示人员看懂、开发人员对照接口：

- `perception.v1`：感知数据协议；`task.v1`：意图任务协议。
- `strategy.v1`：策略协议；`execution.v1`：执行结果协议；`feedback.v1`：反馈诊断协议。
- `MockBackend`：C 模块的模拟执行后端；`Mock`：模拟执行。
- `READY`：已就绪；`SUCCEEDED`：执行成功；`BLOCKED`：安全阻断；`SAFE_STOP`：安全停止。
- `pick/grasp`、`transfer`、`fetch`、`stack`：分别表示单独抓取、搬运、取物到明确目标区、堆叠；底层仍由 `detect_object`、`move_to_object`、`grasp`、`move_to_target`、`release` 五个 C 原子动作执行。

## 自动化验收

除了前端人工点选演示，仓库还提供三层可重复验收：

```bash
# 18 道闭环题：覆盖正常、阻断、已开放动作、恢复、安全停止和 D 修复重试
python -m unittest tests.e2e.test_closed_loop_acceptance -v

# Demo 质量题：检查模块边界、执行证据、最终场景状态和确定性
python -m unittest tests.e2e.test_demo_quality -v

# HTTP/静态前端题：检查健康接口、场景目录、运行接口、错误请求和资源服务
python -m unittest tests.e2e.test_demo_http -v
```

前端浏览器验收至少应覆盖：一个正常成功场景、一个 A 阶段安全阻断场景、
一个 TraceCoder 修复后重试场景、一个 SAFE_STOP 场景和一个执行失败诊断场景。
下拉框中的“成功/阻断/失败”是场景预期；真正的验收结果以运行后 P/A/B/C/D
返回的实际状态、执行证据和反馈诊断为准。

教师展示的固定讲解顺序、启动自检和现场边界说明见
[`TEACHER_DEMO.md`](TEACHER_DEMO.md)。也可以直接使用：

```powershell
powershell -ExecutionPolicy Bypass -File demo/start_demo.ps1 -Port 8766
powershell -ExecutionPolicy Bypass -File demo/check_acceptance.ps1 -Full
```

要让 Demo 的 B 模块使用真实 CodeArts（C 仍为本地 Mock 执行器），先确认 AK/SK 已配置到当前 Windows 用户环境，然后运行：

```powershell
powershell -ExecutionPolicy Bypass -File demo/start_demo.ps1 `
  -Port 8766 `
  -CodeArtsMode required `
  -CodeArtsPolicy quality `
  -CodeArtsModel huaweicloud-maas/openpangu-2.0-flash
```

验收时可将 `-CodeArtsPolicy quality` 改为 `max`，要求规划后的两轮独立审查都通过。

结果区采用参考图的“上三模块 + 中间进度箭头 + 下三模块”布局；当前闭环状态和 P/A/B/C/D 进度节点都显示在中间箭头上。在较窄屏幕上会自动改为单列，避免模块内容被挤压。

## 与真实系统的边界

Demo 默认保持 `CODEARTS_STRATEGY_MODE=off` 以保证离线可复现；通过上面的 `-CodeArtsMode required` 可切换到真实 CodeArts 策略生成。TraceCoder 使用离线规则/HLLM 经验逻辑；C 使用当前仓库的 `MockBackend`。`tracecoder_repair` 专用场景会隔离每次请求的经验库，并故意移除 B 的动作级恢复，以确保演示的是 D→C 的真实修复闭环，而不是 C 在单次执行内自恢复。未来接入真实前端时，只需把 `POST /api/run` 换成网关接口，页面的协议展示和状态组件可以复用。生产环境仍应保留人工确认、权限校验和真机急停，不应把此 Demo 当作控制台。
