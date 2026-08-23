[CmdletBinding()]
param(
    [string]$PythonPath = "",
    [switch]$SkipInstall,
    [switch]$SkipCodeArts,
    [switch]$NonInteractive,
    [ValidateSet("rule", "llm", "hybrid")]
    [string]$IntentMode = "llm",
    [ValidateSet("off", "optional", "required")]
    [string]$TraceCoderMode = "required",
    [ValidateSet("off", "auto", "required")]
    [string]$CodeArtsMode = "auto"
)

<#
.SYNOPSIS
    Configure the local A/B/D LLM and agent providers in one command.

.DESCRIPTION
    The script never changes tracked files. It creates ignored local files:

      .env               -> A / RIA settings
      tracecoder_llm.env -> D / TraceCoder settings
      codearts.env       -> B / CodeArts settings and local credentials

    It also stores CodeArts credentials in the current Windows user's
    environment so the official CLI works outside Python. Re-running the
    script keeps existing secrets when the secret prompt is left blank.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\tools\setup.ps1

.EXAMPLE
    .\tools\setup.ps1 -IntentMode llm -TraceCoderMode required -CodeArtsMode required

.EXAMPLE
    .\tools\setup.ps1 -NonInteractive -SkipInstall
#>

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

function Get-LocalEnvValue {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Name
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }
    $escaped = [regex]::Escape($Name)
    $line = Get-Content -LiteralPath $Path -ErrorAction Stop |
        Where-Object { $_ -match "^\s*$escaped\s*=" } |
        Select-Object -First 1
    if ($null -eq $line) {
        return ""
    }
    $value = ($line -split "=", 2)[1].Trim()
    if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    return $value
}

function Read-ConfigValue {
    param(
        [Parameter(Mandatory)][string]$Prompt,
        [string]$Existing = "",
        [string]$Default = ""
    )
    if ($NonInteractive) {
        if ($Existing) { return $Existing }
        return $Default
    }
    $hint = if ($Existing) { "当前值已存在，直接回车保留" } elseif ($Default) { "默认: $Default" } else { "可留空" }
    $answer = Read-Host "$Prompt（$hint）"
    if ($answer) { return $answer.Trim() }
    if ($Existing) { return $Existing }
    return $Default
}

function Read-ConfigSecret {
    param(
        [Parameter(Mandatory)][string]$Prompt,
        [string]$Existing = ""
    )
    if ($NonInteractive -or $Existing) {
        return $Existing
    }
    $secure = Read-Host "$Prompt（输入不会回显，可留空）" -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

function ConvertTo-EnvValue {
    param([AllowNull()][string]$Value)
    if ($null -eq $Value -or $Value -eq "") {
        return ""
    }
    if ($Value -match '[\s#"]') {
        return '"' + $Value.Replace('"', '\"') + '"'
    }
    return $Value
}

function Write-EnvFile {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][hashtable]$Values,
        [Parameter(Mandatory)][string[]]$Order,
        [Parameter(Mandatory)][string]$Header
    )
    $lines = @($Header.TrimEnd())
    foreach ($name in $Order) {
        $lines += "$name=$(ConvertTo-EnvValue $Values[$name])"
    }
    Set-Content -LiteralPath $Path -Value $lines -Encoding UTF8
}

function Set-UserAndProcessEnvironment {
    param(
        [Parameter(Mandatory)][string]$Name,
        [AllowEmptyString()][string]$Value
    )
    if ($null -eq $Value) { return }
    [Environment]::SetEnvironmentVariable($Name, $Value, "User")
    Set-Item -Path "Env:$Name" -Value $Value
}

function Resolve-Python {
    if ($PythonPath) {
        if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
            throw "Python executable not found: $PythonPath"
        }
        return (Resolve-Path -LiteralPath $PythonPath).Path
    }
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        return $venvPython
    }

    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        Write-Host "[1/6] 使用 py.exe 创建 Python 3.11 虚拟环境..." -ForegroundColor Cyan
        & $launcher.Source -3.11 -m venv (Join-Path $repoRoot ".venv")
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
            throw "无法创建 .venv。请确认已安装 Python 3.11。"
        }
        return $venvPython
    }

    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $python) {
        throw "找不到 Python。请安装 Python 3.11+，或使用 -PythonPath 指定解释器。"
    }
    Write-Host "[1/6] 使用 python.exe 创建虚拟环境..." -ForegroundColor Cyan
    & $python.Source -m venv (Join-Path $repoRoot ".venv")
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "无法创建 .venv。"
    }
    return $venvPython
}

