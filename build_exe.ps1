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

$appName = -join ([char[]](0x62DB, 0x8058, 0x8F6F, 0x4EF6, 0x52A9, 0x624B))
$portableName = "${appName}Portable"
$maimaiRoot = Join-Path $root "src\recruit_assistant\platforms\maimai"
if (-not (Test-Path $maimaiRoot)) {
    throw "Bundled Maimai platform module not found: $maimaiRoot"
}
$maimaiSrcData = "$(Join-Path $maimaiRoot 'src');src"
$maimaiLegacyData = "$(Join-Path $maimaiRoot 'legacy');legacy"
$maimaiConfigData = "$(Join-Path $maimaiRoot 'config');config"

# Main deliverable: single exe for copying to another computer.
& $python -m PyInstaller --noconfirm --clean --windowed --onefile --name RecruitAssistant --paths src --add-data $maimaiSrcData --add-data $maimaiLegacyData --add-data $maimaiConfigData --collect-all DrissionPage --collect-all webview --hidden-import DrissionPage --hidden-import psutil --hidden-import webview --hidden-import clr_loader --hidden-import pythonnet run.py

$builtSingleExe = Join-Path $root "dist\RecruitAssistant.exe"
$singleExe = Join-Path $root "dist\$appName.exe"
if (Test-Path $singleExe) {
    Remove-Item -LiteralPath $singleExe -Force
}
Move-Item -LiteralPath $builtSingleExe -Destination $singleExe
if (-not (Test-Path $singleExe)) {
    throw "Single exe build failed: $singleExe"
}

# Fallback deliverable: onedir portable package, useful if onefile is blocked by antivirus.
& $python -m PyInstaller --noconfirm --clean --windowed --onedir --name RecruitAssistantPortable --paths src --add-data $maimaiSrcData --add-data $maimaiLegacyData --add-data $maimaiConfigData --collect-all DrissionPage --collect-all webview --hidden-import DrissionPage --hidden-import psutil --hidden-import webview --hidden-import clr_loader --hidden-import pythonnet run.py

$builtPortableDir = Join-Path $root "dist\RecruitAssistantPortable"
$portableDir = Join-Path $root "dist\$portableName"
if (Test-Path $portableDir) {
    Remove-Item -LiteralPath $portableDir -Recurse -Force
}
Move-Item -LiteralPath $builtPortableDir -Destination $portableDir

$launcher = Join-Path $portableDir "run_portable.bat"
@"
@echo off
setlocal
cd /d "%~dp0"
start "" ".\RecruitAssistantPortable.exe"
"@ | Set-Content -Path $launcher -Encoding ASCII

Write-Host "Build complete: $singleExe"
Write-Host "Fallback portable package: $launcher"
