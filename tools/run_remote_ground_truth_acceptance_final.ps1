[CmdletBinding()]
param(
    [string]$Server = "10.16.0.40",
    [int]$Port = 5122,
    [string]$User = "stu_01",
    [string]$RemoteBase = "/data/stu_01/workspace",
    [string]$StrategyFile = "reports/gt-20260822-004458/live_chain_ab_ground_truth.json",
    [string]$TaskConfig = "testdata/benchmark/real_isaac_versioned_v1.json",
    [int]$Seed = 20260830,
    [string]$RunId = "",
    [string]$SshKeyPath = "",
    [switch]$InteractiveAuth,
    [int]$ConnectTimeoutSeconds = 12,
    [int]$StartupTimeoutSeconds = 600,
    [ValidateSet("cpu", "cuda", "cuda:0")]
    [string]$Device = "cuda",
    [ValidateSet("0", "1")]
    [string]$GpuIndex = "1",
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$VariantId = "UNSPECIFIED",
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$CaseId = "real-isaac-default",
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$Category = "real_isaac",
    [ValidateSet("SUCCEEDED", "SAFE_STOP", "BLOCKED", "FAILED")]
    [string]$ExpectedStatus = "SUCCEEDED",
    [switch]$AllowStatusMismatch,
    [switch]$KeepRemote
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$strategyPath = Join-Path $repoRoot $StrategyFile
if (-not (Test-Path -LiteralPath $strategyPath -PathType Leaf)) { throw "策略文件不存在: $strategyPath" }
$taskConfigPath = Join-Path $repoRoot $TaskConfig
if (-not (Test-Path -LiteralPath $taskConfigPath -PathType Leaf)) { throw "任务配置不存在: $taskConfigPath" }
foreach ($tool in @("ssh", "scp", "tar")) { if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "缺少命令: $tool" } }
if ($SshKeyPath -and -not (Test-Path -LiteralPath $SshKeyPath -PathType Leaf)) { throw "SSH 私钥不存在: $SshKeyPath" }
if ($ConnectTimeoutSeconds -lt 3 -or $ConnectTimeoutSeconds -gt 120) { throw "ConnectTimeoutSeconds 必须在 3..120 之间" }
if ($StartupTimeoutSeconds -lt 30 -or $StartupTimeoutSeconds -gt 3600) { throw "StartupTimeoutSeconds 必须在 30..3600 之间" }
if (-not $RunId) { $RunId = "gt-final-" + (Get-Date -Format "yyyyMMdd-HHmmss") }
if ($RunId -notmatch '^[A-Za-z0-9._-]+$') { throw "RunId 只能包含字母、数字、点、下划线和短横线" }
$remoteRoot = "$RemoteBase/codearts-$RunId"
$localResult = Join-Path $repoRoot "reports/$RunId"
$bundle = Join-Path ([System.IO.Path]::GetTempPath()) "codearts-$RunId.tar.gz"
$remoteSpec = "$User@$Server"
New-Item -ItemType Directory -Force -Path $localResult | Out-Null
$commonSshOptions = @("-o", "ConnectTimeout=$ConnectTimeoutSeconds", "-o", "StrictHostKeyChecking=no")
if ($SshKeyPath) { $commonSshOptions += @("-i", $SshKeyPath) }
if (-not $InteractiveAuth) { $commonSshOptions += @("-o", "BatchMode=yes") }
$authMode = if ($SshKeyPath) { "key" } elseif ($InteractiveAuth) { "interactive" } else { "batch" }

