[CmdletBinding()]
param(
    [string]$HostAlias = "school",
    [int]$LocalPort = 18554,
    [string]$RemoteHost = "127.0.0.1",
    [int]$RemotePort = 8554,
    [string]$SshKeyPath = ""
)

$ErrorActionPreference = "Stop"
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) { throw "缺少 ssh.exe" }
if ($LocalPort -lt 1 -or $LocalPort -gt 65535) { throw "LocalPort 必须在 1..65535" }
if ($RemotePort -lt 1 -or $RemotePort -gt 65535) { throw "RemotePort 必须在 1..65535" }
if ($SshKeyPath -and -not (Test-Path -LiteralPath $SshKeyPath -PathType Leaf)) {
    throw "SSH 私钥不存在: $SshKeyPath"
}

# The campus sshd currently rejects TCP forwarding with
# "administratively prohibited". Use an SSH stdio relay instead: the local
# TCP client is connected to `ssh ... nc 127.0.0.1 8554`, so all RTSP bytes
# still travel through the encrypted SSH session without -L forwarding.
$listener = [System.Net.Sockets.TcpListener]::new(
    [System.Net.IPAddress]::Parse("127.0.0.1"),
    $LocalPort
)
$listener.Start()

$sshArgs = @(
    "-T", "-q",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=3",
    $HostAlias,
    "nc", $RemoteHost, [string]$RemotePort
)
if ($SshKeyPath) { $sshArgs = @("-i", $SshKeyPath) + $sshArgs }

Write-Host "SSH RTSP stdio 隧道已启动，保持当前窗口运行：" -ForegroundColor Green
Write-Host "rtsp://127.0.0.1:$LocalPort/stream"
Write-Host "校园服务器 SSH 禁止 -L 转发，脚本将按客户端连接建立加密中继。"
Write-Host "按 Ctrl+C 关闭隧道。"

try {
    while ($true) {
        $client = $listener.AcceptTcpClient()
        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $process.StartInfo.FileName = "ssh"
        $process.StartInfo.UseShellExecute = $false
        $process.StartInfo.CreateNoWindow = $true
        $process.StartInfo.RedirectStandardInput = $true
        $process.StartInfo.RedirectStandardOutput = $true
        $process.StartInfo.RedirectStandardError = $false
        foreach ($argument in $sshArgs) {
            [void]$process.StartInfo.ArgumentList.Add($argument)
        }

        try {
            if (-not $process.Start()) { throw "ssh.exe 启动失败" }
            $clientStream = $client.GetStream()
            $sshInput = $process.StandardInput.BaseStream
            $sshOutput = $process.StandardOutput.BaseStream

            $toRemote = $clientStream.CopyToAsync($sshInput)
            $toClient = $sshOutput.CopyToAsync($clientStream)
            [void][System.Threading.Tasks.Task]::WaitAny(
                [System.Threading.Tasks.Task[]]@($toRemote, $toClient)
            )
        }
        finally {
            if ($client) { $client.Close() }
            if ($process) {
                if (-not $process.HasExited) { $process.Kill() }
                $process.Dispose()
            }
        }
    }
}
finally {
    $listener.Stop()
}
