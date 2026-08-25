# 具身智能系统统一联调仓库

本项目面向 2026 年“挑战杯”揭榜挂帅华为赛道，围绕“基于华为云码道（CodeArts）代码智能体解决复杂软件工程问题”构建可复现的具身智能闭环 Demo。系统把自然语言意图、策略生成、仿真执行和结果反馈接成一条带契约、可审计、可安全阻断的链路。

当前结论：**A、B、D 已完成真实智能调用；B 的 7 类开放动作已通过真实 CodeArts CLI 验证；C 已完成 Isaac Sim Ground Truth 感知与远程执行闭环。系统达到比赛演示级完整闭环，但尚未达到真实相机、真机和生产级稳定性标准。**

## 1. 系统架构

~~~text
自然语言指令 + 场景观察
          │
          ▼
P/C 感知边界 ──► perception.v1
          │
          ▼
A 意图理解（DeepSeek） ──► task.v1
          │
          ▼
B 策略生成（华为云 CodeArts） ──► strategy.v1
          │
          ▼
C 执行器（Mock / Isaac Sim / 预留真机） ──► execution.v1
          │
          ▼
D 反馈与纠错（TraceCoder + DeepSeek） ──► feedback.v1
          │
          └── 失败时：安全阻断、有限重试、结构化修复证据
~~~

各模块职责如下：

| 模块 | 当前完成度 | 已验证能力 | 当前边界 |
| --- | --- | --- | --- |
| P/C 感知边界 | 仿真真值链路已完成；真实视觉未完成 | Mock 场景、Isaac Sim USD/PhysX Ground Truth、稳定对象 ID、位姿和关系标准化 | 当前不是 RGB/RGB-D 摄像头识别，真实相机和真机传感器尚未接入 |
| A 意图理解 | 比赛演示级可用 | DeepSeek 智能模式、自然语言解析、目标绑定、歧义识别、安全阻断、task.v1 输出 | 复杂开放世界指令仍需扩大数据集和重复统计；依赖 perception.v1 的稳定事实 |
| B 策略生成 | 真实 CodeArts 智能调用已完成 | CodeArts CLI、AK/SK、代理绕过、动作级提示词和安全校验；`pick`、`grasp`、`pick_and_place`、`place`、`transfer`、`fetch`、`stack` 真实调用 7/7 成功 | 云端排队可能带来 13–19 秒延迟和偶发输出超时；`push`、`pour`、`handover` 等未纳入开放动作集 |
| C 执行器 | Mock 与 Isaac Sim Ground Truth 执行闭环已完成 | 五步抓取搬运、三步抓取、堆叠、轨迹记录、超时/碰撞/安全停止；远程 Isaac Sim 真实位姿发生变化 | 真实相机感知、真机驱动和长期稳定运行尚未验收；Isaac DOF/资产环境警告仍需加固 |
| D TraceCoder | 反馈与有限修复闭环已完成，稳定性持续加固 | DeepSeek required/optional/off、feedback.v1、失败归因、有限补丁、重试和安全校验；可消费 Isaac 执行证据 | 长时间重复测试仍需进一步稳定；修复策略不能绕过 B/C 的 Schema 和动作白名单 |

模块关系：A 只负责把语言变成有约束的 `task.v1`；B 只负责生成并校验 `strategy.v1`；C 只执行白名单原子动作并返回 `execution.v1`；D 只根据执行证据输出 `feedback.v1` 和受限补丁。任何模块失败都会在对应边界阻断，不会静默越权。
## 2. 仓库结构

