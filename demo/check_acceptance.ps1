[CmdletBinding()]
param(
    [switch]$Full,
    [string]$PythonPath = ""
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonCommand = $null
$usePyLauncher = $false
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
            $usePyLauncher = $true
        }
    }
    if ($null -eq $pythonCommand) {
        throw "Python was not found. Install Python, add it to PATH, or pass -PythonPath."
    }
    $pythonExecutable = $pythonCommand.Source
}

Push-Location $repoRoot
try {
    if ($Full) {
        if ($usePyLauncher) {
            & $pythonExecutable -3 -m unittest discover -s tests -t . -q
        } else {
            & $pythonExecutable -m unittest discover -s tests -t . -q
        }
    } else {
        if ($usePyLauncher) {
            & $pythonExecutable -3 -m unittest tests.e2e.test_closed_loop_acceptance tests.e2e.test_demo_scenarios tests.e2e.test_demo_quality tests.e2e.test_demo_http -v
        } else {
            & $pythonExecutable -m unittest tests.e2e.test_closed_loop_acceptance tests.e2e.test_demo_scenarios tests.e2e.test_demo_quality tests.e2e.test_demo_http -v
        }
    }
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
