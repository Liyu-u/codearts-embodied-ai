# 最终真实闭环产品化、部署与 Livestream 验收设计

## 目标与完成定义

产品链路为：

Browser → Huawei Cloud → A DeepSeek → B CodeArts → Cloud Orchestrator → Windows Relay → School Isaac Sim → execution/final_pose → Windows Relay → Huawei Cloud → D TraceCoder → Browser

正式视频链路为：

同一个 Isaac World → WebRTC → Windows Streaming Client → OBS Window Capture → RTMP → Huawei MediaMTX → HLS /live/isaac/index.m3u8 → Browser

本设计取代 2026-08-31-cloud-web-real-closed-loop-design.md 中把 RTSP 描述为正式直播链路的部分。RTSP 工具只保留为开发或备用诊断能力。

只有网页运行完成真实 A、B、同一直播 World 中的 C、D，并通过契约、摘要、来源、最终位姿和直播连续性校验后，才能声明上线完成。Mock、fallback、第二个不可见 Isaac 进程或缓存画面不能替代真实验收。

## 基线与隔离

- 主环境：Conda huawei，Python 3.11.15。
- Isaac 辅助环境：Conda isaacsim，Python 3.12.13。
- 实现基线：实际 origin/main；审计时为 5afdb061615d3f52c1b865567ef82ad3ae656912。
- 原工作区的未提交 Isaac 文件禁止覆盖、清理或混入本分支。
- 实现分支：feat/final-live-cloud-closed-loop，位于隔离 worktree。
- Python 和测试命令显式使用 conda run -n huawei；仅必要时使用 isaacsim。

## 架构选择

采用增量 Candidate-first 方案，复用现有契约、A/B/D 适配器、Isaac Backend/Driver、StrategyInterpreter、SSH/SCP bundle 和验收工具。

拒绝直接覆盖现网 8765、只扩展一次性 bridge、重写运动控制或把正式链路改成 RTSP。这些方案分别无法 rollback、无法保证同一 World，或会破坏已验证执行与直播架构。

## 部署域

Huawei Cloud 负责浏览器 API、真实 A/B/D、状态机、持久队列、事件、证据、账户授权和 Relay API。Cloud 不直接 SSH 校园服务器，不执行 C，不管理 OBS/MediaMTX，也不下发任意代码。

Windows Relay 只建立出站连接：领取 typed job，校验租约和摘要，经 SSH/SCP 与校园服务器 runtime directory 交互，再上传有序事件和 allowlisted 证据。Relay 一次只执行一个作业，断网时原子落盘 spool，重启后恢复同一 job 而不重复执行。

Persistent Live Isaac Worker 长期运行一个唯一的 SimulationApp、Stage、World、Franka、Physics、感知 Provider、ExecutorAdapter、StrategyInterpreter、IsaacSimBackend 和 Driver，同时提供 WebRTC 和策略执行。Worker 不运行 A/B/D，不接受任意代码，不为每个任务 app.close()；任务后重置场景，streaming 生命周期独立于 task。

## Cloud Core

demo/cloud/types.py 定义 RunState、JobState、合法转换和公共快照。主状态为：

CREATED → PREPARING_SCENE → PERCEIVING → UNDERSTANDING → PLANNING → QUEUED_C → EXECUTING → VERIFYING → SUCCEEDED

失败终态为 BLOCKED、FAILED、SAFE_STOPPED、CANCELLED，终态拒绝迟到事件改写。

demo/cloud/store.py 使用 SQLite 保存 runs、jobs、events、artifacts、relay sessions。启用 WAL、foreign keys、busy timeout；event_id 唯一，(run_id, artifact_name) 唯一；claim、renew、complete 和 expired recovery 使用事务及 lease owner 校验。

demo/cloud/scenario_registry.py 只公开有真实 Isaac 证据的场景；第一阶段使用 multi-red-001、multi-green-001、multi-red-003。

demo/cloud/orchestrator.py 负责真实 A/B/D 门禁和 C 生命周期。生产 A/B/D 均 required；provider timeout、fallback、缺失 request ID、无效 schema 或摘要漂移全部 fail closed。

demo/cloud/service.py 组合 Store、Orchestrator、安全策略和健康状态；demo/server.py 只处理 HTTP、静态资源和错误码。

## API 与安全

浏览器使用 GET /api/health、GET /api/scenarios、POST /api/runs、GET /api/runs/{run_id}、GET /api/runs/{run_id}/events、GET /api/livestream。历史 POST /api/run 返回 410，前端不再调用。第一版使用 after_sequence polling 恢复。

Relay 使用独立 Bearer Token：register、heartbeat、claim、lease、events、artifacts、complete。接口限制 body、artifact allowlist、run/job/lease owner、sequence、event_id 和 strategy digest。浏览器 Session 与 Relay Token 分离。

角色为 viewer、operator、admin。公开写 API 不允许匿名。Session 使用 HttpOnly、SameSite，HTTPS 下 Secure。DeepSeek Key、CodeArts AK/SK、SSH Key、Relay Token、RTMP password 不返回浏览器、不写日志、不进入 fixture 或 Git。