~~~text
.
├─ contracts/v1/             # perception/task/strategy/execution/feedback Schema
├─ modules/
│  ├─ perception/            # 感知规范化和 Isaac Ground Truth 适配
│  ├─ intent_understanding/  # A：意图理解与安全门禁
│  ├─ strategy_generation/   # B：CodeArts 策略生成与校验
│  ├─ executor/              # C：Mock、Isaac Sim、真机接入边界
│  └─ evaluator/tracecoder/  # D：反馈、诊断、修复
├─ integration/
│  ├─ adapters/              # A/B/C/D 统一适配器和 Isaac 感知适配器
│  ├─ pipeline.py            # 闭环编排
│  └─ strategy_policy.py     # 策略能力、安全和参数校验
├─ testdata/                 # 脱敏验收题集和基准数据
├─ tests/                    # unit、contract、integration、e2e 测试
├─ docs/                     # 配置、接口、服务器和交付说明
├─ demo/                     # 本地可视化 Demo
├─ tools/
│  ├─ setup.ps1              # Windows 一键配置入口
│  ├─ doctor_config.py       # 配置与 CodeArts 可见性体检
│  ├─ run_ground_truth_executor_acceptance_v4.py # Isaac 容器入口
│  └─ run_remote_ground_truth_acceptance_final.ps1 # 远程验收脚本
├─ .env.example              # A 配置模板（无密钥）
├─ codearts.env.example      # B 配置模板（无 AK/SK）
└─ tracecoder_llm.env.example# D 配置模板（无密钥）
~~~

## 3. 参赛要求对照（华为云 CodeArts 赛道）

官方榜题要求作品围绕明确的复杂软件工程场景，提交“技术方案 + 可运行产品”，并提供 PPT、视频/可访问演示环境、完整源代码、依赖和部署运行说明；代码中必须包含基于 CodeArts 改造或生成的核心业务部分，并说明第三方模型、组件和开源协议。

| 参赛要求 | 本项目现状 | 判定 |
| --- | --- | --- |
| 明确场景、痛点和技术方案 | 具身智能自然语言任务规划、仿真执行和反馈纠错，架构与接口已文档化 | 已具备 |
| 可运行产品/演示 | A→B→C→D 软件闭环可运行；C 已在校园服务器 Isaac Sim 6.0 CUDA 真实执行 | 已具备演示级产品 |
| 使用 CodeArts 核心能力 | B 通过 CodeArts CLI 真实调用智能体，策略带 provider/source/fallback provenance，严格模式失败即阻断 | 已具备 |
| 源代码、部署和复现说明 | 代码、Schema、测试、setup.ps1、配置模板、远程 Isaac 脚本已入库 | 已具备，仍需最终打包核验 |
| PPT、演示视频或线上环境 | 本仓库可支撑录制；当前 Isaac 运行在校园服务器，尚未形成华为云公开演示 URL | 待完成 |
| 原创性与第三方合规说明 | 需在最终方案中补充团队原创声明、DeepSeek/TraceCoder/Isaac 及开源组件来源与许可证 | 待补齐 |
| 量化测试与效果评估 | 已有契约、单元、集成和仿真验收；还需扩大重复运行、异常和延迟统计 | 基本具备，需增强 |

