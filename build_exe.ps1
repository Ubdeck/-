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

& $python -c "import DrissionPage, psutil, webview, PyInstaller"
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -r requirements.txt pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install build dependencies."
    }
}

$appName = -join ([char[]](0x62DB, 0x8058, 0x8F6F, 0x4EF6, 0x52A9, 0x624B))

# Single-file deliverable. Maimai is now a normal Python package, so no source folders need to be copied as data.
& $python -m PyInstaller --noconfirm --clean --windowed --onefile --name RecruitAssistant --paths src --collect-all DrissionPage --collect-all webview --hidden-import DrissionPage --hidden-import psutil --hidden-import webview --hidden-import clr_loader --hidden-import pythonnet run.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

$builtSingleExe = Join-Path $root "dist\RecruitAssistant.exe"
$singleExe = Join-Path $root "dist\$appName.exe"
if (Test-Path $singleExe) {
    Remove-Item -LiteralPath $singleExe -Force
}
Move-Item -LiteralPath $builtSingleExe -Destination $singleExe
if (-not (Test-Path $singleExe)) {
    throw "Single exe build failed: $singleExe"
}

$buildDir = Join-Path $root "build"
$specFile = Join-Path $root "RecruitAssistant.spec"
if (Test-Path $buildDir) {
    Remove-Item -LiteralPath $buildDir -Recurse -Force
}
if (Test-Path $specFile) {
    Remove-Item -LiteralPath $specFile -Force
}

Write-Host "Build complete: $singleExe"
