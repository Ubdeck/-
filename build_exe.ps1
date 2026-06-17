$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Missing venv Python: $python"
}

& $python -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple

# Main deliverable: single exe for copying to another computer.
& $python -m PyInstaller --noconfirm --clean --onefile --name LiepinAutomation --collect-all DrissionPage --hidden-import DrissionPage run.py

$singleExe = Join-Path $root "dist\LiepinAutomation.exe"
if (-not (Test-Path $singleExe)) {
    throw "Single exe build failed: $singleExe"
}

# Fallback deliverable: onedir portable package, useful if onefile is blocked by antivirus.
& $python -m PyInstaller --noconfirm --clean --onedir --name LiepinAutomationPortable --collect-all DrissionPage --hidden-import DrissionPage run.py

$launcher = Join-Path $root "dist\LiepinAutomationPortable\run_portable.bat"
@"
@echo off
setlocal
cd /d "%~dp0"
start "" ".\LiepinAutomationPortable.exe"
"@ | Set-Content -Path $launcher -Encoding ASCII

Write-Host "Build complete: $singleExe"
Write-Host "Fallback portable package: $launcher"
