# 意图理解模块（A）

本目录把本地意图理解引擎接入统一联调仓库，负责：

```text
perception.v1 + instruction
        ↓
场景构建 / 语义解析 / 实体绑定 / 约束与安全校验
        ↓
task.v1
```

## 对外适配器

统一入口在 [`integration/adapters/intent.py`](../../integration/adapters/intent.py)，实现：

```python
run({"instruction": str, "perception": dict}) -> dict
health() -> dict
```

适配器内部调用 `robot_intent_agent`，但跨模块只通过 `contracts/v1/task.schema.json` 输出，不把内部 Pydantic 模型暴露给策略、执行或反馈模块。

## 核心分层

| 层 | 目录/文件 | 职责 |
|---|---|---|
| 场景 | `robot_intent_agent/scene_builder/` | 将 perception.v1 转成语义场景图并推断空间关系 |
| 语义解析 | `semantic_parser/`、`semantic_reasoner/`、`semantic_compiler.py` | 动作、角色、条件、否定、顺序和歧义解析 |
| 实体绑定 | `grounding/` | 只从感知场景选择实体 ID，不由模型臆造 ID |
| 约束 | `constraint/` | 力、速度、空间和用户约束的可行域编译 |
| 安全门禁 | `safety/`、`final_plan_validator.py` | 缺角色、未知实体、冲突约束和危险动作阻断 |
| IR | `ir/`、`schemas/` | 生成可审计的内部任务表示并投影成 task.v1 |
| 领域知识 | `domain/`、`property_inference/` | 动作能力、实体关系、材质和可供性知识 |

## task.v1 映射

- `schema_version` 固定为 `task.v1`。
- `target_ids` 来自感知对象 ID 的确定性绑定。
- `destination_id` 来自 destination/support surface/recipient 的绑定结果。
- `status` 只输出 `READY`、`NEEDS_CLARIFICATION` 或 `BLOCKED`。
- `blocking_reasons` 使用内部校验错误码和未满足角色，便于下游和 TraceCoder 追踪。
- 约束细节保留在 `constraints`；其余内部审计信息放在额外诊断字段中，不参与下游决策。

动作名称采用显式映射：`PLACE -> pick_and_place`，`PUSH -> push`，`STACK -> stack`；其他动作保留为小写语义名。策略模块对暂不支持的动作应返回阻断，不在此处静默改写。

## 依赖与运行

```bash
python -m pip install -r modules/intent_understanding/requirements.txt
python -m unittest tests.contract.test_intent_schema -v
```

默认使用确定性的规则语义编译器；配置 DeepSeek Key 并选择 `llm`/`hybrid` 后，才会启用模型路径，失败时仍保留安全降级与审计信息。
