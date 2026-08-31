[CmdletBinding()]
param(
    [string]$TaskConfig = "testdata/benchmark/real_isaac_supplement_v2.json",
    [string]$StrategyFile = "reports/real-final-v4-happy-20260830-r1/strategy.json",
    [string]$RunPrefix = "supplement-v2",
    [string]$SshKeyPath = "C:\Users\14810\.ssh\id_ed25519_codearts_bridge",
    [ValidateSet("0", "1")]
    [string]$GpuIndex = "1",
    [int]$StartIndex = 1,
    [int]$EndIndex = 0,
    [string]$OutputFile = "reports/supplement-v2-batch-summary-20260830.json",
    [int]$StartupTimeoutSeconds = 900,
    [int]$CooldownSeconds = 12
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $repoRoot $TaskConfig
$strategyPath = Join-Path $repoRoot $StrategyFile
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { throw "任务配置不存在: $configPath" }
if (-not (Test-Path -LiteralPath $strategyPath -PathType Leaf)) { throw "策略文件不存在: $strategyPath" }

$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$runner = Join-Path $PSScriptRoot "run_remote_ground_truth_acceptance_final.ps1"
$summary = [System.Collections.Generic.List[object]]::new()
$allCases = @($config.tasks)
if ($EndIndex -le 0 -or $EndIndex -gt $allCases.Count) { $EndIndex = $allCases.Count }
if ($StartIndex -lt 1 -or $StartIndex -gt $EndIndex) { throw "StartIndex/EndIndex 范围无效" }
$index = 0
foreach ($case in $allCases) {
    $index++
    if ($index -lt $StartIndex -or $index -gt $EndIndex) { continue }
    $runId = "$RunPrefix-$($case.id)-$index"
    $category = [string]$case.category
    $expected = [string]$case.expected_status
    $record = [ordered]@{
        case_id = [string]$case.id
        category = $category
        expected_status = $expected
        run_id = $runId
        command_status = "NOT_STARTED"
        actual_status = $null
        status_match = $false
        report_dir = "reports/$runId"
        error = $null
    }
    Write-Host "[$index/$(@($config.tasks).Count)] $($case.id) expected=$expected" -ForegroundColor Cyan
    try {
        & $runner `
            -TaskConfig $TaskConfig `
            -StrategyFile $StrategyFile `
            -RunId $runId `
            -SshKeyPath $SshKeyPath `
            -Device cuda `
            -GpuIndex $GpuIndex `
            -VariantId V4_FULL `
            -CaseId ([string]$case.id) `
            -Category $category `
            -ExpectedStatus $expected `
            -StartupTimeoutSeconds $StartupTimeoutSeconds `
            -AllowStatusMismatch
        $record.command_status = "COMPLETED"
    }
    catch {
        $record.command_status = "ERROR"
        $record.error = $_.Exception.Message
    }
    $executionPath = Join-Path $repoRoot "reports/$runId/execution.json"
    if (Test-Path -LiteralPath $executionPath -PathType Leaf) {
        $execution = Get-Content -LiteralPath $executionPath -Raw | ConvertFrom-Json
        $record.actual_status = [string]$execution.status
        $record.status_match = ($record.actual_status -eq $expected)
    }
    $summary.Add([pscustomobject]$record)
    if ($index -lt $EndIndex -and $CooldownSeconds -gt 0) {
        Start-Sleep -Seconds $CooldownSeconds
    }
}

$output = [ordered]@{
    schema_version = "remote-isaac-supplement-batch.v1"
    task_config = $TaskConfig
    run_prefix = $RunPrefix
    started_at = $summary | Select-Object -First 1 | ForEach-Object { $null }
    gpu_index = $GpuIndex
    start_index = $StartIndex
    end_index = $EndIndex
    total = $summary.Count
    completed_reports = @($summary | Where-Object { $_.actual_status }).Count
    status_matches = @($summary | Where-Object { $_.status_match }).Count
    command_errors = @($summary | Where-Object { $_.command_status -eq "ERROR" }).Count
    cases = @($summary)
}
$output.started_at = (Get-Date).ToUniversalTime().ToString("o")
$outputPath = Join-Path $repoRoot $OutputFile
$output | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $outputPath -Encoding UTF8
Write-Host "批量汇总：$outputPath; reports=$($output.completed_reports)/$($output.total); status_matches=$($output.status_matches)" -ForegroundColor Green
