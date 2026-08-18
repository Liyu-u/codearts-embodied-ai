# CodeArts 与 TraceCoder 真实模型接入设计

> 状态：总体方向已由吴昌庆于 2026-08-17 确认；等待设计文档最终审阅。
> 范围：CodeArts 策略生成、TraceCoder 大模型增强、统一 Provider、证据留存、Pipeline 修复重试。
> 后续独立设计：IsaacSimBackend、歧义数据集与科研实验、前端演示与正式交付。

## 1. 背景与问题

当前仓库已经完成 `A → B → C(Mock) → D` 的第一阶段函数级闭环，但真实模型并未进入公开联调主路径：

- `integration/adapters/strategy.py` 直接生成确定性的五步 `primitive_plan`，没有调用 CodeArts。
- `modules/strategy_generation/strategy_generator.py` 中存在通用 OpenAI 风格 HTTP 调用，但这不能证明调用的是华为云码道 CodeArts。
- `modules/evaluator/tracecoder/agents.py` 依赖统一仓库中不存在的 `src.generation`，异常后无记录地退回规则结果。
- `modules/evaluator/tracecoder/processor.py` 默认 `use_llm=False`，公开 TraceCoder 适配器没有开启真实模型。
- `integration/pipeline.py` 能取得 `feedback.v1.patch`，但不会验证、应用和重新执行 patch。
- 当前协议没有完整表达策略来源、模型、Agent、请求编号、耗时、回退原因和本地校验结果。

老师要求正式验收能够证明 CodeArts 和大模型真实参与，且不能用静默回退或 Mock 冒充成功。因此本设计建立统一 Provider 边界、严格运行模式和可审计证据链。

## 2. 目标

1. 通过官方 CodeArts CLI 把 `task.v1` 转换为 `strategy.v1`。
2. 通过 OpenAI-compatible Provider 为 TraceCoder 的观察、归因和 patch 建议提供真实 LLM 能力。
3. 支持 `off`、`optional`、`required` 三种模型可用要求。
4. 分离 `rules`、`llm`、`hybrid` 三种实验算法，允许公平对比。
5. 所有模型输出必须经过本地确定性 schema、动作、实体和安全校验。
6. 保存足以证明真实调用发生的证据，同时不泄露 API Key、Token 或密码。
7. 在正式验收模式中禁止静默回退；模型缺席或输出无效必须明确失败。
8. 实现有限次数的 `执行 → 反馈 → patch → 校验 → 重执行` 闭环。

## 3. 非目标

- 本设计不实现 `IsaacSimBackend`；C 真实仿真另立设计和实施计划。
- 本设计不重做前端；只定义前端未来需要消费的来源和证据字段。
- 本设计不允许 CodeArts 或 TraceCoder 输出的任意 Python 在 Kit、服务器或主 Pipeline 内直接执行。
- 本设计不建设通用多用户模型网关、计费平台或生产级密钥管理系统。
- 本设计不把 CodeArts CLI 的本地服务暴露到校园网或公网。

## 4. 核心设计决策

### 4.1 CodeArts 使用 CLI Provider

