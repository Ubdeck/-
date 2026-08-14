$ErrorActionPreference = "Stop"
$utf8Output = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8Output
$OutputEncoding = $utf8Output

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

& $python -c "import DrissionPage, psutil, webview, PyInstaller"
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -r requirements.txt pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install build dependencies."
    }
}

$appName = -join ([char[]](0x62DB, 0x8058, 0x5DE5, 0x5177))
$webData = "$(Join-Path $root 'src\recruit_assistant\web');recruit_assistant\web"

# Single-file deliverable. The web console is bundled as static package data.
& $python -m PyInstaller --noconfirm --clean --windowed --onefile --name RecruitTool --paths src --add-data $webData --collect-all DrissionPage --collect-all webview --hidden-import DrissionPage --hidden-import psutil --hidden-import webview --hidden-import clr_loader --hidden-import pythonnet run.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

$builtSingleExe = Join-Path $root "dist\RecruitTool.exe"
$singleExe = Join-Path $root "dist\$appName.exe"
if (Test-Path $singleExe) {
    Remove-Item -LiteralPath $singleExe -Force
}
Move-Item -LiteralPath $builtSingleExe -Destination $singleExe
if (-not (Test-Path $singleExe)) {
    throw "Single exe build failed: $singleExe"
}

$buildDir = Join-Path $root "build"
$specFile = Join-Path $root "RecruitTool.spec"
if (Test-Path $buildDir) {
    Remove-Item -LiteralPath $buildDir -Recurse -Force
}
if (Test-Path $specFile) {
    Remove-Item -LiteralPath $specFile -Force
}

Write-Host "Build complete: $singleExe"
