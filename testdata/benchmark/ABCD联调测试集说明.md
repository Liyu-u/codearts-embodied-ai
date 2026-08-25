# A/B/C/D 全链路自研联调测试集 v1

文件：`abcd_closed_loop_v1.json`

这套测试集用于衡量整个系统从感知事实、自然语言意图、策略生成、执行器到 TraceCoder 反馈修复的闭环连调水平。它不是单纯的机器人动作成功率测试，也不是训练集；每道题都应保留原始 `task`、`strategy`、`execution`、`feedback` 和重试证据。

## 设计依据

测试设计吸收公开基准的方法，但不直接把公开基准的最终分数当作本项目分数：

- ALFRED：自然语言目标、空间指代、长链任务；
- TEACh：目标不完整时的澄清、交互式任务边界；
- LIBERO/CALVIN：语言条件动作链、抓取/搬运/堆叠等多任务组合；
- BEHAVIOR-1K/RoboCasa365：多对象、多目的地、属性匹配和场景变化；
- failure-aware recovery：故障注入、恢复成功、恢复耗尽和安全停止。

最终判定仍以本项目的 `perception.v1`、`task.v1`、`strategy.v1`、`execution.v1`、`feedback.v1` 和 B/C/D 共用安全策略为准。

## 题集规模

| 类别 | 数量 | 重点 |
|---|---:|---|
| `happy_path` | 20 | 单目标、搬运、取物、堆叠、分拣、指令变体和多对象场景 |
| `intent_safety` | 12 | 歧义、目标不存在、目的地缺失、属性不匹配和澄清 |
| `capability_boundary` | 10 | 支持动作、未支持动作、策略阻断和不进入 C |
| `recoverable_failure` | 8 | 动作级恢复、D patch、策略重新校验和成功重试 |
| `safe_stop` | 6 | 恢复耗尽后的 `SAFE_STOP` 和不可继续反馈 |
| `execution_failure` | 8 | 抓取、搬运、释放故障及错误结果禁止冒充成功 |
| **总计** | **64** |  |

每道题带有固定 `seed`。当前 Mock 后端使用确定性场景；远程 Isaac Sim 批量验收应复用同一 `case_id` 和 seed，并把真实感知/执行证据合并到同一份报告中。

## 运行方式

离线基线：

```powershell
python tools/run_closed_loop_benchmark.py `
  --manifest testdata/benchmark/abcd_closed_loop_v1.json `
  --mode baseline `
  --repeats 1 `
  --output reports/abcd_closed_loop_v1_baseline.json
```

稳定性测试：

```powershell
python tools/run_closed_loop_benchmark.py `
  --manifest testdata/benchmark/abcd_closed_loop_v1.json `
  --mode baseline `
  --repeats 3 `
  --output reports/abcd_closed_loop_v1_baseline_repeat3.json
```

真实 CodeArts 对照测试必须显式允许云端调用：

```powershell
$env:CODEARTS_BENCHMARK_ALLOW_LIVE = '1'
python tools/run_closed_loop_benchmark.py `
  --manifest testdata/benchmark/abcd_closed_loop_v1.json `
  --mode codearts `
  --repeats 1 `
  --policy quality `
  --pure `
  --output reports/abcd_closed_loop_v1_codearts.json
```

也可运行：

```powershell
make abcd-benchmark
```

## 交付判定建议

正式报告至少检查：

- 所有协议契约通过率 100%；
- 需要阻断的题目不能进入 C；
- `strategy.code` 保持 `null`；
- `SAFE_STOP` 题正确率 100%；
- D 要求修复的题目必须真的发生反馈、patch、重新校验和重试；
- 最终成功必须有执行证据和最终场景状态，不能只看模型文本；
- 每道题重复运行后语义签名保持一致，并单独统计端到端、CodeArts 和 TraceCoder P95 延迟。

当前题集是 v1：它优先覆盖现有 Mock/Isaac Ground Truth 闭环。接入真实 RGB-D 相机后，应在相同 case_id 下增加遮挡、深度噪声、低置信度和真值偏差超过阈值的观测变体；接入真机后再增加碰撞、超时、抓取滑落和通信中断的硬件在环变体。
