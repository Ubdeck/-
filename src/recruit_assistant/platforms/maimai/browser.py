from __future__ import annotations

import json
import re
import time
from datetime import datetime
from urllib import request

from DrissionPage import ChromiumOptions, ChromiumPage

from .constants import DEFAULT_BROWSER_PORT, MAIMAI_TALENTS_URL, RUNTIME_DIR


def append_runtime_log(message: str) -> None:
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with (RUNTIME_DIR / "launcher.log").open("a", encoding="utf-8") as file:
            file.write(f"[{timestamp}] [maimai] {message}\n")
    except Exception:
        return


def fetch_json(url: str, timeout: float = 1.5):
    opener = request.build_opener(request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_debug_port_ready(port: int = DEFAULT_BROWSER_PORT, timeout: float = 25.0) -> str:
    append_runtime_log(f"wait_debug_port_ready start port={port} timeout={timeout}")
    end_at = time.time() + timeout
    last_error = ""
    for_host = ("127.0.0.1", "localhost")
    while time.time() < end_at:
        errors = []
        for host in for_host:
            try:
                fetch_json(f"http://{host}:{port}/json/version", timeout=1.2)
                address = f"{host}:{port}"
                append_runtime_log(f"wait_debug_port_ready ok address={address}")
                return address
            except Exception as exc:
                errors.append(f"{host}: {exc}")
        last_error = "; ".join(errors)
        time.sleep(0.4)
    raise RuntimeError(f"浏览器连接失败，请检查 {port} 调试端口。\n最后错误: {last_error or 'unknown'}")


def wait_page_target_ready(address: str, timeout: float = 25.0) -> None:
    end_at = time.time() + timeout
    last_error = ""
    while time.time() < end_at:
        try:
            targets = fetch_json(f"http://{address}/json/list", timeout=1.2)
            if any(item.get("type") == "page" for item in targets):
                return
            last_error = "CDP 已就绪，但还没有可接管的 page target"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.4)
    raise RuntimeError(f"已连接到 {address} 调试端口，但没有可接管的页面标签。\n最后状态: {last_error or 'unknown'}")


def same_site_url(current_url: str, target_url: str) -> bool:
    current = str(current_url or "").lower()
    target = str(target_url or "").lower()
    try:
        current_host = re.sub(r"^www\.", "", current.split("//", 1)[-1].split("/", 1)[0])
        target_host = re.sub(r"^www\.", "", target.split("//", 1)[-1].split("/", 1)[0])
        return bool(current_host and target_host and current_host == target_host)
    except Exception:
        return current.startswith(target)


def connect_chromium_page(
    target_url: str | None = None,
    port: int = DEFAULT_BROWSER_PORT,
    connect_timeout: float = 25.0,
    retries: int = 5,
) -> ChromiumPage:
    target_url = target_url or MAIMAI_TALENTS_URL
    last_error = None
    for index in range(retries):
        try:
            address = wait_debug_port_ready(port=port, timeout=connect_timeout)
            wait_page_target_ready(address=address, timeout=connect_timeout)
            page = ChromiumPage(ChromiumOptions().set_address(address))
            current_url = ""
            try:
                current_url = page.url or ""
            except Exception:
                current_url = ""
            if target_url and (not current_url or not same_site_url(current_url, target_url)):
                page.get(target_url)
                page.wait.load_start()
            append_runtime_log(f"connect_chromium_page ok attempt={index + 1} url={current_url}")
            return page
        except Exception as exc:
            last_error = exc
            append_runtime_log(f"connect_chromium_page fail attempt={index + 1} error={exc}")
            time.sleep(0.8)
    raise RuntimeError(f"接管脉脉浏览器失败，已重试 {retries} 次，最后错误：{last_error}")
