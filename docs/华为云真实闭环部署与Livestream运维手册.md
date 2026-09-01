# 华为云真实闭环部署与 Livestream 运维手册

> 分支：`feat/final-live-cloud-closed-loop`
> 适用：华为 ECS（Ubuntu 22.04）+ 校园服务器（Isaac Sim 6.0）+ Windows Relay

---

## 1. 架构总览

```
浏览器
  ↓ 同源 HTTP(S)
华为云 ECS（nginx：/api/ → candidate，/live/ → 8888）
  ↓ /api/*
demo.server（127.0.0.1:8876 candidate / 8765 production）
  → CloudStore(SQLite) → CloudOrchestrator → A/B/D 真实 Provider
  ↑ outbound HTTPS（Bearer CLOUD_RELAY_TOKEN）
Windows Relay（tools/cloud_relay_agent.py，心跳/领取/续租/事件/证据/完成）
  ↓ SSH/SCP（alias school，10.16.0.40:5122）
校园服务器 Persistent Live Isaac Worker（tools/run_live_isaac_worker.py）
  ↓ 同一 Kit/World/Franka
WebRTC → Windows Streaming Client → OBS → RTMP → MediaMTX → HLS /live/isaac/index.m3u8 → 浏览器
```

核心约束：**执行的 Isaac World 与 WebRTC 直播的 Isaac World 是同一个 SimulationApp/Kit 进程**，
证据通过 `kit_instance_id` / `world_id` 关联。Livestream 完全由操作者拥有，任务系统不拥有其生命周期。

---

## 2. 本地开发 / 测试（Windows，Conda）

```powershell
conda env list
conda run -n huawei python --version   # 3.11.15（主环境）
conda run -n isaacsim python --version # 3.12.13（仅本地 Isaac 探针）

# 全量测试
conda run -n huawei python -m unittest discover -v
conda run -n huawei python -m pytest -q

# 本地启动 candidate（可选，用于手工检查页面）
$env:CLOUD_BIND_PORT="8876"
conda run -n huawei python -m demo.server
```

本地测试必须使用 huawei 环境；禁止 `python`/`pip` 裸命令；禁止向系统 Python 全局安装。

---

## 3. 华为云 Candidate 部署

candidate 绑定 `127.0.0.1:8876`；production `8765` 保持不动；`/live/` 永不修改。

```powershell
# 前置：Windows 已有 SSH alias "huawei"（113.44.1.44）
# 远端复用现有 /opt/codearts/venv；不安装 Conda。

# 1) 校验本地与远端前置
.\tools\deploy_huawei_cloud.ps1 Validate

# 2) 上传版本化 release（/opt/codearts/releases/<sha>/）并启动 candidate
.\tools\deploy_huawei_cloud.ps1 DeployCandidate

# 3) candidate API 健康 + 只读 HLS 探测（不要求直播在线）
.\tools\deploy_huawei_cloud.ps1 CheckCandidate

# 4) 全部门禁通过后原子切换（/api/ → 8876；/live/ → 8888 不变）
.\tools\deploy_huawei_cloud.ps1 Cutover

# 5) 失败时回滚（恢复 previous nginx 配置）
.\tools\deploy_huawei_cloud.ps1 Rollback
```

环境变量（`/etc/codearts/demo.env`，示例见 `deploy/huawei/closed-loop.env.example`）：

| 变量 | 说明 |
|---|---|
| `CLOUD_BIND_HOST` / `CLOUD_BIND_PORT` | candidate 绑定 127.0.0.1:8876 |
| `CLOUD_RELAY_TOKEN` | Relay Bearer（绝不写日志/前端/Git） |
| `CLOUD_OPERATOR_PASSWORD` | 浏览器登录密码（`POST /api/login`），未配置则禁用登录 |
| `CLOUD_HLS_URL` | `/live/isaac/index.m3u8`（同源相对路径） |
| `CLOUD_A_MODE` / `CLOUD_B_MODE` / `CLOUD_D_MODE` | `required`，fail closed |

浏览器 API：`GET /api/health`、`GET /api/scenarios`、`GET /api/livestream`、
`GET /api/session`、`POST /api/login`、`POST /api/runs`、
`GET /api/runs/{run_id}`、`GET /api/runs/{run_id}/events?after_sequence=N`。
旧同步 `POST /api/run` 已退役（410），前端绝不调用。

端口：公网 80/443、SSH 22、RTMP 1935（限 Windows 公网 IP/32）。8888、8876、49100、47998 不开放公网。

---

## 4. Windows Relay