Write-Host "CodeArts Embodied AI 本地一键配置" -ForegroundColor Green
Write-Host "仓库: $repoRoot"

$python = Resolve-Python
if (-not $SkipInstall) {
    Write-Host "[2/6] 安装 Python 依赖..." -ForegroundColor Cyan
    & $python -m pip install -r (Join-Path $repoRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "依赖安装失败。网络受限时可先执行本脚本 -SkipInstall，再手动安装依赖。"
    }
} else {
    Write-Host "[2/6] 跳过依赖安装（-SkipInstall）" -ForegroundColor Yellow
}

$riaPath = Join-Path $repoRoot ".env"
$tracePath = Join-Path $repoRoot "tracecoder_llm.env"
$codeartsPath = Join-Path $repoRoot "codearts.env"

$oldRiaKey = Get-LocalEnvValue $riaPath "RIA_DEEPSEEK_API_KEY"
$oldTraceKey = Get-LocalEnvValue $tracePath "TRACECODER_LLM_API_KEY"
$sharedKey = if ($oldRiaKey) { $oldRiaKey } else { $oldTraceKey }
$deepseekKey = Read-ConfigSecret "DeepSeek API Key" $sharedKey

$riaModel = Read-ConfigValue "A 模型名" (Get-LocalEnvValue $riaPath "RIA_DEEPSEEK_MODEL") "deepseek-v4-flash"
$riaBase = Read-ConfigValue "A Base URL" (Get-LocalEnvValue $riaPath "RIA_DEEPSEEK_BASE_URL") "https://api.deepseek.com"
$riaEngine = if ($PSBoundParameters.ContainsKey("IntentMode")) { $IntentMode } else { Read-ConfigValue "A 运行模式（rule/llm/hybrid）" (Get-LocalEnvValue $riaPath "RIA_PLANNER_ENGINE") $IntentMode }

$traceModel = Read-ConfigValue "D 模型名" (Get-LocalEnvValue $tracePath "TRACECODER_LLM_MODEL") $riaModel
$traceBase = Read-ConfigValue "D Base URL" (Get-LocalEnvValue $tracePath "TRACECODER_LLM_BASE_URL") $riaBase
$traceMode = if ($PSBoundParameters.ContainsKey("TraceCoderMode")) { $TraceCoderMode } else { Read-ConfigValue "D 运行模式（off/optional/required）" (Get-LocalEnvValue $tracePath "TRACECODER_LLM_MODE") $TraceCoderMode }
$traceKey = if ($oldTraceKey) { $oldTraceKey } else { $deepseekKey }