当前能够由官方文档确认的自动化入口是 `codearts run`。它支持非交互调用、模型、Agent、附加文件和 JSON 输出。因此 CodeArts 的正式接入点为 `CodeArtsCliProvider`，而不是假设存在 `/chat/completions` HTTP 接口。命令依据见[华为云码道 CLI 命令说明](https://support.huaweicloud.com/usermanual-cli/codeartsagent_cli_0034.html)。

本机 CodeArts CLI 当前版本为 `26.1.10`。账号授权、可用模型和 Agent 仍必须通过单独 smoke test 确认。

若赛事方后续提供专用 CodeArts API，则新增 `CodeArtsApiProvider`，不修改上层策略接口。

### 4.2 TraceCoder 使用 OpenAI-compatible Provider

TraceCoder 通过统一接口调用 OpenAI-compatible 服务。Provider 负责 Base URL、API Key、模型、超时、重试、JSON 解析和请求证据；TraceCoder 角色只负责构造业务 payload 和消费结构化结果。

`agents.py` 不再导入外部 `src.generation`，也不直接读取环境变量或访问网络。

### 4.3 模型可用要求与实验算法分离

两个维度不得混用：

```text
requirement_mode = off | optional | required
algorithm_mode   = rules | llm | hybrid
```

`requirement_mode` 定义运行时是否必须真实调用模型：

| 模式 | 是否调用模型 | 失败时行为 | 是否允许回退 |
|---|---:|---|---:|
| `off` | 否 | 使用本地规则 | 不涉及 |
| `optional` | 是 | 记录失败后允许使用规则 | 是，必须显式记录 |
| `required` | 是 | 整次阶段失败并阻断后续执行 | 否 |

`algorithm_mode` 定义实验组：

| 算法 | 修复建议来源 | 始终保留的确定性能力 |
|---|---|---|
| `rules` | 规则 Observation/Analysis/Repair | schema、实体、动作、安全和 patch 校验 |
| `llm` | LLM Observation/Analysis/Repair | schema、实体、动作、安全和 patch 校验 |
| `hybrid` | 规则产生约束和基线，LLM 产生或改进候选 | schema、实体、动作、安全、候选评分和选择 |

允许的主要组合：

- `off + rules`：纯规则基线。
- `optional + hybrid`：日常开发模式，模型失败时可见地回退。
- `required + llm`：纯 LLM 正式实验。
- `required + hybrid`：规则+LLM 正式实验，LLM 调用和有效结构化建议都是必要条件。

不允许 `off + llm` 或 `off + hybrid`。

### 4.4 正式验收禁止静默降级

CodeArts 正式验收使用 `CODEARTS_STRATEGY_MODE=required`。TraceCoder 正式在线验收使用 `TRACECODER_LLM_MODE=required`。

在 `required` 模式下，以下任一情况必须返回结构化失败，不能改用规则结果继续冒充成功：

- Provider 不存在或未配置；
- CLI 未安装或未授权；
- 模型或 Agent 不可用；
- 超时、限流或网络失败；
- 非零进程退出码；
- 返回内容不是合法 JSON；
- JSON 不符合输出结构；
- 引用了不存在的对象 ID；
- 产生未知动作或不安全 patch。

## 5. 目标架构

```text
task.v1
  ↓
StrategyAdapter
  ├─ off       → LocalPrimitivePlanner
  └─ optional/required → CodeArtsCliProvider
                         ↓
                 strategy.v1 candidate
                         ↓
             StrategySafetyValidator
                         ↓
                    strategy.v1
                         ↓
ExecutorAdapter(Mock / future Isaac)
                         ↓
                    execution.v1
                         ↓
TraceCoderAdapter
  ├─ rules  → PolicyAgentSuite
  ├─ llm    → LLMPolicyAgentSuite(OpenAICompatibleProvider)
  └─ hybrid → rules baseline + LLM candidate
                         ↓
                 feedback.v1 + patch
                         ↓
             PatchSafetyValidator
                         ↓
          bounded retry or SAFE_STOP
```

统一 Provider 只负责“可靠地获得结构化模型结果”，不负责机器人业务决策。业务 prompt、允许动作和输出结构由调用模块定义；最终安全判断始终由本地代码执行。

## 6. 文件边界

### 6.1 新增文件

| 文件 | 单一职责 |
|---|---|
| `integration/providers/__init__.py` | 导出 Provider 公共类型和工厂 |
| `integration/providers/models.py` | `ProviderRequest`、`ProviderResult`、错误枚举 |
| `integration/providers/base.py` | `StructuredModelProvider` 抽象接口 |
| `integration/providers/codearts_cli.py` | 安全调用 CodeArts CLI 并解析 JSON |
| `integration/providers/openai_compatible.py` | 调用 OpenAI-compatible chat completion |
| `integration/providers/fake.py` | 单元测试的确定性 Provider |
| `integration/provider_factory.py` | 按配置创建 Provider，禁止业务模块自行读密钥 |
| `integration/run_evidence.py` | 生成 run ID、脱敏并写入模型调用证据 |
| `contracts/v1/run_manifest.schema.json` | 一次运行的代码、环境、Provider 和产物清单 |
| `tests/unit/test_provider_modes.py` | off/optional/required 行为 |
| `tests/unit/test_codearts_cli_provider.py` | CLI 退出码、超时和 JSON 解析 |
| `tests/unit/test_openai_provider.py` | HTTP 成功、错误、重试和证据 |
| `tests/integration/test_codearts_mock_pipeline.py` | CodeArts 候选接 MockBackend |
| `tests/integration/test_tracecoder_llm_pipeline.py` | LLM 修复与有界重执行 |
| `tests/online/test_codearts_online.py` | 真实 CodeArts 成功和失败证据 |
| `tests/online/test_tracecoder_online.py` | 五类真实 LLM 在线验收 |

### 6.2 修改文件

| 文件 | 修改内容 |
|---|---|
| `integration/adapters/strategy.py` | 注入 Provider；按模式调用 CodeArts；验证并返回来源证据 |
| `modules/strategy_generation/strategy_generator.py` | 旧 HTTP 逻辑退出公开主路径；保留模板能力作为本地 planner，或后续拆分 |
| `modules/evaluator/tracecoder/agents.py` | 注入 Provider；移除 `src.generation` 和静默异常 |
| `modules/evaluator/tracecoder/processor.py` | 接收算法模式、模型调用结果和候选来源 |
| `integration/adapters/tracecoder.py` | 接收模式配置；输出真实调用证据；区分经验库与 LLM |
| `integration/pipeline.py` | 全边界校验、结构化错误、patch 校验和有限重执行 |
| `contracts/v1/strategy.schema.json` | 收紧步骤并增加 `provenance` |
| `contracts/v1/feedback.schema.json` | 增加模型调用、patch 校验和最终来源证据 |
| `contracts/v1/execution.schema.json` | 收紧步骤、安全事件和 backend 来源 |
| `.env.example` | 增加模式和非秘密配置；API Key 只放占位符 |
| `.gitignore` | 排除真实密钥、在线运行证据和临时 Provider 工作目录 |

### 6.3 后续独立修改

- `modules/executor/isaac_backend.py` 和 `integration/config/*.toml` 属于 Isaac 子项目。
- 前端只消费本设计输出的 `provenance` 和 run manifest，不在本设计中实现。

## 7. Provider 公共接口

内部 Python 接口使用不可变请求和结构化结果。业务代码不得直接调用 `subprocess` 或 HTTP。

```python
@dataclass(frozen=True)
class ProviderRequest:
    purpose: str
    model: str
    system_prompt: str
    payload: dict
    timeout_s: float
    max_output_tokens: int
    agent: str | None = None


@dataclass(frozen=True)
class ProviderResult:
    ok: bool
    provider: str
    model: str
    agent: str | None
    request_id: str | None
    duration_ms: int
    attempts: int
    output_json: dict | None
    raw_output_sha256: str | None
    error_type: str | None
    error_message: str | None
```

Provider 方法：

```python
class StructuredModelProvider(Protocol):
    def complete_json(self, request: ProviderRequest) -> ProviderResult: ...
    def health(self) -> dict: ...
```

Provider 不返回本地 fallback。是否回退由 adapter 根据 `requirement_mode` 决定，避免 Provider 内部隐藏真实失败。

## 8. 配置设计

配置分为可提交配置和秘密环境变量。

### 8.1 可提交配置

后续 `integration/config/local.toml` 使用 Python 标准库 `tomllib` 读取，避免为离线环境增加 YAML 依赖。核心字段：

```toml
[strategy]
requirement_mode = "optional"
provider = "codearts_cli"
model_source = "environment"
agent_source = "environment"
timeout_s = 90
max_attempts = 2

[tracecoder]
requirement_mode = "optional"
algorithm_mode = "hybrid"
provider = "openai_compatible"
model_source = "environment"
timeout_s = 45
max_attempts = 2
max_repair_rounds = 2

[evidence]
enabled = true
store_redacted_output = true
store_raw_output = false
```

模型和 Agent 的最终名称必须来自真实 smoke test，不在设计阶段猜测。

### 8.2 环境变量

```text
CODEARTS_STRATEGY_MODE=off|optional|required
CODEARTS_MODEL=<model>
CODEARTS_AGENT=<agent>
CODEARTS_TIMEOUT_S=90

TRACECODER_LLM_MODE=off|optional|required
TRACECODER_ALGORITHM=rules|llm|hybrid
TRACECODER_API_KEY=<secret>
TRACECODER_BASE_URL=<url>
TRACECODER_MODEL=<model>
TRACECODER_TIMEOUT_S=45
```

CodeArts CLI 使用 CLI 自身的账号授权，不在项目 `.env` 中保存账号密码。若未来启用赛事专用 API，再单独增加秘密变量。

环境变量只覆盖 TOML；配置解析后必须在运行报告中记录非秘密最终值。

## 9. CodeArts CLI Provider

### 9.1 调用方式

Provider 使用参数数组启动进程，不使用 shell 字符串拼接。逻辑命令为：

```text
codearts run <prompt>
  --model <model>
  --agent <agent>
  --file <task-json>
  --format json
```

实际参数顺序以本机 `codearts help` 和 smoke test 为准。

### 9.2 隔离边界

- 每次调用建立独立临时目录，只放脱敏后的 task 和输出约束。
- CodeArts 不在团队仓库根目录运行，防止模型意外修改代码。
- 不使用 `--auto`。
- 专用 Agent 不授予 bash、edit 或外网工具；只要求读取附加 JSON 并返回策略 JSON。
- 超时后结束本次 CLI 进程树并记录 `TIMEOUT`。
- stdout/stderr 只作为数据解析和诊断，不能再交给 shell 执行。

### 9.3 输出解析

CodeArts `--format json` 的最外层结果与 Agent 回复中的策略对象分两层解析：

1. 解析 CLI 外层 JSON，取得 session/request ID、模型和 assistant content。
2. 从 assistant content 中解析唯一 JSON 对象。
3. 验证 `strategy.v1`。
4. 验证 task ID、对象 ID、目标区域 ID和动作白名单。
5. 验证步骤数、重试数和安全约束。

不从 Markdown 中执行代码。允许为了兼容模型输出而去除单层 JSON fence，但不能使用贪婪正则猜取多个对象。

### 9.4 错误处理

CodeArts 错误分类：

- `CLI_NOT_FOUND`
- `AUTH_REQUIRED`
- `MODEL_NOT_FOUND`
- `AGENT_NOT_FOUND`
- `TIMEOUT`
- `PROCESS_ERROR`
- `INVALID_CLI_JSON`
- `INVALID_STRATEGY_JSON`
- `SCHEMA_REJECTED`
- `UNKNOWN_ACTION`
- `UNKNOWN_ENTITY`

进程/网络临时失败可以按配置重试；身份、模型、Agent 和确定性校验错误不做相同请求的盲目重试。非法 JSON 只允许一次带明确错误信息的格式修复请求。

## 10. TraceCoder LLM Provider

### 10.1 三角色请求

TraceCoder 的真实 LLM 调用分为三个 purpose：

- `trace_observation`：指出应关注的失败步骤和状态字段。
- `trace_diagnosis`：根据 execution 证据定位失败原因。
- `trace_patch`：生成受限 patch 操作或完整 `strategy.v1` 候选。

每个调用独立记录证据。后一个角色只消费前一个角色经过 schema 校验的结构化输出。

### 10.2 LLM 不拥有的权限

LLM 不得：

- 改写真实 execution 事实；
- 把安全违规判断为成功；
- 生成 C capabilities 之外的动作；
- 引用 perception 中不存在的 ID；
- 增加无限重试；
- 修改 `task_id`；
- 取消 SAFE_STOP；
- 输出或执行 Python 源码。

### 10.3 Hybrid 候选选择

Hybrid 模式同时保留规则基线和 LLM 候选：

1. 规则层从 execution 提取确定性失败事实和约束。
2. LLM 基于这些事实产生候选诊断和 patch。
3. 两个候选都经过相同静态、安全和轻量仿真评估。
4. 只选择安全且严格改善任务分数的候选。
5. 记录 `candidate_sources`、各自分数和 `selected_source`。

在 `required + hybrid` 中，LLM 调用和结构化建议必须成功。即使最终选中规则候选，也必须保留 LLM 候选及其未被选择的确定性原因；正式展示必须说明 `applied_patch_source`，不能只显示“LLM 已调用”。

## 11. 本地确定性验证

所有 CodeArts strategy 和 TraceCoder patch 使用同一验证顺序：

1. JSON 可解析；
2. schema 合法；
3. task ID 一致；
4. step ID 唯一；
5. action 存在于 C capabilities；
6. object/destination ID 存在于 perception；
7. 引用表达式只能引用已完成的前序步骤；
8. 主步骤、恢复步骤、重试和模型修复轮数不超限；
9. 不包含非空 `code`；
10. 不会覆盖 SAFE_STOP 或删除强制安全步骤。

任一检查失败都要返回机器可读错误，不能只写自然语言日志。

## 12. 协议扩展

### 12.1 `strategy.v1.provenance`

```json
{
  "source": "codearts_cli",
  "requirement_mode": "required",
  "provider": "codearts_cli",
  "model": "model-name",
  "agent": "agent-name",
  "request_id": "request-or-session-id",
  "duration_ms": 1234,
  "attempts": 1,
  "fallback_used": false,
  "fallback_reason": null,
  "validation": {
    "schema_passed": true,
    "entities_passed": true,
    "actions_passed": true,
    "safety_passed": true
  }
}
```

### 12.2 `feedback.v1.provenance`

```json
{
  "requirement_mode": "required",
  "algorithm_mode": "hybrid",
  "model_calls": [
    {
      "purpose": "trace_diagnosis",
      "provider": "openai_compatible",
      "model": "model-name",
      "request_id": "request-id",
      "duration_ms": 900,
      "ok": true
    }
  ],
  "candidate_sources": ["rules", "llm"],
  "selected_source": "llm",
  "applied_patch_source": "llm",
  "fallback_used": false,
  "fallback_reason": null
}
```

现有 `diagnosis` 字符串和 `patch` 字段在 v1 中暂时保留，避免一次修改破坏所有队友代码。新增 provenance 为向后兼容扩展；正式运行时由 runtime validation 根据模式要求其存在。

### 12.3 `run_manifest.v1`

一次运行至少记录：

- run ID、开始/结束时间和最终状态；
- Git commit 和工作树是否干净；
- pipeline、contract 和配置版本；
- strategy/TraceCoder Provider 非秘密配置；
- 模型调用 request ID、耗时、尝试次数、哈希和错误；
- Mock/Isaac/real backend 来源；
- 输入、策略、执行、反馈、patch 和截图文件的 SHA256；
- 是否发生回退、重试和 SAFE_STOP。

## 13. 证据目录与脱敏

默认本地目录：

```text
artifacts/runs/<run_id>/
├─ manifest.json
├─ input/
│  ├─ perception.json
│  └─ task.json
├─ providers/
│  ├─ codearts.json
│  ├─ trace_observation-01.json
│  ├─ trace_diagnosis-01.json
│  └─ trace_patch-01.json
├─ strategy/
│  ├─ initial.json
│  └─ patched-01.json
├─ execution/
│  ├─ attempt-01.json
│  └─ attempt-02.json
└─ feedback/
   └─ attempt-01.json
```

脱敏规则：

- 不写入 API Key、Authorization header、CodeArts 密码或 Cookie。
- API Key 只记录 `configured=true/false`。
- 默认不保存原始完整 prompt；保存模板版本、结构化输入和 SHA256。
- 模型输出在去除密钥和绝对用户路径后保存；原始输出默认只保存 SHA256。
- 错误信息过滤环境变量值和本机用户目录。
- `artifacts/runs/` 默认不提交 Git；正式汇报只复制人工审阅后的证据包。

## 14. Pipeline 重试状态机

```text
GENERATE_STRATEGY
  ├─ provider/validation failed → BLOCKED
  └─ valid → EXECUTE

EXECUTE
  ├─ SUCCEEDED → TRACE_EVALUATE → FINISHED
  ├─ SAFE_STOP → TRACE_EVALUATE → SAFE_STOPPED
  └─ FAILED → TRACE_REPAIR

TRACE_REPAIR
  ├─ not retryable → FAILED
  ├─ provider failed in required mode → MODEL_FAILED
  ├─ patch rejected → PATCH_REJECTED
  ├─ retry budget exhausted → SAFE_STOPPED
  └─ valid improving patch → EXECUTE(next attempt)
```

约束：

- 默认最多 2 次执行尝试，即初始执行加 1 次修复重试。
- 任何安全事件立即禁止自动重试。
- 重复 patch、没有改善的 patch 或相同失败签名连续出现时停止。
- 每次重执行必须产生独立 `execution.v1`，不能覆盖原证据。

## 15. 测试设计

### 15.1 离线单元测试

- Provider 成功返回合法 JSON。
- CLI 不存在、非零退出、超时和残留进程清理。
- HTTP 超时、限流、5xx 和非法 JSON。
- `off` 不发生任何模型调用。
- `optional` 回退并写明原因。
- `required` 返回失败且不调用规则替代。
- API Key 和密码不出现在日志或证据中。
- 非法 action、错误实体、非空 code 和超限重试被拒绝。

### 15.2 CodeArts 在线测试

至少保存三条可重复成功记录：

1. 正常 pick-and-place。
2. 同义表达但绑定相同实体。
3. 带明确安全约束的任务。

至少保存三类失败处理记录：

1. 非法 JSON 或缺字段；
2. 未知动作；
3. 错误实体 ID。

失败处理使用“真实 CLI 响应的脱敏录制样例 + 可控故障注入”稳定复现；如果真实在线调用本身出现失败，则另存为实际问题证据，但不依赖模型恰好犯错才能通过回归测试。另记录真实成功调用的平均/中位/P95 延迟、输出稳定性和重试次数。在线成功测试必须断言 `source=codearts_cli`、`fallback_used=false`、request/session ID 非空。

### 15.3 TraceCoder 在线测试

必须覆盖：

1. 正常任务：模型完成分析但不制造无必要 patch。
2. 抓取失败：定位 grasp 并给出合法恢复建议。
3. 目标未达成：区分执行成功与任务结果失败。
4. 无效修复：本地 validator 拒绝模型 patch。
5. 持续失败：耗尽次数后 SAFE_STOP。

在线测试断言真实 Provider 调用证据存在，且 `required` 模式没有规则回退。

### 15.4 三组修复实验

固定同一任务集、Mock 场景、失败注入和最大次数，比较：

- `off + rules`
- `required + llm`
- `required + hybrid`

指标：任务成功率、有效 patch 率、非法 patch 率、平均修复轮数、SAFE_STOP 正确率、延迟和成本。确定性安全 validator 在三组中保持相同，不作为 LLM 能力的一部分。

## 16. UI 未来消费字段

前端必须展示而不能推测：

- 策略来源：本地规则或 CodeArts；
- CodeArts 模型、Agent、耗时和验证结论；
- 执行后端：Mock、Isaac Sim 或 real；
- TraceCoder 算法：rules、llm 或 hybrid；
- 每轮模型是否成功、是否回退、patch 是否被应用；
- 最终状态和 SAFE_STOP 原因。

只要 `fallback_used=true`，界面必须显示明显标记，不能仍标成“CodeArts 策略成功”或“LLM 修复成功”。

## 17. 分阶段验收

### Gate A：Provider 基础

- Fake Provider 离线测试通过。
- CodeArts CLI 和 OpenAI Provider 的错误分类、超时、重试和脱敏测试通过。
- required 模式没有静默回退。

### Gate B：CodeArts → Mock

- 完成 3 条真实成功和 3 类失败记录。
- 正式成功记录全部为 `source=codearts_cli`、`fallback_used=false`。
- 结构化策略通过本地验证并由 MockBackend 执行。

### Gate C：TraceCoder LLM → Mock

- 五类在线测试通过。
- patch 验证和有限重执行状态机通过。
- rules/llm/hybrid 三组实验可重复。

### Gate D：交给 Isaac

- 相同 `strategy.v1` 可以不经转换交给未来 `IsaacSimBackend`。
- execution 来源和 TraceCoder 证据能明确标识 `backend=isaac_sim`。
- 只有 Gate A～C 通过后才进入真实 Isaac 端到端验收。

## 18. 已知风险与处理

| 风险 | 处理 |
|---|---|
| CodeArts CLI 未授权或模型/Agent 不可见 | 先运行只读 smoke test；正式开发前固定真实名称 |
| CLI JSON 外壳版本变化 | 保存 CLI 版本；解析器做版本检查，未知格式明确失败 |
| CodeArts 是编码 Agent，可能尝试工具调用 | 隔离临时目录、禁用危险权限、不使用 `--auto` |
| 模型输出不稳定 | 低温度/Agent 指令、严格 schema、一次格式修复、确定性 validator |
| optional 模式掩盖问题 | UI 和 manifest 强制显示回退；正式验收使用 required |
| LLM 建议安全但无效果 | 只有确定性评分严格改善才允许重执行 |
| 在线测试耗费额度 | 单独标记，默认跳过；固定小型数据集和最大输出 Token |
| API 密钥泄露 | 环境变量注入、日志脱敏、secret scan、证据包人工审阅 |

## 19. 实施顺序

本设计通过最终审阅后，拆成三个可独立合并的实施计划：

1. **Provider 基础与证据层**：公共类型、Fake/OpenAI Provider、模式、脱敏和 run manifest。
2. **CodeArts CLI 策略接入**：真实 smoke test、CLI Provider、strategy adapter、Mock 在线验收。
3. **TraceCoder LLM 与重执行闭环**：三角色 Provider、三算法模式、patch 状态机和五类在线验收。

IsaacSimBackend 作为第四个独立子项目，在前三项的接口稳定后开始。

## 20. 需要在实施前填入的真实运行事实

以下不是设计选择，必须由 smoke test 获取后写入本地配置和运行证据：

- CodeArts 当前授权账号是否有效；
- 赛事代金券或套餐是否已经到账、可用额度是否满足在线测试；
- 实际可用模型名称；
- 实际可用 Agent 名称及权限；
- `codearts run --format json` 的真实输出外壳样例；
- TraceCoder LLM 的 Base URL、模型和请求 ID 字段；
- 比赛方是否另行提供 CodeArts 专用 API；
- 官方提交日期、评审日期和必交材料。

这些事实未确认前可以实现 Fake Provider 和错误路径，但不能宣称真实 CodeArts 或 TraceCoder 在线验收完成。
