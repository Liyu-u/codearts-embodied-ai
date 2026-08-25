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

# Reuse the reviewed transport runner, changing only the remote Python entry.
# This keeps SSH/SCP cleanup and evidence handling identical across camera runs.
$runnerPath = Join-Path $PSScriptRoot "run_remote_camera_acceptance.ps1"
$runnerText = Get-Content -LiteralPath $runnerPath -Raw
$runnerText = $runnerText.Replace("run_camera_executor_acceptance_v2.py", "run_camera_executor_acceptance_v3.py")
$runner = [scriptblock]::Create($runnerText)
$forward = @{
    Server = $Server
    Port = $Port
    User = $User
    RemoteBase = $RemoteBase
    StrategyFile = $StrategyFile
    RunId = $RunId
    SshKeyPath = $SshKeyPath
    ConnectTimeoutSeconds = $ConnectTimeoutSeconds
    StartupTimeoutSeconds = $StartupTimeoutSeconds
    Device = $Device
}
if ($InteractiveAuth) { $forward["InteractiveAuth"] = $true }
if ($KeepRemote) { $forward["KeepRemote"] = $true }
& $runner @forward
exit $LASTEXITCODE
