[CmdletBinding()]
param(
    [string]$Server = "10.16.0.40",
    [int]$Port = 5122,
    [string]$User = "stu_01",
    [string]$RemoteBase = "/data/stu_01/workspace",
    [string]$StrategyFile,
    [string]$RunId = "",
    [string]$SshKeyPath = "",
    [switch]$InteractiveAuth,
    [int]$ConnectTimeoutSeconds = 12,
    [int]$StartupTimeoutSeconds = 900,
    [ValidateSet("cpu", "cuda", "cuda:0")]
    [string]$Device = "cuda",
    [switch]$KeepRemote
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $StrategyFile) { throw "StrategyFile 必须指定" }
$strategyPath = Join-Path $repoRoot $StrategyFile
if (-not (Test-Path -LiteralPath $strategyPath -PathType Leaf)) { throw "策略文件不存在: $strategyPath" }
foreach ($tool in @("ssh", "scp", "tar")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "缺少命令: $tool" }
}
if ($SshKeyPath -and -not (Test-Path -LiteralPath $SshKeyPath -PathType Leaf)) { throw "SSH 私钥不存在: $SshKeyPath" }
if (-not $RunId) { $RunId = "camera-" + (Get-Date -Format "yyyyMMdd-HHmmss") }
if ($RunId -notmatch '^[A-Za-z0-9._-]+$') { throw "RunId 只能包含字母、数字、点、下划线和短横线" }

$remoteRoot = "$RemoteBase/codearts-$RunId"
$localResult = Join-Path $repoRoot "reports/$RunId"
$bundle = Join-Path ([System.IO.Path]::GetTempPath()) "codearts-$RunId-camera.tar.gz"
$remoteSpec = "$User@$Server"
New-Item -ItemType Directory -Force -Path $localResult | Out-Null
$commonSshOptions = @("-o", "ConnectTimeout=$ConnectTimeoutSeconds", "-o", "StrictHostKeyChecking=no")
if ($SshKeyPath) { $commonSshOptions += @("-i", $SshKeyPath) }
if (-not $InteractiveAuth) { $commonSshOptions += @("-o", "BatchMode=yes") }
$authMode = if ($SshKeyPath) { "key" } elseif ($InteractiveAuth) { "interactive" } else { "batch" }

function Invoke-SshChecked([string]$Command) {
    # Keep embedded docker quotes intact when a multi-line bash program is
    # passed through Windows PowerShell/OpenSSH.
    $encodedCommand = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Command))
    $remoteCommand = "printf '%s' '$encodedCommand' | base64 -d | bash"
    & ssh -n -T -p $Port @commonSshOptions $remoteSpec $remoteCommand
    if ($LASTEXITCODE -ne 0) { throw "远程命令失败，退出码 $LASTEXITCODE" }
}
function Copy-ToRemote([string]$LocalPath, [string]$RemotePath) {
    & scp -P $Port @commonSshOptions $LocalPath "$remoteSpec`:$RemotePath"
    if ($LASTEXITCODE -ne 0) { throw "上传失败，退出码 $LASTEXITCODE" }
}
function Copy-FromRemote([string]$RemotePath, [string]$LocalPath) {
    & scp -P $Port @commonSshOptions "$remoteSpec`:$RemotePath" $LocalPath
    if ($LASTEXITCODE -ne 0) { throw "下载失败，退出码 $LASTEXITCODE" }
}

