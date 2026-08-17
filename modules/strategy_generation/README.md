# 策略生成模块（B 角色 — 冯海）

## 模块用途

统一联调入口接收意图理解模块（A 角色）的 `task.v1`，通过华为云码道
CodeArts 官方 CLI 调用代码智能体生成结构化 `strategy.v1`，本地安全校验后
交给执行模块（C 角色）。CodeArts 不被当作 `/chat/completions` 模型接口使用。

> 安全边界：`integration/adapters/strategy.py` 的公开输出始终为
> `code: null`。C 不加载、不解释、不执行 B 生成的 Python 代码。

核心能力：
- 使用 `codearts run --format json` 非交互调用 CodeArts 智能体
- 使用项目级 `robot-strategy` Skill 约束策略生成流程
- 校验动作白名单、参数、稳定实体 ID、步骤上限和恢复策略
- 支持 `off`、`auto`、`required` 三种运行模式
- 保留本地确定性五步策略作为可审计的安全回退

## 文件说明

| 文件 | 说明 |
|---|---|
| `codearts_agent.py` | CodeArts CLI 调用、输出提取、策略安全校验 |
| `.codeartsdoer/skills/robot-strategy/SKILL.md` | 项目级 CodeArts 策略 Skill |
| `strategy_generator.py` | 旧版模板/模型代码生成实验，不进入 B-C 正式链路 |
| `code_validator.py` | 旧版 Python 代码校验器，不进入 B-C 正式链路 |
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

CodeArts 成功时输出经过本地复核的结构化策略；`mode` 为
`codearts_agent`，并通过 `provenance` 记录实际调用通道：

```json
{
  "schema_version": "strategy.v1",
  "task_id": "task-001",
  "steps": [
    {"step_id": "task-001-detect", "action": "detect_object", "arguments": {"object_name": "obj-001"}},
    {"step_id": "task-001-approach", "action": "move_to_object", "arguments": {"object_id": "$task-001-detect.object_id"}},
    {"step_id": "task-001-grasp", "action": "grasp", "arguments": {"object_id": "$task-001-detect.object_id"}},
    {"step_id": "task-001-move-target", "action": "move_to_target", "arguments": {"target": "zone-001"}},
    {"step_id": "task-001-release", "action": "release", "arguments": {}}
  ],
  "code": null,
  "mode": "codearts_agent",
  "provenance": {
    "provider": "huaweicloud-codearts-agent",
    "transport": "codearts-cli"
  }
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

## CodeArts 配置

先安装并登录华为云码道 CLI，确认下面的命令可运行：

```bash
codearts models
codearts agent list
```

然后配置环境变量：

```dotenv
CODEARTS_STRATEGY_MODE=required
CODEARTS_CLI=codearts
CODEARTS_STRATEGY_AGENT=
CODEARTS_STRATEGY_MODEL=
CODEARTS_STRATEGY_TIMEOUT_S=120
```

三种模式：

- `required`：比赛演示推荐。CodeArts 未安装、调用失败或输出不安全时阻断。
- `auto`：开发联调推荐。优先调用 CodeArts，失败时使用本地五步策略，并记录失败原因。
- `off`：完全关闭 CodeArts，只使用本地确定性策略。

如果已经创建专用的只读 CodeArts 智能体，将名称写入
`CODEARTS_STRATEGY_AGENT`。运行时智能体应禁止文件写入和 Shell 权限。

## 调用方式

B 适配器调用保持不变：

```python
from integration.adapters import strategy

result = strategy.run(task_v1_json)
assert result["mode"] == "codearts_agent"
```

## 测试

```bash
python -m unittest tests.unit.test_codearts_agent tests.contract.test_strategy_schema -v
```

## 当前边界

1. 公开适配器第一阶段只接受 `READY + pick_and_place`。
2. 必须有且只有一个稳定 `target_ids`，并且必须有稳定 `destination_id`。
3. 其他动作和缺少绑定的任务输出阻断结果，不交给 C 执行。
4. `object_name` 与 `target` 是为了兼容 D 现有字段名；字段值仍是 perception 的稳定 ID。
5. CodeArts 输出永远被视为不可信输入，必须通过本地校验才交给 C。
6. 内部旧版 Python 代码生成器仍可单独研究，但不是 B-C 正式接口。
7. 当前只完成 CLI/Skill 接入；后续可把校验、Mock/Isaac 执行封装成 MCP 工具。
