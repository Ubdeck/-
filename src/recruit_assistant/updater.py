from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote

from .version import APP_VERSION, GITHUB_REPO


GITHUB_RELEASE_API = "https://api.github.com/repos/{repo}/releases/latest"
USER_AGENT = f"RecruitTool/{APP_VERSION}"
URL_PROXY_PREFIXES = (
    "",
    "https://gh-proxy.com/",
    "https://gh.llkk.cc/",
    "https://ghfast.top/",
)


def parse_version(value: str) -> tuple[int, ...]:
    text = str(value or "").strip().lstrip("vV")
    parts = re.findall(r"\d+", text)
    return tuple(int(part) for part in parts[:4]) or (0,)


def is_newer_version(latest: str, current: str = APP_VERSION) -> bool:
    latest_parts = parse_version(latest)
    current_parts = parse_version(current)
    size = max(len(latest_parts), len(current_parts))
    return latest_parts + (0,) * (size - len(latest_parts)) > current_parts + (0,) * (size - len(current_parts))


def candidate_urls(url: str) -> list[str]:
    value = str(url or "").strip()
    if not value:
        return []
    return [prefix + value for prefix in URL_PROXY_PREFIXES]


def request_json(url: str, timeout: float = 8.0) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def request_json_with_fallback(url: str, timeout: float = 8.0) -> tuple[dict, str]:
    errors: list[str] = []
    for candidate in candidate_urls(url):
        try:
            return request_json(candidate, timeout=timeout), candidate
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
    raise RuntimeError("；".join(errors[-3:]) or "所有更新源都不可用。")


def select_release_asset(release: dict) -> dict | None:
    assets = [asset for asset in release.get("assets", []) if str(asset.get("name") or "").lower().endswith(".exe")]
    if not assets:
        return None
    preferred = [
        asset
        for asset in assets
        if (
            "招聘工具" in str(asset.get("name") or "")
            or "招聘软件助手" in str(asset.get("name") or "")
            or "RecruitTool" in str(asset.get("name") or "")
            or "RecruitAssistant" in str(asset.get("name") or "")
        )
    ]
    return (preferred or assets)[0]


def check_for_update(repo: str = GITHUB_REPO, current_version: str = APP_VERSION) -> dict:
    try:
        release, source_url = request_json_with_fallback(GITHUB_RELEASE_API.format(repo=quote(repo, safe="/")))
    except HTTPError as exc:
        return {
            "ok": False,
            "current_version": current_version,
            "error": f"GitHub Release 检查失败：HTTP {exc.code}",
        }
    except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "current_version": current_version,
            "error": f"GitHub Release 检查失败：{exc}",
        }
    except RuntimeError as exc:
        return {
            "ok": False,
            "current_version": current_version,
            "error": f"GitHub Release 检查失败：{exc}",
        }

    latest_version = str(release.get("tag_name") or release.get("name") or "").strip()
    asset = select_release_asset(release)
    update_available = bool(latest_version and is_newer_version(latest_version, current_version))
    return {
        "ok": True,
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": update_available,
        "release_url": release.get("html_url") or "",
        "release_name": release.get("name") or latest_version,
        "asset_name": asset.get("name") if asset else "",
        "asset_url": asset.get("browser_download_url") if asset else "",
        "source_url": source_url,
        "has_windows_asset": bool(asset),
    }


def safe_asset_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\\\|?*]+', "_", str(name or "").strip())
    return cleaned or "RecruitTool-update.exe"


def download_update(asset_url: str, asset_name: str, app_dir: Path, progress_callback=None) -> Path:
    if not asset_url:
        raise RuntimeError("最新 Release 没有可下载的 Windows exe。")
    update_dir = app_dir / "runtime" / "updates"
    update_dir.mkdir(parents=True, exist_ok=True)
    target = update_dir / safe_asset_name(asset_name)
    temp_target = target.with_suffix(target.suffix + ".download")
    errors: list[str] = []
    for candidate in candidate_urls(asset_url):
        try:
            request = urllib.request.Request(candidate, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=45) as response:
                total = int(response.headers.get("Content-Length") or "0")
                downloaded = 0
                with temp_target.open("wb") as file:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        file.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total, candidate)
            with temp_target.open("rb") as file:
                signature = file.read(2)
            if signature != b"MZ" or temp_target.stat().st_size < 1024 * 1024:
                raise RuntimeError("下载内容不是有效的 Windows exe。")
            temp_target.replace(target)
            if progress_callback:
                progress_callback(target.stat().st_size, target.stat().st_size, candidate)
            return target
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
            try:
                temp_target.unlink(missing_ok=True)
            except OSError:
                pass
    raise RuntimeError("下载更新失败，所有下载源都不可用：" + "；".join(errors[-3:]))


