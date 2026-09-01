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

### 唯一推荐启动命令（后台运行）

```bash
# 服务器（10.16.0.40:5122，Isaac Sim 6.0 容器 + GPU physical 0 + CPU 物理）
ssh school

# 只有确认旧 live-isaac-worker 是本项目 Worker 后，才 scoped 删除它：
docker rm -f live-isaac-worker 2>/dev/null || true

docker run -d --name live-isaac-worker \
  --no-healthcheck \
  --entrypoint bash \
  --gpus '"device=0"' \
  --network=host \
  --ipc=host \
  -e ACCEPT_EULA=Y \
  -e PRIVACY_CONSENT=N \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  -e ISAACSIM_ASSET_ROOT=/isaacsim_assets/Assets/Isaac/6.0 \
  -v /data/stu_01/docker/isaac-sim/cache/main/ov:/home/isaac/.cache/ov:rw \
  -v /data/stu_01/docker/isaac-sim/cache/main/warp:/home/isaac/.cache/warp:rw \
  -v /data/stu_01/docker/isaac-sim/cache/computecache:/home/isaac/.cache/computecache:rw \
  -v /data/stu_01/docker/isaac-sim/config:/home/isaac/.config/omniverse:rw \
  -v /data/stu_01/docker/isaac-sim/data/documents:/home/isaac/Documents:rw \
  -v /data/stu_01/docker/isaac-sim/data/Kit:/home/isaac/.local/share/ov/data/Kit:rw \
  -v /data/stu_01/docker/isaac-sim/logs:/home/isaac/.nvidia-omniverse/logs:rw \
  -v /data/stu_01/docker/isaac-sim/pkg:/home/isaac/.local/share/ov/pkg:rw \
  -v /data/stu_01/isaac_assets:/isaacsim_assets:ro \
  -v /data/stu_01/workspace/final-live-cloud-closed-loop:/workspace:rw \
  -v /data/stu_01/workspace/live-runtime:/data/stu_01/workspace/live-runtime:rw \
  nvcr.io/nvidia/isaac-sim:6.0.0 \
  -lc 'cd /isaac-sim && ./python.sh /workspace/tools/run_live_isaac_worker.py \
  --device cpu \
  --idle-sleep-s 0.05 \
  --stream-view-mode auto \
  --stream-camera-eye 1.35,-1.45,1.05 \
  --stream-camera-target 0.40,0.00,0.25 \
  --/app/headless=true \
  --/exts/omni.kit.livestream.app/primaryStream/publicIp=10.16.0.40 \
  --/exts/omni.kit.livestream.app/primaryStream/signalPort=49100 \
  --/exts/omni.kit.livestream.app/primaryStream/streamPort=47998 \
  --/persistent/isaac/asset_root/default=/isaacsim_assets/Assets/Isaac/6.0'
```

- `-d` 后台运行；`--no-healthcheck` 避免镜像默认 AppReady healthcheck 误导；
  GPU physical 0；`--network=host`（WebRTC 49100/47998 必需）；`--ipc=host`；
  assets 只读；runtime 与 project workspace 可写；不新增公网端口。
- **三个 livestream 参数是必须项**（真实服务器验证过）：缺省时 49100 signaling
  可连但 47998 media 不建立、Client 黑屏；写入后 49100 ESTAB + 47998 UDP + 真实画面。
  不要提供不含这三个参数的 production 命令。
- 流目标固定 **1280×720 / 30 FPS**（SimulationApp width/height=1280×720，
  WebRTC Client 与 OBS 同选 1280×720）。
- 缓存挂载使用实际验证过的 `/home/isaac/...` 布局（镜像默认用户 isaac），
  不使用 `/root/.cache/...`。

### Stage 生命周期不变量（禁止违反）

streaming Kit 创建的 **SimulationApp / USD Context / Hydra Renderer / WebRTC session**
必须跨 worker 整个生命周期保持**同一实例**。`PersistentIsaacSession.create` 固定：

```python
driver.connect(defer_start=True, create_stage=False)
```

绝不在 live worker 里调用 `stage_utils.create_new_stage()`（重建 stage 会关闭
streaming stage World0 → World1，破坏 Hydra/RTX renderer，表现为
`HydraEngine rtx failed creating scene renderer` + `NVST_R_BUSY` + 黑屏）。
只有批处理 runner（batch 模式）才 `create_stage=True`。任务间只 `reset_for_task()`，
不 `app.close()`、不重启 SimulationApp、不重启 WebRTC、不换 Streaming Experience、
不关 Timeline。

