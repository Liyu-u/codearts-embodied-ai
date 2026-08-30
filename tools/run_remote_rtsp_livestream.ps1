[CmdletBinding()]
param(
    [string]$Server = "10.16.0.40",
    [int]$Port = 5122,
    [string]$User = "stu_01",
    [string]$RemoteBase = "/data/stu_01/workspace",
    [string]$RunId = "rtsp",
    [string]$SshKeyPath = "",
    [switch]$InteractiveAuth,
    [ValidatePattern('^[0-9]+$')]
    [string]$GpuDevice = "0",
    [int]$RtspPort = 8554,
    [string]$MountPath = "/stream",
    [int]$Width = 1280,
    [int]$Height = 720,
    [int]$Fps = 30,
    [int]$StartupTimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$remoteSpec = "$User@$Server"
$containerName = "codearts-$RunId-rtsp"
$remoteRoot = "$RemoteBase/codearts-$RunId-rtsp"

if ($RunId -notmatch '^[A-Za-z0-9._-]+$') { throw "RunId 只能包含字母、数字、点、下划线和短横线" }
if ($RtspPort -lt 1 -or $RtspPort -gt 65535) { throw "RtspPort 必须在 1..65535" }
if (-not $MountPath.StartsWith("/")) { throw "MountPath 必须以 / 开头" }
if ($Width -le 0 -or $Height -le 0 -or $Fps -le 0) { throw "Width、Height 和 Fps 必须为正数" }
foreach ($tool in @("ssh", "scp")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "缺少命令: $tool" }
}
if ($SshKeyPath -and -not (Test-Path -LiteralPath $SshKeyPath -PathType Leaf)) {
    throw "SSH 私钥不存在: $SshKeyPath"
}

$commonSshOptions = @("-o", "ConnectTimeout=12", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3")
if ($SshKeyPath) { $commonSshOptions += @("-i", $SshKeyPath) }
if (-not $InteractiveAuth) { $commonSshOptions += @("-o", "BatchMode=yes") }

function Invoke-SshChecked([string]$Command) {
    $encodedCommand = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Command))
    $remoteCommand = "printf '%s' '$encodedCommand' | base64 -d | bash"
    & ssh -n -T -p $Port @commonSshOptions $remoteSpec $remoteCommand
    if ($LASTEXITCODE -ne 0) { throw "远程命令失败，退出码 $LASTEXITCODE" }
}

function Copy-ToRemote([string]$LocalPath, [string]$RemotePath) {
    & scp -P $Port @commonSshOptions $LocalPath "$remoteSpec`:$RemotePath"
    if ($LASTEXITCODE -ne 0) { throw "上传失败，退出码 $LASTEXITCODE" }
}

$localFiles = @(
    @{ Local = (Join-Path $repoRoot "tools/run_isaac_rtsp_livestream.py"); Remote = "$remoteRoot/tools/run_isaac_rtsp_livestream.py" },
    @{ Local = (Join-Path $repoRoot "tools/run_isaac_camera_perception.py"); Remote = "$remoteRoot/tools/run_isaac_camera_perception.py" },
    @{ Local = (Join-Path $repoRoot "tools/run_executor_acceptance.py"); Remote = "$remoteRoot/tools/run_executor_acceptance.py" }
)
foreach ($file in $localFiles) {
    if (-not (Test-Path -LiteralPath $file.Local -PathType Leaf)) { throw "本地文件不存在: $($file.Local)" }
}

Write-Host "[1/4] 检查校园服务器 Docker、GPU 和镜像..." -ForegroundColor Cyan
Invoke-SshChecked "set -eu; hostname; docker --version; nvidia-smi --query-gpu=name --format=csv,noheader; docker image inspect nvcr.io/nvidia/isaac-sim:6.0.0 >/dev/null"

