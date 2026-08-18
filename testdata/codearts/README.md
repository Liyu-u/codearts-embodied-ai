# CodeArts 策略测试集

这组测试集按系统职责分类。带 `normal_scale_` 前缀的是当前正式规模题集；不带前缀的文件保留作兼容性冒烟题集：

| 测试集 | 用途 | 关键通过条件 |
|---|---|---|
| `normal_scale_functional` | 10 个正常功能任务 | 成功、动作顺序正确、strategy.v1 契约通过 |
| `normal_scale_semantic` | 8 个语义/实体保真任务 | 目标 ID、目的地 ID 精确匹配 |
| `normal_scale_safety` | 10 个安全边界任务 | 阻断、不调用 CodeArts、不产生步骤或代码 |
| `normal_scale_resilience` | 6 个 planner/critic 故障注入任务 | `auto` 回退、`required` 阻断 |
| `normal_scale_stability` | 6 个重复稳定性任务 | 每个任务默认重复 3 次，结果和 ID 稳定 |

离线运行（不产生云端调用）：

```powershell
& 'C:\Users\14810\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tools\run_codearts_testsets.py `
  --set normal_scale_functional --set normal_scale_semantic `
  --set normal_scale_safety --set normal_scale_resilience --set normal_scale_stability `
  --repeats 2 --output reports\codearts_testsets_normal_scale.json
```

运行器会校验统一格式 `codearts-testset.v1`，并对正常用例检查 `expect.actions`、`expect.target_id` 和 `expect.destination_id`。故障集使用受控 Mock 注入，不会误发真实请求；`provider_attempts` 表示尝试走 provider，`provider_calls` 只统计真实 CodeArts 调用。

真实 CodeArts 运行建议分批执行。先用一个正常样本确认线路，再扩展到完整集：

```powershell
& 'C:\Users\14810\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tools\run_codearts_testsets.py `
  --set normal_quality --live --policy quality --limit 1 --pure `
  --model huaweicloud-maas/openpangu-2.0-flash --timeout-s 180 `
  --output reports\codearts_testset_normal_live.json
```

稳定性回归：

```powershell
& 'C:\Users\14810\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tools\run_codearts_testsets.py `
  --set stability_repeat --live --policy planner --repeats 3 --pure `
  --model huaweicloud-maas/openpangu-2.0-flash --timeout-s 180 `
  --output reports\codearts_testset_stability_live.json
```

验收报告必须同时满足：`all_passed=true`、`all_stable=true`、`contract_failures=0`；正常 live 测试还必须满足 `provider_calls == 成功样本数`，并且 `critic_passes` 与策略档位一致。真实测试不要和其他 CodeArts 任务并发运行。