$riaValues = @{
    RIA_DEEPSEEK_API_KEY = $deepseekKey
    RIA_DEEPSEEK_BASE_URL = $riaBase
    RIA_DEEPSEEK_MODEL = $riaModel
    RIA_DEEPSEEK_TEMPERATURE = (Get-LocalEnvValue $riaPath "RIA_DEEPSEEK_TEMPERATURE")
    RIA_DEEPSEEK_MAX_TOKENS = (Get-LocalEnvValue $riaPath "RIA_DEEPSEEK_MAX_TOKENS")
    RIA_DEEPSEEK_TIMEOUT_S = (Get-LocalEnvValue $riaPath "RIA_DEEPSEEK_TIMEOUT_S")
    RIA_DEEPSEEK_MAX_RETRIES = (Get-LocalEnvValue $riaPath "RIA_DEEPSEEK_MAX_RETRIES")
    RIA_DEEPSEEK_THINKING = (Get-LocalEnvValue $riaPath "RIA_DEEPSEEK_THINKING")
    RIA_DEEPSEEK_REASONING_EFFORT = (Get-LocalEnvValue $riaPath "RIA_DEEPSEEK_REASONING_EFFORT")
    RIA_LLM_CACHE_ENABLED = (Get-LocalEnvValue $riaPath "RIA_LLM_CACHE_ENABLED")
    RIA_LLM_CACHE_MAX_ENTRIES = (Get-LocalEnvValue $riaPath "RIA_LLM_CACHE_MAX_ENTRIES")
    RIA_LLM_FAILURE_POLICY = (Get-LocalEnvValue $riaPath "RIA_LLM_FAILURE_POLICY")
    RIA_PLANNER_ENGINE = $riaEngine
    RIA_LLM_FALLBACK_ON_LOW_CONFIDENCE = (Get-LocalEnvValue $riaPath "RIA_LLM_FALLBACK_ON_LOW_CONFIDENCE")
    RIA_RULE_CONFIDENCE_THRESHOLD = (Get-LocalEnvValue $riaPath "RIA_RULE_CONFIDENCE_THRESHOLD")
    RIA_DEPLOYMENT_DOMAIN = (Get-LocalEnvValue $riaPath "RIA_DEPLOYMENT_DOMAIN")
    RIA_DAILY_MAX_FORCE_N = (Get-LocalEnvValue $riaPath "RIA_DAILY_MAX_FORCE_N")
    RIA_DAILY_MAX_VELOCITY_MS = (Get-LocalEnvValue $riaPath "RIA_DAILY_MAX_VELOCITY_MS")
    RIA_INDUSTRIAL_MAX_FORCE_N = (Get-LocalEnvValue $riaPath "RIA_INDUSTRIAL_MAX_FORCE_N")
    RIA_INDUSTRIAL_MAX_VELOCITY_MS = (Get-LocalEnvValue $riaPath "RIA_INDUSTRIAL_MAX_VELOCITY_MS")
}
if (-not $riaValues.RIA_DEEPSEEK_TEMPERATURE) { $riaValues.RIA_DEEPSEEK_TEMPERATURE = "0.0" }
if (-not $riaValues.RIA_DEEPSEEK_MAX_TOKENS) { $riaValues.RIA_DEEPSEEK_MAX_TOKENS = "2400" }
if (-not $riaValues.RIA_DEEPSEEK_TIMEOUT_S) { $riaValues.RIA_DEEPSEEK_TIMEOUT_S = "15" }
if (-not $riaValues.RIA_DEEPSEEK_MAX_RETRIES) { $riaValues.RIA_DEEPSEEK_MAX_RETRIES = "1" }
if (-not $riaValues.RIA_DEEPSEEK_THINKING) { $riaValues.RIA_DEEPSEEK_THINKING = "disabled" }
if (-not $riaValues.RIA_DEEPSEEK_REASONING_EFFORT) { $riaValues.RIA_DEEPSEEK_REASONING_EFFORT = "low" }
if (-not $riaValues.RIA_LLM_CACHE_ENABLED) { $riaValues.RIA_LLM_CACHE_ENABLED = "true" }
if (-not $riaValues.RIA_LLM_CACHE_MAX_ENTRIES) { $riaValues.RIA_LLM_CACHE_MAX_ENTRIES = "128" }
if (-not $riaValues.RIA_LLM_FAILURE_POLICY) { $riaValues.RIA_LLM_FAILURE_POLICY = if ($riaEngine -eq "llm") { "block" } else { "fallback" } }
if (-not $riaValues.RIA_LLM_FALLBACK_ON_LOW_CONFIDENCE) { $riaValues.RIA_LLM_FALLBACK_ON_LOW_CONFIDENCE = if ($riaEngine -eq "llm") { "false" } else { "true" } }
if (-not $riaValues.RIA_RULE_CONFIDENCE_THRESHOLD) { $riaValues.RIA_RULE_CONFIDENCE_THRESHOLD = "0.6" }
if (-not $riaValues.RIA_DEPLOYMENT_DOMAIN) { $riaValues.RIA_DEPLOYMENT_DOMAIN = "daily" }
if (-not $riaValues.RIA_DAILY_MAX_FORCE_N) { $riaValues.RIA_DAILY_MAX_FORCE_N = "10" }
if (-not $riaValues.RIA_DAILY_MAX_VELOCITY_MS) { $riaValues.RIA_DAILY_MAX_VELOCITY_MS = "0.30" }
if (-not $riaValues.RIA_INDUSTRIAL_MAX_FORCE_N) { $riaValues.RIA_INDUSTRIAL_MAX_FORCE_N = "8" }
if (-not $riaValues.RIA_INDUSTRIAL_MAX_VELOCITY_MS) { $riaValues.RIA_INDUSTRIAL_MAX_VELOCITY_MS = "0.15" }

