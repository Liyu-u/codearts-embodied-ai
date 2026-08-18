[CmdletBinding()]
param(
    [int]$Port = 8765,
    [int]$WaitSeconds = 15,
    [string]$PythonPath = "",
    [ValidateSet("off", "auto", "required")]
    [string]$CodeArtsMode = "",
    [string]$CodeArtsModel = ""
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonCommand = $null
$pythonArguments = @("demo/server.py")
if ($PythonPath) {
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw "Python executable not found: $PythonPath"
    }
    $pythonExecutable = (Resolve-Path -LiteralPath $PythonPath).Path
} else {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $pythonCommand) {
        $pythonCommand = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($pythonCommand) {
            $pythonArguments = @("-3", "demo/server.py")
        }
    }
    if ($null -eq $pythonCommand) {
        throw "Python was not found. Install Python, add it to PATH, or pass -PythonPath."
    }
    $pythonExecutable = $pythonCommand.Source
}

$portPattern = ":{0}\s+.*LISTENING" -f $Port
$existingListener = netstat -ano | Select-String $portPattern
if ($existingListener) {
    throw "Port $Port is already in use. Choose another port, for example: powershell -File demo/start_demo.ps1 -Port 8766"
}

$env:DEMO_PORT = "$Port"
if ($CodeArtsMode) {
    $env:CODEARTS_STRATEGY_MODE = $CodeArtsMode
}
if ($CodeArtsModel) {
    $env:CODEARTS_STRATEGY_MODEL = $CodeArtsModel
}
$tempRoot = [System.IO.Path]::GetTempPath()
$stdoutLog = Join-Path $tempRoot "closed-loop-demo-$Port.out.log"
$stderrLog = Join-Path $tempRoot "closed-loop-demo-$Port.err.log"

$process = Start-Process `
    -FilePath $pythonExecutable `
    -ArgumentList $pythonArguments `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

$health = $null
$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
        if ($health.status -eq "ok" -and $health.healthy -eq $true) {
            break
        }
    } catch {
        # The server is still starting; keep polling.
    }
    Start-Sleep -Milliseconds 250
}

if ($null -eq $health -or $health.status -ne "ok" -or $health.healthy -ne $true) {
    if ($process -and -not $process.HasExited) {
        Stop-Process -Id $process.Id
    }
    throw "Demo health check failed. See logs: $stdoutLog and $stderrLog"
}

Write-Output "Demo started: http://127.0.0.1:$Port/"
Write-Output "Process PID: $($process.Id)"
Write-Output "Health: $($health.status); all four modules are connected"
Write-Output "Stdout log: $stdoutLog"
Write-Output "Stop with: Stop-Process -Id $($process.Id)"
