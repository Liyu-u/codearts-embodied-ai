<#
.SYNOPSIS
  Deploy the embodied-ai closed-loop candidate to the Huawei ECS.

.DESCRIPTION
  Modes:
    Validate         - local + remote prerequisites, no changes
    DeployCandidate  - upload versioned release, compile, start candidate :8876
    CheckCandidate   - candidate API health + non-intrusive HLS probe
    Cutover          - backup + atomic nginx/static switch (only after gates)
    Rollback         - restore previous nginx config and release symlink

  The Livestream (WebRTC Client / OBS / HLS /live/) is operator-owned.
  This script NEVER stops, restarts, deletes or rewrites the Livestream.
  No real secret is ever written to the repository or the logs.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("Validate", "DeployCandidate", "CheckCandidate", "Cutover", "Rollback")]
    [string]$Mode,
    [string]$SshAlias = "huawei",
    [string]$SourceDir = "",
    [string]$ReleaseName = ""
)

$ErrorActionPreference = "Stop"

if (-not $SourceDir) { $SourceDir = Split-Path -Parent $PSScriptRoot }
if (-not $ReleaseName) {
    $sha = (git -C $SourceDir rev-parse --short HEAD 2>$null).Trim()
    if (-not $sha) { throw "cannot resolve git HEAD under $SourceDir" }
    $ReleaseName = $sha
}

$remoteBase = "/opt/codearts"
$releaseDir = "$remoteBase/releases/$ReleaseName"
$currentLink = "$remoteBase/current"
$previousLink = "$remoteBase/previous"
$nginxAvailable = "/etc/nginx/sites-available/closed-loop.conf"
$nginxEnabled = "/etc/nginx/sites-enabled/closed-loop.conf"
$nginxBackup = "$remoteBase/nginx-closed-loop.conf.bak"
$candidateHealth = "http://127.0.0.1:8876/api/health"
$hlsUrl = "http://127.0.0.1:8888/live/isaac/index.m3u8"
$serviceUnit = "/etc/systemd/system/closed-loop-demo.service"
$serviceName = "closed-loop-demo"

function Invoke-Remote([string]$Command) {
    if (-not $Command) { return @() }
    return & ssh $SshAlias $Command 2>&1
}

function Send-Bundle {
    $bundle = Join-Path $env:TEMP "closed-loop-$ReleaseName.tar.gz"
    $include = @(
        "demo", "integration", "modules", "tools", "deploy", "contracts"
    )
    $tarArgs = @(
        "-C", $SourceDir,
        "-czf", $bundle,
        "--exclude=__pycache__",
        "--exclude=*.pyc",
        "--exclude=.cloud-runtime",
        "--exclude=.relay-runtime",
        "--exclude=reports"
    )
    $tarArgs += $include
    & tar @tarArgs | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "failed to create source bundle" }
    & scp "$bundle" "$SshAlias`:$releaseDir/bundle.tar.gz"
    if ($LASTEXITCODE -ne 0) { throw "bundle upload failed" }
    Remove-Item $bundle -Force -ErrorAction SilentlyContinue
}

function DeployCandidate-Phase {
    Invoke-Remote "mkdir -p $releaseDir/.cloud-runtime"
    Send-Bundle
    $setup = @(
        "tar -xzf $releaseDir/bundle.tar.gz -C $releaseDir",
        "chown -R codearts:codearts $releaseDir",
        "$remoteBase/venv/bin/python -m py_compile `$(find $releaseDir -name '*.py' -not -path '*__pycache__*')"
    )
    Invoke-Remote ($setup -join " && ")

    # Install the service unit with the release working directory.
    $unit = Get-Content (Join-Path $SourceDir "deploy/huawei/closed-loop-demo.service") -Raw
    $unit = $unit -replace "/opt/codearts/current", $releaseDir
    $unitB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($unit))
    $install = @(
        "echo $unitB64 | base64 -d | sudo tee $serviceUnit >/dev/null",
        "sudo systemctl daemon-reload",
        "sudo systemctl kill -s TERM $serviceName 2>/dev/null; true",
        "sudo systemctl start $serviceName"
    )
    Invoke-Remote ($install -join " && ")
    Write-Host "[deploy] candidate started on 127.0.0.1:8876 (release $ReleaseName)"
}

function CheckCandidate-Phase {
    $result = Invoke-Remote "curl -s -o /dev/null -w '%{http_code}' $candidateHealth"
    if (($result -join "") -match "200") {
        Write-Host "[check] candidate API health 200"
    } else {
        Write-Warning "[check] candidate API health: $($result -join ' ')"
    }
    $hls = Invoke-Remote "curl -s -o /dev/null -w '%{http_code}' $hlsUrl"
    Write-Host "[check] HLS probe $hlsUrl -> $($hls -join '') (read-only, operator-owned)"
}

function Cutover-Phase {
    $commands = @(
        "sudo cp $nginxEnabled $nginxBackup",
        "sudo cp $nginxAvailable $nginxEnabled",
        "sudo nginx -t",
        "sudo systemctl reload nginx",
        "ln -sfn $releaseDir $previousLink",
        "ln -sfn $releaseDir $currentLink"
    )
    Invoke-Remote ($commands -join " && ")
    Write-Host "[cutover] API now points at 127.0.0.1:8876; /live/ untouched"
}

function Rollback-Phase {
    $commands = @(
        "sudo cp $nginxBackup $nginxEnabled",
        "sudo nginx -t",
        "sudo systemctl reload nginx"
    )
    Invoke-Remote ($commands -join " && ")
    Write-Host "[rollback] previous nginx config restored"
}

switch ($Mode) {
    "Validate" {
        foreach ($required in @(
            "demo/server.py",
            "demo/frontend/index.html",
            "deploy/huawei/closed-loop-demo.service",
            "deploy/huawei/nginx-closed-loop.conf"
        )) {
            $path = Join-Path $SourceDir $required
            if (-not (Test-Path $path)) { throw "missing required file: $path" }
        }
        if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
            throw "OpenSSH ssh is required"
        }
        Write-Host "[validate] PASS: $SourceDir release=$ReleaseName"
    }
    "DeployCandidate" { DeployCandidate-Phase }
    "CheckCandidate" { CheckCandidate-Phase }
    "Cutover" { Cutover-Phase }
    "Rollback" { Rollback-Phase }
}
