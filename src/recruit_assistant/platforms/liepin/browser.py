from __future__ import annotations

import json
import re
import time
from datetime import datetime
from urllib import request

from DrissionPage import ChromiumOptions, ChromiumPage
from DrissionPage.errors import ContextLostError

from .constants import COMPANY_PLACEHOLDER, DEFAULT_BROWSER_PORT, JOB_PLACEHOLDER, KEYWORDS_PLACEHOLDER, RUNTIME_DIR, SEARCH_URL


def open_ordinary_search(page, *, check_stopped=None, allow_login=False) -> None:
    page.get(SEARCH_URL)
    deadline = time.monotonic() + 20
    ready_since = None
    next_click = 0.0
    status = ""
    while time.monotonic() < deadline:
        if check_stopped:
            check_stopped()
        now = time.monotonic()
        try:
            status = page.run_js(
                """
                const [mayClick, ...placeholders] = arguments;
                const visible = e => e.getBoundingClientRect().width > 0
                  && e.getBoundingClientRect().height > 0 && getComputedStyle(e).visibility === 'visible';
                if (/login|passport/i.test(location.hostname + location.pathname)) return 'login';
                if (location.origin !== 'https://lpt.liepin.com') return 'loading';
                const path = location.pathname.replace(/\\/$/, '');
                const tab = [...document.querySelectorAll('[role=tab]')]
                  .find(e => visible(e) && e.innerText.trim() === '普通搜索');
                if (path === '/ai-search' && tab && mayClick) {
                  tab.click();
                  return 'switching';
                }
                if (path !== '/search' || (tab && tab.getAttribute('aria-selected') !== 'true')) {
                  return 'waiting for ordinary search';
                }
                const inputs = [...document.querySelectorAll('input[placeholder]')]
                  .filter(e => visible(e) && !e.disabled && !e.readOnly);
                return placeholders.every(p => inputs.some(e => e.placeholder === p)) ? 'ready' : 'loading';
                """,
                now >= next_click, KEYWORDS_PLACEHOLDER, JOB_PLACEHOLDER, COMPANY_PLACEHOLDER,
            )
        except ContextLostError:
            status = "loading"
        if status == "login":
            if allow_login:
                return
            raise RuntimeError("请先登录猎聘，再运行任务。")
        if status == "switching":
            next_click = now + 1.5
        if status == "ready":
            if ready_since is None:
                ready_since = now
            elif now - ready_since >= 1:
                return
        else:
            ready_since = None
        time.sleep(0.2)
    raise RuntimeError(f"未能进入猎聘普通搜索或筛选表单尚未就绪（{status}）。")


def append_runtime_log(message: str) -> None:
    try:
        log_dir = RUNTIME_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "launcher.log"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with log_file.open("a", encoding="utf-8") as file:
            file.write(f"[{timestamp}] {message}\n")
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
    candidates = ("127.0.0.1", "localhost")
    while time.time() < end_at:
        errors = []
        for host in candidates:
            try:
                fetch_json(f"http://{host}:{port}/json/version", timeout=1.2)
                address = f"{host}:{port}"
                append_runtime_log(f"wait_debug_port_ready ok address={address}")
                return address
            except Exception as exc:
                errors.append(f"{host}: {exc}")
        last_error = "; ".join(errors)
        time.sleep(0.4)
    append_runtime_log(f"wait_debug_port_ready fail port={port} error={last_error}")
    raise RuntimeError(
        f"浏览器连接失败，请检查 {port} 端口是否为浏览器，且已添加 "
        f"\"--remote-debugging-port={port}\" 启动项。"
        f"\n已尝试地址: 127.0.0.1:{port}, localhost:{port}, [::1]:{port}"
        f"\n最后错误: {last_error or 'unknown'}"
    )

def wait_page_target_ready(address: str, timeout: float = 25.0) -> None:
    append_runtime_log(f"wait_page_target_ready start address={address} timeout={timeout}")
    end_at = time.time() + timeout
    last_error = ""
    while time.time() < end_at:
        try:
            targets = fetch_json(f"http://{address}/json/list", timeout=1.2)
            if any(item.get("type") == "page" for item in targets):
                append_runtime_log(f"wait_page_target_ready ok address={address} targets={len(targets)}")
                return
            last_error = "CDP 已就绪，但还没有可接管的 page target"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.4)
    append_runtime_log(f"wait_page_target_ready fail address={address} error={last_error}")
    raise RuntimeError(
        f"已连接到 {address} 调试端口，但没有可接管的页面标签。"
        f"\n最后状态: {last_error or 'unknown'}"
    )

def browser_websocket_url(address: str) -> str | None:
    try:
        version = fetch_json(f"http://{address}/json/version", timeout=1.2)
        return version.get("webSocketDebuggerUrl")
    except Exception as exc:
        append_runtime_log(f"browser_websocket_url fail address={address} error={exc}")
        return None

def connect_chromium_page(
    search_url: str | None = None,
    port: int = DEFAULT_BROWSER_PORT,
    connect_timeout: float = 25.0,
    retries: int = 5,
) -> ChromiumPage:
    append_runtime_log(f"connect_chromium_page start port={port} search_url={search_url or ''} retries={retries}")
    last_error = None
    for index in range(retries):
        try:
            address = wait_debug_port_ready(port=port, timeout=connect_timeout)
            wait_page_target_ready(address=address, timeout=connect_timeout)
            ws_url = browser_websocket_url(address)
            options = ChromiumOptions().set_address(address)
            append_runtime_log(f"connect_chromium_page using address={address} ws={bool(ws_url)}")
            append_runtime_log(f"connect_chromium_page attempt={index + 1}")
            page = ChromiumPage(options)
            current_url = ""
            try:
                current_url = page.url or ""
            except Exception:
                current_url = ""
            if search_url and (not current_url or not same_site_url(current_url, search_url)):
                page.get(search_url)
                page.wait.load_start()
            append_runtime_log(f"connect_chromium_page ok attempt={index + 1} url={current_url}")
            return page
        except Exception as exc:
            last_error = exc
            append_runtime_log(f"connect_chromium_page fail attempt={index + 1} error={exc}")
            time.sleep(0.8)
    append_runtime_log(f"connect_chromium_page final_fail error={last_error}")
    raise RuntimeError(f"接管浏览器失败，已重试 {retries} 次，最后错误：{last_error}")

def same_site_url(current_url: str, target_url: str) -> bool:
    current = str(current_url or "").lower()
    target = str(target_url or "").lower()
    try:
        current_host = re.sub(r"^www\.", "", current.split("//", 1)[-1].split("/", 1)[0])
        target_host = re.sub(r"^www\.", "", target.split("//", 1)[-1].split("/", 1)[0])
        return bool(current_host and target_host and current_host == target_host)
    except Exception:
        return current.startswith(target)
