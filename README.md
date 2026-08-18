# 具身智能系统统一联调仓库

本仓库用于把各成员的模块接入同一条可复现、可审计的机器人闭环：

```text
自然语言 + perception_observation
        ↓
意图理解与实体绑定
        ↓ task.v1
CodeArts 策略生成与校验
        ↓ strategy.v1
Mock / Isaac Sim / 真机执行
        ↓ execution.v1
TraceCoder / TraceProbe 反馈
        ↓ feedback.v1
回归测试、失败诊断与策略修正
```

> 当前主线基线：`04d34af`（2026-08-18）。`origin/main` 与本地 `main` 已同步，包含用户代码、PR3（TraceCoder LLM Provider）和基于主线重构后的 PR4（正式感知接口与规范化执行字段）。

## 一、仓库原则

1. `contracts/` 是唯一接口标准；模块不得私自约定字段。
2. 模块通过适配器连接，不直接引用其他模块的内部代码。
3. 先 Mock，后仿真，最后真机；每一步都保留可追踪的 `task_id`。
4. 危险、缺字段或无法执行的任务必须明确阻断，不能静默猜测。
5. 外部模型输出是不可信输入，必须经过本地 Schema、动作白名单和安全门禁后才能进入执行器。

## 二、当前状态（2026-08-18）

| 环节 | 已完成的代码能力 | 当前真实状态与边界 |
| --- | --- | --- |
| A 感知/意图 | Mock 感知、实体绑定、意图解析和安全门禁；新增 `perception_observation` 正式输入及 `ObservationNormalizer` | 软件闭环可运行；尚未接入真实相机、Isaac Sim 感知管线或真机传感器 |
| B 策略 | `strategy.v1` 校验、动作白名单、CodeArts CLI 适配器和离线模板；支持 `off/auto/required` | CodeArts 接入代码已在主线；当前环境能找到 CLI，但没有提交 agent/model 配置，不能据此宣称云端策略已在线稳定运行 |
| C 执行 | `execution.v1`、`MockBackend`、轨迹和安全事件输出；支持 `placement_mode: stack_on` | 当前可验证的执行后端仍是 Mock；Isaac Sim 和真机后端尚未进入主线 |
| D 反馈 | TraceCoder 规则引擎、经验库、Provider 抽象和 LLM Provider；`off/optional/required` 三种模式 | 默认离线安全模式；当前环境未配置 TraceCoder API key/model，LLM 实际调用未启用 |
| 契约 | `perception_observation` 正式 Schema、Schema `$ref` 校验、规范化字段和契约测试 | `object_id`、`destination_id` 是正式跨模块字段；兼容别名只保留在 Mock/边界适配器内 |

目前已经打通的是**软件级离线闭环**：意图 → task.v1 → 策略 → Mock 执行 → TraceCoder feedback.v1。它证明了接口和错误处理能够联调，但不等同于真实机器人已经完成闭环。

主线最近一次全量验证结果为：

```text
105 passed, 1 skipped, 115 subtests passed
```

### 主线与本地工作区的区别

远程 `main` 只包含已提交、可审查的基线。当前本地工作区另有一组未提交的 CodeArts 策略质量审查实验改动，涉及策略适配器、CodeArts 客户端、模块说明、测试和基准脚本，并包含新增的策略测试/说明文件；它们**不属于 `04d34af`，也不会随本次 README 提交进入远程主线**。提交这些代码前应单独完成测试、评审和回滚点创建；本地生成的 `.codeartsdoer/`、`.opencode/`、PPT、渲染目录和报告也不应加入 Git。

## 三、目录结构和任务

```text
.
├─ contracts/                 # 模块间 JSON Schema 和版本说明
│  └─ v1/                     # perception/task/strategy/execution/feedback
├─ modules/                   # 各成员模块的接入位置
│  ├─ perception/             # 感知、场景快照和观察规范化
│  ├─ intent_understanding/   # A：语言理解、目标绑定、安全门禁
│  ├─ strategy_generation/    # B：CodeArts 生成可执行策略
│  ├─ executor/               # C：Mock、Isaac Sim 或真机执行
│  └─ evaluator/              # D：TraceCoder、TraceProbe、评测
├─ integration/               # 跨模块编排，不放业务实现
│  ├─ adapters/               # 每个模块的 run()/health() 适配器
│  ├─ config/                 # local/sim/real 环境配置
│  └─ pipeline.py             # 端到端调用顺序
├─ testdata/                  # 脱敏、可复现的联调输入
│  ├─ daily/                  # 日常场景
│  └─ industrial/             # 工业场景
├─ tests/                     # 分层测试
│  ├─ contract/               # JSON Schema 和版本兼容
│  ├─ integration/            # 两个或多个模块联调
│  └─ e2e/                    # 全链路验收
├─ docs/                      # 联调手册、会议决议、故障记录
├─ .github/workflows/         # 自动化契约检查
├─ Makefile                   # 统一开发命令
└─ .env.example               # 环境变量模板，禁止提交真实密钥
```

成员只维护对应的 `modules/<模块名>/` 和 `integration/adapters/`。如果模块已有独立仓库，可以作为外部代码引入，但对外仍必须实现统一适配器：

```python
run(input_json: dict) -> output_json: dict
health() -> dict
```

输入和输出必须分别符合 `contracts/v1` 中的 Schema。接口修改时，同时修改 Schema、示例和契约测试，并说明兼容策略。

## 四、当前正式接口约定

### 感知输入：`perception_observation`

正式感知消息位于 [`contracts/v1/perception_observation.schema.json`](contracts/v1/perception_observation.schema.json)，要求携带 `observation_id`、`scene_id`、时间/时钟域、坐标系、来源信息和对象列表。每个对象使用稳定的 `object_id`，并明确位姿、几何、外观候选和跟踪信息。