def current_executable() -> Path | None:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return None


def powershell_executable() -> str:
    system_root = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    candidate = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate if candidate.exists() else "powershell.exe")


def launch_update_installer_and_exit(downloaded_exe: Path, app_name: str) -> None:
    target_exe = current_executable()
    if target_exe is None:
        raise RuntimeError("当前是源码运行模式，只有打包后的 exe 支持一键安装更新。")
    script = downloaded_exe.parent / "install_update.ps1"
    log_path = downloaded_exe.parent / "install_update.log"
    log_path.write_text(
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} launcher prepared source={downloaded_exe} target={target_exe}\n",
        encoding="utf-8",
    )
    script.write_text(
        '''
param(
  [int]$TargetPid,
  [string]$SourceExe,
  [string]$TargetExe,
  [string]$LogPath
)

$ErrorActionPreference = "Continue"

function Write-InstallLog {
  param([string]$Message)
  $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff') $Message"
  Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value $line
}

function Get-TargetProcesses {
  param([string]$TargetPath)
  $targetFullPath = [System.IO.Path]::GetFullPath($TargetPath)
  @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
    try {
      $_.Id -ne $PID -and $_.Path -and ([System.IO.Path]::GetFullPath($_.Path) -ieq $targetFullPath)
    } catch {
      $false
    }
  })
}

Write-InstallLog "installer start pid=$TargetPid source=$SourceExe target=$TargetExe"
Start-Sleep -Milliseconds 800

$deadline = (Get-Date).AddSeconds(20)
do {
  $blocking = Get-TargetProcesses -TargetPath $TargetExe
  if ($blocking.Count -eq 0) {
    break
  }
  Write-InstallLog ("waiting target processes: " + (($blocking | ForEach-Object { $_.Id }) -join ","))
  Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)

$blocking = Get-TargetProcesses -TargetPath $TargetExe
if ($blocking.Count -gt 0) {
  Write-InstallLog ("force stopping target processes: " + (($blocking | ForEach-Object { $_.Id }) -join ","))
  foreach ($proc in $blocking) {
    try {
      Stop-Process -Id $proc.Id -Force -ErrorAction Stop
    } catch {
      Write-InstallLog "force stop failed pid=$($proc.Id): $($_.Exception.Message)"
    }
  }
  Start-Sleep -Milliseconds 800
}

try {
  if (Test-Path -LiteralPath $TargetExe) {
    Copy-Item -LiteralPath $TargetExe -Destination "$TargetExe.bak" -Force -ErrorAction Stop
    Write-InstallLog "backup success"
  }
} catch {
  Write-InstallLog "backup failed: $($_.Exception.Message)"
}

$copied = $false
for ($i = 1; $i -le 40; $i++) {
  try {
    Copy-Item -LiteralPath $SourceExe -Destination $TargetExe -Force -ErrorAction Stop
    Write-InstallLog "copy success attempt $i"
    $copied = $true
    break
  } catch {
    Write-InstallLog "copy failed attempt ${i}: $($_.Exception.Message)"
    Start-Sleep -Milliseconds 500
  }
}

if (-not $copied) {
  Write-InstallLog "ERROR copy failed after retries"
  exit 1
}

try {
  Write-InstallLog "update installed; user should restart app manually"
  exit 0
} catch {
  Write-InstallLog "ERROR finalizing update failed: $($_.Exception.Message)"
  exit 1
}
'''.strip(),
        encoding="utf-8",
    )
    if os.name == "nt":
        # Start PowerShell directly. The former VBS -> shell.Run hop is blocked by
        # Windows Script Host policies on some machines with error 800A0046.
        creation_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            [
                powershell_executable(),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(script),
                str(os.getpid()),
                str(downloaded_exe),
                str(target_exe),
                str(log_path),
            ],
            cwd=str(target_exe.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creation_flags,
        )
    else:
        subprocess.Popen([powershell_executable(), "-File", str(script)], cwd=str(target_exe.parent))
    time.sleep(1.0)
    os._exit(0)
