# 闭环量化评测题集

这套题集面向参赛作品的量化评测，不替代原有的单元、契约和协议验收。

面向 A/B/C/D 全链路联调的自研 v1 题集见：[ABCD联调测试集说明](ABCD联调测试集说明.md) 和 [abcd_closed_loop_v1.json](abcd_closed_loop_v1.json)。它在本 README 所述基准的基础上增加了公开具身智能基准中的语言落地、对话澄清、多任务组合和失败恢复测试思想，并通过 `--manifest` 参数独立运行。

## 题集结构

当前共 30 道题：

| 类型 | 数量 | 验证内容 |
|---|---:|---|
| `happy_path` | 10 | 多场景成功任务和实体消歧后的成功执行 |
| `intent_safety` | 5 | 歧义、目标缺失、目标不存在和不可放置目标 |
| `capability_boundary` | 5 | 未覆盖动作、缺少交付信息和自定义动作阻断 |
| `recoverable_failure` | 3 | TraceCoder修复或动作级恢复（其中 2 道明确要求 D 生成 patch） |
| `safe_stop` | 2 | 恢复耗尽后的安全停止 |
| `execution_failure` | 5 | 执行器故障注入后的失败诊断 |

每次运行会记录：

- `task_id`、请求编号和测试题编号；
- P/A/B/C/D结果；
- CodeArts provider、request_id、策略档位、critic轮次和回退情况；
- 策略动作、`strategy.v1`契约结果和`code=null`结果；
- 执行状态、步骤数、安全事件、重试次数和停止原因；
- 可用于回放的 task、strategy、execution、feedback 原始对象。

## 离线基线

离线基线使用本地确定性策略和Mock执行器，不调用真实CodeArts：

```powershell
python tools/run_closed_loop_benchmark.py `
  --mode baseline `
  --repeats 1 `
  --output reports/closed_loop_benchmark_baseline.json
```

稳定性测试：

```powershell
python tools/run_closed_loop_benchmark.py `
  --mode baseline `
  --repeats 3 `
  --output reports/closed_loop_benchmark_baseline_repeat3.json
```

## 真实 CodeArts

真实CodeArts运行会产生云端调用和较长延迟，建议先运行单道题，再扩大范围。运行前确认本地CodeArts凭证已经配置：

```powershell
python tools/run_closed_loop_benchmark.py `
  --mode codearts `
  --repeats 1 `
  --policy quality `
  --pure `
  --model huaweicloud-maas/openpangu-2.0-flash `
  --output reports/closed_loop_benchmark_codearts.json
```

基线与CodeArts对照运行会主动调用真实CodeArts，为避免误触发，必须显式确认：

```powershell
$env:CODEARTS_BENCHMARK_ALLOW_LIVE = '1'
python tools/run_closed_loop_benchmark.py `
  --compare `
  --repeats 1 `
  --policy quality `
  --pure `
  --output reports/closed_loop_benchmark_compare.json
```

## 指标解释

- `pass_rate`：测试运行结果是否符合每道题预期；
- `case_stability_rate`：同一道题重复运行的语义结果是否一致；
- `strategy_contract_pass_rate`：策略通过契约校验的比例；
- `provider_calls`：真实调用CodeArts的次数；
- `fallback_count`：CodeArts失败后回退本地策略的次数；
- `execution_success_rate`：真正进入执行器的任务中成功的比例；
- `repair_success_rate`：明确标记 `requires_d_repair=true` 的失败任务中，最终成功且发生重试的比例；动作级自恢复单独保留在 `recoverable_failure` 类别中，不冒充 D 修复。
- `safe_stop_correct_rate`：预期安全停止的题目实际进入`SAFE_STOP`的比例。

当前运行器默认使用Mock后端。Isaac Sim批量验收应复用相同的case_id和strategy.v1，并将远程`execution.json`合并到同一套报告中；不能把Mock结果冒充Isaac结果。
