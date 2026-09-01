# 最终部署缺口审计（FINAL GAP AUDIT）

审计日期：2026-09-01  
审计基线：`origin/main@5afdb061615d3f52c1b865567ef82ad3ae656912`  
实施分支：`feat/final-live-cloud-closed-loop`  
隔离工作树：`tmp_orchestrate_smoke/worktrees/final-live-cloud-closed-loop`

## 1. 结论

当前仓库已经具备 A/B/C/D 单次闭环、真实提供方证据审计、一次性 Windows→校园服务器桥接、Isaac 执行与安全检查等重要基础，但还不是可上线的“浏览器发起—云端持久编排—Windows Relay—常驻 Isaac Worker—证据回传—D 复核—网页恢复”的产品。

最新主分支的云端改造只提交了认证、凭据和安全边界的一部分。状态机、SQLite 存储、场景注册表、编排器、云服务、Relay 客户端、常驻 Worker、候选部署与最终前端均未完成。`demo/server.py` 已提前引用这些未提交模块，导致主分支当前无法导入，属于明确的既有阻断。

因此本轮不能在现有公网服务上直接覆盖发布。正确路线是：先在隔离分支补齐并本地验证，再部署到 Huawei Candidate 端口 `8876`，保留生产 `8765` 和现有 `/live/` 链路，完成真实验收后才允许切换。

## 2. 不可变约束

- 正式直播链路固定为：校园服务器常驻 Isaac World → WebRTC → Windows Isaac Sim Streaming Client → OBS 窗口采集 → RTMP → 华为云 MediaMTX → HLS `/live/isaac/index.m3u8` → 浏览器。
- 不用 RTSP 代替 WebRTC→Windows Client→OBS 这一正式采集链路。
- 当前代码审计、单元测试和 Candidate 构建阶段，Livestream 未开启是正常状态，不能据此判定失败。
- 只有到达“校园服务器 + Windows Relay + Persistent Live Isaac Worker + Huawei HLS”最终真实 E2E 阶段，才暂停并要求人工开启 WebRTC Streaming Client 和 OBS。
- 自动化脚本不得启动、停止、重启或改写 WebRTC Client、OBS、MediaMTX、`/live/` nginx 配置。
- Isaac Worker 必须在同一个 Kit/`SimulationApp`/`World` 生命周期中连续处理场景准备、感知、执行和取证，不能每个动作重启 Kit。
- 云端 A/B/D 正式验收使用 required 模式；回退、缺少真实请求证据或非法结构均必须 fail-closed。
- C 成功必须有 `provenance.backend=isaac`、一致的 run/task/hash、Isaac 终态位姿及允许清单内的证据。
- 密钥、SSH 私钥、Relay Token、发布凭据和数据库内部路径不得返回浏览器或进入 Git。

## 3. Git 与环境基线

| 项目 | 结果 |
|---|---|
| 远端基线 | `origin/main@5afdb061615d3f52c1b865567ef82ad3ae656912` |
| 原工作区 | 有用户未提交的 Isaac 相关改动，未修改、未清理、未覆盖 |
| 实施位置 | 独立 worktree 和独立功能分支 |
| `huawei` 环境 | Python 3.11.15 |
| `isaacsim` 环境 | Python 3.12.13；只用于 Isaac 侧运行 |
| `pytest`（huawei） | 未安装，`python -m pytest -q` 当前无法执行 |
| `numpy`（huawei） | 未安装；两个相机单测无法导入 |
| `cryptography`（huawei） | 未安装；凭据加密单测无法导入 |

并行运行多个 `conda run` 会争用 `%TEMP%/__conda_tmp_*.txt`。这属于 Conda 启动器并发限制，不是仓库缺陷。后续测试使用 `huawei` 环境的绝对 Python 路径串行运行，正式文档仍保留 `conda activate huawei` 的用户操作方式。

## 4. 最新主分支测试基线

执行 `python -m unittest discover -v`：

- 共发现 297 个测试；
- 287 个通过；
- 1 个真实 CodeArts 测试按环境开关跳过；
- 9 个导入错误；
- 总退出码为 1。

既有错误明细：