function Invoke-SshChecked([string]$Command) {
    # Passing a multi-line bash program as a native Windows argument lets
    # PowerShell/OpenSSH reinterpret embedded quotes (notably the docker -lc
    # command).  Encode the program first so the remote shell receives the
    # exact bytes we generated.
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
function Get-FailureClass([string]$Message) {
    if ($Message -match "Permission denied|publickey|password|ConnectTimeout|connection|timed out|上传失败|下载失败") { return "transport_auth" }
    if ($Message -match "schema|task_id|契约|contract") { return "contract" }
    if ($Message -match "SAFE_STOP|安全|collision|workspace|timeout") { return "safety_or_execution" }
    return "runner"
}

try {
    Write-Host "[1/6] 检查 SSH、Docker、GPU$GpuIndex..." -ForegroundColor Cyan
    Invoke-SshChecked "set -eu; hostname; docker --version; nvidia-smi -i $GpuIndex --query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu --format=csv,noheader"
    Write-Host "[2/6] 打包最终 Ground Truth 入口..." -ForegroundColor Cyan
    & tar -czf $bundle -C $repoRoot contracts integration modules $TaskConfig tools/run_executor_acceptance.py tools/real_isaac_experiment.py tools/run_ground_truth_executor_acceptance.py tools/run_ground_truth_executor_acceptance_v4.py
    if ($LASTEXITCODE -ne 0) { throw "本地打包失败，退出码 $LASTEXITCODE" }
    Invoke-SshChecked "mkdir -p '$remoteRoot/results' && chmod 777 '$remoteRoot' '$remoteRoot/results'"
    Copy-ToRemote $bundle "$remoteRoot/codearts-bundle.tar.gz"
    Copy-ToRemote $strategyPath "$remoteRoot/live_chain_ab.json"
    Write-Host "[3/6] 启动 Isaac Sim Ground Truth + C..." -ForegroundColor Cyan
    $containerName = "codearts-$RunId"
    $remoteRun = @(
        "set -u",
        "remote_root='__REMOTE_ROOT__'",
        "device='__DEVICE__'",
        "gpu_index='__GPU_INDEX__'",
        "timeout_seconds='__TIMEOUT__'",
        "container_name='__CONTAINER_NAME__'",
        "variant_id='__VARIANT_ID__'",
        "case_id='__CASE_ID__'",
        "category='__CATEGORY__'",
        "expected_status='__EXPECTED_STATUS__'",
        'cleanup() { timeout 30 docker rm -f "$container_name" >/dev/null 2>&1 || true; }',
        'save_logs() { timeout 30 docker logs "$container_id" > "$remote_root/results/container.log" 2>&1 || true; }',
        'trap cleanup EXIT INT TERM',
        'rm -f "$remote_root/results/perception.json" "$remote_root/results/execution.json" "$remote_root/results/progress.jsonl" "$remote_root/results/container.log"',
        'tar -xzf "$remote_root/codearts-bundle.tar.gz" -C "$remote_root"',
        'timeout 30 docker rm -f "$container_name" >/dev/null 2>&1 || true',
        'gpu_args=()',
        'if [ "$device" != "cpu" ]; then gpu_args+=(--gpus "device=$gpu_index"); fi',
        'container_id=""',
        'if container_id=$(timeout "$timeout_seconds" docker run -d --name "$container_name" --rm --entrypoint bash "${gpu_args[@]}" --network none -u 1234:1234 \',
        '  -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=N -e ISAACSIM_ASSET_ROOT=/isaacsim_assets/Assets/Isaac/6.0 \',
        '  -v "$remote_root:/workspace" -v /data/stu_01/isaac_assets:/isaacsim_assets:ro nvcr.io/nvidia/isaac-sim:6.0.0 \',
        '  -lc "cd /isaac-sim && ./python.sh /workspace/tools/run_ground_truth_executor_acceptance_v4.py --device $device --gpu-index __GPU_INDEX__ --seed __SEED__ --experiment-run-id __RUN_ID__ --result-dir /workspace/results --task-config /workspace/__TASK_CONFIG__ --strategy-file /workspace/live_chain_ab.json --variant-id $variant_id --case-id $case_id --category $category --expected-status $expected_status --/app/headless=true --/persistent/isaac/asset_root/default=/isaacsim_assets/Assets/Isaac/6.0"); then',
        '  if [ -z "$container_id" ]; then',
        '    echo "CONTAINER_START_EMPTY_ID"',
        '    exit 1',
        '  fi',
        '  echo "CONTAINER_STARTED $container_id"',
        'else',
        '  echo "CONTAINER_START_FAILED"',
        '  exit 124',
        'fi',
        'run_deadline=$(( $(date +%s) + timeout_seconds ))',
        'while [ "$(date +%s)" -lt "$run_deadline" ]; do',
        '  if [ -s "$remote_root/results/perception.json" ] && [ -s "$remote_root/results/execution.json" ]; then',
        '    save_logs',
        '    echo REPORT_READY',
        '    exit 0',
        '  fi',
        '  running=$(timeout 10 docker inspect -f ''{{.State.Running}}'' "$container_id" 2>/dev/null || echo false)',
        '  if [ "$running" != "true" ]; then',
        '    save_logs',
        '    echo "CONTAINER_EXITED"',
        '    tail -n 100 "$remote_root/results/container.log" 2>/dev/null || true',
        '    exit 1',
        '  fi',
        '  sleep 2',
        'done',
        'save_logs',
        'echo "REPORT_TIMEOUT"',
        'tail -n 100 "$remote_root/results/container.log" 2>/dev/null || true',
        'exit 124'
    ) -join ([char]10)
    $remoteRun = $remoteRun.Replace('__REMOTE_ROOT__', $remoteRoot).Replace('__DEVICE__', $Device).Replace('__GPU_INDEX__', $GpuIndex).Replace('__SEED__', [string]$Seed).Replace('__RUN_ID__', $RunId).Replace('__TASK_CONFIG__', $TaskConfig).Replace('__TIMEOUT__', [string]$StartupTimeoutSeconds).Replace('__CONTAINER_NAME__', $containerName).Replace('__VARIANT_ID__', $VariantId).Replace('__CASE_ID__', $CaseId).Replace('__CATEGORY__', $Category).Replace('__EXPECTED_STATUS__', $ExpectedStatus)
    Invoke-SshChecked $remoteRun
    Write-Host "[4/6] 下载感知、执行、进度证据..." -ForegroundColor Cyan
    Copy-FromRemote "$remoteRoot/results/perception.json" (Join-Path $localResult "perception.json")
    Copy-FromRemote "$remoteRoot/results/execution.json" (Join-Path $localResult "execution.json")
    Copy-FromRemote "$remoteRoot/results/strategy.json" (Join-Path $localResult "strategy.json")
    Copy-FromRemote "$remoteRoot/results/progress.jsonl" (Join-Path $localResult "progress.jsonl")
    Copy-FromRemote "$remoteRoot/results/container.log" (Join-Path $localResult "container.log")
    $perception = Get-Content -LiteralPath (Join-Path $localResult "perception.json") -Raw | ConvertFrom-Json
    $execution = Get-Content -LiteralPath (Join-Path $localResult "execution.json") -Raw | ConvertFrom-Json
    if ($perception.schema_version -ne "perception.v1") { throw "Isaac Sim 感知契约错误: schema_version=$($perception.schema_version)" }
    if ($perception.execution_context.source -ne "isaac_sim.usd_physx") { throw "感知来源不是 live USD/PhysX: $($perception.execution_context.source)" }
    if ($execution.schema_version -ne "execution.v1") { throw "Isaac Sim 执行契约错误: schema_version=$($execution.schema_version)" }
    if ($execution.status -ne $ExpectedStatus -and -not $AllowStatusMismatch) {
        throw "Isaac Sim 执行结果与预期不一致: expected=$ExpectedStatus; actual=$($execution.status)"
    }
    $runMetadata = [ordered]@{
        schema_version = "remote-isaac-run.v1"
        run_id = $RunId
        server = $Server
        port = $Port
        user = $User
        remote_root = $remoteRoot
        auth_mode = $authMode
        device = $Device
        gpu_index = $GpuIndex
        variant_id = $VariantId
        case_id = $CaseId
        category = $Category
        expected_status = $ExpectedStatus
        status_match = ($execution.status -eq $ExpectedStatus)
        status = $execution.status
        perception_source = $perception.execution_context.source
        execution_task_id = $execution.task_id
        completed_at = (Get-Date).ToUniversalTime().ToString("o")
    }
    $runMetadata | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $localResult "remote_run.json") -Encoding UTF8
    if (-not $KeepRemote) { Invoke-SshChecked "rm -f '$remoteRoot/codearts-bundle.tar.gz'" }
    Write-Host "通过：$($execution.status); task_id=$($execution.task_id); evidence=$localResult" -ForegroundColor Green
}
catch {
    $failure = [ordered]@{
        schema_version = "remote-isaac-run.v1"
        run_id = $RunId
        status = "FAILED"
        failure_class = Get-FailureClass $_.Exception.Message
        message = $_.Exception.Message
        auth_mode = $authMode
        completed_at = (Get-Date).ToUniversalTime().ToString("o")
    }
    $failure | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $localResult "remote_run.json") -Encoding UTF8
    throw
}
finally {
    if (Test-Path -LiteralPath $bundle) { Remove-Item -LiteralPath $bundle -Force -ErrorAction SilentlyContinue }
}