try {
    Write-Host "[1/6] 检查 SSH、Docker、GPU..." -ForegroundColor Cyan
    Invoke-SshChecked "set -eu; hostname; docker --version; nvidia-smi --query-gpu=name --format=csv,noheader"
    Write-Host "[2/6] 打包摄像头 C 入口..." -ForegroundColor Cyan
    & tar --exclude='__pycache__' --exclude='*.pyc' -czf $bundle -C $repoRoot contracts integration modules tools/run_executor_acceptance.py tools/run_isaac_camera_perception.py tools/run_camera_executor_acceptance.py tools/run_camera_executor_acceptance_v2.py tools/run_camera_executor_acceptance_v3.py tools/run_camera_executor_acceptance_v4.py tools/run_camera_executor_acceptance_v5.py tools/run_camera_executor_acceptance_v6.py
    if ($LASTEXITCODE -ne 0) { throw "本地打包失败，退出码 $LASTEXITCODE" }
    Invoke-SshChecked "mkdir -p '$remoteRoot/results' && chmod 777 '$remoteRoot' '$remoteRoot/results'"
    Copy-ToRemote $bundle "$remoteRoot/codearts-camera-bundle.tar.gz"
    Copy-ToRemote $strategyPath "$remoteRoot/live_chain_ab.json"

    Write-Host "[3/6] 启动 Isaac Sim RGB-D 摄像头 + C..." -ForegroundColor Cyan
    $containerName = "codearts-$RunId"
    $remoteRun = @(
        "set -u",
        "remote_root='__REMOTE_ROOT__'",
        "device='__DEVICE__'",
        "timeout_seconds='__TIMEOUT__'",
        "container_name='__CONTAINER_NAME__'",
        'cleanup() { timeout 30 docker rm -f "$container_name" >/dev/null 2>&1 || true; }',
        'trap cleanup EXIT INT TERM',
        'rm -f "$remote_root/results"/*',
    'tar -xzf "$remote_root/codearts-camera-bundle.tar.gz" -C "$remote_root"',
    'chmod 777 "$remote_root" "$remote_root/results"',
        'timeout 30 docker rm -f "$container_name" >/dev/null 2>&1 || true',
        'gpu_args=()',
        'if [ "$device" != "cpu" ]; then gpu_args+=(--gpus device=0); fi',
    'docker_rc=0',
    'timeout "$timeout_seconds" docker run --name "$container_name" --rm --entrypoint /isaac-sim/python.sh "${gpu_args[@]}" --network none -u 1234:1234 \',
    '  -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=N -e ISAACSIM_ASSET_ROOT=/isaacsim_assets/Assets/Isaac/6.0 \',
    '  -v "$remote_root:/workspace" -v /data/stu_01/isaac_assets:/isaacsim_assets:ro nvcr.io/nvidia/isaac-sim:6.0.0 \',
    '  /workspace/tools/run_camera_executor_acceptance_v6.py --device "$device" --result-dir /workspace/results --strategy-file /workspace/live_chain_ab.json --frames 60 > "$remote_root/results/container.log" 2>&1 || docker_rc=$?',
    'echo "CONTAINER_RC=$docker_rc"',
    'echo "RUNNER_STATUS=$docker_rc" >> "$remote_root/results/container.log"',
    'test -s "$remote_root/results/progress.jsonl"',
    'test -s "$remote_root/results/perception.json"',
        'test -s "$remote_root/results/execution.json"',
        'exit 0'
    ) -join ([char]10)
    $remoteRun = $remoteRun.Replace('__REMOTE_ROOT__', $remoteRoot).Replace('__DEVICE__', $Device).Replace('__TIMEOUT__', [string]$StartupTimeoutSeconds).Replace('__CONTAINER_NAME__', $containerName)
    Invoke-SshChecked $remoteRun

    Write-Host "[4/6] 下载摄像头、C 执行与评估证据..." -ForegroundColor Cyan
    foreach ($name in @("camera_observation.json", "perception.json", "camera_metrics.json", "execution.json", "progress.jsonl", "container.log", "evaluation.json", "camera_post.json", "rgb.png")) {
        try { Copy-FromRemote "$remoteRoot/results/$name" (Join-Path $localResult $name) } catch { Write-Warning "证据缺失: $name" }
    }
    $perception = Get-Content -LiteralPath (Join-Path $localResult "perception.json") -Raw | ConvertFrom-Json
    $execution = Get-Content -LiteralPath (Join-Path $localResult "execution.json") -Raw | ConvertFrom-Json
    if ($perception.schema_version -ne "perception.v1") { throw "摄像头感知契约错误: schema_version=$($perception.schema_version)" }
    if ($execution.schema_version -ne "execution.v1") { throw "C 执行契约错误: schema_version=$($execution.schema_version)" }
    $runMetadata = [ordered]@{
        schema_version = "remote-camera-isaac-run.v1"
        run_id = $RunId
        server = $Server
        port = $Port
        user = $User
        remote_root = $remoteRoot
        auth_mode = $authMode
        device = $Device
        status = $execution.status
        perception_source = "isaac_camera_rgbd"
        online_pose_source = "rgbd_depth_backprojection"
        ground_truth_used_for_online_pose = $false
        execution_task_id = $execution.task_id
        completed_at = (Get-Date).ToUniversalTime().ToString("o")
    }
    $runMetadata | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $localResult "remote_run.json") -Encoding UTF8
    if (-not $KeepRemote) { Invoke-SshChecked "rm -f '$remoteRoot/codearts-camera-bundle.tar.gz'" }
    Write-Host "完成：C status=$($execution.status); task_id=$($execution.task_id); evidence=$localResult" -ForegroundColor Green
}
catch {
    $failure = [ordered]@{
        schema_version = "remote-camera-isaac-run.v1"
        run_id = $RunId
        status = "FAILED"
        failure_class = "runner"
        message = $_.Exception.Message
        auth_mode = $authMode
        completed_at = (Get-Date).ToUniversalTime().ToString("o")
    }
    $failure | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $localResult "remote_run.json") -Encoding UTF8
    throw
}
finally {
    if (Test-Path -LiteralPath $bundle) { Remove-Item -LiteralPath $bundle -Force -ErrorAction SilentlyContinue }
}
