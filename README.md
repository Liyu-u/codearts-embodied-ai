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

> 当前主线基线：C1 最终修复批次（2026-08-18）。本批次按“契约与安全门禁 → 模块联调 → 证据与指标 → 真实依赖验收”的顺序提交到 `main`；远程 `main` 的最新提交以 GitHub 为准。

## 一、仓库原则

1. `contracts/` 是唯一接口标准；模块不得私自约定字段。
2. 模块通过适配器连接，不直接引用其他模块的内部代码。
3. 先 Mock，后仿真，最后真机；每一步都保留可追踪的 `task_id`。
4. 危险、缺字段或无法执行的任务必须明确阻断，不能静默猜测。
5. 外部模型输出是不可信输入，必须经过本地 Schema、动作白名单和安全门禁后才能进入执行器。

## 二、当前状态（2026-08-18）

| 环节 | 已完成的代码能力 | 当前真实状态与边界 |
| --- | --- | --- |
| A 感知/意图 | Mock 感知、实体绑定、意图解析和安全门禁；`perception_observation` 正式输入、稳定 ID、UUID `task_id`、执行能力校验和尺寸校验 | 软件闭环可运行；尚未接入真实相机、Isaac Sim 感知管线或真机传感器 |
| B 策略 | `strategy.v1` 校验、五动作白名单、CodeArts CLI 适配器、`off/auto/required` 模式、结构化 provenance 和 required 失败阻断 | CodeArts 接入代码已提交；当前环境没有可复核的线上 agent/model 配置，不能据此宣称云端策略已在线稳定运行 |
| C 执行 | `execution.v1`、`MockBackend`、capabilities 透传、轨迹/安全事件、动作与恢复上限；支持 `placement_mode: stack_on` | 当前可验证的执行后端仍是 Mock；Isaac Sim 和真机后端尚未进入主线 |
| D 反馈 | TraceCoder 规则引擎、经验库、Provider 抽象、LLM 三模式、安全事件识别、patch 校验和有限重试 | 默认离线安全模式；当前环境未配置 TraceCoder API key/model，LLM 实际调用未启用 |
| 契约 | `perception_observation` 正式 Schema、Schema `$ref` 校验、规范化字段和契约测试 | `object_id`、`destination_id` 是正式跨模块字段；兼容别名只保留在 Mock/边界适配器内 |

目前已经打通的是**软件级离线闭环**：意图 → task.v1 → 策略 → Mock 执行 → TraceCoder feedback.v1。它证明了接口、安全门禁、错误处理和可追溯证据能够联调，但不等同于真实机器人已经完成闭环。软件级完成度约 95%；生产级完成度约 65%–75%，差距主要来自真实服务、仿真/硬件后端和线上证据。

主线最近一次全量验证结果为：

```text
129 passed, 1 skipped, 118 subtests passed
```

### 本次 C1 修复提交范围

本次提交包含 C1 最终清单要求的代码、契约、测试、演示和交付指南：

- A：执行能力门控、稳定对象 ID、唯一 UUID `task_id`、task.v1 目的地字段收紧、尺寸校验和歧义指标入口；
- B：CodeArts `required` 模式、五种原子动作、`code=null`、调用 provenance 和失败阻断；
- C：统一 capabilities、动作/参数/引用校验、执行 provenance、恢复上限和安全事件边界；
- D：安全事件识别、patch 合法性与“无变化”拦截、`patch=null` 语义和有限重试；
- 公共层：共享策略校验器、收紧的 v1 Schema、Pipeline correlation/run 元数据、前端来源展示和验收指标。

本地生成的 `.codeartsdoer/`、`.opencode/`、PPT、渲染目录、运行报告和包含个人路径的附件不属于源代码交付，不应加入 Git。真实 CodeArts、在线 LLM、Isaac Sim 和真机验证仍需在具备相应凭证/环境的机器上完成。

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

以下顺序延续本次 C1 修复后的交付方案：先保护已提交基线，再逐步接入真实依赖；每一步都保持 Mock 回归集可运行。

1. **完成 GitHub 基线保护**：确认 C1 提交已推送，添加可回滚标签，并让 CI 固定运行契约、集成和 E2E 测试。
2. **冻结 v1 契约与正式字段**：继续保证 `object_id`、`destination_id` 只在边界兼容旧字段，禁止旧字段穿透正式 Schema。
3. **打通可信能力链**：把 `perception_observation` 与场景/执行能力注册表关联；缺少抓取或目的地能力时必须阻断。
4. **接入 Isaac Sim**：输出真实轨迹、碰撞/安全事件、耗时和可复现日志，复用 `execution.v1` 与 Mock 回归集。
5. **受控启用 CodeArts**：配置不入库的 agent/model，完成 3 个成功样本及超时、非法 JSON、未知动作、错误实体 ID 失败矩阵。
6. **受控启用 TraceCoder LLM**：先 `optional` 后 `required`，完成正常、抓取失败、目标未达、非法修复、持续失败五类证据采集。
7. **小范围真机试运行**：在仿真通过后加入急停、权限、超时、速度/工作空间限制、人工确认和审计日志。
8. **持续指标与仓库治理**：持续采集准确率、歧义 F1、漏澄清率、危险误执行率、延迟、回退率和成本；禁止提交密钥、个人路径、真机地址和大体积日志。

每个阶段的合并请求都应写明：输入/输出协议、影响范围、回滚方式、测试命令与结果，以及是否改变了真实执行边界。出现大量代码冲突或接口重构时，先暂停合并并单独评审，不把冲突解决结果直接视为功能正确。

## 八、提交和联调规则

- 分支命名：`feature/<模块>-<功能>` 或 `codex/<模块>-<功能>`。
- Pull Request 必须写明：输入协议、输出协议、测试命令和结果。
- CI 未通过契约测试时不得合并。
- 不提交 API 密钥、真机地址、个人路径和大体积日志。
- 每个失败都要能用 `task_id` 在 `testdata/` 中复现。
- 合并顺序遵循“先基线/契约，再模块接入，最后真实后端”：任何后续 PR 都应基于最新 `main` 重放并重新跑全量测试。

详细操作说明见 [`docs/联调仓库使用手册.md`](docs/联调仓库使用手册.md)；本轮按 C1 最终清单实施的修复顺序、验证门槛和 GitHub 分阶段交付方式见 [`docs/C1最终修复与GitHub交付指南.md`](docs/C1最终修复与GitHub交付指南.md)。