Write-Host "[2/4] 上传 RTSP 启动器及相机场景依赖..." -ForegroundColor Cyan
Invoke-SshChecked "set -eu; mkdir -p '$remoteRoot/tools'; chmod 755 '$remoteRoot' '$remoteRoot/tools'"
foreach ($file in $localFiles) { Copy-ToRemote $file.Local $file.Remote }

$remoteRun = @(
    'set -u',
    "remote_root='__REMOTE_ROOT__'",
    "container_name='__CONTAINER_NAME__'",
    "gpu_device='__GPU_DEVICE__'",
    "rtsp_port='__RTSP_PORT__'",
    "mount_path='__MOUNT_PATH__'",
    "startup_timeout='__STARTUP_TIMEOUT__'",
    'if docker ps -a --format "{{.Names}}" | grep -Fxq "$container_name"; then',
    '  echo "RTSP_CONTAINER_EXISTS name=$container_name"',
    '  docker ps -a --filter "name=^/$container_name$" --format "table {{.Names}}\\t{{.Status}}"',
    '  exit 20',
    'fi',
    'docker run -d --no-healthcheck --name "$container_name" --gpus "device=$gpu_device" --network host -u 1234:1234 -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=N -e ISAACSIM_ASSET_ROOT=/isaacsim_assets/Assets/Isaac/6.0 -v "$remote_root:/workspace:ro" -v /data/stu_01/isaac_assets:/isaacsim_assets:ro --entrypoint /isaac-sim/python.sh nvcr.io/nvidia/isaac-sim:6.0.0 /workspace/tools/run_isaac_rtsp_livestream.py --port "$rtsp_port" --mount-path "$mount_path" --width __WIDTH__ --height __HEIGHT__ --fps __FPS__ --startup-timeout "$startup_timeout" --enable isaacsim.streaming.rtsp --/app/headless=true --/persistent/isaac/asset_root/default=/isaacsim_assets/Assets/Isaac/6.0',
    'polls=$((startup_timeout / 2 + 1))',
    'i=0',
    'while [ "$i" -lt "$polls" ]; do',
    '  if docker logs "$container_name" 2>&1 | grep -q "RTSP_READY port="; then',
    '    echo "RTSP_CONTAINER_STARTED name=$container_name port=$rtsp_port path=$mount_path"',
    '    exit 0',
    '  fi',
    '  running=$(docker inspect -f "{{.State.Running}}" "$container_name" 2>/dev/null || true)',
    '  if [ "$running" != true ]; then',
    '    echo RTSP_CONTAINER_EXITED',
    '    docker logs --tail 160 "$container_name" 2>&1 || true',
    '    exit 21',
    '  fi',
    '  i=$((i + 1))',
    '  sleep 2',
    'done',
    'echo "RTSP_START_TIMEOUT name=$container_name"',
    'docker logs --tail 160 "$container_name" 2>&1 || true',
    'exit 22'
) -join ([char]10)
$remoteRun = $remoteRun.Replace('__REMOTE_ROOT__', $remoteRoot).Replace('__CONTAINER_NAME__', $containerName).Replace('__GPU_DEVICE__', $GpuDevice).Replace('__RTSP_PORT__', [string]$RtspPort).Replace('__MOUNT_PATH__', $MountPath).Replace('__WIDTH__', [string]$Width).Replace('__HEIGHT__', [string]$Height).Replace('__FPS__', [string]$Fps).Replace('__STARTUP_TIMEOUT__', [string]$StartupTimeoutSeconds)

Write-Host "[3/4] 启动 Isaac Sim RTSP 容器并等待 8554 就绪..." -ForegroundColor Cyan
Invoke-SshChecked $remoteRun

Write-Host "[4/4] RTSP 已启动。" -ForegroundColor Green
Write-Host "服务器地址: rtsp://127.0.0.1:$RtspPort$MountPath（需通过 SSH 隧道访问）"
Write-Host ('停止命令: ssh -p {0} {1} "docker rm -f {2}"' -f $Port, $remoteSpec, $containerName)
Write-Host "下一步: .\tools\start_rtsp_ssh_tunnel.ps1"
