param(
    [string]$Environment = 'nanjing_zncl',
    [string]$PythonExecutable,
    [ValidateRange(1, 65535)][int]$BackendPort = 50111,
    [ValidateRange(1, 65535)][int]$FrontendPort = 50112,
    [string]$ListenAddress = '0.0.0.0',
    [switch]$CheckOnly,
    [switch]$SmokeTest
)

$ErrorActionPreference = 'Stop'
$taskArguments = @((Join-Path $PSScriptRoot 'start.py'), '--host', $ListenAddress,
    '--backend-port', [string]$BackendPort, '--frontend-port', [string]$FrontendPort)
if ($CheckOnly) { $taskArguments += '--check' }
if ($SmokeTest) { $taskArguments += '--smoke-test' }
Push-Location -LiteralPath $PSScriptRoot
try {
    if ($PythonExecutable) {
        & $PythonExecutable @taskArguments
    }
    elseif (Get-Command conda -ErrorAction SilentlyContinue) {
        & conda run --no-capture-output -n $Environment python @taskArguments
    }
    else {
        throw 'Conda was not found. Activate your environment or pass -PythonExecutable with the Python executable path.'
    }
    if ($LASTEXITCODE -ne 0) { throw "Startup failed with exit code $LASTEXITCODE." }
}
finally {
    Pop-Location
}
