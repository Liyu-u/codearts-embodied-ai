# 具身智能系统统一联调仓库

本项目面向 2026 年“挑战杯”揭榜挂帅华为赛道，围绕“基于华为云码道（CodeArts）代码智能体解决复杂软件工程问题”构建可复现的具身智能闭环 Demo。系统把自然语言意图、策略生成、仿真执行和结果反馈接成一条带契约、可审计、可安全阻断的链路。

当前结论：**A、B、D 已完成真实智能调用；B 的 7 类开放动作已通过真实 CodeArts CLI 验证；C 已完成 Isaac Sim Ground Truth 和 RGB-D 相机感知仿真闭环。严格主对照已在 30 个案例、每题 5 次重复下完成 V0/V2/V4 比较：V4 为 150/150 条评测通过。当前交付范围是 Mock/Isaac Sim 仿真平台，不包含真实机器人或物理相机 HIL。**

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
C 执行器（Mock / Isaac Sim 仿真） ──► execution.v1
          │
          ▼
D 反馈与纠错（TraceCoder + DeepSeek） ──► feedback.v1
          │
          └── 失败时：安全阻断、有限重试、结构化修复证据
~~~

各模块职责如下：

| 模块 | 当前完成度 | 已验证能力 | 当前边界 |
| --- | --- | --- | --- |
| P/C 感知边界 | Isaac Sim Ground Truth 与 RGB-D 相机仿真链路已完成 | 稳定对象 ID、RGB-D 深度反投影、位姿/关系标准化、在线规划与真值隔离；相机质量指标可审计 | 真实相机标定和真机传感器不在本阶段范围 |
| A 意图理解 | DeepSeek 智能模式已在线验证 | 自然语言解析、目标绑定、歧义识别、安全阻断、`task.v1` 输出；主对照语义精确匹配率 100% | 复杂开放世界指令仍需扩大数据集和重复统计；依赖 `perception.v1` 的稳定事实 |
| B 策略生成 | 真实 CodeArts 智能调用已完成 | CodeArts CLI、AK/SK、代理绕过、动作级提示词和安全校验；7 类开放动作真实调用 7/7 成功，主对照策略契约通过率 100% | 在线调用仍有较高延迟；V1 在线结果单独报告，不与 V0/V2/V4 主对照合并 |
| C 执行器 | Mock、Isaac Sim Ground Truth 与 RGB-D 相机仿真执行闭环已完成 | 五步抓取搬运、三步抓取、堆叠、轨迹记录、超时/碰撞/安全停止、`task_id` 连续性校验；V4 主对照误报成功率 0% | 真实硬件驱动不在本阶段范围；Isaac DOF/资产环境警告仍需按服务器环境加固 |
| D TraceCoder | 反馈与有限修复闭环已完成 | DeepSeek required/optional/off、`feedback.v1`、失败归因、有限补丁、重试和安全校验；V4 可恢复故障恢复率 100% | 修复策略不能绕过 B/C 的 Schema 和动作白名单；真实机器人 HIL 不在本阶段范围 |

模块关系：A 只负责把语言变成有约束的 `task.v1`；B 只负责生成并校验 `strategy.v1`；C 只执行白名单原子动作并返回 `execution.v1`；D 只根据执行证据输出 `feedback.v1` 和受限补丁。任何模块失败都会在对应边界阻断，不会静默越权。
## 2. 仓库结构

~~~text
.
├─ contracts/v1/             # perception/task/strategy/execution/feedback Schema
├─ modules/
│  ├─ perception/            # 感知规范化和 Isaac Ground Truth 适配
│  ├─ intent_understanding/  # A：意图理解与安全门禁
│  ├─ strategy_generation/   # B：CodeArts 策略生成与校验
│  ├─ executor/              # C：Mock、Isaac Sim 仿真执行边界
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
- RGB-D 相机仿真链路使用相机观测和深度反投影生成在线位姿，Ground Truth 仅用于离线对照。
- 远程启动、容器清理、长时间稳定性和 Isaac 的 DOF 警告仍需结合目标服务器持续加固；真机不在本阶段验收范围。

### D：TraceCoder 反馈

- DeepSeek required/optional/off 模式和结构化 feedback.v1 已接通。
- 可根据执行证据判定通过、归因失败并生成有限修复补丁；修复必须再次校验。
- 已完成与 Isaac 执行证据的消费验证。

## 5. 已完成验收证据

### 5.1 2026-08-29 正式主对照与消融结果

实验编号为 `exp-20260828-01`，协议版本为 `embodied-task-experiment-v1/1.0.0`。V0、V2、V4 使用同一份 30 案例清单、同一 Mock 执行后端、相同场景/故障注入和相同 seed，每个案例重复 5 次，共 150 次运行。主对照结果如下：