### Streaming View 配置层（stream_view_mode）

生产默认 **`--stream-view-mode auto`**（native 优先，usd-camera 兜底）：

| mode | 行为 |
|---|---|
| `auto`（默认） | 先尝试 Isaac 6.0 原生 Perspective View classmethod API（`ViewportManager.set_camera` + `set_camera_view`，相机 `/OmniverseKit_Persp`）；失败 → fallback 到 `/World/Camera` + look-at transform + active viewport 绑定 |
| `viewport` | 只用 native API，失败即 `STREAM_VIEW_FAILED` |
| `usd-camera` | 只用 `/World/Camera` 兜底（`--stream-camera` 是它的废弃别名） |
| `off` | 完全不改 Camera/View（诊断隔离用，非生产） |

native 实现（Isaac Sim 6.0 classmethod API，**不存在 `get_instance()` 步骤**）：

```python
from isaacsim.core.rendering_manager import ViewportManager

ViewportManager.set_camera("/OmniverseKit_Persp")
ViewportManager.set_camera_view(
    "/OmniverseKit_Persp",
    eye=[1.35, -1.45, 1.05],     # --stream-camera-eye（可配置）
    target=[0.40, 0.00, 0.25],   # --stream-camera-target（可配置）
)
# set_resolution((1280, 720)) 为 best-effort，失败不影响 camera
# 之后 30 × app.update() 让 view 生效
```

比赛现场如需调整构图，只改启动参数 `--stream-camera-eye` / `--stream-camera-target`
（`X,Y,Z` 逗号分隔 3 浮点），不需要改代码、不拖动 WebRTC Client、不进 Isaac UI。
默认 eye/target 覆盖 Franka、red/green cubes、`zone_unstack_target`，Z-up 水平构图。

fallback 与 native 都遵守 Stage 不变量：不建新 Stage、不重建 SimulationApp、
不重启 WebRTC、不改 Experience、不关 Timeline。两者都失败 → 输出
`[worker] STREAM_VIEW_FAILED reason=...`，worker **继续运行**，
`stream_view_configured=false`，绝不伪装成功。
`STREAM_VIEW_READY` 前先检查场景 prim：`/World/robot`、至少一个 cube
（`/World/red_cube` 等）、`/World/zone_unstack_target` 都必须存在。

预期成功日志（验收必须看到 `mode=viewport`，不是 usd-camera）：

```
[worker] STREAM_VIEW_READY mode=viewport camera=/OmniverseKit_Persp eye=1.35,-1.45,1.05 target=0.40,0.00,0.25 api=ViewportManager.set_camera_view
```

### 运行时与证据（同一 World 不变量）

- 运行时目录：`/data/stu_01/workspace/live-runtime/`（inbox/active/events/results）。
- 证据 `perception.json`、`execution.json`、`final_pose.json`、`progress.jsonl`
  全部带 `kit_instance_id` / `world_id`，与直播 World 同源 ——
  CONTROL 中执行 strategy 的 World 必须就是 VIDEO 中 WebRTC 显示的 World。
- 一次只执行一个任务；worker crash 后重启可恢复 active job，不重复执行。

### READY 语义（严格区分，禁止混淆）

```
WORKER_READY        != STREAM_VIEW_READY != WEBRTC MEDIA READY
WORKER_READY        stdout {"status":"WORKER_READY",...}：job loop / runtime 就绪。
STREAM_VIEW_READY   [worker] STREAM_VIEW_READY mode=... camera=...：Camera/View 配置成功。
WEBRTC MEDIA READY  只能由真实 WebRTC Client 视频 + media session + 服务器日志共同验收。
```

`STREAM_WARMUP_ELAPSED <s>` 只是**已耗时**，不是任何就绪声明。没有固定
"180s/210s 后即 ready" 的说法。

### NVST_R_BUSY 处理

app 加载期 / client 连接切换期偶发 `NVST_R_BUSY` 属正常，不判失败；但
**持续 BUSY + WebRTC 黑屏 = 失败**。一个 Isaac 实例只允许一个 WebRTC client：
只开 Windows Native WebRTC Client ×1（OBS 是 Window Capture，不算第二个 client）；
不要同时开 browser viewer、不要连续 spam Reload/Connect。

