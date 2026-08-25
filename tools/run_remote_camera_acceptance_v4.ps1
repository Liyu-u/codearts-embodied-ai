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

$source = Join-Path $PSScriptRoot "run_remote_camera_acceptance.ps1"
$temporary = Join-Path $PSScriptRoot ".run_remote_camera_acceptance_$PID.ps1"
$text = Get-Content -LiteralPath $source -Raw
$text = $text.Replace("run_camera_executor_acceptance_v2.py", "run_camera_executor_acceptance_v4.py")
Set-Content -LiteralPath $temporary -Value $text -Encoding UTF8
try {
    $forward = @{
        Server = $Server; Port = $Port; User = $User; RemoteBase = $RemoteBase
        StrategyFile = $StrategyFile; RunId = $RunId; SshKeyPath = $SshKeyPath
        ConnectTimeoutSeconds = $ConnectTimeoutSeconds; StartupTimeoutSeconds = $StartupTimeoutSeconds; Device = $Device
    }
    if ($InteractiveAuth) { $forward["InteractiveAuth"] = $true }
    if ($KeepRemote) { $forward["KeepRemote"] = $true }
    & $temporary @forward
    exit $LASTEXITCODE
}
finally {
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
}
