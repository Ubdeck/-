$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$python = Join-Path $root ".venv\Scripts\python.exe"
$runner = Join-Path $root "run.py"

if (Test-Path -LiteralPath $pythonw) {
    Start-Process -FilePath $pythonw -ArgumentList @($runner) -WorkingDirectory $root -WindowStyle Hidden
} elseif (Test-Path -LiteralPath $python) {
    Start-Process -FilePath $python -ArgumentList @($runner) -WorkingDirectory $root -WindowStyle Hidden
} else {
    throw "Python virtual environment is unavailable: $pythonw"
}
