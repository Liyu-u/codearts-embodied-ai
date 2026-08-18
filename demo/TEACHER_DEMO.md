# 教师展示脚本

## 展示目标

本 Demo 展示真实接入之前的完整软件闭环：

```text
自然语言指令
  → P 感知场景
  → A 意图任务
  → B 安全策略
  → C 原子执行证据
  → D 反馈诊断/修复
  → C 重试或安全停止
  → 前端验收结果
```

P 和 C 当前使用符合正式协议的可控模拟输入/执行后端；A、B、D、总线、协议校验和前端展示均走当前仓库实现。

## 展示前自检

在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File demo/check_acceptance.ps1
powershell -ExecutionPolicy Bypass -File demo/start_demo.ps1 -Port 8766
```

启动脚本会检查端口、启动本地服务，并轮询 `/api/health`。确认浏览器打开：

```text
http://127.0.0.1:8766/
```

## 推荐展示顺序

| 顺序 | 场景 | 要讲清楚的内容 | 期望结果 |
|---|---|---|---|
| 1 | 叠放方块（成功） | P/A/B/C/D 正常路径和五步原子策略 | `SUCCEEDED`，验收 `PASS` |
| 2 | 同名方块（安全阻断） | A 发现两个候选，不猜测目标，C 未进入 | `BLOCKED`，停在 A |
| 3 | 推送动作（策略阻断） | A 能理解 `push`，但 B 按当前能力边界阻断 | `BLOCKED`，停在 B |
| 4 | TraceCoder 修复后重试（成功） | C 首次失败，D 生成 patch，C 第二次执行 | `SUCCEEDED`，重试 1 次 |
| 5 | 抓取持续失败（安全停止） | 恢复耗尽后安全停止，不无限重试 | `SAFE_STOP` |
| 6 | 目标区不可放置（执行失败） | C 返回执行证据，D 保留失败诊断 | `FAILED`，展开详情查看原因 |

每次运行后先看中间的“本次验收”条：

```text
预期：SUCCEEDED · 实际：SUCCEEDED · PASS · 实际结果符合场景预期
```

再展开对应模块查看证据：

- A：动作、目标和阻断原因；
- B：原子策略或 `UNSUPPORTED_ACTION`；
- C：步骤状态、恢复阶段、轨迹和安全事件；
- D：诊断轮次、patch、重试决策和停止原因。

## 现场说明边界

可以明确说明：

1. 当前 Demo 不是静态页面，运行结果来自实际的 P/A/B/C/D 编排和协议对象；
2. C 使用 MockBackend，所以演示的是执行协议、恢复和安全逻辑，不是机械臂物理成功率；
3. 真实相机、Isaac Sim 或机器人执行器接入后，替换 P/C 的适配器，前端和总线协议可以复用；
4. 当前 Demo 已开放 `pick/grasp`、`pick_and_place/place`、`transfer`、明确目标区的 `fetch` 和 `stack`；`stack` 会显示显式 `stack_on` 落点约束。`push`、动态抓取、交接、倒液、等待和自定义动作仍通过安全阻断或澄清展示能力边界。

## 演示结束

最后展示自动化验收结果：

```powershell
powershell -ExecutionPolicy Bypass -File demo/check_acceptance.ps1 -Full
```

全量测试通过后，再关闭本次启动的 PID。不要直接结束不属于本次 Demo 的其他本地服务。
