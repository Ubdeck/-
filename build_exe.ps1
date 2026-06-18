$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
$venvOk = $false
if (Test-Path $python) {
    try {
        $oldPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $python --version *> $null
        $venvOk = ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $oldPreference
    }
}

if (-not $venvOk) {
    $basePython = (Get-Command py -ErrorAction SilentlyContinue)
    if ($basePython) {
        & py -3 -m venv --clear ".venv"
        if ($LASTEXITCODE -ne 0) {
            $basePython = $null
        }
    }
    if (-not $basePython) {
        $basePython = (Get-Command python -ErrorAction SilentlyContinue)
        if ($basePython) {
            & python -m venv --clear ".venv"
        } else {
            $localPython = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Recurse -Filter python.exe -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -notmatch "\\Lib\\venv\\scripts\\" } |
                Sort-Object FullName -Descending |
                Select-Object -First 1
            if (-not $localPython) {
                throw "Python 3 not found. Install Python 3 first, then rerun build_exe.ps1."
            }
            & $localPython.FullName -m venv --clear ".venv"
        }
        if ($LASTEXITCODE -ne 0) {
            throw "Python 3 not found. Install Python 3 first, then rerun build_exe.ps1."
        }
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment."
    }
}

& $python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
& $python -m pip install -r requirements.txt pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple

# Main deliverable: single exe for copying to another computer.
& $python -m PyInstaller --noconfirm --clean --onefile --name LiepinAutomation --collect-all DrissionPage --hidden-import DrissionPage --hidden-import psutil run.py

$singleExe = Join-Path $root "dist\LiepinAutomation.exe"
if (-not (Test-Path $singleExe)) {
    throw "Single exe build failed: $singleExe"
}

# Fallback deliverable: onedir portable package, useful if onefile is blocked by antivirus.
& $python -m PyInstaller --noconfirm --clean --onedir --name LiepinAutomationPortable --collect-all DrissionPage --hidden-import DrissionPage --hidden-import psutil run.py

$launcher = Join-Path $root "dist\LiepinAutomationPortable\run_portable.bat"
@"
@echo off
setlocal
cd /d "%~dp0"
start "" ".\LiepinAutomationPortable.exe"
"@ | Set-Content -Path $launcher -Encoding ASCII

Write-Host "Build complete: $singleExe"
Write-Host "Fallback portable package: $launcher"