[`modules/perception/observation_normalizer.py`](modules/perception/observation_normalizer.py) 只负责把正式消息转换成内部可消费的场景表示；它不会根据类别名称猜测抓取能力、目标区或可执行动作。执行能力必须来自可信的执行器/场景注册信息。

### 策略输入输出：`strategy.v1`

正式策略使用稳定 ID，不再把自然语言名称当作跨模块主键：

```json
{
  "schema_version": "strategy.v1",
  "task_id": "task-001",
  "steps": [
    {"step_id": "s1", "action": "detect_object", "arguments": {"object_id": "obj-red-cube"}},
    {"step_id": "s2", "action": "move_to_target", "arguments": {"destination_id": "target-bin", "placement_mode": "stack_on"}}
  ]
}
```

`object_name`、`target` 等旧字段只在 MockBackend 的直接调用兼容层保留；新的正式策略、适配器和测试应使用 `object_id`、`destination_id`。

## 五、日常使用

推荐先运行与 CI 一致的 Python 测试，再使用 Make 目标：

```bash
python -m pytest tests -q
make contract-test    # 检查所有协议 JSON
make integration-test # 运行模块联调测试
make acceptance-test  # 运行数据驱动的闭环验收题集
make e2e              # 运行完整闭环测试
```

闭环验收题位于 [`testdata/acceptance/`](testdata/acceptance/)，由 [`tests/e2e/test_closed_loop_acceptance.py`](tests/e2e/test_closed_loop_acceptance.py) 统一读取并检查，覆盖 `task.v1 → strategy.v1 → execution.v1 → feedback.v1`。

仓库还提供一个不依赖前端构建工具的可视化演示页：

```bash
python demo/server.py
```

打开 <http://127.0.0.1:8765/> 即可体验“环境预设 → 自然语言 → A 意图 → B 策略 → C Mock 执行 → D 反馈”。详细说明见 [`demo/README.md`](demo/README.md)。

## 六、TraceCoder 接入说明（feedback 环节）

**定位**：`modules/evaluator/`（D 模块）内的 TraceCoder 负责执行后反馈、失败归因和可控策略修正。

**代码位置**：

- 引擎：`modules/evaluator/tracecoder/`（轻量仿真、三角色修复和经验库，离线可运行）
- Provider：`modules/evaluator/tracecoder/providers/`（真实 LLM Provider 与 Fake Provider）
- 适配器：`integration/adapters/tracecoder.py`（`run()` / `health()`，输出 `feedback.v1`）
- 编排：`integration/pipeline.py` 把 `{task, strategy, execution, perception}` 传给反馈环节

TraceCoder 支持三种运行模式：

- `off`：只使用本地规则/轻量引擎，默认安全模式；
- `optional`：优先调用 LLM，失败时回退本地引擎并保留原因；
- `required`：LLM 不可用或输出不符合契约时阻断，不静默降级。

密钥和模型名只允许通过本地环境变量或未跟踪的配置文件提供，不能提交到仓库。当前主线验证重点是离线能力、回退路径、证据字段和契约校验；启用真实 LLM 前还需要做稳定性、延迟、费用和失败样本评测。

## 七、下一步工作路线

以下顺序延续此前的合并方案：先冻结并保护当前基线，再逐步把真实依赖接入；每一步都保持 Mock 回归集可运行。

1. **冻结 v1 契约和基线**：为感知、策略、执行和反馈字段补齐所有权、版本兼容规则、错误码和示例；为 `04d34af` 保留可回滚标签。
2. **完成正式字段迁移**：让所有真实生产者和消费者使用 `object_id`、`destination_id`；兼容别名只留在边界，并增加“旧字段不得穿透正式 Schema”的测试。
3. **打通感知到执行的可信能力链**：把 `perception_observation` 与场景/执行能力注册表关联，明确哪些对象可抓取、哪些目标可放置；缺少可信能力时必须阻断。
4. **接入 Isaac Sim**：实现独立的执行适配器，输出真实轨迹、碰撞/安全事件、耗时和可复现日志，继续复用 `execution.v1` 和 Mock 测试。
5. **受控启用 CodeArts**：在不入库密钥的环境配置 agent/model，先运行 `auto` 和重复稳定性测试，再考虑 `required`；任何模型输出仍须经过本地校验和动作白名单。
6. **受控启用 TraceCoder LLM**：先 `optional` 后 `required`，建立失败归因准确率、延迟、回退率和成本指标；没有证据时保持 `off`。
7. **小范围真机试运行**：在仿真通过后增加急停、权限、超时、速度/工作空间限制、人工确认和审计日志，禁止跳过安全门禁直接执行。
8. **持续治理仓库**：CI 强制契约测试和最小闭环验收；定期清理本地生成物，禁止把密钥、个人路径、真机地址和大体积日志提交到 Git。

每个阶段的合并请求都应写明：输入/输出协议、影响范围、回滚方式、测试命令与结果，以及是否改变了真实执行边界。出现大量代码冲突或接口重构时，先暂停合并并单独评审，不把冲突解决结果直接视为功能正确。

## 八、提交和联调规则

- 分支命名：`feature/<模块>-<功能>` 或 `codex/<模块>-<功能>`。
- Pull Request 必须写明：输入协议、输出协议、测试命令和结果。
- CI 未通过契约测试时不得合并。
- 不提交 API 密钥、真机地址、个人路径和大体积日志。
- 每个失败都要能用 `task_id` 在 `testdata/` 中复现。
- 合并顺序遵循“先基线/契约，再模块接入，最后真实后端”：任何后续 PR 都应基于最新 `main` 重放并重新跑全量测试。

详细操作说明见 [`docs/联调仓库使用手册.md`](docs/联调仓库使用手册.md)。
