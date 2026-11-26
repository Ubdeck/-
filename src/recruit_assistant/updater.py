from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote

import psutil

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
        "asset_digest": (asset or {}).get("digest") or "",
        "source_url": source_url,
        "has_windows_asset": bool(asset),
    }


def safe_asset_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\\\|?*]+', "_", str(name or "").strip())
    return cleaned or "RecruitTool-update.exe"


def download_update(asset_url: str, asset_name: str, app_dir: Path, progress_callback=None, *, expected_digest: str = "") -> Path:
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
                digest = hashlib.sha256()
                with temp_target.open("wb") as file:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        file.write(chunk)
                        digest.update(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total, candidate)
            if total and downloaded != total:
                raise RuntimeError("更新文件下载不完整。")
            if expected_digest and expected_digest.lower() != f"sha256:{digest.hexdigest()}":
                raise RuntimeError("下载文件与 Release 公布的校验值不一致。")
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


def append_install_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as file:
        file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


def target_executable_processes(target_exe: Path) -> list[psutil.Process]:
    target = str(target_exe.resolve()).casefold()
    processes: list[psutil.Process] = []
    for process in psutil.process_iter(["pid", "exe"]):
        try:
            if process.pid != os.getpid() and str(process.info.get("exe") or "").casefold() == target:
                processes.append(process)
        except (psutil.Error, OSError):
            continue
    return processes


def install_downloaded_update(source_exe: Path, target_exe: Path, target_pid: int, log_path: Path) -> int:
    """Run inside the downloaded executable and replace the previous executable."""
    source_exe = source_exe.resolve()
    target_exe = target_exe.resolve()
    log_path = log_path.resolve()
    append_install_log(log_path, f"self installer start pid={target_pid} source={source_exe} target={target_exe}")
    if source_exe == target_exe or source_exe.suffix.lower() != ".exe" or target_exe.suffix.lower() != ".exe":
        append_install_log(log_path, "ERROR invalid source or target executable")
        return 2

    deadline = time.time() + 25
    while time.time() < deadline:
        blockers = target_executable_processes(target_exe)
        if not blockers:
            break
        time.sleep(0.5)

    blockers = target_executable_processes(target_exe)
    for process in blockers:
        try:
            append_install_log(log_path, f"stopping target process pid={process.pid}")
            process.terminate()
        except (psutil.Error, OSError) as exc:
            append_install_log(log_path, f"terminate failed pid={process.pid}: {exc}")
    if blockers:
        _gone, alive = psutil.wait_procs(blockers, timeout=3)
        for process in alive:
            try:
                process.kill()
            except (psutil.Error, OSError) as exc:
                append_install_log(log_path, f"kill failed pid={process.pid}: {exc}")

    backup = target_exe.with_suffix(target_exe.suffix + ".bak")
    replacement = target_exe.with_suffix(target_exe.suffix + ".new")
    for attempt in range(1, 41):
        try:
            if target_exe.exists() and attempt == 1:
                shutil.copy2(target_exe, backup)
                append_install_log(log_path, f"backup created: {backup}")
            shutil.copy2(source_exe, replacement)
            with replacement.open("rb") as file:
                if file.read(2) != b"MZ":
                    raise RuntimeError("replacement is not a Windows executable")
            os.replace(replacement, target_exe)
            append_install_log(log_path, f"update installed on attempt {attempt}")
            return 0
        except Exception as exc:
            append_install_log(log_path, f"copy failed attempt {attempt}: {exc}")
            replacement.unlink(missing_ok=True)
            time.sleep(0.5)
    append_install_log(log_path, "ERROR update copy failed after retries")
    return 1


def launch_update_installer_and_exit(downloaded_exe: Path, app_name: str) -> None:
    target_exe = current_executable()
    if target_exe is None:
        raise RuntimeError("当前是源码运行模式，只有打包后的 exe 支持一键安装更新。")
    log_path = downloaded_exe.parent / "install_update.log"
    log_path.write_text(
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} launcher prepared source={downloaded_exe} target={target_exe}\n",
        encoding="utf-8",
    )
    creation_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    subprocess.Popen(
        [
            str(downloaded_exe),
            "--install-update",
            "--target-pid",
            str(os.getpid()),
            "--target-exe",
            str(target_exe),
            "--log-path",
            str(log_path),
        ],
        cwd=str(downloaded_exe.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creation_flags,
    )
    time.sleep(1.0)
    os._exit(0)