### 第一轮真实验收流程（只测 School → WebRTC Client，先关 OBS/不启动）

```text
STEP 1  Windows：完全退出所有 Isaac Sim WebRTC Streaming Client（只留一个）。
        OBS 暂不启动或停止 Streaming。
STEP 2  同步最新 tools/run_live_isaac_worker.py 到
        /data/stu_01/workspace/final-live-cloud-closed-loop/tools/
STEP 3  学校：docker ps —— 只有一个本项目 live Isaac。
STEP 4  scoped：docker rm -f live-isaac-worker
STEP 5  用上面的"唯一推荐启动命令"启动。
STEP 6  docker logs -f live-isaac-worker
        必须出现 WORKER_READY 与
        STREAM_VIEW_READY mode=viewport camera=/OmniverseKit_Persp ...（native 成功）
        若出现 STREAM_VIEW_FAILED，或 STREAM_VIEW_READY mode=usd-camera
        → 停止 WebRTC 验收并分析（生产验收必须 mode=viewport）。
STEP 7  docker ps --filter 'name=^/live-isaac-worker$' \
        --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
STEP 8  ss -ltnp | grep ':49100' || true
STEP 9  只启动一个 Windows Native WebRTC Client，连接 10.16.0.40
        （不要写 10.16.0.40:49100），Client 分辨率选 1280×720。
STEP 10 连接成功后：ss -lunp | grep ':47998' || true
STEP 11 docker logs --since 5m live-isaac-worker 2>&1 \
        | grep -Ei 'WORKER_READY|STREAM_VIEW|HydraEngine|renderer|webrtc|NVST|49100|47998|fatal|failed'
```

### 第一轮真正 PASS 标准（必须同时满足）

```text
A. 只有一个 Isaac container。
B. Simulation App Startup Complete。
C. WORKER_READY。
D. STREAM_VIEW_READY mode=viewport（不是 usd-camera）。
E. TCP 49100 正常（ESTAB ↔ Windows）。
F. 只连接一个 WebRTC Client。
G. Windows WebRTC Client 能看到 Franka / cube / target（不是纯黑、空地、极近 ground close-up）。
H. 视频持续更新，不是冻结的一帧。
I. 连接后媒体正常，必要时看到 UDP 47998。
J. 没有持续性的 NVST_R_BUSY。
K. 没有导致黑屏的 HydraEngine rtx failed creating scene renderer
   （启动早期有 warning 但之后真实画面稳定 → 记录 warning，不误判）。
L. Worker 一直 Up。
M. SimulationApp 没 shutdown。
N. 没有第二个 Isaac。
O. 镜头水平：世界 Z 轴视觉上保持竖直，无明显 roll。
P. 不需要手动鼠标旋转/缩放/平移调整构图。
Q. Franka + cube + target 同时出现在合理构图中（1280×720）。
==> LIVE CAMERA PASS
```

### DOF mismatch 追踪（TODO，不在本轮 Camera 修复里处理）

当前日志有 `DOF types mismatch`（USD 对 gripper 第 9 DOF 为 Invalid、Physics tensor
为 Translation）。本轮不改 Driver。**Streaming PASS 后**，C 真执行验收必须单独验证：
gripper DOF 7/8、grasp、release、reset、final_pose 没有因该 warning 产生假成功。

### 第一轮 PASS 之后

1. 恢复 OBS：Window Capture = Isaac Sim WebRTC Streaming Client → 开始直播；
2. Windows：`curl.exe -sS -L -o NUL -w "%{http_code}`n" "http://113.44.1.44/live/isaac/index.m3u8"` 必须 200；
3. `curl.exe -sS -L "http://113.44.1.44/live/isaac/index.m3u8"` 必须含 `#EXTM3U`；
4. 之后才进行最终 Cloud E2E（Browser → POST /api/runs → A → B → strategy.v1 →
   Huawei Job Queue → Windows Relay → School live-runtime → PersistentIsaacSession →
   adapter.run(strategy)），WebRTC Client 中必须亲眼看到 Franka 运动，
   且 `execution.json` 的 `provenance.backend=="isaac"`、world_id / kit_instance_id
   与 livestream worker 一致。

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
