[CmdletBinding()]
param(
    [string]$Server = "10.16.0.40",
    [int]$Port = 5122,
    [string]$User = "stu_01",
    [string]$RemoteBase = "/data/stu_01/workspace",
    [string]$StrategyFile = "reports/live_chain_ab.json",
    [ValidateSet("direct", "stack_on")]
    [string]$PlacementMode = "direct",
    [ValidateSet("cpu", "cuda", "cuda:0")]
    [string]$Device = "cuda",
    [switch]$KeepRemote
)

<#
.SYNOPSIS
    Package the current executor, run it in remote Isaac Sim, and fetch evidence.

.DESCRIPTION
    This script is intentionally independent of local DeepSeek/CodeArts calls.
    The caller supplies a strategy.v1 report (the default is the live A/B report)
    and this script performs the reproducible C-stage hand-off:

      local code + strategy -> SSH/SCP -> isolated remote workspace -> Docker
      Isaac Sim/CUDA -> execution.v1 + progress.jsonl -> local reports folder

    The script never reads or uploads .env, tracecoder_llm.env, CSV credentials,
    or other secret files. SSH authentication is delegated to OpenSSH and may
    prompt for the server password unless key-based authentication is enabled.
#>

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$strategyPath = Join-Path $repoRoot $StrategyFile
if (-not (Test-Path -LiteralPath $strategyPath -PathType Leaf)) {
    throw "策略文件不存在: $strategyPath"
}

foreach ($tool in @("ssh", "scp", "tar")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "缺少命令: $tool"
    }
}

$runId = "live-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$remoteRoot = "$RemoteBase/codearts-$runId"
$localResult = Join-Path $repoRoot "reports/$runId"
$bundle = Join-Path ([System.IO.Path]::GetTempPath()) "codearts-$runId.tar.gz"
$remoteSpec = "$User@$Server"

New-Item -ItemType Directory -Force -Path $localResult | Out-Null

function Invoke-SshChecked {
    param([Parameter(Mandatory)][string]$Command)
    # -n/-T prevent an async Docker child from inheriting the interactive
    # terminal and keeping the SSH channel open after the report is ready.
    & ssh -n -T -p $Port -o StrictHostKeyChecking=no $remoteSpec $Command
    if ($LASTEXITCODE -ne 0) {
        throw "远程命令失败，退出码 $LASTEXITCODE"
    }
}

function Copy-ToRemote {
    param(
        [Parameter(Mandatory)][string]$LocalPath,
        [Parameter(Mandatory)][string]$RemotePath
    )
    & scp -P $Port -o StrictHostKeyChecking=no $LocalPath "$remoteSpec`:$RemotePath"
    if ($LASTEXITCODE -ne 0) {
        throw "上传失败，退出码 $LASTEXITCODE"
    }
}

function Copy-FromRemote {
    param(
        [Parameter(Mandatory)][string]$RemotePath,
        [Parameter(Mandatory)][string]$LocalPath
    )
    & scp -P $Port -o StrictHostKeyChecking=no "$remoteSpec`:$RemotePath" $LocalPath
    if ($LASTEXITCODE -ne 0) {
        throw "下载失败，退出码 $LASTEXITCODE"
    }
}

try {
    Write-Host "[1/6] 检查远程 SSH、Docker 和 GPU..." -ForegroundColor Cyan
    Invoke-SshChecked "hostname; docker --version; nvidia-smi --query-gpu=name --format=csv,noheader"

    Write-Host "[2/6] 打包当前 A/B/C/D 联调代码（不包含密钥）..." -ForegroundColor Cyan
    & tar -czf $bundle -C $repoRoot contracts integration modules tools/run_executor_acceptance.py
    if ($LASTEXITCODE -ne 0) {
        throw "本地打包失败，退出码 $LASTEXITCODE"
    }

    Write-Host "[3/6] 创建隔离远程工作目录并上传代码、策略..." -ForegroundColor Cyan
    Invoke-SshChecked "mkdir -p '$remoteRoot/results' && chmod 777 '$remoteRoot' '$remoteRoot/results'"
    Copy-ToRemote $bundle "$remoteRoot/codearts-bundle.tar.gz"
    Copy-ToRemote $strategyPath "$remoteRoot/live_chain_ab.json"

    Write-Host "[4/6] 启动 Isaac Sim 6.0 CUDA 容器并执行策略..." -ForegroundColor Cyan
    $remoteRun = @"
set -eu
rm -f '$remoteRoot/results/execution.json' '$remoteRoot/results/progress.jsonl' '$remoteRoot/results/container.log'
tar -xzf '$remoteRoot/codearts-bundle.tar.gz' -C '$remoteRoot'
nohup setsid docker run --rm --entrypoint bash --gpus 'device=0' --network none -u 1234:1234 \
  -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=N \
  -e ISAACSIM_ASSET_ROOT=/isaacsim_assets/Assets/Isaac/6.0 \
  -v '${remoteRoot}:/workspace' \
  -v /data/stu_01/isaac_assets:/isaacsim_assets:ro \
  nvcr.io/nvidia/isaac-sim:6.0.0 \
  -lc 'cd /isaac-sim && ./python.sh /workspace/tools/run_executor_acceptance.py --device $Device --result-dir /workspace/results --placement-mode $PlacementMode --strategy-file /workspace/live_chain_ab.json --/app/headless=true --/persistent/isaac/asset_root/default=/isaacsim_assets/Assets/Isaac/6.0' \
  > '$remoteRoot/results/container.log' 2>&1 < /dev/null &
docker_pid=`$!
echo "`$docker_pid" > '$remoteRoot/results/docker.pid'
for attempt in `$(seq 1 240); do
  if [ -s '$remoteRoot/results/execution.json' ]; then
    echo REPORT_READY
    exit 0
  fi
  if ! kill -0 "`$docker_pid" 2>/dev/null; then
    echo CONTAINER_EXITED
    tail -n 80 '$remoteRoot/results/container.log' || true
    exit 1
  fi
  sleep 2
done
echo ISAAC_TIMEOUT
tail -n 80 '$remoteRoot/results/container.log' || true
kill "`$docker_pid" 2>/dev/null || true
exit 1
"@.Trim()
    Invoke-SshChecked $remoteRun

    Write-Host "[5/6] 下载 execution.v1 和进度证据..." -ForegroundColor Cyan
    Copy-FromRemote "$remoteRoot/results/execution.json" (Join-Path $localResult "execution.json")
    Copy-FromRemote "$remoteRoot/results/progress.jsonl" (Join-Path $localResult "progress.jsonl")

    $execution = Get-Content -LiteralPath (Join-Path $localResult "execution.json") -Raw | ConvertFrom-Json
    if ($execution.status -ne "SUCCEEDED") {
        throw "Isaac Sim 执行未通过: status=$($execution.status)"
    }
    if (-not $KeepRemote) {
        Write-Host "[6/6] 清理远程临时代码包（保留结果目录）..." -ForegroundColor Cyan
        Invoke-SshChecked "rm -f '$remoteRoot/codearts-bundle.tar.gz'"
    } else {
        Write-Host "[6/6] 已保留远程工作目录: $remoteRoot" -ForegroundColor Yellow
    }

    Write-Host "通过：$($execution.status); task_id=$($execution.task_id); moved_m=$($execution.cube_moved_m)" -ForegroundColor Green
    Write-Host "本地证据目录: $localResult" -ForegroundColor Green
}
finally {
    if (Test-Path -LiteralPath $bundle) {
        Remove-Item -LiteralPath $bundle -Force -ErrorAction SilentlyContinue
    }
}
