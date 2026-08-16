# 策略生成模块（B 角色 — 冯海）

## 模块用途

把意图理解模块（A 角色）输出的 `task.v1` JSON，翻译成可执行 Python 控制脚本，
供执行模块（C 角色）在 Isaac Sim 中加载运行。

核心能力：
- 根据动作类型选择策略模板（CaP — Code as Policies）
- 填充目标物体、位置、约束等参数
- 对生成代码做三层安全校验（语法 + 黑白名单 + 物理断言）
- 支持双模式：模板模式（离线可用）和 LLM 模式（调用 CodeArts API）

## 文件说明

| 文件 | 说明 |
|---|---|
| `strategy_generator.py` | 策略代码生成器（双模式 + 6 个 CaP 模板） |
| `code_validator.py` | 代码安全校验器（AST + 正则黑白名单 + 物理断言） |
| `codearts_system_prompt.md` | CodeArts 系统提示词（含 5 个 CaP 样例） |
| `__init__.py` | 模块初始化 |

## 输入格式

接收标准 `task.v1` JSON（通过适配器转换）：

```json
{
  "schema_version": "task.v1",
  "task_id": "task-001",
  "action": "pick_and_place",
  "target_ids": ["obj-001"],
  "status": "READY",
  "blocking_reasons": []
}
```

## 输出格式

输出标准 `strategy.v1` JSON：

```json
{
  "schema_version": "strategy.v1",
  "task_id": "task-001",
  "steps": [{"step_id": "...", "action": "...", "arguments": {...}}],
  "code": "def task_main(): ..."
}
```

## 启动方法

通过适配器调用（联调仓库统一入口）：

```python
from integration.adapters import strategy
result = strategy.run(task_v1_json)
```

直接运行适配器自测：

```bash
python integration/adapters/strategy.py
```

## 依赖安装

本模块仅依赖 Python 标准库（`ast`、`re`、`json`、`pathlib`），无需额外安装。

LLM 模式支持配置 `CODEARTS_API_KEY`（也兼容 `OPENAI_API_KEY`）、
`CODEARTS_BASE_URL` 和 `CODEARTS_MODEL` 环境变量。

## Mock 使用方法

模板模式默认可用，无需 LLM 和 Isaac Sim：

```python
from strategy_generator import generate_strategy
result = generate_strategy({
    "intent_id": "task-001",
    "action": "pick_and_place",
    "target_object": "红色方块",
    "destination": {"x": 0.2, "y": 0.0, "z": 0.03},
})
print(result["code"])
```

## 测试

```bash
# 契约测试（验证输出符合 strategy.v1 协议）
pytest tests/contract/test_strategy_contract.py -v

# 模块单元测试
pytest tests/contract/test_strategy_generation.py -v
```

## 当前边界

1. 未配置 LLM Key 时自动使用模板模式；未知 action 只有在配置 LLM 后才可能由 LLM 处理。
2. 适配器会严格校验 `task.v1` 的版本、任务 ID、目标物体和必要坐标；不完整的 READY 任务会阻断，不再静默使用默认目标或默认目的地。
3. 当前仓库没有 `destination_id` 到 Isaac Sim 场景坐标的解析器，因此需要位置的动作仍需上游提供显式 `destination` 坐标。
4. `target_ids` 会被保留在 intent 和 strategy 步骤中；如只有 ID 没有可搜索的对象名称，仍需要下游场景解析器完成 ID 到对象的映射。
5. 代码校验通过只是执行前的一道门禁，真实机器人执行仍需 C 模块提供明确的运行时 API 和沙箱。

