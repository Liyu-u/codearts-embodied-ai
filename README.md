# 具身智能系统统一联调仓库

本仓库用于把各成员的模块接入同一条可复现的机器人闭环：

```text
自然语言 + 感知 JSON
        ↓
意图理解与实体绑定
        ↓ task.v1
CodeArts 策略生成
        ↓ strategy.v1
Isaac Sim / 真机执行
        ↓ execution.v1
TraceCoder / TraceProbe 反馈
        ↓ feedback.v1
回归测试与策略修正
```

## 一、仓库原则

1. `contracts/` 是唯一接口标准；模块不得私自约定字段。
2. 模块通过适配器连接，不直接引用其他模块的内部代码。
3. 先 Mock，后仿真，最后真机；每一步都保留可追踪的 `task_id`。
4. 危险、缺字段或无法执行的任务必须明确阻断，不能静默猜测。

## 二、目录结构和任务

```text
.
├─ contracts/                 # 模块间 JSON Schema 和版本说明
│  └─ v1/                     # perception/task/strategy/execution/feedback
├─ modules/                   # 各成员模块的接入位置
│  ├─ perception/             # 感知：输出真实物体、位姿、能力信息
│  ├─ intent_understanding/   # A：语言理解、目标绑定、安全门禁
│  ├─ strategy_generation/    # B：CodeArts 生成可执行策略
│  ├─ executor/               # C：Isaac Sim 或真机执行
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

## 三、每个模块应该放在哪里

成员只维护对应的 `modules/<模块名>/` 和 `integration/adapters/`。如果模块已有独立仓库，可以作为外部代码引入，但对外仍必须实现统一适配器：

```text
run(input_json: dict) -> output_json: dict
health() -> dict
```

输入和输出必须分别符合 `contracts/v1` 中的 Schema。接口修改时，同时修改 Schema、示例和契约测试，并说明兼容策略。

## 四、日常使用

```bash
make contract-test   # 检查所有协议 JSON
make integration-test # 运行模块联调测试
make e2e              # 运行完整闭环测试
```

推荐开发顺序：

1. 用 `testdata/` 的 Mock 感知数据打通五段 JSON；
2. 接入真实意图理解和策略生成；
3. 接入 Isaac Sim，核对执行轨迹和安全事件；
4. 接入 TraceCoder 的失败诊断与重试；
5. 最后接真机，并增加急停、权限、超时和人工确认。

## 五、提交和联调规则

- 分支命名：`feature/<模块>-<功能>`。
- Pull Request 必须写明：输入协议、输出协议、测试命令和结果。
- CI 未通过契约测试时不得合并。
- 不提交 API 密钥、真机地址、个人路径和大体积日志。
- 每个失败都要能用 `task_id` 在 `testdata/` 中复现。

## 六、后续研讨重点

1. 冻结 `v1` 字段和错误码，明确谁负责每个字段。
2. 完成 Mock 闭环，再进行 Isaac Sim 联调。
3. 统一执行结果、轨迹、耗时和安全事件格式。
4. 建立“感知 → 理解 → 策略 → 执行 → 反馈”的端到端回归集。
5. 仿真通过后再进行真机小范围验证，禁止直接跳过安全门禁。

详细操作说明见 [`docs/联调仓库使用手册.md`](docs/联调仓库使用手册.md)。

## 七、TraceCoder 模块接入说明（feedback 环节）

**定位**：`modules/evaluator/`（D 模块）内的 TraceCoder 负责"执行后反馈与策略修正"，
即把上游执行结果喂给它做失败归因，返回可再次迭代的修复后策略。

**代码位置**：
- 引擎：`modules/evaluator/tracecoder/`（轻量仿真 + 三角色修复 + HLLM 经验库，离线可跑）
- 适配器：`integration/adapters/tracecoder.py`（`run()` / `health()`，输出 `feedback.v1`）
- 编排：`integration/pipeline.py` 已把 `{task, strategy, execution}` 三份上下文传给 `feedback.run()`

**调用方式**（pipeline 已接入，也可单独调用）：
```python
from integration.adapters.tracecoder import run, health
health()                                   # 模块健康检查
out = run({"task": task_v1, "strategy": strategy_v1, "execution": execution_v1})
# out = feedback.v1: {schema_version, task_id, diagnosis, retryable, patch}
```

**测试**（Python 标准库，无需额外依赖）：
```bash
python -m unittest discover -s tests -t .
```
契约测试（tests/contract/）校验 TraceCoder 输出与 `contracts/v1/feedback.v1` 一致；
集成测试（tests/integration/）用 Mock 执行器跑通"意图 → 策略 → 执行 → 反馈"闭环。

**演进规划**：当前 `execution.v1` 由 TraceCoder 内置轻量仿真（或 Mock 执行器）产出；
后续接入 Isaac Sim 后，真实仿真日志同样以 `execution.v1` 输入，引擎的
诊断/修复逻辑无需改动——适配器接口已按"执行日志 + 当前策略"为输入设计。