官方榜题及提交说明以[2026 年榜单 PDF 中第 18 项华为赛道](https://youth.qau.edu.cn/userfiles/files/tw/20260510150244.pdf)和[华为云竞赛提交页面](https://developer.huaweicloud.com/competition/information/1300000228/submission)为准；最终以主办方最新通知为准。

## 4. 当前完成度与边界

### A：意图理解

- DeepSeek 真实调用已验证，支持实体绑定、稳定 ID、歧义识别和安全阻断。
- 正式智能验收使用 RIA_PLANNER_ENGINE=llm，不依赖规则回退。
- 输入仍来自统一 perception.v1；真实相机接入属于 C/P 的后续工作。

### B：CodeArts 策略生成

- CodeArts CLI、AK/SK、模型和智能体可见性已配置并通过体检。
- required 模式已实测：调用失败会阻断，不静默回退到本地规则。
- 策略必须符合 strategy.v1、动作白名单和目标能力约束。

### C：感知与执行

- Mock 执行器、Isaac Sim 6.0 CUDA 执行器和 Ground Truth 感知适配器已完成。
- 远程测试中抓取、移动、释放五步动作均成功，物体位姿发生真实变化。
- 当前感知是 Isaac Sim 的 USD/PhysX 状态读取，不是 RGB/RGB-D 相机视觉识别；真机驱动尚未验收。
- 远程启动、容器清理、长时间稳定性和 Isaac 的 DOF 警告仍需继续加固。

### D：TraceCoder 反馈

- DeepSeek required/optional/off 模式和结构化 feedback.v1 已接通。
- 可根据执行证据判定通过、归因失败并生成有限修复补丁；修复必须再次校验。
- 已完成与 Isaac 执行证据的消费验证。

## 5. 已完成验收证据

最近一次 Ground Truth Isaac Sim 闭环证据位于 reports/gtv2-20260822-013151/（可作为内部验收附件，不含密钥）：

- perception.json：schema_version=perception.v1，来源 isaac_sim.usd_physx，位姿来自实时 USD/PhysX 驱动。
- execution.json：status=SUCCEEDED，5 个动作步骤完成，物体位移约 0.1119 m，安全事件数为 0。
- feedback_summary.json：D 判定 D_ACCEPTED，DeepSeek 调用 3/3 成功、0 次回退，质量评分 97.2。
- task_id=ce09edf0-08b5-40bc-8c20-4cd2177caf2f 在 A/B/C/D 证据中一致。
- perception.v1 和 execution.v1 契约校验均返回 0 个错误。

已通过的针对性测试：

~~~text
Ground Truth 感知单元 + 感知契约 + 感知服务：10 passed
Mock Isaac pipeline + Isaac backend + execution contract：21 passed
~~~

完整测试集仍有一个既有 TraceCoder 契约测试出现长时间不结束，尚未将全量测试声明为全绿；这不影响上述已通过的 Ground Truth/执行器针对性测试，但应在发布前修复或隔离。

## 6. 拉取、配置和部署

### 6.1 拉取代码

~~~powershell
git clone https://github.com/Liyu-u/codearts-embodied-ai.git
cd codearts-embodied-ai
~~~

如果已有本地副本：

~~~powershell
git fetch origin
git pull --ff-only origin main
~~~

### 6.2 Windows 本地一键配置

首次运行：

~~~powershell
powershell -ExecutionPolicy Bypass -File .\tools\setup.ps1
~~~

脚本会创建 .venv、安装依赖、生成本地配置并执行体检。真实密钥只写入被 Git 忽略的本地文件/用户环境，不会提交仓库。

严格智能模式：

~~~powershell
.\tools\setup.ps1 -IntentMode llm -TraceCoderMode required -CodeArtsMode required
.\.venv\Scripts\python.exe tools\doctor_config.py --live-codearts
~~~

配置文件对应关系：

| 文件 | 模块 | Git 状态 |
| --- | --- | --- |
| .env | A / DeepSeek | 本地私密，不提交 |
| codearts.env | B / CodeArts CLI AK/SK | 本地私密，不提交 |
| tracecoder_llm.env | D / TraceCoder | 本地私密，不提交 |
| .env.example、codearts.env.example、tracecoder_llm.env.example | 配置模板 | 提交 |

CodeArts CLI 也可直接读取当前用户环境变量：

~~~powershell
[Environment]::SetEnvironmentVariable('CODEARTS_CLI_AK', '<你的AK>', 'User')
[Environment]::SetEnvironmentVariable('CODEARTS_CLI_SK', '<你的SK>', 'User')
~~~

配置后重新打开终端，再执行 codearts models、codearts agent list 和 doctor_config.py --live-codearts。

### 6.3 本地闭环与测试

~~~powershell
# 运行离线/Mock 闭环
python -m unittest discover -s tests/contract -t . -q
python -m unittest discover -s tests/integration -t . -q
python -m unittest tests.e2e.test_closed_loop_acceptance -v

# Ground Truth 感知单元
python -m unittest tests.unit.test_isaac_ground_truth_perception -v
~~~

本地 Demo 默认使用 C Mock，适合展示消息流和故障修复；它不能替代 Isaac Sim 真实执行证据。

前端接口、配置方法、当前 Demo 边界和后续 Isaac Sim/RGB-D/真机接入要求见 [`docs/前端接口与仿真平台接入说明.md`](docs/前端接口与仿真平台接入说明.md)。

上线前最终验收使用 `tools/run_final_acceptance.py`，按离线回归、CodeArts 在线、LLM 留出泛化、Isaac Sim HIL、RGB-D 相机 HIL 和真机安全六档分别出具证据。完整矩阵见 `testdata/acceptance/final_acceptance_matrix_v1.json`；只有六档全部 `PASS` 才能将最终报告判定为上线可接受。

### 6.4 远程 Isaac Sim 验收

前提：校园 VPN、SSH 账号、远程服务器上的 Docker、NVIDIA GPU、Isaac Sim 6.0 镜像和资产目录均可用。

使用已有 A/B 策略运行 C：

~~~powershell
.\tools\run_remote_ground_truth_acceptance_final.ps1 -Server 10.16.0.40 -Port 5122 -User stu_01 -StrategyFile reports/gt-20260822-004458/live_chain_ab_ground_truth.json -Device cuda
~~~

脚本只上传代码包和脱敏策略，不读取 .env、CSV、AK/SK；结果下载到 reports/gt-final-<timestamp>/。远程服务器的 Isaac 资产路径、镜像和端口属于部署环境，不写入仓库凭证。运行结束后应检查容器是否退出并清理临时目录。

当前脚本主要用于 C 的远程验收；A/B/D 已在本地严格智能模式运行。后续应将 A→B→远程 C→D 编排成一个带重试、超时、容器清理和制品校验的一键命令。

## 7. 提交前检查清单

1. 不提交 .env、codearts.env、tracecoder_llm.env、CSV、AK/SK、VPN/SSH 密码、个人路径和大体积临时日志。
2. 修改 contracts/v1/ 时，同步更新适配器、示例和契约测试。
3. 运行 git diff --check、目标单元/契约/集成测试和一次真实 CodeArts 体检。
4. 记录每次验收的 task_id、模型、provider、fallback、执行状态、耗时、安全事件和结果文件。
5. 提交 PR 时说明输入输出协议、影响范围、回滚方式、测试命令和结果。
6. 最终参赛包还需要单独准备：技术方案 PPT、演示视频或华为云可访问环境、部署说明、测试报告、第三方组件与许可证说明、原创性声明和审核通过的报名表。

## 8. 后续优化顺序

**P0：** 将 A→B→C→D 统一为一键远程编排；增加 SSH 重试、全链路超时、容器回收、制品校验和失败现场保留。

**P0：** 对正常、歧义、策略非法、执行超时、碰撞安全停止、D 修复等场景重复运行 5–10 次，形成成功率、延迟、回退率和恢复率统计。

**P1：** 排查 Isaac Sim DOF 类型警告，验证 GPU/资产版本一致性和物理结果可重复性。

**P1：** 增加 USD 语义自动发现、坐标系/单位检查和场景版本校验。

**P2：** 用 RGB/RGB-D 相机感知替代或补充 Ground Truth，并用 Ground Truth 作为仿真真值对照。

**P2：** 修复全量测试中的既有 TraceCoder 长时间测试，建立 CI 全绿门禁，再制作最终演示视频和比赛提交压缩包。

## 9. 修改和联调原则

1. contracts/v1/ 是跨模块接口唯一标准；模块不得私自穿透其他模块内部实现。
2. 外部模型输出必须经过 Schema、动作白名单、参数和安全门禁；危险或歧义任务必须阻断。
3. 正式链路统一使用 task_id、object_id、destination_id，并保留 provenance。
4. 验证顺序为 Mock → 仿真 → 真机；任何真实后端都不得破坏 Mock 回归集。
5. 真实凭证只通过本地环境变量或被忽略的配置文件提供，禁止进入源代码、报告和提交记录。
## 真实在线闭环批量验收

最终验收使用 `testdata/benchmark/real_isaac_cases.json`，每个样本固定 seed、独立 `run_id`，保存 A/B/C/反馈原始证据，并把传输认证失败与业务、安全、契约失败分开统计。

批量运行默认启用 SSH `BatchMode`，不会读取或保存密码；请先为远端账号配置可用私钥：

```powershell
python tools/run_real_acceptance_batch.py --repeats 3 --ssh-key C:/path/to/isaac_ed25519 --output reports/real-acceptance-summary.json
python tools/summarize_real_acceptance.py --root reports --pattern 'real-acceptance-*' --output reports/real-acceptance-summary.json
```

`--interactive-remote` 仅用于单次人工冒烟，不用于统计验收。传输错误最多按 `--transport-retries` 重试；业务失败、契约错误和 `SAFE_STOP` 不自动重试。批量验收前应确认：契约通过率 100%、错误成功率 0、安全停止正确率 100%，并在同一任务集上比较旧配置与当前配置的成功率、修复成功率、请求次数、token 和 P95 延迟。