| 版本 | 运行次数 | 总体通过率 | 合法任务成功率 | 可恢复故障恢复率 | 安全停止正确率 | 危险任务误执行率 | 假成功率 | 案例稳定率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| V0_RULE_BASELINE | 150 | 93.33% | 84.62% | 33.33% | 100% | 0% | 0% | 100% |
| V2_FULL_NO_D | 150 | 93.33% | 84.62% | 33.33% | 100% | 0% | 0% | 100% |
| V4_FULL | 150 | 100% | 100% | 100% | 100% | 0% | 0% | 100% |

V4 相较 V0：合法任务成功率提升 15.38 个百分点，可恢复故障恢复率提升 66.67 个百分点；危险任务误执行率和假成功率均为 0。V0/V2/V4 是严格主比较，V1 CodeArts-B 仅作为在线辅助对照，使用相同 30 案例但重复 3 次，结果为总体通过率 93.33%、合法任务成功率 84.62%。

这组结果说明：只加入 CodeArts 策略生成并未改变主对照的核心指标；去掉 D 也无法恢复可恢复故障；完整 V4 的主要增益来自 A 的结构化意图链路和 D 的故障诊断/有限修复闭环。所有消融结果仅用于实验比较，不改变产品默认的安全门禁。

### 5.2 Isaac Sim 与 RGB-D 摄像机独立证据

- V4 已在远程 Isaac Sim 6.0 CUDA 环境完成 1 个绿色方块搬运任务：`SUCCEEDED`，5 个动作步骤完成，安全事件 0，最终物理状态验证通过。该证据证明真实 USD/PhysX 执行链路可用，但不代表 30 个案例全部在 Isaac Sim 上完成。
- RGB-D 摄像机仿真闭环已通过：识别 3 个对象，RGB 640×480，Depth 480×640，深度有效率 1.0，质量状态 `READY`；C 的 5 个步骤全部成功，在线位姿来自 RGB-D 深度反投影，不使用 Ground Truth。
- Isaac RTSP 仿真流的 OPTIONS、DESCRIBE、SETUP、PLAY 均通过，并收到 H.264 RTP 数据。远程环境没有物理 `/dev/video*` 设备，因此真实 USB/工业相机 HIL 和真实机器人 HIL 不属于当前交付证据。

### 5.3 当前回归与证据边界

当前完整 Python 回归为 `290 tests`，结果为 `OK`，其中 `1` 项按配置跳过。Mock 主对照、CodeArts 在线、LLM 留出集和 Isaac Sim 结果必须分开报告，不能合并成一个“真实仿真成功率”。

主对照的脱敏汇总见 `experiments/summaries/final_v0_v2_v4_20260829.md`；完整原始报告保留在本地 `reports/` 目录，按 `.gitignore` 规则不提交。

### 历史 Ground Truth Isaac Sim 闭环证据（2026-08-22）

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

当前完整测试集已完成隔离配置下的回归：`290 tests`，`OK`，`1 skipped`。跳过项是按配置关闭的在线测试，不影响离线契约、集成和执行器回归结果。

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
# 协议、题集和运行配置预检
python tools/validate_experiment_protocol.py

# 完整离线/Mock 回归（当前结果：290 tests，OK，1 skipped）
python -m unittest discover -s tests -t . -q

# 运行离线/Mock 闭环专项测试
python -m unittest discover -s tests/contract -t . -q
python -m unittest discover -s tests/integration -t . -q
python -m unittest tests.e2e.test_closed_loop_acceptance -v

# Ground Truth 感知单元
python -m unittest tests.unit.test_isaac_ground_truth_perception -v
~~~

在线大规模 CodeArts 批测（正式五套清单、每题重复 3 次；中断后可用同一命令续跑）：

~~~powershell
python tools/run_codearts_testsets.py --live --policy quality --repeats 3 `
  --transport-retries 2 --retry-backoff-s 2 --resume `
  --output reports/codearts_online_scale.json
~~~

报告会记录每套清单的通过率、稳定率、provider 调用/尝试次数、传输重试次数以及延迟 P50/P95。完整仿真平台矩阵使用 `tools/run_final_acceptance.py`；它只包含 Mock、CodeArts/LLM 在线链路、Isaac Sim 和 RGB-D 相机仿真，不执行真机档位。

本地 Demo 默认使用 C Mock，适合展示消息流和故障修复；它不能替代 Isaac Sim 真实执行证据。

前端接口、配置方法、当前 Demo 边界和后续 Isaac Sim/RGB-D/真机接入要求见 [`docs/前端接口与仿真平台接入说明.md`](docs/前端接口与仿真平台接入说明.md)。