$traceValues = @{
    TRACECODER_LLM_MODE = $traceMode
    TRACECODER_LLM_MODEL = $traceModel
    TRACECODER_LLM_BASE_URL = $traceBase
    TRACECODER_LLM_API_KEY = $traceKey
    TRACECODER_LLM_TIMEOUT_S = (Get-LocalEnvValue $tracePath "TRACECODER_LLM_TIMEOUT_S")
    TRACECODER_LLM_MAX_RETRIES = (Get-LocalEnvValue $tracePath "TRACECODER_LLM_MAX_RETRIES")
    TRACECODER_LLM_TEMPERATURE = (Get-LocalEnvValue $tracePath "TRACECODER_LLM_TEMPERATURE")
    TRACECODER_LLM_MAX_TOKENS = (Get-LocalEnvValue $tracePath "TRACECODER_LLM_MAX_TOKENS")
    TRACECODER_LLM_JSON_MODE = (Get-LocalEnvValue $tracePath "TRACECODER_LLM_JSON_MODE")
    TRACECODER_LLM_THINKING = (Get-LocalEnvValue $tracePath "TRACECODER_LLM_THINKING")
    TRACECODER_LLM_REASONING_EFFORT = (Get-LocalEnvValue $tracePath "TRACECODER_LLM_REASONING_EFFORT")
    TRACECODER_MAX_REPAIR_ATTEMPTS = (Get-LocalEnvValue $tracePath "TRACECODER_MAX_REPAIR_ATTEMPTS")
    TRACECODER_LLM_HARD_MAX_TOKENS = (Get-LocalEnvValue $tracePath "TRACECODER_LLM_HARD_MAX_TOKENS")
    TRACECODER_LLM_HARD_THINKING = (Get-LocalEnvValue $tracePath "TRACECODER_LLM_HARD_THINKING")
    TRACECODER_LLM_HARD_REASONING_EFFORT = (Get-LocalEnvValue $tracePath "TRACECODER_LLM_HARD_REASONING_EFFORT")
    TRACECODER_LLM_HARD_MAX_RETRIES = (Get-LocalEnvValue $tracePath "TRACECODER_LLM_HARD_MAX_RETRIES")
    TRACECODER_HARD_MAX_REPAIR_ATTEMPTS = (Get-LocalEnvValue $tracePath "TRACECODER_HARD_MAX_REPAIR_ATTEMPTS")
    TRACECODER_LLM_EXPERT_MAX_TOKENS = (Get-LocalEnvValue $tracePath "TRACECODER_LLM_EXPERT_MAX_TOKENS")
    TRACECODER_LLM_EXPERT_MAX_RETRIES = (Get-LocalEnvValue $tracePath "TRACECODER_LLM_EXPERT_MAX_RETRIES")
    TRACECODER_EXPERT_MAX_REPAIR_ATTEMPTS = (Get-LocalEnvValue $tracePath "TRACECODER_EXPERT_MAX_REPAIR_ATTEMPTS")
    TRACECODER_EXPERT_OPTIMIZE_QUALITY = (Get-LocalEnvValue $tracePath "TRACECODER_EXPERT_OPTIMIZE_QUALITY")
}
if (-not $traceValues.TRACECODER_LLM_TIMEOUT_S) { $traceValues.TRACECODER_LLM_TIMEOUT_S = "20" }
if (-not $traceValues.TRACECODER_LLM_MAX_RETRIES) { $traceValues.TRACECODER_LLM_MAX_RETRIES = "1" }
if (-not $traceValues.TRACECODER_LLM_TEMPERATURE) { $traceValues.TRACECODER_LLM_TEMPERATURE = "0.0" }
if (-not $traceValues.TRACECODER_LLM_MAX_TOKENS) { $traceValues.TRACECODER_LLM_MAX_TOKENS = "3072" }
if (-not $traceValues.TRACECODER_LLM_JSON_MODE) { $traceValues.TRACECODER_LLM_JSON_MODE = "true" }
if (-not $traceValues.TRACECODER_LLM_THINKING) { $traceValues.TRACECODER_LLM_THINKING = "disabled" }
if (-not $traceValues.TRACECODER_LLM_REASONING_EFFORT) { $traceValues.TRACECODER_LLM_REASONING_EFFORT = "low" }
if (-not $traceValues.TRACECODER_MAX_REPAIR_ATTEMPTS) { $traceValues.TRACECODER_MAX_REPAIR_ATTEMPTS = "1" }
if (-not $traceValues.TRACECODER_LLM_HARD_MAX_TOKENS) { $traceValues.TRACECODER_LLM_HARD_MAX_TOKENS = "6144" }
if (-not $traceValues.TRACECODER_LLM_HARD_THINKING) { $traceValues.TRACECODER_LLM_HARD_THINKING = "enabled" }
if (-not $traceValues.TRACECODER_LLM_HARD_REASONING_EFFORT) { $traceValues.TRACECODER_LLM_HARD_REASONING_EFFORT = "low" }
if (-not $traceValues.TRACECODER_LLM_HARD_MAX_RETRIES) { $traceValues.TRACECODER_LLM_HARD_MAX_RETRIES = "1" }
if (-not $traceValues.TRACECODER_HARD_MAX_REPAIR_ATTEMPTS) { $traceValues.TRACECODER_HARD_MAX_REPAIR_ATTEMPTS = "1" }
if (-not $traceValues.TRACECODER_LLM_EXPERT_MAX_TOKENS) { $traceValues.TRACECODER_LLM_EXPERT_MAX_TOKENS = "8192" }
if (-not $traceValues.TRACECODER_LLM_EXPERT_MAX_RETRIES) { $traceValues.TRACECODER_LLM_EXPERT_MAX_RETRIES = "1" }
if (-not $traceValues.TRACECODER_EXPERT_MAX_REPAIR_ATTEMPTS) { $traceValues.TRACECODER_EXPERT_MAX_REPAIR_ATTEMPTS = "2" }
if (-not $traceValues.TRACECODER_EXPERT_OPTIMIZE_QUALITY) { $traceValues.TRACECODER_EXPERT_OPTIMIZE_QUALITY = "true" }

