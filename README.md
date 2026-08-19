# 具身智能系统统一联调仓库

本仓库把 A（意图理解）、B（CodeArts 策略）、C（感知/执行）和 D（TraceCoder 反馈）接入同一条可审计链路：

```text
自然语言 + perception_observation
        ↓
A 意图理解 → task.v1
        ↓
B CodeArts 策略生成与校验 → strategy.v1
        ↓
C Mock / Isaac Sim / 真机执行 → execution.v1
        ↓
D TraceCoder 反馈与修复 → feedback.v1
```

当前主线基线为 2026-08-19。软件级 A→B→C(Mock)→D 闭环已经打通；真实机器人执行仍待 C 模块接入真实后端。

## 1. 仓库修改原则

1. `contracts/v1/` 是跨模块接口唯一标准，禁止模块私自定义字段。
2. 模块只能通过 `integration/adapters/` 和公共契约通信，不直接依赖其他模块内部实现。
3. 跨模块主键使用稳定的 `task_id`、`object_id` 和 `destination_id`；旧字段不得穿透正式接口。
4. 外部模型输出一律视为不可信输入，必须经过 Schema、动作白名单、参数校验和安全门禁。
5. 验证顺序为 Mock → 仿真 → 真机；每一步都保留可追踪的执行证据。
6. 危险、歧义、缺字段或不可执行任务必须阻断，不能静默猜测或自动绕过安全门禁。
7. API Key、AK/SK、真机地址、个人路径和大体积运行日志不得提交到仓库。

## 2. 仓库目录结构

```text
.
├─ contracts/v1/             # perception/task/strategy/execution/feedback Schema
├─ modules/
│  ├─ perception/            # C：观察输入与场景规范化
│  ├─ intent_understanding/  # A：语言理解、实体绑定、安全门禁
│  ├─ strategy_generation/   # B：CodeArts 策略生成与校验
│  ├─ executor/              # C：Mock、Isaac Sim 或真机执行
│  └─ evaluator/tracecoder/  # D：反馈、诊断、修复和经验库
├─ integration/
│  ├─ adapters/              # 各模块统一 run()/health() 适配器
│  ├─ pipeline.py            # 端到端编排
│  └─ strategy_policy.py     # 策略能力与安全校验
├─ testdata/                 # 脱敏、可复现的验收和 CodeArts 题集
├─ tests/                    # contract、integration、e2e 分层测试
├─ docs/                     # 接口、联调和交付规则
├─ demo/                     # 本地演示
├─ .github/workflows/        # CI
└─ .env.example              # 环境变量模板，不含真实密钥
```

## 3. 当前模块完成度和状态（2026-08-19）

| 模块 | 已完成 | 当前状态和边界 |
| --- | --- | --- |
| A 意图理解 | DeepSeek LLM 语义编译、实体绑定、稳定 ID、歧义/安全阻断、`task.v1` 输出 | 已完成真实智能调用；正式启动需显式使用 `engine=llm` 或设置 `RIA_PLANNER_ENGINE=llm`；尚未接入真实相机感知 |
| B CodeArts | CodeArts CLI 适配、DeepSeek 模型、`strategy.v1` 校验、五动作白名单、provenance、`required` 失败阻断 | 已完成真实 CodeArts 调用和策略校验；模型凭证只在本地配置 |
| C 感知/执行 | `perception.v1`、`execution.v1`、能力透传、动作/参数校验、恢复上限、安全停止、`stack_on` | 当前可验证后端仍是 `MockBackend`；Isaac Sim、真实传感器和真机后端尚未接入主线 |
| D TraceCoder | 规则/LLM Provider、`optional/required/off` 模式、失败归因、patch 校验、有限重试、经验记录 | 已完成 DeepSeek `required` 模式实测；当前反馈依据仍来自 Mock 执行证据 |

### 系统当前水平和测试结果

- 软件级闭环：A → B → C(Mock) → D 已完成，可输出 `task.v1 → strategy.v1 → execution.v1 → feedback.v1` 全链路证据。
- 严格智能模式实测：A=`llm`、B=`required`、D=`required`，两个正式验收场景均成功，未发生规则回退。
  - 正常抓取放置：策略生成、五步动作执行和反馈均成功。
  - 失败修复重试：首次抓取失败，D 生成合法修复补丁，重试后成功，`retry_count=1`。
- 最近一次主线测试基线：`129 passed, 1 skipped, 118 subtests passed`。
- 当前尚不能宣称真实机器人闭环完成；缺口集中在 C 的真实感知、Isaac Sim/真机执行、安全联调和现场验收。

## 4. 正式接口约定

### 4.1 模块消息

正式消息和 Schema 位于 [`contracts/v1/`](contracts/v1/)：

```text
perception_observation → perception.v1 → task.v1
task.v1 → strategy.v1 → execution.v1 → feedback.v1
```

- 感知输入使用 `perception_observation`，由 C 边界规范化为 `perception.v1`。
- `task.v1`、`strategy.v1`、`execution.v1`、`feedback.v1` 必须通过对应 JSON Schema 校验。
- 对象和目标使用稳定的 `object_id`、`destination_id`；`object_name`、`target` 只允许存在于 Mock 直接调用兼容层。
- 每个模块适配器统一实现：

```python
run(input_json: dict) -> output_json: dict
health() -> dict
```

### 4.2 策略和执行约定

- 允许动作：`detect_object`、`move_to_object`、`grasp`、`move_to_target`、`release`。
- `strategy.code` 必须为 `null` 或空字符串；策略只能通过动作白名单执行。
- `detect_object`、`move_to_object`、`grasp` 使用 `object_id`；`move_to_target` 使用 `destination_id`。
- 堆叠必须显式使用 `placement_mode: "stack_on"`，并通过目标能力校验。
- 执行失败、恢复耗尽或触发安全事件时必须返回结构化 `execution.v1`，不得伪造成功。

## 5. 提交和联调规则

1. 分支命名使用 `feature/<模块>-<功能>` 或 `codex/<模块>-<功能>`。
2. 修改接口时必须同时更新 Schema、示例、适配器和契约测试，并说明兼容策略。
3. Pull Request 必须写明输入/输出协议、影响范围、回滚方式、测试命令和结果。
4. 合并前至少运行：

```bash
python -m pytest tests -q
python -m pytest tests/contract -q
python -m pytest tests/integration -q
python -m pytest tests/e2e -q
```

5. 联调顺序遵循“基线/契约 → 模块适配 → Mock 回归 → 仿真 → 真机”；任何真实后端接入都必须保持 Mock 回归集可运行。
6. 每个失败必须能通过 `task_id` 在 `testdata/` 中复现，并保留 provenance、模型调用、回退和安全事件信息。
7. 真实 CodeArts、DeepSeek、TraceCoder、Isaac Sim 和真机凭证只通过本地环境变量或未跟踪配置提供。

常用数据驱动验收入口：[`tests/e2e/test_closed_loop_acceptance.py`](tests/e2e/test_closed_loop_acceptance.py)；正式验收数据位于 [`testdata/acceptance/`](testdata/acceptance/)。
