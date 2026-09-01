[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$CloudUrl = $env:CLOUD_RELAY_URL,

    [Parameter(Mandatory = $false)]
    [string]$Server = $(if ($env:ISAAC_RELAY_SERVER) { $env:ISAAC_RELAY_SERVER } else { "10.16.0.40" }),

    [Parameter(Mandatory = $false)]
    [int]$Port = $(if ($env:ISAAC_RELAY_PORT) { [int]$env:ISAAC_RELAY_PORT } else { 5122 }),

    [Parameter(Mandatory = $false)]
    [string]$User = $(if ($env:ISAAC_RELAY_USER) { $env:ISAAC_RELAY_USER } else { "stu_01" }),

    [Parameter(Mandatory = $false)]
    [string]$SshKey = $env:ISAAC_RELAY_SSH_KEY,

    [Parameter(Mandatory = $false)]
    [string]$KnownHosts = $env:ISAAC_RELAY_KNOWN_HOSTS,

    [Parameter(Mandatory = $false)]
    [string]$RemoteRoot = $(if ($env:ISAAC_RELAY_REMOTE_ROOT) { $env:ISAAC_RELAY_REMOTE_ROOT } else { "/data/stu_01/workspace/live-runtime" }),

    [Parameter(Mandatory = $false)]
    [string]$RelayId = $(if ($env:ISAAC_RELAY_ID) { $env:ISAAC_RELAY_ID } else { "windows-campus-relay" }),

    [Parameter(Mandatory = $false)]
    [string]$PythonPath = "python",

    [Parameter(Mandatory = $false)]
    [string]$StateDir = ".relay-runtime",

    [switch]$Once,
    [switch]$CheckConfig
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($CloudUrl)) {
    throw "请设置 CLOUD_RELAY_URL，或通过 -CloudUrl 指定华为云服务地址。"
}
if ([string]::IsNullOrWhiteSpace($env:CLOUD_RELAY_TOKEN)) {
    throw "请在当前 PowerShell 会话设置 CLOUD_RELAY_TOKEN。"
}
if ([string]::IsNullOrWhiteSpace($SshKey) -or -not (Test-Path -LiteralPath $SshKey -PathType Leaf)) {
    throw "请设置 ISAAC_RELAY_SSH_KEY，或通过 -SshKey 指定存在的 SSH 私钥。"
}
if ([string]::IsNullOrWhiteSpace($KnownHosts) -or -not (Test-Path -LiteralPath $KnownHosts -PathType Leaf)) {
    throw "请设置 ISAAC_RELAY_KNOWN_HOSTS，或通过 -KnownHosts 指定已核验的 known_hosts 文件。"
}

$arguments = @(
    "-m", "tools.cloud_relay_agent",
    "--cloud-url", $CloudUrl,
    "--relay-id", $RelayId,
    "--state-dir", $StateDir,
    "--server", $Server,
    "--port", $Port.ToString(),
    "--user", $User,
    "--ssh-key", (Resolve-Path -LiteralPath $SshKey).Path,
    "--known-hosts", (Resolve-Path -LiteralPath $KnownHosts).Path,
    "--remote-root", $RemoteRoot
)
if ($Once) { $arguments += "--once" }
if ($CheckConfig) { $arguments += "--check-config" }

& $PythonPath @arguments
exit $LASTEXITCODE
