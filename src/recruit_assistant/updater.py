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


def parse_version(value: str) -> tuple[int, ...]:
    text = str(value or "").strip().lstrip("vV")
    parts = re.findall(r"\d+", text)
    return tuple(int(part) for part in parts[:4]) or (0,)


def is_newer_version(latest: str, current: str = APP_VERSION) -> bool:
    latest_parts = parse_version(latest)
    current_parts = parse_version(current)
    size = max(len(latest_parts), len(current_parts))
    return latest_parts + (0,) * (size - len(latest_parts)) > current_parts + (0,) * (size - len(current_parts))


def request_json(url: str, timeout: float = 8.0) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


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
        release = request_json(GITHUB_RELEASE_API.format(repo=quote(repo, safe="/")))
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
        "has_windows_asset": bool(asset),
    }


def safe_asset_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\\\|?*]+', "_", str(name or "").strip())
    return cleaned or "RecruitTool-update.exe"


def download_update(asset_url: str, asset_name: str, app_dir: Path) -> Path:
    if not asset_url:
        raise RuntimeError("最新 Release 没有可下载的 Windows exe。")
    update_dir = app_dir / "runtime" / "updates"
    update_dir.mkdir(parents=True, exist_ok=True)
    target = update_dir / safe_asset_name(asset_name)
    temp_target = target.with_suffix(target.suffix + ".download")
    request = urllib.request.Request(asset_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        with temp_target.open("wb") as file:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                file.write(chunk)
    temp_target.replace(target)
    return target


def current_executable() -> Path | None:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return None


def launch_update_installer_and_exit(downloaded_exe: Path, app_name: str) -> None:
    target_exe = current_executable()
    if target_exe is None:
        raise RuntimeError("当前是源码运行模式，只有打包后的 exe 支持一键安装更新。")
    script = downloaded_exe.parent / "install_update.ps1"
    script.write_text(
        """
param(
  [int]$TargetProcessId,
  [string]$SourceExe,
  [string]$TargetExe
)
$ErrorActionPreference = "Stop"
Wait-Process -Id $TargetProcessId -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 800
$backup = "$TargetExe.bak"
if (Test-Path -LiteralPath $TargetExe) {
  Copy-Item -LiteralPath $TargetExe -Destination $backup -Force
}
Copy-Item -LiteralPath $SourceExe -Destination $TargetExe -Force
Start-Process -FilePath $TargetExe -WorkingDirectory (Split-Path -Parent $TargetExe)
""".strip(),
        encoding="utf-8-sig",
    )
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-TargetProcessId",
            str(os.getpid()),
            "-SourceExe",
            str(downloaded_exe),
            "-TargetExe",
            str(target_exe),
        ],
        cwd=str(target_exe.parent),
        creationflags=creationflags,
    )
    time.sleep(0.2)
    os._exit(0)
