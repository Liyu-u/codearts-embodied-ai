# C1 最终修复与 GitHub 交付指南

本仓库的最终修复按风险从高到低分为 A → 共享校验 → D → 真实 Provider → 前端与指标。每一阶段都必须先通过本地回归，再提交独立 commit；禁止把未验证的多阶段改动一次性推到 `main`。

## 1. 修复顺序与完成条件

### A：意图与执行门禁

1. 感知对象必须保留稳定 `id`；缺失或重复 ID 直接 `BLOCKED`，不得生成 `obj-001` 等临时 ID。
2. 每次任务使用独立 UUID `task_id`；请求号/场景号只作为 correlation metadata。
3. 目标动作必须有 `execution.graspable=true`，目的地动作必须有 `execution.valid_destination=true`。字段缺失和明确为 `false` 都不得进入 B/C。
4. 尺寸必须显式提供 `x/y/z` 或 `width/height/depth`（也兼容三元数组），不得静默补默认尺寸。
5. `task.v1` 只传 `destination_id`，不得传目的地坐标。

验收：`tests/unit/test_intent_execution_gate.py`、`tests/contract/test_intent_schema.py`、`tests/e2e/test_closed_loop_acceptance.py`。

### 共享策略校验：B/C/D/Pipeline 唯一安全边界

1. 五个原子动作固定为 `detect_object`、`move_to_object`、`grasp`、`move_to_target`、`release`。
2. 参数只接受稳定 `object_id`/`destination_id`；引用必须指向已经完成的步骤，禁止前向或自引用。
3. 恢复 `max_attempts` 不超过 C capabilities 的上限（默认 3），`on_exhausted` 必须是 `stop`，`code` 必须为 `null`。
4. D patch 必须经过同一校验器，并且执行形状必须真的变化。

验收：`tests/unit/test_strategy_policy.py`、`tests/contract/test_strategy_schema.py`、`tests/unit/test_strategy_interpreter.py`。

### D：执行证据与安全重试

1. 识别 `WORKSPACE_VIOLATION`、`COLLISION_DETECTED`、`ACTION_TIMEOUT`、`RECOVERY_EXHAUSTED`，以及 `error/critical` 严重度事件。
2. 只有真实执行状态为 `FAILED`、无安全事件、patch 合法且确实变化时才允许重试。
3. `SUCCEEDED`、`SAFE_STOP`、非法 patch、未变化 patch 或达到重试上限时，`retryable=false` 且 `patch=null`。
4. Pipeline 默认最多重试 2 次，单动作恢复最多 3 次。

验收：`tests/contract/test_feedback_schema.py`、`tests/integration/test_tracecoder_pipeline.py`、`tests/integration/test_tracecoder_llm.py`。

### 真实 Provider 与证据

正式 CodeArts 验收必须设置 `CODEARTS_STRATEGY_MODE=required`；CLI 超时、非法 JSON、未知动作、错误实体 ID、认证失败都必须阻断，不能回退到本地规则。TraceCoder 正式在线验收使用 `TRACECODER_LLM_MODE=required`。

每个 Provider 结果必须保留：`source/provider`、`agent`、`model`、`request_id/run_id`、`latency_ms`、`fallback`、`validation`。离线规则、Fake Provider、历史报告与真实 CodeArts/LLM 结果必须分开标注。

### 前端与指标

前端显示 A 的请求/实际引擎，B 的 CodeArts 或本地规则来源，C 的 Mock/真实后端，D 的 LLM 模式、模型、耗时、请求号、校验及回退状态。验收指标至少包括绑定准确率、歧义 F1、漏澄清率、危险误执行率。

## 2. 本地验证命令

在仓库根目录执行：

```powershell
& 'C:\Users\14810\AppData\Local\Programs\Python\Python311\python.exe' -m pytest tests -q
```

提交前还要执行：

```powershell
git diff --check
```

真实 Provider 只在具备授权和模型配置的环境运行；不得把 API key、账号、Cookie 或本地生成的 PPT/报告目录提交到仓库。

## 3. 按顺序提交 GitHub

建议从 `main` 创建工作分支：

```powershell
git switch main
git pull --ff-only origin main
git switch -c codex/c1-final-repair
```

按下列顺序拆分 commit，每个 commit 都能独立说明“改了什么、为什么安全、哪些测试通过”：

1. `fix(A): enforce execution gates and stable task identity`
   - A 门禁、UUID、对象 ID、尺寸、`task.v1` 字段和 A 测试。
2. `fix(contract): add shared strategy policy and capabilities`
   - 共享 validator、strategy schema、B/C capabilities、引用和恢复限制。
3. `fix(D): harden safety events and retry decisions`
   - 安全事件、patch 合法性、`patch=null` 规则、重试上限和 D 测试。
4. `feat(provider): record required CodeArts and TraceCoder evidence`
   - required 模式、真实/回退来源、模型/Agent/请求号/耗时/校验，以及 Provider 测试报告。
5. `feat(ui): show runtime provenance and acceptance metrics`
   - 前端来源证据、指标报告和最终验收测试。
6. `docs: document C1 repair and delivery gates`
   - 本文档及运行说明。

每次提交流程：

```powershell
git add <本阶段明确文件>
git diff --cached --check
git commit -m "<上面的 commit message>"
git push -u origin codex/c1-final-repair
```

GitHub 上按 commit 顺序创建 PR 或分阶段 PR。每个 PR 的描述固定包含：变更范围、契约变化、测试命令/结果、是否使用真实 Provider、是否存在回退、风险与回滚点。CI 全绿后再合并；最终合并顺序必须与上述编号一致。

不要执行 `git add .`：工作区中的 `.codeartsdoer/`、`.opencode/`、PPT、渲染目录、临时报告和本地 DOCX 均属于本地工件，不是系统修复内容。