Write-Host "[3/6] 生成 A/D 本地配置（密钥不会显示）..." -ForegroundColor Cyan
Write-EnvFile $riaPath $riaValues @(
    "RIA_DEEPSEEK_API_KEY", "RIA_DEEPSEEK_BASE_URL", "RIA_DEEPSEEK_MODEL",
    "RIA_DEEPSEEK_TEMPERATURE", "RIA_DEEPSEEK_MAX_TOKENS", "RIA_DEEPSEEK_TIMEOUT_S",
    "RIA_DEEPSEEK_MAX_RETRIES", "RIA_DEEPSEEK_THINKING", "RIA_DEEPSEEK_REASONING_EFFORT",
    "RIA_LLM_CACHE_ENABLED", "RIA_LLM_CACHE_MAX_ENTRIES", "RIA_LLM_FAILURE_POLICY", "RIA_PLANNER_ENGINE",
    "RIA_LLM_FALLBACK_ON_LOW_CONFIDENCE", "RIA_RULE_CONFIDENCE_THRESHOLD",
    "RIA_DEPLOYMENT_DOMAIN", "RIA_DAILY_MAX_FORCE_N", "RIA_DAILY_MAX_VELOCITY_MS",
    "RIA_INDUSTRIAL_MAX_FORCE_N", "RIA_INDUSTRIAL_MAX_VELOCITY_MS"
) "# Generated by tools/setup.ps1; local only."
Write-EnvFile $tracePath $traceValues @(
    "TRACECODER_LLM_MODE", "TRACECODER_LLM_MODEL", "TRACECODER_LLM_BASE_URL",
    "TRACECODER_LLM_API_KEY", "TRACECODER_LLM_TIMEOUT_S", "TRACECODER_LLM_MAX_RETRIES",
    "TRACECODER_LLM_TEMPERATURE", "TRACECODER_LLM_MAX_TOKENS", "TRACECODER_LLM_JSON_MODE",
    "TRACECODER_LLM_THINKING", "TRACECODER_LLM_REASONING_EFFORT", "TRACECODER_MAX_REPAIR_ATTEMPTS",
    "TRACECODER_LLM_HARD_MAX_TOKENS", "TRACECODER_LLM_HARD_THINKING", "TRACECODER_LLM_HARD_REASONING_EFFORT",
    "TRACECODER_LLM_HARD_MAX_RETRIES", "TRACECODER_HARD_MAX_REPAIR_ATTEMPTS",
    "TRACECODER_LLM_EXPERT_MAX_TOKENS", "TRACECODER_LLM_EXPERT_MAX_RETRIES", "TRACECODER_EXPERT_MAX_REPAIR_ATTEMPTS",
    "TRACECODER_EXPERT_OPTIMIZE_QUALITY"
) "# Generated by tools/setup.ps1; local only."

