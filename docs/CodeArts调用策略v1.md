# CodeArts 调用策略 v1

## 目标

让 CodeArts 负责“理解约束并提出策略”，让本地代码负责“编译、验证和执行”。这样既能最大化使用智能体能力，又不会把执行安全交给一次不透明的模型输出。

## 一次请求的链路

```text
task.v1
  -> B 路由（action/模式/策略档位）
  -> CodeArts planner 生成 strategy.v1
  -> 本地契约编译（动作、ID、引用、顺序、恢复、code=null）
  -> 可选 CodeArts critic（1 或 2 轮，只能 PASS）
  -> 本地契约再验一次
  -> C 执行器
```

CodeArts 的文本、工具事件和 JSON 都是不可信输入；只有通过本地校验的结构化策略才会到达 C。

## 三档调用策略

| 档位 | CodeArts 调用 | 适用场景 | 失败行为 |
|---|---:|---|---|
| `planner` | 1 次 planner | 开发、低延迟、高吞吐 | `auto` 回退本地五步；`required` 阻断 |
| `quality` | 1 次 planner + 1 次独立 critic | 正式 Demo、日常稳定运行 | critic 非 PASS 时回退/阻断 |
| `max` | 1 次 planner + 2 次独立 critic | 发布验收、高风险抽样回归 | 两轮均 PASS 才放行 |

critic 不负责修复和改写策略。它只检查白名单动作、稳定实体 ID、步骤顺序、引用、恢复上限和 `code=null`，输出：

```json
{"status":"PASS","issues":[],"risk_level":"LOW"}
```

## 推荐配置

```dotenv
CODEARTS_STRATEGY_MODE=auto
CODEARTS_STRATEGY_POLICY=quality
CODEARTS_CLI_PURE=1
CODEARTS_STRATEGY_TIMEOUT_S=180
```

正式演示切换为 `required`；验收时切换为 `max`。`CODEARTS_CLI_PURE=1` 用于隔离项目插件，Demo 进程内还会串行化 CLI 请求，避免本地 CodeArts 会话竞争。

## 测试与证据

离线策略矩阵：

```powershell
& 'C:\Users\14810\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s tests -t . -q
```

真实质量冒烟（需要已配置 CodeArts CLI 凭据）：

```powershell
& 'C:\Users\14810\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tools\benchmark_codearts.py `
  --live --pure --policy quality --repeats 1 --cases 1 `
  --model huaweicloud-maas/openpangu-2.0-flash `
  --output reports\codearts_quality_smoke.json
```

验收条件：`provider_calls == total`、`successes == total`、`fallback_count == 0`、`contract_failure_count == 0`，并且每条记录 `critic_passes` 等于 1（`max` 等于 2）。

## 观测指标

每条策略保留 planner/critic provenance、策略档位、critic 轮数、CodeArts 延迟和错误原因。建议持续看：

1. CodeArts 实际调用率（不能只看 `mode` 字段）。
2. 本地契约通过率和 critic PASS 率。
3. 回退率、阻断率、P50/P95 延迟。
4. 同一任务重复运行的策略稳定性。

当质量收益不足以覆盖延迟时降到 `planner`；当出现不安全输出或高风险动作时升到 `max`，而不是关闭本地闸门。