```powershell
# 配置（只从进程环境读取，不落盘不提交）
$env:CLOUD_RELAY_TOKEN="<change-me>"
$env:CLOUD_API_URL="https://<domain-or-ip>"
$env:CLOUD_SSH_ALIAS="school"

# 常驻启动
.\tools\start_cloud_relay.ps1

# 或直接
conda run -n huawei python -m tools.cloud_relay_agent --help
```

Relay 行为：10s 心跳、20s 领取/续租、断网本地 spool、重启续传、同一 job 不重复执行。
SSH 优先 alias `school`（`stu_01@10.16.0.40:5122`），仅允许 BatchMode/SSH Key；
禁止硬编码密码、提交私钥或关闭主机校验。若 BatchMode 无法免密 → 作为 deployment blocker 报告。

---

## 5. 校园服务器 Persistent Live Isaac Worker

```bash
# 服务器（10.16.0.40:5122，Isaac Sim 6.0 容器 + GPU 0 + CPU 物理）
ssh school

docker rm -f live-isaac-worker 2>/dev/null
docker run --rm --name live-isaac-worker --entrypoint bash \
  --gpus '"device=0"' --network=host --ipc=host \
  -v /data/stu_01/docker/isaac-sim/cache/kit:/isaac-sim/kit/cache:rw \
  -v /data/stu_01/docker/isaac-sim/cache/ov:/root/.cache/ov:rw \
  -v /data/stu_01/docker/isaac-sim/cache/pip:/root/.cache/pip:rw \
  -v /data/stu_01/docker/isaac-sim/cache/glcache:/root/.cache/nvidia/GLCache:rw \
  -v /data/stu_01/docker/isaac-sim/cache/computecache:/root/.cache/nvidia/compute_cache:rw \
  -v /data/stu_01/docker/isaac-sim/cache/hf:/root/.cache/huggingface:rw \
  -v /data/stu_01/isaac_assets:/isaacsim_assets:ro \
  -v /data/stu_01/workspace/final-live-cloud-closed-loop:/workspace:rw \
  -v /data/stu_01/workspace/live-runtime:/data/stu_01/workspace/live-runtime:rw \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  nvcr.io/nvidia/isaac-sim:6.0.0 \
  -lc 'cd /isaac-sim && ./python.sh /workspace/tools/run_live_isaac_worker.py --device cpu --idle-sleep-s 0.05 --/app/headless=true --/persistent/isaac/asset_root/default=/isaacsim_assets/Assets/Isaac/6.0'
```

- **Stage 生命周期不变量（禁止违反）**：streaming Kit 创建的 SimulationApp / USD
  Context / Hydra Renderer / WebRTC session 必须跨 worker 整个生命周期保持**同一实例**。
  `PersistentIsaacSession.create` 固定 `driver.connect(defer_start=True, create_stage=False)`，
  绝不在 live worker 里调用 `stage_utils.create_new_stage()`（重建 stage 会关闭
  streaming stage World0 → World1，破坏 Hydra/RTX renderer，表现为
  `HydraEngine rtx failed creating scene renderer` + `NVST_R_BUSY` + 黑屏）。
  只有批处理 runner 才重建 stage。
- **第一轮（单变量）**：不加自定义相机（`--stream-camera` 默认关闭），先证明
  full.streaming.kit + 默认 stage + Franka/cubes + `app.update()` 能稳定出画面；
  通过后才单独加 `/World/Camera`（再单独验证相机 transform / viewport binding）。
- **READY 语义**：stdout `{"status": "WORKER_READY", ...}` 只表示 job loop / runtime
  就绪；`STREAM_WARMUP_ELAPSED <s>` 只是**已耗时**，不是流就绪声明。
  流是否就绪以 WebRTC Client 中看到真实媒体为准。
- **NVST_R_BUSY 处理**：app 加载期 / client 连接切换期可能出现，属正常，不判失败；
  若黑屏/无媒体流则不能忽略。每个 Isaac 实例**只允许一个 WebRTC client**：
  不要同时开 native client 和 browser viewer，不要连续 spam Reload/Connect；
  OBS 是 Window Capture，不算第二个 client。
- 运行时目录：`/data/stu_01/workspace/live-runtime/`（inbox/active/events/results）。
- 证据 `perception.json`、`execution.json`、`final_pose.json`、`progress.jsonl` 全部带
  `kit_instance_id` / `world_id`，与直播 World 同源。
- 一次只执行一个任务；worker crash 后重启可恢复 active job，不重复执行。

### 第一轮真实验收标准（仅 Persistent Worker，不跑 Cloud E2E）

