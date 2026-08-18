# 自动化闭环验收题集

`cases/` 中每个 JSON 文件是一道可执行验收题，包含：

- `scene`：感知场景来源；
- `instruction`：用户自然语言指令；
- `executor.failures`：可选的 C 模块故障注入；
- `expected`：task、strategy、execution、feedback 和安全停止的判定条件。

当前题集共 18 道：

| 编号 | 类型 | 验证内容 |
|---|---|---|
| 001-002 | 正常流程 | P/A/B/C/D 成功闭环与最终物体状态 |
| 003-005 | 意图安全 | 目标歧义、目标不存在、目标区缺失时在 A 阶段阻断 |
| 006 | 策略安全 | B 阻断当前阶段不支持的动作 |
| 007 | 执行反馈 | C 返回失败，D 产生不可继续的反馈 |
| 008 | 执行安全 | 抓取和恢复抓取持续失败后安全停止 |
| 009 | 反馈恢复 | TraceCoder 修复策略后重新交给 C 并成功 |
| 010-015 | 动作边界 | A 识别 pick/transfer/dynamic_grasp/stack/pour/wait，B 按当前能力范围阻断 |
| 016-017 | 动作边界 | fetch/handover 因缺少交付或接收方信息在 A 阶段澄清 |
| 018 | 动作边界 | custom 无法安全归类，在 A 阶段澄清并禁止执行 |

当前闭环的业务动作边界是：A 输出 `READY` 且实体绑定完整时，`pick/grasp`、
`PLACE`（映射为 `pick_and_place`）、`transfer`、有明确目标区的 `fetch` 和
`stack` 可以进入 B；它们分别复用三步或五步 C 原子策略。
`handover`、`dynamic_grasp`、`push`、`pour`、`wait` 仍可被 A 识别或归类，
但当前没有共同的 C 执行源，因此 B 阻断或 A 要求澄清。
无法安全归类的 `custom` 直接在 A 阶段阻断，不进入 B/C。
C 的执行原子动作是 `detect_object`、`move_to_object`、`grasp`、
`move_to_target`、`release`；恢复和 `stop` 是执行控制机制，不是当前用户级业务动作。

运行题集：

```bash
make acceptance-test
```

全链路 E2E 也会通过 `tests/e2e/` 自动发现这套题。测试默认关闭 CodeArts CLI，使用确定性的 `strategy.v1` 原子策略和 Mock C 后端，保证 CI 和离线联调可重复。