$oldCodeartsKey = Get-LocalEnvValue $codeartsPath "CODEARTS_CLI_AK"
$oldCodeartsSecret = Get-LocalEnvValue $codeartsPath "CODEARTS_CLI_SK"
if (-not $oldCodeartsKey) { $oldCodeartsKey = [Environment]::GetEnvironmentVariable("CODEARTS_CLI_AK", "User") }
if (-not $oldCodeartsSecret) { $oldCodeartsSecret = [Environment]::GetEnvironmentVariable("CODEARTS_CLI_SK", "User") }

if ($SkipCodeArts) {
    $effectiveCodeartsMode = "off"
    $codeartsKey = $oldCodeartsKey
    $codeartsSecret = $oldCodeartsSecret
    Write-Host "[4/6] 跳过 CodeArts 配置（-SkipCodeArts）" -ForegroundColor Yellow
} else {
    $codeartsKey = Read-ConfigSecret "CodeArts AK（已有 CLI 登录可留空）" $oldCodeartsKey
    $codeartsSecret = Read-ConfigSecret "CodeArts SK（已有 CLI 登录可留空）" $oldCodeartsSecret
    $effectiveCodeartsMode = if ($PSBoundParameters.ContainsKey("CodeArtsMode")) { $CodeArtsMode } else { Read-ConfigValue "B 运行模式（off/auto/required）" (Get-LocalEnvValue $codeartsPath "CODEARTS_STRATEGY_MODE") $CodeArtsMode }
    Write-Host "[4/6] 写入 B CodeArts 本地配置..." -ForegroundColor Cyan
}