| 失败项 | 根因 | 分类 |
|---|---|---|
| `tests.e2e.test_demo_http` | `demo.server` 在定义/导入前调用 `configure_cloud_service` | 主分支既有代码错误 |
| `tests.e2e.test_demo_quality` | 同上 | 主分支既有代码错误 |
| `tests.e2e.test_demo_scenarios` | 同上 | 主分支既有代码错误 |
| `tests.integration.test_benchmark_remote` | 间接导入 `demo.server` 后同上 | 主分支既有代码错误 |
| `tests.unit.test_benchmark_summary` | 间接导入 `demo.server` 后同上 | 主分支既有代码错误 |
| `tests.unit.test_auth_primitives` | `pytest` 未安装 | 既有环境缺口 |
| `tests.unit.test_credentials` | `pytest` 未安装 | 既有环境缺口 |
| `tests.unit.test_isaac_camera_perception` | `numpy` 未安装 | 既有环境缺口 |
| `tests.unit.test_isaac_camera_real` | `numpy` 未安装 | 既有环境缺口 |

额外证据：

- `demo/server.py:215` 调用 `_CLOUD_SERVICE = configure_cloud_service()`；导入语句直到第 218 行才出现。
- `demo/cloud/service.py`、`store.py`、`types.py`、`scenario_registry.py` 当前均不存在。
- 该顺序错误和未完成引用由提交 `5afdb06` 引入，不是本实施分支新增。
- `demo.cloud.security`、`tools.live_intelligent_e2e`、`tools.run_live_intelligent_bridge` 可单独导入。
- `demo.server` 不能导入。

## 5. 2026-08-31 Cloud Web Real Closed Loop 计划逐项分类

状态定义：

- `DONE`：代码、测试和当前基线证据均完整。
- `PARTIAL`：已有可复用实现，但尚未达到原任务接口或验收要求。
- `TODO`：目标文件/行为基本不存在。
- `BROKEN`：已有入口引用不完整实现，导致当前运行失败。

| 原任务 | 状态 | 已有内容 | 剩余缺口 |
|---|---|---|---|
| 1. 状态机和已验证场景注册表 | TODO | benchmark 中已有三个真实场景数据 | 缺 `types.py`、转换约束、公开快照、不可变场景注册表 |
| 2. 事务型持久存储 | TODO | 无 | 缺 SQLite runs/jobs/leases/events/artifacts、幂等、恢复与并发领取 |
| 3. Relay 与证据安全 | PARTIAL | `security.py`、安全测试、env 示例已提交 | 需要接入实际 HTTP 边界、与租约/主体绑定并做端到端验证 |
| 4. 云端 A/B/D 编排器 | TODO | 已有离线 adapters 和证据审计工具 | 缺异步编排、required 证据门、C 两阶段任务、D 有界修复 |
| 5. 浏览器与 Relay HTTP API | BROKEN | `server.py` 已写入部分新路由 | 引用未提交的 `service.py`，导入即失败；缺认证、租约、事件游标和正确错误码 |
| 6. 出站 Relay HTTPS 客户端 | TODO | 无 | 缺鉴权、幂等键、重试分类、超时和脱敏 |
| 7. 类型化 Isaac Job Runner | PARTIAL | 有一次性 SSH/SCP bridge 和真实 Isaac 工具 | 缺严格 job 类型、digest 校验、允许清单证据、只清理当前任务容器 |
| 8. 可恢复 Windows Relay Agent | TODO | 无 | 缺心跳、长轮询、续租、本地 spool、断网恢复和单任务执行 |
| 9. 真实工作流前端 | PARTIAL | 当前页面可配 API Base 且有 HLS 播放入口 | 仍包含模拟 SVG、预留语音/2D、固定机器人命令和旧 `/api/run`；不能真实恢复 run |
| 10. 华为云分阶段部署 | TODO | 只有 env 示例 | 缺非 root systemd、nginx candidate、版本化上传、健康检查和回滚脚本 |
| 11. 运维与录制文档 | TODO | 有历史部署/桥接资料 | 正式直播叙述不统一，缺 Candidate、Relay、Worker、证据、恢复、回滚完整手册 |
| 12. 本地集成与全量回归 | BROKEN | 既有 287 个测试可通过 | 主分支导入失败、环境依赖不完整，也没有 fake Relay 端到端恢复测试 |
| 13. 分阶段真实部署与校园连接 | TODO | 历史一次性服务器运行和 SSH 资料可复用 | 尚无本轮 Candidate、常驻 Relay/Worker 和独立健康证据 |
| 14. 真实验收与公网切换 | TODO | 历史实验报告和审计器可复用 | 尚无新架构下重复运行、安全矩阵、刷新/重启恢复、HLS 连续性与切换证据 |