1. 只有一个 Isaac container（`docker ps`）。
2. `full.streaming.kit` 正常加载。
3. 没有 `HydraEngine rtx failed creating scene renderer`，或 renderer 后续明确恢复并出视频。
4. TCP 49100（信令）正常 LISTEN。
5. WebRTC Client **只连接一次**（先完全关闭旧 client，再启动单个）。
6. Client 持续显示真实 Isaac 场景（Franka/cubes），不是黑屏。
7. 无持续性 `NVST_R_BUSY`。
8. worker 一直运行，不关闭 SimulationApp。
9. `app.update()` 一直驱动同一个 App。
10. 无需启动第二个 Isaac。

以上全部 PASS 后，才加入自定义 Camera（`--stream-camera`）；Camera PASS 后，
才进行 Huawei → CodeArts → Relay → strategy → 同一 live Worker 的最终 E2E。

---

## 6. Livestream（操作者拥有）

正式链路：校园 Isaac Sim → WebRTC(49100/47998) → Windows Streaming Client → OBS Window Capture
→ RTMP(1935) → MediaMTX → HLS(127.0.0.1:8888) → nginx `/live/` → 浏览器。

**部署与任务脚本禁止**：停止/重启 MediaMTX、删除 livestream 容器、修改 `/etc/mediamtx.yml`、
修改 publisher/path、关闭 OBS、删除或重写 `/live/`。

部署前后必须只读探测 HLS：

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8888/live/isaac/index.m3u8   # 期望 200
```

### 人工门禁

到达最终真实 E2E 之前，Livestream 未开启**不算失败**。进入真实 E2E 阶段时，先停下并显示：

```
现在需要开启 Livestream
```

等操作者确认 WebRTC Streaming Client 与 OBS 已启动、`/live/isaac/index.m3u8` 返回 HTTP 200 后，
才执行真实 HLS/Isaac E2E。自动化不得自行启动、替换或重构直播架构。

---

## 7. 验收判据（REAL SUCCEEDED）

一次真实成功必须同时满足：

- 五个 v1 契约通过：`perception.v1`、`task.v1`、`strategy.v1`、`execution.v1`、`feedback.v1`
- A/B/D 有真实 Provider request ID，无 fallback
- `strategy.code` 为空；C 只执行 allowlisted primitives
- run_id / task_id / perception digest / strategy digest 连续
- `execution.json` 的 `provenance.backend == "isaac"`；`final_pose.json` 来自同一 World
- `kit_instance_id` / `world_id` 与直播证据一致（执行机器人 == 直播里的机器人）
- SAFE_STOP 后无成功动作；D 修复最多一次
- 浏览器页面刷新后仍能恢复该 run（`GET /api/runs/{run_id}` + `after_sequence` 事件轮询）

---

## 8. Cutover / Rollback

Cutover 前必须全部通过：candidate health 200、Relay/Worker 证据一致、HLS 只读探测 200。
Cutover 只切换网站/API（`/api/` → 8876 + 静态前端 `current` 软链），**不触碰 Livestream**。

```powershell
.\tools\deploy_huawei_cloud.ps1 Cutover     # nginx -t 通过后原子切换
.\tools\deploy_huawei_cloud.ps1 Rollback    # 恢复 previous 配置/release
```

失败时：恢复 `previous` 软链与备份 nginx 配置，`nginx -t` 后 reload；旧 web/API 保留。

---

## 9. HTTPS 状态

当前无真实域名 → 报告 `HTTPS READY / WAITING_FOR_DOMAIN`，不虚构证书。
代码/部署资产支持 `DOMAIN` + `CLOUD_HTTPS=1`（登录 Cookie 加 `Secure`）。
获得域名后配置 ACME/Certbot；HTTPS 下 `/api/` 与 `/live/` 必须同源。

---

## 10. 运维速查

| 现象 | 检查 |
|---|---|
| 页面各组件显示"未连接" | `GET /api/health`、Relay 心跳、Worker 是否在线 |
| 直播 OFFLINE | `curl /live/isaac/index.m3u8`；WebRTC Client/OBS 是否开启 |
| 任务卡在 QUEUED_C | Relay 是否在线、SSH `school` 是否可达、Worker 是否领取 |
| 执行成功但证据缺失 | `live-runtime/results/<run_id>/` 是否齐全、world_id 是否一致 |
| candidate 启动失败 | `systemctl status closed-loop-demo`、`journalctl -u closed-loop-demo` |
| 回滚 | `deploy_huawei_cloud.ps1 Rollback`；`previous` 软链 |

无数据显示"未连接 / 等待中 / 无数据"，前端不生成猜测值。