真实上线（华为云 + Windows Relay + Persistent Live Isaac Worker + HLS）的部署、运维、
Livestream 人工门禁与回滚，见 [`docs/华为云真实闭环部署与Livestream运维手册.md`](docs/华为云真实闭环部署与Livestream运维手册.md)。
`demo/` 下同步 Mock 接口（含旧 `POST /api/run`）仅作为开发/调试用途，不用于线上验收；
线上使用异步 `POST /api/runs` + `after_sequence` 事件轮询 + 同源 HLS（`/live/isaac/index.m3u8`）。

上线前最终验收使用 `tools/run_final_acceptance.py`，按离线回归、CodeArts 在线、LLM 留出泛化、Isaac Sim HIL 和 RGB-D 相机 HIL 五档分别出具证据。完整矩阵见 `testdata/acceptance/final_acceptance_matrix_v1.json`；只有五档全部 `PASS` 才能将仿真平台报告判定为上线可接受。

### 6.4 远程 Isaac Sim 验收

前提：校园 VPN、SSH 账号、远程服务器上的 Docker、NVIDIA GPU、Isaac Sim 6.0 镜像和资产目录均可用。

使用已有 A/B 策略运行 C：

~~~powershell
.\tools\run_remote_ground_truth_acceptance_final.ps1 -Server 10.16.0.40 -Port 5122 -User stu_01 -StrategyFile reports/gt-20260822-004458/live_chain_ab_ground_truth.json -Device cuda
~~~

脚本只上传代码包和脱敏策略，不读取 .env、CSV、AK/SK；结果下载到 reports/gt-final-<timestamp>/。远程服务器的 Isaac 资产路径、镜像和端口属于部署环境，不写入仓库凭证。运行结束后应检查容器是否退出并清理临时目录。

当前已提供 Ground Truth、RGB-D 相机和 RTSP 三类 Isaac Sim 远程入口，均支持 SSH 私钥、超时、容器清理和证据回收；真实 USB/工业相机和真实机器人仍需单独接入，不纳入本阶段统计。

RGB-D 相机与 RTSP 仿真流：

~~~powershell
.\tools\run_remote_camera_acceptance_v7.ps1 -Server 10.16.0.40 -Port 5122 -User stu_01 `
  -SshKeyPath C:/path/to/isaac_ed25519 -StrategyFile reports/live_chain_ab.json `
  -RunId camera-<timestamp> -Device cuda

.\tools\run_remote_rtsp_livestream.ps1 -Server 10.16.0.40 -Port 5122 -User stu_01 `
  -SshKeyPath C:/path/to/isaac_ed25519 -RunId rtsp-<timestamp>
.\tools\start_rtsp_ssh_tunnel.ps1 -SshKeyPath C:/path/to/isaac_ed25519
~~~

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

**P2：** 扩展 RGB/RGB-D 相机的遮挡、光照、深度噪声和运动目标场景，并继续用 Ground Truth 作为仿真真值对照。

**P2：** 接入真实相机标定、真实机器人驱动、现场急停和碰撞/断连 HIL；建立 CI 全绿门禁，再制作最终演示视频和比赛提交压缩包。

## 9. 修改和联调原则

1. contracts/v1/ 是跨模块接口唯一标准；模块不得私自穿透其他模块内部实现。
2. 外部模型输出必须经过 Schema、动作白名单、参数和安全门禁；危险或歧义任务必须阻断。
3. 正式链路统一使用 task_id、object_id、destination_id，并保留 provenance。
4. 当前验证顺序为 Mock → Isaac Sim 仿真；任何仿真后端都不得破坏 Mock 回归集，真机不属于本阶段交付范围。
5. 真实凭证只通过本地环境变量或被忽略的配置文件提供，禁止进入源代码、报告和提交记录。
## 真实在线闭环批量验收

最终验收使用 `testdata/benchmark/real_isaac_cases.json`，每个样本固定 seed、独立 `run_id`，保存 A/B/C/反馈原始证据，并把传输认证失败与业务、安全、契约失败分开统计。

批量运行默认启用 SSH `BatchMode`，不会读取或保存密码；请先为远端账号配置可用私钥：

```powershell
python tools/run_real_acceptance_batch.py --repeats 3 --ssh-key C:/path/to/isaac_ed25519 --output reports/real-acceptance-summary.json
python tools/summarize_real_acceptance.py --root reports --pattern 'real-acceptance-*' --output reports/real-acceptance-summary.json
```

`--interactive-remote` 仅用于单次人工冒烟，不用于统计验收。传输错误最多按 `--transport-retries` 重试；业务失败、契约错误和 `SAFE_STOP` 不自动重试。批量验收前应确认：契约通过率 100%、错误成功率 0、安全停止正确率 100%，并在同一任务集上比较旧配置与当前配置的成功率、修复成功率、请求次数、token 和 P95 延迟。
