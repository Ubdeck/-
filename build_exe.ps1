$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "未找到虚拟环境 Python：$python"
}

& $python -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple

& $python -m PyInstaller --noconfirm --clean --onefile --name LiepinAutomation run.py

Write-Host "打包完成：$root\dist\LiepinAutomation.exe"