公网只需要 80/443、SSH 22 和受限来源 RTMP 1935；不得公网开放 8888、8876、49100、47998。没有真实域名时报告 HTTPS READY / WAITING_FOR_DOMAIN，不虚构证书。

## Runtime Directory 与 C 安全

校园服务器使用 /data/stu_01/workspace/live-runtime/ 下的 inbox、active、events、results、control。Relay 只上传安全 run_id 的 typed JSON。Worker 通过原子 rename 领取，校验 job schema、strategy digest、task_id/run_id 和 primitive allowlist。

结果只允许 perception.json、strategy.json、execution.json、final_pose.json、progress.jsonl、container_log_summary.json，使用临时文件加原子替换。重复 job 返回已有证据，不再次驱动机器人。

strategy.code 必须为空。C 只解释 allowlisted primitive actions。禁止 eval、exec、pickle、任意 shell payload 和动态代码执行。

REAL SUCCEEDED 要求五个 v1 契约通过；run_id、task_id、perception digest、strategy digest 连续；execution provenance 为 isaac；final_pose 来自同一 World 且目标满足；SAFE_STOP 后无成功动作；A/B/D 有真实 request ID 且无 fallback。

## Relay 可靠性

默认 10 秒 heartbeat、20 秒 long poll、20 秒 lease renew，backoff 上限 30 秒。active job、lease、last sequence、spool 和 completion receipt 持久化。

SSH 优先使用 alias school，目标 stu_01@10.16.0.40:5122。只允许 BatchMode/SSH Key；禁止硬编码密码、提交私钥或关闭 host verification。SSH failure、Isaac timeout 或 Worker crash 必须产生明确失败或安全停止证据，不能回退 Mock。

## 前端真实性

前端只渲染 API 数据：Cloud/Relay/Isaac/Livestream、场景与指令、HLS、A/B/C/D、当前动作、结果、安全事件、时间线和证据。无数据显示“未连接”“等待中”或“无数据”。

删除无来源的 CPU、内存、机器人型号/IP、负载、固定关节角、固定安全状态、虚构数量、虚构 telemetry 和无实际控制效果的按钮。

HLS 使用同源 /live/isaac/index.m3u8，优先 vendoring hls.min.js，保留 native HLS fallback。LIVE 只能由实际 media progress 判定。

## Livestream 保护与人工门禁

Livestream 不属于任务生命周期。代码和部署脚本禁止停止或重启 MediaMTX、删除 livestream container、修改 /etc/mediamtx.yml、修改 publisher/path、关闭 OBS、删除或重写 /live/。

审计、单元测试、集成测试、Candidate 构建和非直播 smoke 允许 Livestream 未开启，不能因此失败。

只有校园服务器、Windows Relay、Persistent Worker 和 Huawei Candidate 准备完成后，过程必须暂停并显示：

> 现在需要开启 Livestream

操作者确认 WebRTC Streaming Client 和 OBS 已启动后，才执行 HLS 连续性、同一 World 动作可见性和最终 E2E。自动化不得自行启动、替换或重构直播架构。

## Candidate 与 rollback

现网 8765、/opt/codearts/app、codearts-demo.service 和 /live/ 在 Candidate 验收前保持不变。新版本位于 /opt/codearts/releases/<git-sha>/，通过 current 软链接和独立服务绑定 127.0.0.1:8876。

部署顺序：上传版本、创建或复用 venv、py_compile、启动 candidate、API health、Relay/fake-worker smoke、HLS 非侵入探测、nginx -t、原子切换 API 和静态前端。失败时恢复 previous symlink/config，只回滚网站/API。Huawei 远端不安装 Conda。

## 测试与验收

1. latest-main 隔离 baseline，区分 pre-existing failure。
2. Cloud Core 采用 failing test → minimal implementation → focused test → regression。
3. Relay 覆盖 auth、lease、duplicate、断网 spool、restart、SSH failure 和 timeout。
4. Worker 覆盖 traversal、digest、atomic claim、single job、reset、duplicate 和 crash recovery；本地测试不要求 Livestream。
5. 前端覆盖 /api/runs、刷新恢复、无假数据、same-origin HLS。
6. 部署覆盖 candidate port、无 /live/ 修改、nginx -t、版本目录和 rollback。
7. Candidate 完成 Cloud/Relay/fake-worker smoke。
8. 人工开启 Livestream 后执行真实 HLS/Isaac E2E。

真实矩阵包括 3 个正常场景各 3 次，以及歧义阻断、非法策略阻断、安全停止、一次受限修复、Relay/SSH 断线、Cloud/Relay 重启和页面刷新恢复。

## 交付物与非目标

交付 gap audit、Cloud/Relay/Worker/前端/部署代码与测试、docs/华为云真实闭环部署与Livestream运维手册.md、本地/Candidate/E2E/rollback 证据、diff、架构、命令、HTTPS 状态和最终 commit SHA。

不接真实机械臂，不把 Mock 声明为真实 Isaac，不允许浏览器直连 Provider/SSH/Isaac，不删除已有实验能力，不替换 WebRTC/OBS，不在缺少外部证据时虚构上线完成。
