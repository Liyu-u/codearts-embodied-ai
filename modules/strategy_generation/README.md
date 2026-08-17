# 策略生成模块（B 角色 — 冯海）

## 模块用途

模块内部保留模板/LLM 代码生成能力；统一联调入口把意图理解模块（A 角色）
输出的 `task.v1` 降解成受信任的原子动作，交给执行模块（C 角色）。

> 安全边界：`integration/adapters/strategy.py` 的公开输出始终为
> `code: null`。C 不加载、不解释、不执行 B 生成的 Python 代码。

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
  "destination_id": "zone-001",
  "status": "READY",
  "blocking_reasons": []
}
```

## 输出格式

第一阶段输出标准 `strategy.v1` 五步抓取放置策略：

```json
{
  "schema_version": "strategy.v1",
  "task_id": "task-001",
  "steps": [
    {"step_id": "task-001-detect", "action": "detect_object", "arguments": {"object_id": "obj-001"}},
    {"step_id": "task-001-approach", "action": "move_to_object", "arguments": {"object_id": "$task-001-detect.object_id"}},
    {"step_id": "task-001-grasp", "action": "grasp", "arguments": {"object_id": "$task-001-detect.object_id"}},
    {"step_id": "task-001-move-target", "action": "move_to_target", "arguments": {"destination_id": "zone-001"}},
    {"step_id": "task-001-release", "action": "release", "arguments": {}}
  ],
  "code": null
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
python -m unittest tests.contract.test_strategy_schema -v
```

## 当前边界

1. 公开适配器第一阶段只接受 `READY + pick_and_place`。
2. 必须有且只有一个稳定 `target_ids`，并且必须有稳定 `destination_id`。
3. 其他动作和缺少绑定的任务输出阻断结果，不交给 C 执行。
4. 正式 B→C 接口只使用 `object_id` 与 `destination_id`；旧字段 `object_name`、`target` 会被 C 拒绝。
5. 内部代码生成器仍可单独研究，但它的代码不是 B-C 联调接口的一部分。
