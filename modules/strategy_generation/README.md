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
- 支持 `planner`、`quality`、`max` 三档调用策略：生成后可由独立 CodeArts 审查
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
    {"step_id": "task-001-detect", "action": "detect_object", "arguments": {"object_id": "obj-001"}},
    {"step_id": "task-001-approach", "action": "move_to_object", "arguments": {"object_id": "$task-001-detect.object_id"}},
    {"step_id": "task-001-grasp", "action": "grasp", "arguments": {"object_id": "$task-001-detect.object_id"}},
    {"step_id": "task-001-move-target", "action": "move_to_target", "arguments": {"destination_id": "zone-001"}},
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
CODEARTS_STRATEGY_MODEL=huaweicloud-maas/openpangu-2.0-flash
CODEARTS_STRATEGY_TIMEOUT_S=180
# 调用策略：planner（默认）、quality（一次独立审查）、max（两次独立审查）
CODEARTS_STRATEGY_POLICY=quality
# 可选：隔离项目插件，仅执行纯 CodeArts 非交互请求
CODEARTS_CLI_PURE=1
# 若 Windows 上出现 .config/opencode 的 EEXIST/EPERM，可指向一个可写目录
# XDG_CONFIG_HOME=C:\\Users\\<user>\\.codeartsdoer\\cli-data\\xdg-config
# XDG_DATA_HOME=C:\\Users\\<user>\\AppData\\Local\\CodeArts\\xdg-data
```

三种模式：

- `required`：比赛演示推荐。CodeArts 未安装、调用失败或输出不安全时阻断。
- `auto`：开发联调推荐。优先调用 CodeArts，失败时使用本地五步策略，并记录失败原因。
- `off`：完全关闭 CodeArts，只使用本地确定性策略。

调用策略（仅在 CodeArts 实际启用时生效）：

- `planner`：一次 CodeArts 规划，延迟最低，适合开发和高吞吐。
- `quality`：规划后再发起一次独立 critic；critic 只返回 `PASS/REPAIR_REQUIRED/BLOCK`，
  只有 `PASS` 且本地契约再次通过才进入 C，适合正式演示。
- `max`：规划后连续两次独立 critic 都必须 `PASS`，适合验收、回归和高风险动作；代价是约
  3 倍 CodeArts 调用耗时/配额。

推荐路由：日常使用 `auto + quality`，正式演示 `required + quality`，发布验收或抽样回归
使用 `required + max`。无论档位如何，本地 action/ID/引用/code 安全闸门始终开启，CodeArts
不能直接改写候选策略，也不能绕过执行器契约。

Demo 使用线程锁串行化 CodeArts 请求；重复基准运行时不要同时启动其他 CodeArts
评估任务，否则云端并发或本地会话资源会影响延迟和稳定性结论。

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

1. 公开适配器只接受 `READY` 且已完成稳定实体绑定的任务。
2. 当前共同开放的用户级动作是 `pick/grasp`、`pick_and_place/place`、`transfer`、`fetch`（必须有明确目标区）和 `stack`；CodeArts 对这些动作使用动作级提示词和对应的三步/五步策略校验。
3. 抓取类动作必须且只能有一个稳定 `target_ids`；搬运/放置/堆叠还必须有稳定 `destination_id`，`stack` 不能把目标物自身作为底座。
4. 正式 B→C 接口只使用 `object_id` 与 `destination_id`；旧字段仅在 MockBackend 直接调用兼容层保留。
5. `stack` 通过 C 的 `move_to_target` 传递 `placement_mode=stack_on`，不执行任意代码或新增隐式动作。
6. `push`、`dynamic_grasp`、`handover`、`pour`、`wait`、`custom` 仍输出阻断/澄清结果，因为当前没有完整的 C 执行源或安全闭环。
7. CodeArts 输出永远被视为不可信输入，必须通过本地校验才交给 C；内部旧版 Python 代码生成器不是 B-C 正式接口。
