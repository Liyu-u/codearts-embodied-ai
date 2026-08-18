# 交接文档 · D 模块（TraceCoder 诊断与反馈）

> 收件人：D 负责人（王翊航 / 郭家腾）
> 发送人：C 负责人（吴昌庆）
> 日期：2026-08-17
> 关联分支：`feature/executor-isaac-v1`（统一联调仓库）

---

## 一、你现在需要做的三件事

1. **TraceCoder 真实接入大语言模型**：建立统一 LLM Provider，并支持 `off / optional / required` 三种模式；
2. **把 `execution.v1` 当作唯一事实来源**：统一内部模拟对象 ID，消除"外层 `final_passed=true` 但内部 `simulation_final_passed=false`"的矛盾；
3. **修复建议（patch）必须经过本地白名单与安全校验**，且只能提出 C `capabilities` 里存在的动作。

## 二、接口契约（必须遵守）

你的模块目录：`modules/evaluator/`；适配器：`integration/adapters/tracecoder.py`。

```text
run(input_json: dict) -> feedback_v1: dict
health() -> dict
```

输入：`execution.v1`。输出 `feedback.v1`，协议见 `contracts/v1/feedback.schema.json`：

| 字段 | 必填 | 说明 |
|---|---|---|
| `schema_version` | 是 | 固定 `feedback.v1` |
| `task_id` | 是 | 原样沿用 |
| `diagnosis` | 是 | 结构化诊断文本 |
| `retryable` | 否 | 是否可重试 |
| `patch` | 否 | 修复建议（`object` 或 `null`），只允许 C 白名单动作 |

## 三、老师对 TraceCoder 的具体要求（P0）

1. **统一 LLM Provider**：配置 model / base_url / api_key / timeout / retry / 结构化 JSON 输出；
2. **三种模式**：`off`（纯规则）/ `optional`（失败才调 LLM）/ `required`（强制 LLM）；
3. LLM 负责三件事：**观察重点、失败归因、修复建议**；生成的 patch 仍必须走**本地白名单 + 安全校验**，不允许直接执行；
4. **保存真实调用证据**：模型名、请求编号、耗时、是否回退、修复前后策略与执行结果；
5. 对比 **纯规则 / 纯 LLM / 规则+LLM** 三组效果；
6. 验收至少覆盖五类在线测试：**正常任务、抓取失败、目标未达成、无效修复、持续失败安全停止**，并能证明模型确实参与、而不是悄悄回退到规则。

## 四、C 会给你什么（execution.v1 重点字段）

- `status`：`SUCCEEDED` / `FAILED` / `SAFE_STOP`；
- `steps[]`：每条含 `phase / action / arguments / status / reason / duration_ms`，以及 C 后端写入的 `pose / collisions / grasp_force_n`；
- `safety_events[]`：后端安全事件（`WORKSPACE_VIOLATION` / `COLLISION_DETECTED` / `ACTION_TIMEOUT` …）排前，解释器安全停止事件排后；
- `trajectory_points[]`、`total_duration_ms`。

**读取建议：**

1. 用 `task_id` 串联 task / strategy / execution / feedback；
2. `SAFE_STOP` 时优先读 `safety_events`，不要只看最后一个动作；
3. Mock 的 `INJECTED_FAILURE` 标成测试条件，不要误报为真实 Isaac 故障。

## 五、必须改掉的三个旧问题

1. **内部临时 ID**：不要再用 `obj_1 / obj_2` 重新编号，直接使用 perception 的稳定 ID（`green_cube` / `zone_unstack_target`）；
2. **结论矛盾**：`execution.v1` 是最终事实来源，成功时只验证，失败且可修复时才生成 patch，内部模拟只作辅助；
3. **patch 未闭环**：D 只能产生 `capabilities` 支持、schema 合法、白名单内的 patch，由 pipeline 有上限地重新执行。

## 六、你需要确认并回给 C 的问题

1. 是否确认 `execution.v1` 是最终事实来源，内部模拟不覆盖真实成功结论？
2. 何时统一内部模拟与 perception 的对象 ID？
3. `feedback.v1.patch` 由哪个模块重新执行、重试上限多少？

## 七、验收口径

- `run(execution.v1)` 输出通过 `feedback.v1` schema 校验；
- 成功链路返回一致的 `final_passed`，不再出现内外矛盾；
- 五类在线测试（正常 / 抓取失败 / 目标未达 / 无效修复 / 持续失败安全停止）可复现；
- LLM 调用证据完整（模型名、请求编号、耗时、回退标记、修复前后对比）。

---

关联文档：`docs/Isaac执行器接口说明.md`（第 5 节"D 应该如何使用"）。