## 6. 2026-08-31 Live Intelligent E2E 计划逐项分类

| 原任务 | 状态 | 证据与说明 |
|---|---|---|
| 1. Variant 与外部策略协议 | DONE | `real_isaac_experiment.py` 和 ground-truth runner 已有实现及对应单测 |
| 2. 证据审计器与指标 | DONE | `tools/live_intelligent_e2e.py` 及单测存在，当前可导入 |
| 3. 在线编排和报告 | PARTIAL | 一次性 bridge/matrix 工具已存在；它不是持久云编排、Relay 或常驻 Worker |
| 4. 验证和真实执行 | PARTIAL | 有历史/阶段性执行能力；缺本轮最终架构的 fresh candidate 和真实 HLS/Isaac 联合证据 |

## 7. 可复用资产

- A/B/D adapters、required/fallback 证据字段和协议校验。
- `integration.contract_validation` 及 perception/strategy/execution/feedback contracts。
- `tools/live_intelligent_e2e.py` 的真实证据资格审计。
- `tools/run_live_intelligent_bridge.py` 中已验证的 SSH/SCP、Base64 命令和远端等待思路。
- Isaac 驱动、真实后端、安全边界、动态物体和终态位姿采集。
- `auth.py` 的密码哈希/Token 基础、`credentials.py` 的加密边界、`security.py` 的 bearer 和 artifact allowlist。
- `/api/livestream` 与现有 HLS 播放入口，但必须移除外网 CDN 依赖并改为仓库内静态资源。

## 8. 本轮必须新增或重构的文件域

1. 云端核心：`demo/cloud/types.py`、`scenario_registry.py`、`store.py`、`orchestrator.py`、`service.py`。
2. HTTP 边界：重构 `demo/server.py`，增加浏览器会话/角色、Relay 注册/心跳/租约/证据接口。
3. Relay：`tools/relay/client.py`、`runtime_protocol.py`、`isaac_job.py`、`tools/cloud_relay_agent.py`。
4. 常驻 C：`tools/persistent_isaac_worker.py` 及 Isaac 环境内单 Kit/World 作业处理。
5. 前端：真实 A/B/C/D 时间线、run 恢复、健康、HLS 状态和证据摘要；删除模拟数据冒充。
6. 部署：candidate systemd/nginx、版本化上传、健康检查、HLS 前后探测、原子切换与回滚。
7. 文档：Windows Relay、校园 Worker、华为 Candidate、Livestream 人工门和证据包完整复现手册。

## 9. 明确非目标

- 不修改 A、B、D 的算法归属或把 Mock 伪装成真实提供方。
- 不开放校园服务器新的入站端口；Relay 仍由 Windows 主动连云端和校园 SSH。
- 不把浏览器直接连接校园服务器。
- 不允许云端传输任意 shell 命令，所有作业均为类型化 allowlist。
- 不自动控制 WebRTC Client、OBS 或 MediaMTX。
- 不在最终真实门之前要求 Livestream 在线。
- 不覆盖当前生产 `8765`，不修改现有 `/live/`，不在 Candidate 验证前切公网。
- 不提交真实 AK/SK、DeepSeek Key、SSH 私钥、Relay Token、数据库或运行证据大文件。

## 10. 实施顺序和上线门

1. 修复云端导入基线并补齐状态机、场景注册表、存储、认证和编排器。
2. 完成浏览器/Relay API、Windows Relay 与可恢复 spool。
3. 完成 persistent worker 核心和 Isaac 单 Kit/World 接入。
4. 完成真实前端、本地 HLS.js、Candidate 部署和回滚资产。
5. 在本地 fake worker 下完成全量回归；Livestream 可以是 OFFLINE。
6. 部署 Candidate `8876`，验证旧生产和 `/live/` 完全不受影响。
7. 连通校园服务器和 persistent worker，但不擅自开启直播。
8. 到最终 HLS/Isaac E2E 门时暂停，明确提示人工开启 Livestream，得到确认后再执行真实验收。
9. 只有 A/B/D required 证据、C Isaac 证据、安全矩阵、断线恢复和 HLS 连续性全部通过，才给出 GO；否则保留旧生产并报告 NO-GO。