$codeartsValues = @{
    CODEARTS_CLI = (Get-LocalEnvValue $codeartsPath "CODEARTS_CLI")
    CODEARTS_STRATEGY_MODE = $effectiveCodeartsMode
    CODEARTS_STRATEGY_AGENT = (Get-LocalEnvValue $codeartsPath "CODEARTS_STRATEGY_AGENT")
    CODEARTS_STRATEGY_MODEL = (Get-LocalEnvValue $codeartsPath "CODEARTS_STRATEGY_MODEL")
    CODEARTS_STRATEGY_TIMEOUT_S = (Get-LocalEnvValue $codeartsPath "CODEARTS_STRATEGY_TIMEOUT_S")
    CODEARTS_STRATEGY_POLICY = (Get-LocalEnvValue $codeartsPath "CODEARTS_STRATEGY_POLICY")
    CODEARTS_CLI_PURE = (Get-LocalEnvValue $codeartsPath "CODEARTS_CLI_PURE")
    NO_PROXY = (Get-LocalEnvValue $codeartsPath "NO_PROXY")
    CODEARTS_CLI_AK = $codeartsKey
    CODEARTS_CLI_SK = $codeartsSecret
}
if (-not $codeartsValues.CODEARTS_CLI) { $codeartsValues.CODEARTS_CLI = "codearts" }
if (-not $codeartsValues.CODEARTS_STRATEGY_TIMEOUT_S) { $codeartsValues.CODEARTS_STRATEGY_TIMEOUT_S = "60" }
if (-not $codeartsValues.CODEARTS_STRATEGY_MAX_RETRIES) { $codeartsValues.CODEARTS_STRATEGY_MAX_RETRIES = "1" }
if (-not $codeartsValues.CODEARTS_STRATEGY_RETRY_BACKOFF_S) { $codeartsValues.CODEARTS_STRATEGY_RETRY_BACKOFF_S = "0.2" }
if (-not $codeartsValues.CODEARTS_STRATEGY_POLICY) { $codeartsValues.CODEARTS_STRATEGY_POLICY = "planner" }
if (-not $codeartsValues.CODEARTS_CLI_PURE) { $codeartsValues.CODEARTS_CLI_PURE = "1" }
$defaultCodeartsNoProxy = "snap-access.cn-north-4.myhuaweicloud.com,.myhuaweicloud.com,localhost,127.0.0.1"
if (-not $codeartsValues.NO_PROXY) { $codeartsValues.NO_PROXY = $defaultCodeartsNoProxy }
if (-not $codeartsValues.no_proxy) { $codeartsValues.no_proxy = $codeartsValues.NO_PROXY }
Write-EnvFile $codeartsPath $codeartsValues @(
    "CODEARTS_CLI", "CODEARTS_STRATEGY_MODE", "CODEARTS_STRATEGY_AGENT",
    "CODEARTS_STRATEGY_MODEL", "CODEARTS_STRATEGY_TIMEOUT_S", "CODEARTS_STRATEGY_MAX_RETRIES",
    "CODEARTS_STRATEGY_RETRY_BACKOFF_S", "CODEARTS_STRATEGY_POLICY",
    "CODEARTS_CLI_PURE", "NO_PROXY", "no_proxy", "CODEARTS_CLI_AK", "CODEARTS_CLI_SK"
) "# Generated by tools/setup.ps1; local only."

if (-not $SkipCodeArts) {
    Set-UserAndProcessEnvironment "CODEARTS_CLI_AK" $codeartsKey
    Set-UserAndProcessEnvironment "CODEARTS_CLI_SK" $codeartsSecret
    Set-UserAndProcessEnvironment "CODEARTS_CLI" $codeartsValues.CODEARTS_CLI
    Set-UserAndProcessEnvironment "CODEARTS_STRATEGY_MODE" $effectiveCodeartsMode
    Set-UserAndProcessEnvironment "CODEARTS_STRATEGY_AGENT" $codeartsValues.CODEARTS_STRATEGY_AGENT
    Set-UserAndProcessEnvironment "CODEARTS_STRATEGY_MODEL" $codeartsValues.CODEARTS_STRATEGY_MODEL
    Set-UserAndProcessEnvironment "CODEARTS_STRATEGY_TIMEOUT_S" $codeartsValues.CODEARTS_STRATEGY_TIMEOUT_S
    Set-UserAndProcessEnvironment "CODEARTS_STRATEGY_POLICY" $codeartsValues.CODEARTS_STRATEGY_POLICY
    Set-UserAndProcessEnvironment "CODEARTS_CLI_PURE" $codeartsValues.CODEARTS_CLI_PURE
    Set-UserAndProcessEnvironment "NO_PROXY" $codeartsValues.NO_PROXY
    Set-UserAndProcessEnvironment "no_proxy" $codeartsValues.no_proxy
}

Write-Host "[5/6] 检查本地配置和模块依赖..." -ForegroundColor Cyan
Push-Location $repoRoot
try {
    & $python tools\doctor_config.py
    $doctorExit = $LASTEXITCODE
}
finally {
    Pop-Location
}

Write-Host "[6/6] 配置文件已生成：.env、tracecoder_llm.env、codearts.env" -ForegroundColor Green
Write-Host "启动离线 Demo：.\demo\start_demo.ps1" -ForegroundColor Green
Write-Host "启动真实 B：.\demo\start_demo.ps1 -CodeArtsMode required -CodeArtsPolicy quality" -ForegroundColor Green
Write-Host "重新检查：.\.venv\Scripts\python.exe tools\doctor_config.py" -ForegroundColor Green

if ($doctorExit -ne 0) {
    Write-Warning "配置已写入，但体检发现错误；请根据上面的 ERROR 修复后重新运行体检。"
    exit $doctorExit
}
