# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
import websocket
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import urllib.error
import urllib.request
from urllib.parse import urlparse

import psutil

if __package__ in (None, ""):
    package_root = Path(__file__).resolve().parents[1]
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))
    from recruit_assistant.environment import get_env_value
    from recruit_assistant.platforms.liepin import DEFAULT_MATCH_REQUIREMENTS, SEARCH_URL as LIEPIN_SEARCH_URL, LiepinSearchPage, SearchFilters
    from recruit_assistant.platforms.maimai import DEFAULT_MAIMAI_GREETING, MAIMAI_TALENTS_URL, MaimaiRecruitPage
    from recruit_assistant.updater import check_for_update, download_update, launch_update_installer_and_exit
    from recruit_assistant.version import APP_VERSION
else:
    from .environment import get_env_value
    from .platforms.liepin import DEFAULT_MATCH_REQUIREMENTS, SEARCH_URL as LIEPIN_SEARCH_URL, LiepinSearchPage, SearchFilters
    from .platforms.maimai import DEFAULT_MAIMAI_GREETING, MAIMAI_TALENTS_URL, MaimaiRecruitPage
    from .updater import check_for_update, download_update, launch_update_installer_and_exit
    from .version import APP_VERSION

try:
    import winreg
except ImportError:
    winreg = None


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def find_free_local_port(start: int = 8765) -> int:
    for port in range(start, start + 120):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free local port found for the desktop app.")


APP_DIR = get_app_dir()
RUNTIME_DIR = APP_DIR / "runtime"
CONFIG_PATH = RUNTIME_DIR / "recruit_assistant_config.json"
LEGACY_CONFIG_PATH = RUNTIME_DIR / "liepin_web_config.json"
DEFAULT_REQUIREMENTS = DEFAULT_MATCH_REQUIREMENTS
DEFAULT_BROWSER_PORT = 9225
SEARCH_URL = LIEPIN_SEARCH_URL
APP_NAME = "招聘工具"
STARTUP_LOG_PATH = RUNTIME_DIR / "startup.log"
PLATFORM_DEFS = {
    "liepin": {
        "key": "liepin",
        "name": "猎聘",
        "home_url": LIEPIN_SEARCH_URL,
        "jobs_supported": True,
    },
    "maimai": {
        "key": "maimai",
        "name": "脉脉",
        "home_url": MAIMAI_TALENTS_URL,
        "jobs_supported": False,
    },
}
LOCAL_DEBUG_ENDPOINTS = (
    ("127.0.0.1", "127.0.0.1"),
    ("localhost", "localhost"),
    ("[::1]", "::1"),
)
BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

WEB_ASSETS = {
    "/assets/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def normalize_platform(value: str | None) -> str:
    platform = str(value or "liepin").strip().lower()
    return platform if platform in PLATFORM_DEFS else "liepin"


def platform_name(platform: str | None) -> str:
    return str(PLATFORM_DEFS[normalize_platform(platform)]["name"])


def platform_home_url(platform: str | None) -> str:
    return str(PLATFORM_DEFS[normalize_platform(platform)]["home_url"])


def jobs_path(platform: str | None) -> Path:
    return RUNTIME_DIR / f"{normalize_platform(platform)}_jobs.json"


def web_asset_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "recruit_assistant" / "web"
    return Path(__file__).resolve().parent / "web"


def load_web_asset(name: str) -> str:
    if not name or Path(name).name != name:
        raise ValueError(f"Invalid web asset name: {name!r}")
    path = web_asset_root() / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Web asset is unavailable: {path}") from exc


def connect_platform_page(url: str, port: int):
    list_url = debug_browser_http_url(port, "/json/list")
    if not list_url:
        raise RuntimeError(f"浏览器调试端口 {port} 未就绪。")
    targets = json.loads(request_local(list_url, timeout=5))
    page_target = next((item for item in targets if item.get("type") == "page" and item.get("webSocketDebuggerUrl")), None)
    if not page_target:
        raise RuntimeError("浏览器调试端口已就绪，但没有可切换的页面标签。")
    ws = websocket.create_connection(page_target["webSocketDebuggerUrl"], timeout=5)
    try:
        ws.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}, ensure_ascii=False))
        ws.recv()
    finally:
        ws.close()
    return url


def startup_log(message: str) -> None:
    try:
        log_dir = RUNTIME_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "launcher.log"
        existing = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}\n"
        log_path.write_text(existing + line, encoding="utf-8")
    except OSError:
        pass


def is_local_port_open(port: int) -> bool:
    for _http_host, socket_host in LOCAL_DEBUG_ENDPOINTS:
        try:
            with socket.create_connection((socket_host, port), timeout=0.5):
                return True
        except OSError:
            continue
    return False


def find_edge_executable() -> Path | None:
    registry_candidates: list[Path] = []
    if winreg is not None and os.name == "nt":
        registry_keys = [
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"),
        ]
        for root, key_path in registry_keys:
            try:
                with winreg.OpenKey(root, key_path) as key:
                    value, _ = winreg.QueryValueEx(key, "")
                    registry_candidates.append(Path(value.strip('"')))
            except OSError:
                continue
    path_from_env = shutil.which("msedge") or shutil.which("msedge.exe")
    if path_from_env:
        candidate = Path(path_from_env)
        if candidate.exists():
            return candidate
    candidates = registry_candidates + [
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("PROGRAMW6432", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def browser_launch_env() -> dict:
    env = os.environ.copy()
    for key in list(env.keys()):
        upper_key = key.upper()
        if upper_key.startswith("PYINSTALLER_") or upper_key in {"_MEIPASS2", "PYTHONHOME", "PYTHONPATH"}:
            env.pop(key, None)
    env["NO_PROXY"] = "127.0.0.1,localhost,::1,[::1]"
    env["no_proxy"] = "127.0.0.1,localhost,::1,[::1]"
    return env


def reset_windows_dll_dir() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.kernel32.SetDllDirectoryW(None)
        startup_log("reset_windows_dll_dir applied")
    except Exception as exc:
        startup_log(f"reset_windows_dll_dir failed: {exc}")


def is_writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def browser_runtime_dir() -> Path:
    local_app_data = Path.home() / "AppData" / "Local" / "liepin-auto-runtime"
    home_fallback = Path.home() / ".liepin-auto-runtime"
    temp_fallback = Path(tempfile.gettempdir()) / "liepin-auto-runtime"
    candidates = [local_app_data, home_fallback, temp_fallback, RUNTIME_DIR]
    for candidate in candidates:
        if is_writable_dir(candidate):
            return candidate
    raise RuntimeError("无法创建浏览器运行目录，请检查当前用户目录写入权限。")


def request_local(path: str, timeout: float = 1.0) -> str:
    req = urllib.request.Request(url=path, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as response:
        return response.read().decode("utf-8")


def debug_browser_http_url(port: int, path: str) -> str | None:
    for http_host, _socket_host in LOCAL_DEBUG_ENDPOINTS:
        url = f"http://{http_host}:{port}{path}"
        try:
            request_local(url, timeout=1.0)
            return url
        except Exception:
            continue
    return None


def debug_browser_ready(port: int = DEFAULT_BROWSER_PORT, timeout: float = 1.0) -> bool:
    errors = []
    for http_host, _socket_host in LOCAL_DEBUG_ENDPOINTS:
        url = f"http://{http_host}:{port}/json/version"
        try:
            request_local(url, timeout=timeout)
            startup_log(f"debug browser http ready on {http_host}:{port}")
            return True
        except Exception as exc:
            errors.append(f"{http_host}: {exc}")
    try:
        if is_local_port_open(port):
            startup_log(f"debug browser tcp open but http check failed on {port}: {'; '.join(errors)}")
    except Exception:
        pass
    return False


def debug_browser_ready_stable(port: int = DEFAULT_BROWSER_PORT, checks: int = 2, interval: float = 0.3, timeout: float = 0.8) -> bool:
    for _ in range(checks):
        if not debug_browser_ready(port=port, timeout=timeout):
            return False
        time.sleep(interval)
    return True


def profile_in_cmdline(profile_dir: Path, cmdline: list[str] | tuple[str, ...] | None) -> bool:
    needle = str(profile_dir).lower()
    return bool(needle and needle in " ".join(cmdline or []).lower())


def kill_profile_browser_processes(profile_dir: Path) -> None:
    targets = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name in {"msedge.exe", "chrome.exe"} and profile_in_cmdline(profile_dir, proc.info.get("cmdline")):
                startup_log(f"stopping stale browser pid={proc.pid} profile={profile_dir}")
                proc.terminate()
                targets.append(proc)
        except (psutil.Error, OSError):
            continue
    _, alive = psutil.wait_procs(targets, timeout=3)
    for proc in alive:
        try:
            startup_log(f"killing stale browser pid={proc.pid} profile={profile_dir}")
            proc.kill()
        except (psutil.Error, OSError):
            continue


def browser_debug_diag(port: int) -> str:
    parts = []
    for http_host, socket_host in LOCAL_DEBUG_ENDPOINTS:
        try:
            with socket.create_connection((socket_host, port), timeout=0.5):
                parts.append(f"tcp:{http_host}:{port}=open")
        except OSError as exc:
            parts.append(f"tcp:{http_host}:{port}=closed:{exc}")
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if name in {"msedge.exe", "chrome.exe"} and f"--remote-debugging-port={port}" in cmdline:
                parts.append(f"browser_pid={proc.pid} cmd={cmdline[:500]}")
        except (psutil.Error, OSError):
            continue
    return " | ".join(parts)


def launch_browser_candidate(
    browser_path: str,
    profile_dir: Path,
    port: int,
    extra_args: list[str] | None = None,
    start_url: str = SEARCH_URL,
) -> bool:
    launch_args = [
        browser_path,
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,msEdgeFre",
        "--disable-gpu",
        "--disable-gpu-compositing",
        "--disable-gpu-sandbox",
        "--disable-gpu-watchdog",
        "--disable-software-rasterizer",
        "--disable-gpu-shader-disk-cache",
        "--disable-background-networking",
        "--disable-crash-reporter",
        "--in-process-gpu",
        f"--user-data-dir={profile_dir}",
        "--new-window",
        start_url,
    ]
    if extra_args:
        launch_args[1:1] = extra_args
    startup_log(f"launch candidate args={' '.join(launch_args[1:])}")
    stderr_path = RUNTIME_DIR / "browser-stderr.log"
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_file = stderr_path.open("ab")
    process = subprocess.Popen(
        launch_args,
        env=browser_launch_env(),
        stdout=subprocess.DEVNULL,
        stderr=stderr_file,
    )
    for _ in range(45):
        if debug_browser_ready_stable(port=port, checks=2, interval=0.3, timeout=0.8):
            startup_log(f"browser candidate ready: {browser_path}")
            stderr_file.close()
            return True
        if process.poll() is not None:
            startup_log(f"browser candidate exited early: {browser_path} exit={process.returncode}")
            break
        time.sleep(0.5)
    stderr_file.close()
    stderr_tail = ""
    try:
        stderr_tail = stderr_path.read_text(encoding="utf-8", errors="ignore")[-1200:].replace("\r", " ").replace("\n", " ")
    except OSError:
        pass
    startup_log(f"browser candidate not ready: {browser_path}; diag={browser_debug_diag(port)}; stderr_tail={stderr_tail}")
    try:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
    kill_profile_browser_processes(profile_dir)
    return False


def verify_debug_browser_async(port: int = DEFAULT_BROWSER_PORT) -> None:
    def worker() -> None:
        for _ in range(20):
            if debug_browser_ready_stable(port=port, checks=2, interval=0.3, timeout=0.8):
                startup_log(f"debug browser ready on {port}")
                return
            time.sleep(0.5)
        startup_log(f"debug browser not ready on {port} after async verification")

    threading.Thread(target=worker, daemon=True).start()


def ensure_edge_debugging(port: int = DEFAULT_BROWSER_PORT, default_url: str | None = None) -> None:
    if debug_browser_ready_stable(port=port, checks=2, interval=0.4, timeout=0.8):
        startup_log(f"debug browser already ready on {port}")
        if default_url:
            try:
                connect_platform_page(default_url, port)
            except Exception as exc:
                startup_log(f"default page navigation failed on existing browser: {exc}")
        return
    reset_windows_dll_dir()
    runtime_dir = browser_runtime_dir()
    profile_dir = runtime_dir / f"browser-profile-{port}"
    profile_dir.mkdir(parents=True, exist_ok=True)
    startup_log(f"ensure_debug_browser runtime_dir={runtime_dir} profile_dir={profile_dir}")
    kill_profile_browser_processes(profile_dir)
    launch_variants = [
        [],
        ["--remote-debugging-address=127.0.0.1"],
    ]
    browser_paths: list[str] = []
    found = find_edge_executable()
    if found:
        browser_paths.append(str(found))
    for candidate in BROWSER_CANDIDATES:
        if candidate not in browser_paths:
            browser_paths.append(candidate)
    for browser_path in browser_paths:
        if not Path(browser_path).exists():
            startup_log(f"browser candidate missing: {browser_path}")
            continue
        for extra_args in launch_variants:
            try:
                startup_log(f"trying browser candidate: {browser_path} extra={extra_args}")
                if launch_browser_candidate(browser_path, profile_dir, port, extra_args, start_url=default_url or SEARCH_URL):
                    return
            except Exception as exc:
                startup_log(f"spawn failed for {browser_path} extra={extra_args}: {exc}")
                continue
    startup_log("no browser candidate could be confirmed")
    verify_debug_browser_async(port)


def ensure_debug_browser_for_work(port: int = DEFAULT_BROWSER_PORT, timeout: float = 35) -> bool:
    if debug_browser_ready_stable(port=port, checks=2, interval=0.3, timeout=0.8):
        return True
    startup_log(f"work requested debug browser, launching port={port}")
    ensure_edge_debugging(port)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if debug_browser_ready_stable(port=port, checks=2, interval=0.3, timeout=0.8):
            startup_log(f"debug browser ready for work on {port}")
            return True
        time.sleep(0.5)
    startup_log(f"debug browser not ready for work on {port} after {timeout}s")
    return False


OPTION_GROUPS = {
    "experience": ["", "不限", "在校/应届", "1-3年", "3-5年", "5-10年"],
    "education": ["", "不限", "本科", "硕士", "博士/博士后", "大专", "中专/中技", "高中及以下"],
    "recruitment_type": ["", "不限", "统招本科", "统招硕士", "统招博士", "统招大专"],
    "active_status": ["", "不限", "今天活跃", "3天内活跃", "7天内活跃", "30天内活跃", "最近三个月活跃", "最近半年活跃"],
    "job_status": ["", "不限", "离职，正在找工作", "在职，急寻新工作", "在职，看看新机会", "在职，暂无跳槽打算"],
    "job_hop_frequency": ["", "不限", "近5年不超过3段", "近3年不超过2段", "近2段均不低于2年"],
    "gender_requirement": ["", "不限", "男", "女"],
    "language_requirement": ["", "不限", "英语", "日语", "粤语"],
    "graduation_year": ["", "不限", "2025年毕业", "2026年毕业", "2027年毕业", "2028年毕业", "2029年毕业", "2030年毕业"],
}

INDUSTRY_GROUPS = {
    "AI/互联网/IT": [
        "不限", "游戏", "电子商务", "新零售", "在线社交媒体", "生活服务O2O",
        "在线教育", "互联网医疗", "云计算/大数据", "人工智能", "物联网",
        "区块链", "网络/信息安全", "计算机软件", "计算机硬件",
        "智能硬件/消费电子", "IT服务", "互联网", "自动驾驶", "具身智能与机器人",
    ],
    "电子/通信/半导体": ["电子/半导体/集成电路", "通信设备", "运营商/增值服务", "仪器仪表", "光电子"],
    "房地产/建筑": ["房地产开发经营", "建筑设计", "工程施工", "物业服务", "装修装饰"],
    "金融": ["银行", "保险", "证券/基金/期货", "互联网金融", "投资/融资", "信托/担保/拍卖"],
    "消费品": ["食品/饮料", "服装/纺织/皮革", "家具/家电", "快消品", "奢侈品"],
    "医疗/健康": ["医疗器械", "医药制造", "生物技术", "医疗服务", "互联网医疗", "大健康"],
    "汽车": ["汽车整车", "汽车零部件", "新能源汽车", "汽车后市场", "智能驾驶"],
    "机械/制造": ["机械设备", "工业自动化", "电气机械", "新材料", "化工", "能源/电力"],
    "教育培训/科研": ["高等教育", "职业培训", "K12教育", "科研院所", "在线教育"],
    "专业服务": ["咨询", "法律", "财务/审计/税务", "人力资源服务", "检测/认证"],
    "广告/传媒/文化/体育": ["广告营销", "影视/媒体", "出版", "游戏", "体育", "文化艺术"],
    "生活服务": ["餐饮", "酒店/旅游", "美容/美发", "家政服务", "生活服务O2O"],
    "交通/物流/贸易/零售": ["交通运输", "物流/仓储", "贸易/进出口", "零售/批发", "供应链"],
}

FUNCTION_GROUPS = {
    "IT互联网技术": [
        "Java", "C++", "PHP", "Python", "C", ".NET", "C#", "Golang", "Node.js",
        "Ruby", "WEB前端开发", "HTML5", "Android", "iOS", "U3D", "鸿蒙开发工程师",
        "自动化测试", "功能测试", "性能测试", "软件测试", "测试开发", "数据分析师",
        "数据挖掘工程师", "大数据开发工程师", "推荐算法", "大模型算法", "自然语言处理(NLP)",
        "机器学习", "深度学习", "运维工程师", "网络/信息安全", "架构师", "技术/研发经理",
    ],
    "电子/通信/半导体": ["硬件工程师", "嵌入式", "单片机", "FPGA开发", "IC设计", "通信工程师", "射频工程师", "电子工程师"],
    "销售/客服": ["销售代表", "客户经理", "大客户销售", "渠道销售", "销售经理/主管", "销售总监", "售前支持", "客服专员", "客户成功"],
    "运营": ["用户运营", "产品运营", "内容运营", "活动运营", "商家运营", "数据运营", "新媒体运营", "社区运营", "运营经理/主管"],
    "人力/行政/财务/法务": ["HRBP", "招聘", "培训", "薪酬绩效", "人力资源经理", "行政", "会计", "财务经理", "法务"],
    "高级管理": ["CEO/总裁/总经理", "COO", "CFO", "CTO/CIO", "副总裁/副总经理", "事业部负责人", "合伙人"],
    "市场/公关/广告/会展": ["市场营销", "品牌", "公关", "媒介", "广告销售", "会展策划", "市场经理/主管"],
    "生产/制造/研发": ["生产经理/车间主任", "工艺工程师", "质量管理", "设备工程师", "机械工程师", "研发工程师"],
    "制药/医疗器械/医疗护理": ["医药代表", "医疗器械销售", "临床研究", "注册", "药品研发", "医生", "护士", "医学经理"],
    "汽车": ["汽车销售", "汽车设计", "汽车电子工程师", "自动驾驶", "车身设计", "质量工程师"],
    "房地产/建筑/物业": ["置业顾问", "地产项目管理", "建筑设计师", "土建工程师", "造价工程师", "物业经理"],
    "金融": ["投资经理", "融资经理", "风控", "信贷管理", "证券分析师", "基金经理", "银行客户经理"],
    "产品": ["产品经理", "高级产品经理", "产品总监", "商业产品经理", "数据产品经理", "AI产品经理"],
    "设计": ["UI设计师", "视觉设计师", "交互设计师", "平面设计师", "工业设计", "设计总监"],
    "教育/培训": ["教师", "培训师", "课程顾问", "教研", "校长", "教学管理"],
    "供应链/物流/采购/贸易": ["采购", "供应链经理", "物流经理", "仓储管理", "外贸业务员", "关务"],
    "生活服务/零售": ["店长", "导购", "餐饮管理", "酒店管理", "旅游顾问", "美容顾问"],
    "影视/媒体": ["编导", "摄影", "剪辑", "记者", "主播", "制片人"],
    "咨询/翻译": ["咨询顾问", "战略咨询", "猎头顾问", "翻译", "同声传译"],
    "能源/环保/农业": ["新能源", "电力工程师", "环保工程师", "农业技术", "化工工程师"],
    "项目管理": ["项目经理", "项目主管", "PMO", "实施顾问", "交付经理"],
    "公务员/其他": ["公务员", "其他职位"],
}


def default_filter_config() -> dict:
    return {
        "platform": "liepin",
        "port": DEFAULT_BROWSER_PORT,
        "selected_chat_job": None,
        "maimai_city": "",
        "maimai_education": "",
        "maimai_experience": "",
        "maimai_graduation_year": "",
        "maimai_company": "",
        "maimai_greeting": DEFAULT_MAIMAI_GREETING,
        "maimai_keywords": "",
        "maimai_keyword_mode": "所有",
        "maimai_gender": "",
        "maimai_age_min": "",
        "maimai_age_max": "",
        "keywords": "",
        "job_name": "",
        "company_name": "",
        "current_city": "",
        "expected_city": "",
        "experience": "",
        "education": [],
        "recruitment_type": "",
        "school_types": [],
        "active_status": "",
        "job_status": "",
        "job_hop_frequency": "",
        "age_min": "",
        "age_max": "",
        "gender_requirement": "",
        "language_requirement": "",
        "graduation_year": "",
        "current_industries": [],
        "expected_industries": [],
        "current_functions": [],
        "expected_functions": [],
        "use_keywords_ai_words": False,
        "use_job_ai_words": False,
        "use_company_ai_words": False,
        "deepseek_api_key": get_env_value("DEEPSEEK_API_KEY"),
        "deepseek_model": "deepseek-chat",
        "match_requirements": DEFAULT_REQUIREMENTS,
        "auto_communicate": True,
        "request_resume_after_communicate": True,
        "request_phone_after_communicate": False,
        "candidate_limit": 4,
        "maimai_page_limit": 1,
        "maimai_auto_communicate": False,
    }


def new_task(name: str = "默认任务") -> dict:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "enabled": False,
        "times": [],
        "config": default_filter_config(),
        "created_at": now,
        "updated_at": now,
        "last_run_at": "",
        "last_status": "未运行",
        "last_runs": {},
    }


class AppState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.run_lock = threading.Lock()
        self.platform_open_lock = threading.Lock()
        self.logs: list[dict] = []
        self.results: list[dict] = []
        self.running = False
        self.running_task = ""
        self.stop_requested = False
        self.task_stop_event = threading.Event()
        self.stop_event = threading.Event()
        self.update_status: dict = {"phase": "idle", "message": "", "percent": 0, "downloaded": 0, "total": 0}
        self.data = self.load()

    def load(self) -> dict:
        source_path = CONFIG_PATH if CONFIG_PATH.exists() else LEGACY_CONFIG_PATH
        if source_path.exists():
            try:
                data = json.loads(source_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}
        defaults = default_filter_config()
        data.setdefault("defaults", defaults)
        merged_defaults = defaults | data.get("defaults", {})
        if not merged_defaults.get("match_requirements"):
            merged_defaults["match_requirements"] = DEFAULT_REQUIREMENTS
        if not merged_defaults.get("deepseek_model"):
            merged_defaults["deepseek_model"] = "deepseek-chat"
        data["defaults"] = merged_defaults
        tasks = data.get("tasks") or []
        if not tasks:
            task = new_task()
            task["config"] = merged_defaults.copy()
            tasks = [task]
        for task in tasks:
            task["config"] = normalize_config(task.get("config") or {})
        data["tasks"] = tasks
        data.setdefault("active_task_id", tasks[0]["id"])
        return data

    def save(self) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_log(self, message: str, event: str = "log", data: dict | None = None) -> None:
        item = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "event": event,
            "message": message,
            "data": data or {},
        }
        with self.lock:
            self.logs.append(item)
            self.logs = self.logs[-500:]
            if event == "candidate_result":
                self.results.append(data or {})

    def progress(self, payload: dict) -> None:
        if isinstance(payload, dict):
            self.add_log(payload.get("message", str(payload)), payload.get("event", "log"), payload.get("data", {}))
        else:
            self.add_log(str(payload))

    def set_update_status(self, **values) -> dict:
        with self.lock:
            current = dict(self.update_status)
            current.update(values)
            self.update_status = current
            return dict(self.update_status)

    def start_update_install_async(self) -> dict:
        with self.lock:
            phase = str((self.update_status or {}).get("phase") or "")
            if phase in {"checking", "downloading", "installing"}:
                return dict(self.update_status)
            self.update_status = {"phase": "checking", "message": "正在检查更新", "percent": 0, "downloaded": 0, "total": 0}
        threading.Thread(target=self.install_update_worker, daemon=True).start()
        return dict(self.update_status)

    def install_update_worker(self) -> None:
        try:
            result = check_for_update()
            if not result.get("ok"):
                message = str(result.get("error") or "检查更新失败")
                self.set_update_status(phase="error", message=message)
                self.add_log(message)
                return
            if not result.get("update_available"):
                message = "当前已是最新版本。"
                self.set_update_status(phase="idle", message=message, percent=100)
                self.add_log(message)
                return
            if not result.get("has_windows_asset"):
                message = "最新 Release 没有 Windows exe 文件。"
                self.set_update_status(phase="error", message=message)
                self.add_log(message)
                return
            if not getattr(sys, "frozen", False):
                message = "当前是源码运行模式，只有打包后的 exe 支持一键安装更新。"
                self.set_update_status(phase="error", message=message)
                self.add_log(message)
                return

            latest = str(result.get("latest_version") or "")
            self.set_update_status(phase="downloading", message=f"正在下载 {latest}", percent=0, downloaded=0, total=0)
            self.add_log(f"正在下载新版本：{latest}")

            def on_progress(downloaded: int, total: int, source_url: str) -> None:
                percent = round(downloaded * 100 / total, 1) if total else 0
                message = f"正在下载 {latest}：{percent}%" if total else f"正在下载 {latest}：{downloaded // 1024} KB"
                self.set_update_status(
                    phase="downloading",
                    message=message,
                    percent=percent,
                    downloaded=downloaded,
                    total=total,
                    source_url=source_url,
                )

            downloaded = download_update(
                str(result.get("asset_url") or ""),
                str(result.get("asset_name") or ""),
                APP_DIR,
                progress_callback=on_progress,
            )
            self.set_update_status(phase="installing", message="下载完成，正在退出并安装更新", percent=100)
            self.add_log("更新已下载，软件即将退出并安装新版本。")

            def run_installer() -> None:
                try:
                    launch_update_installer_and_exit(downloaded, APP_NAME)
                except Exception as exc:
                    message = f"启动更新安装器失败：{exc}"
                    self.set_update_status(phase="error", message=message)
                    self.add_log(message)

            threading.Timer(0.8, run_installer).start()
        except Exception as exc:
            message = f"安装更新失败：{exc}"
            self.set_update_status(phase="error", message=message)
            self.add_log(message)

    def active_platform(self) -> str:
        task = self.find_task(str(self.data.get("active_task_id") or ""))
        if task:
            return normalize_platform((task.get("config") or {}).get("platform"))
        return normalize_platform((self.data.get("defaults") or {}).get("platform"))

    def get_jobs(self, platform: str | None = None) -> list[dict]:
        path = jobs_path(platform or self.active_platform())
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "defaults": self.data.get("defaults", {}),
                "tasks": self.data.get("tasks", []),
                "active_task_id": self.data.get("active_task_id", ""),
                "jobs": self.get_jobs(),
                "platforms": list(PLATFORM_DEFS.values()),
                "logs": self.logs[-250:],
                "results": self.results[-200:],
                "running": self.running,
                "running_task": self.running_task,
                "stop_requested": self.stop_requested,
                "options": OPTION_GROUPS,
                "industry_groups": INDUSTRY_GROUPS,
                "function_groups": FUNCTION_GROUPS,
                "app_version": APP_VERSION,
                "update_status": dict(self.update_status),
            }

    def find_task(self, task_id: str) -> dict | None:
        return next((task for task in self.data.get("tasks", []) if task.get("id") == task_id), None)

    def save_task(self, payload: dict) -> dict:
        with self.lock:
            task_id = payload.get("id") or uuid.uuid4().hex[:12]
            task = self.find_task(task_id)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if not task:
                task = new_task(payload.get("name") or "新任务")
                task["id"] = task_id
                self.data["tasks"].append(task)
            task["name"] = payload.get("name") or task.get("name") or "未命名任务"
            task["enabled"] = bool(payload.get("enabled"))
            task["times"] = normalize_times(payload.get("times", []))
            task["config"] = normalize_config(payload.get("config") or {})
            task["updated_at"] = now
            self.data["active_task_id"] = task["id"]
            defaults = self.data.get("defaults", {}).copy()
            for key in (
                "platform",
                "deepseek_api_key",
                "deepseek_model",
                "match_requirements",
                "maimai_page_limit",
                "maimai_auto_communicate",
                "maimai_greeting",
                "auto_communicate",
                "request_resume_after_communicate",
                "request_phone_after_communicate",
                "candidate_limit",
                "port",
            ):
                if key in task["config"]:
                    defaults[key] = task["config"][key]
            self.data["defaults"] = defaults
            self.save()
            return task

    def delete_task(self, task_id: str) -> None:
        with self.lock:
            tasks = [task for task in self.data.get("tasks", []) if task.get("id") != task_id]
            if not tasks:
                tasks = [new_task()]
            self.data["tasks"] = tasks
            if self.data.get("active_task_id") == task_id:
                self.data["active_task_id"] = tasks[0]["id"]
            self.save()

    def set_active(self, task_id: str) -> None:
        with self.lock:
            if self.find_task(task_id):
                self.data["active_task_id"] = task_id
                self.save()

    def run_task_async(self, task_id: str, reason: str = "手动运行") -> None:
        task = self.find_task(task_id)
        if not task:
            self.add_log(f"任务不存在：{task_id}")
            return
        thread = threading.Thread(target=self.run_task, args=(task_id, reason), daemon=True)
        thread.start()

    def refresh_jobs_async(self, port: int) -> None:
        threading.Thread(target=self.refresh_jobs, args=(self.active_platform(), port), daemon=True).start()

    def open_platform_async(self, platform: str, port: int) -> None:
        threading.Thread(target=self.open_platform, args=(platform, port), daemon=True).start()

    def refresh_jobs(self, platform: str, port: int) -> None:
        platform = normalize_platform(platform)
        if not self.run_lock.acquire(blocking=False):
            self.add_log("当前有任务正在运行，暂不刷新职位。")
            return
        try:
            with self.lock:
                self.running = True
                self.stop_requested = False
                self.task_stop_event.clear()
                self.running_task = f"刷新{platform_name(platform)}职位"
            if platform != "liepin":
                self.add_log(f"{platform_name(platform)}职位列表刷新暂未接入，切换招聘渠道时会自动进入对应主界面。")
                return
            self.add_log("正在获取猎聘职位列表...")
            self.add_log(f"正在确认 {port} 浏览器调试端口...")
            if not ensure_debug_browser_for_work(port):
                raise RuntimeError(f"浏览器调试端口 {port} 未就绪，请确认自动化浏览器已打开。")
            page = LiepinSearchPage(port=port, stop_event=self.task_stop_event)
            jobs = page.fetch_job_list()
            self.add_log(f"已获取 {len(jobs)} 个职位。")
        except Exception as exc:
            self.add_log(f"获取职位失败：{exc}")
        finally:
            with self.lock:
                self.running = False
                self.running_task = ""
                self.stop_requested = False
                self.task_stop_event.clear()
            self.run_lock.release()

    def run_task(self, task_id: str, reason: str) -> None:
        if not self.run_lock.acquire(blocking=False):
            self.add_log(f"已有任务运行中，跳过：{reason}")
            return
        task = self.find_task(task_id)
        if not task:
            self.run_lock.release()
            return
        try:
            with self.lock:
                self.running = True
                self.stop_requested = False
                self.task_stop_event.clear()
                self.running_task = task.get("name", "")
                self.results = []
            self.add_log(f"开始运行任务：{task.get('name', '')}（{reason}）")
            config = normalize_config(task.get("config") or {})
            port = int(config.get("port") or DEFAULT_BROWSER_PORT)
            platform = normalize_platform(config.get("platform"))
            self.add_log(f"正在确认 {port} 浏览器调试端口...")
            if not ensure_debug_browser_for_work(port):
                raise RuntimeError(f"浏览器调试端口 {port} 未就绪，请确认自动化浏览器已打开。")
            if platform == "liepin":
                filters, _port = build_filters(config)
                page = LiepinSearchPage(port=port, progress_callback=self.progress, stop_event=self.task_stop_event)
                self.add_log("正在获取猎聘职位列表...")
                jobs = page.fetch_job_list()
                if not filters.selected_chat_job and jobs:
                    filters.selected_chat_job = jobs[0]
                self.add_log(f"已获取 {len(jobs)} 个职位，准备进入猎聘搜索页。")
                page.open()
                self.add_log("已进入猎聘搜索页，开始填入筛选条件并搜索。")
                result = page.apply_filters(filters)
            elif platform == "maimai":
                page = MaimaiRecruitPage(port=port, progress_callback=self.progress, stop_event=self.task_stop_event)
                self.add_log("正在打开脉脉人才主界面。")
                result = page.run(config)
            else:
                raise RuntimeError(f"暂不支持的平台：{platform}")
            if result and "results" in result:
                with self.lock:
                    self.results = result.get("results", [])
                if platform == "maimai":
                    summary = f"脉脉批量完成：处理 {result.get('processed', 0)} 人，匹配 {result.get('matched', 0)} 人。"
                else:
                    summary = f"批量完成：处理 {result.get('processed', 0)} 人，匹配 {result.get('matched', 0)} 人。"
            elif result:
                summary = f"完成：AI结果 {result.get('decision', '')}，{result.get('score', 0)} 分。"
            else:
                summary = "已完成。"
            with self.lock:
                task["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                task["last_status"] = summary
                self.save()
            self.add_log(summary)
        except Exception as exc:
            stopped = self.task_stop_event.is_set()
            with self.lock:
                task["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                task["last_status"] = "已停止" if stopped else f"失败：{exc}"
                self.save()
            self.add_log("任务已停止。" if stopped else f"任务失败：{exc}")
        finally:
            with self.lock:
                self.running = False
                self.running_task = ""
                self.stop_requested = False
                self.task_stop_event.clear()
            self.run_lock.release()

    def scheduler_loop(self) -> None:
        while not self.stop_event.wait(10):
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            run_key = now.strftime("%Y-%m-%d %H:%M")
            due: list[str] = []
            with self.lock:
                for task in self.data.get("tasks", []):
                    if not task.get("enabled"):
                        continue
                    if current_time not in normalize_times(task.get("times", [])):
                        continue
                    last_runs = task.setdefault("last_runs", {})
                    if last_runs.get(current_time) == run_key:
                        continue
                    last_runs[current_time] = run_key
                    due.append(task["id"])
                if due:
                    self.save()
            for task_id in due:
                self.run_task_async(task_id, f"定时 {current_time}")

    def stop_current_task(self) -> bool:
        with self.lock:
            if not self.running:
                self.stop_requested = False
                self.task_stop_event.clear()
                return False
            self.stop_requested = True
            self.task_stop_event.set()
            running_task = self.running_task
        self.add_log(f"正在停止当前任务：{running_task or '未命名任务'}")
        return True

    def open_platform(self, platform: str, port: int) -> None:
        platform = normalize_platform(platform)
        target_url = platform_home_url(platform)
        current_platform_name = platform_name(platform)
        if self.running:
            self.add_log(f"当前有任务正在运行，暂不切换到{current_platform_name}首页。")
            return
        if not self.platform_open_lock.acquire(blocking=False):
            self.add_log(f"正在切换到{current_platform_name}，已忽略重复请求。")
            return
        try:
            self.add_log(f"正在打开{current_platform_name}初始页面：{target_url}")
            if not ensure_debug_browser_for_work(port):
                raise RuntimeError(f"浏览器调试端口 {port} 未就绪。")
            connect_platform_page(target_url, port)
            self.add_log(f"已切换当前页面到{current_platform_name}。")
        except Exception as exc:
            self.add_log(f"切换当前页面到{current_platform_name}失败：{exc}")
        finally:
            self.platform_open_lock.release()


def normalize_times(values: list | str) -> list[str]:
    if isinstance(values, str):
        raw = values.replace("，", ",").replace("；", ",").replace(";", ",").split(",")
    else:
        raw = values
    result: list[str] = []
    for item in raw:
        value = str(item or "").strip()
        if not value:
            continue
        try:
            parsed = datetime.strptime(value, "%H:%M").strftime("%H:%M")
        except ValueError:
            continue
        if parsed not in result:
            result.append(parsed)
    return sorted(result)


def normalize_config(config: dict) -> dict:
    base = default_filter_config()
    base.update(config or {})
    base["platform"] = normalize_platform(base.get("platform"))
    try:
        base["port"] = int(base.get("port") or DEFAULT_BROWSER_PORT)
    except (TypeError, ValueError):
        base["port"] = DEFAULT_BROWSER_PORT
    try:
        base["candidate_limit"] = max(int(base.get("candidate_limit") or 1), 1)
    except (TypeError, ValueError):
        base["candidate_limit"] = 1
    try:
        base["maimai_page_limit"] = max(int(base.get("maimai_page_limit") or 1), 1)
    except (TypeError, ValueError):
        base["maimai_page_limit"] = 1
    if not str(base.get("maimai_greeting") or "").strip():
        base["maimai_greeting"] = DEFAULT_MAIMAI_GREETING
    for key in ("education", "school_types"):
        value = base.get(key) or []
        if isinstance(value, str):
            raw = value.replace("；", ",").replace("、", ",").replace(";", ",").split(",")
        else:
            raw = value
        cleaned: list[str] = []
        for item in raw:
            text = str(item or "").strip()
            if text and text not in cleaned:
                cleaned.append(text)
        base[key] = cleaned
    for key in ("current_industries", "expected_industries", "current_functions", "expected_functions"):
        value = base.get(key) or []
        if isinstance(value, str):
            raw = value.replace("，", ",").replace("、", ",").replace("；", ",").replace(";", ",").split(",")
        else:
            raw = value
        cleaned: list[str] = []
        for item in raw:
            text = str(item or "").strip()
            if text and text not in cleaned:
                cleaned.append(text)
        base[key] = cleaned[:5]
    for key in (
        "use_keywords_ai_words",
        "use_job_ai_words",
        "use_company_ai_words",
        "auto_communicate",
        "maimai_auto_communicate",
        "request_resume_after_communicate",
        "request_phone_after_communicate",
    ):
        base[key] = bool(base.get(key))
    return base


def build_filters(config: dict) -> tuple[SearchFilters, int]:
    cfg = normalize_config(config)
    filters = SearchFilters(
        selected_chat_job=cfg.get("selected_chat_job"),
        match_requirements=str(cfg.get("match_requirements") or "").strip(),
        deepseek_api_key=str(cfg.get("deepseek_api_key") or "").strip(),
        deepseek_model=str(cfg.get("deepseek_model") or "deepseek-chat").strip(),
        auto_communicate=bool(cfg.get("auto_communicate")),
        request_resume_after_communicate=bool(cfg.get("request_resume_after_communicate")),
        request_phone_after_communicate=bool(cfg.get("request_phone_after_communicate")),
        candidate_limit=int(cfg.get("candidate_limit") or 1),
        keywords=str(cfg.get("keywords") or "").strip(),
        job_name=str(cfg.get("job_name") or "").strip(),
        company_name=str(cfg.get("company_name") or "").strip(),
        current_city=str(cfg.get("current_city") or "").strip(),
        expected_city=str(cfg.get("expected_city") or "").strip(),
        experience=str(cfg.get("experience") or "").strip(),
        education=",".join(cfg.get("education") or []),
        recruitment_type=str(cfg.get("recruitment_type") or "").strip(),
        school_types=",".join(cfg.get("school_types") or []),
        active_status=str(cfg.get("active_status") or "").strip(),
        job_status=str(cfg.get("job_status") or "").strip(),
        job_hop_frequency=str(cfg.get("job_hop_frequency") or "").strip(),
        age_min=str(cfg.get("age_min") or "").strip(),
        age_max=str(cfg.get("age_max") or "").strip(),
        gender_requirement=str(cfg.get("gender_requirement") or "").strip(),
        language_requirement=str(cfg.get("language_requirement") or "").strip(),
        graduation_year=str(cfg.get("graduation_year") or "").strip(),
        current_industries=",".join(cfg.get("current_industries") or []),
        expected_industries=",".join(cfg.get("expected_industries") or []),
        current_functions=",".join(cfg.get("current_functions") or []),
        expected_functions=",".join(cfg.get("expected_functions") or []),
        use_keywords_ai_words=bool(cfg.get("use_keywords_ai_words")),
        use_job_ai_words=bool(cfg.get("use_job_ai_words")),
        use_company_ai_words=bool(cfg.get("use_company_ai_words")),
    )
    return filters, int(cfg.get("port") or DEFAULT_BROWSER_PORT)



STATE = AppState()


class Handler(BaseHTTPRequestHandler):
    server_version = "RecruitAssistant/1.0"

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self.send_text(load_web_asset("index.html"), "text/html; charset=utf-8")
        elif path in WEB_ASSETS:
            asset_name, content_type = WEB_ASSETS[path]
            self.send_text(load_web_asset(asset_name), content_type)
        elif path == "/api/state":
            self.send_json(STATE.snapshot())
        elif path == "/api/update/status":
            self.send_json({"ok": True, "status": STATE.update_status})
        else:
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        payload = self.read_json()
        if path == "/api/tasks/save":
            task = STATE.save_task(payload)
            self.send_json({"ok": True, "task": task})
        elif path == "/api/tasks/delete":
            STATE.delete_task(str(payload.get("id") or ""))
            self.send_json({"ok": True})
        elif path == "/api/tasks/active":
            STATE.set_active(str(payload.get("id") or ""))
            self.send_json({"ok": True})
        elif path == "/api/tasks/run":
            STATE.run_task_async(str(payload.get("id") or ""), "手动运行")
            self.send_json({"ok": True})
        elif path == "/api/tasks/stop":
            stopped = STATE.stop_current_task()
            self.send_json({"ok": True, "stopped": stopped})
        elif path == "/api/jobs/refresh":
            port = int(payload.get("port") or DEFAULT_BROWSER_PORT)
            platform = normalize_platform(payload.get("platform") or STATE.active_platform())
            threading.Thread(target=STATE.refresh_jobs, args=(platform, port), daemon=True).start()
            self.send_json({"ok": True})
        elif path == "/api/platform/open":
            port = int(payload.get("port") or DEFAULT_BROWSER_PORT)
            platform = normalize_platform(payload.get("platform") or STATE.active_platform())
            STATE.open_platform_async(platform, port)
            self.send_json({"ok": True})
        elif path == "/api/update/check":
            result = check_for_update()
            if result.get("ok"):
                if result.get("update_available"):
                    STATE.add_log(f"发现新版本：{result.get('latest_version')}，当前版本：{result.get('current_version')}")
                else:
                    STATE.add_log(f"当前已是最新版本：{result.get('current_version')}")
            else:
                STATE.add_log(str(result.get("error") or "检查更新失败"))
            self.send_json(result)
        elif path == "/api/update/install":
            status = STATE.start_update_install_async()
            self.send_json({"ok": True, "status": status})
        elif path == "/api/update/status":
            self.send_json({"ok": True, "status": STATE.update_status})
        else:
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_text(self, text: str, content_type: str) -> None:
        data = text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)



def start_server(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> tuple[ThreadingHTTPServer, str]:
    ensure_edge_debugging(DEFAULT_BROWSER_PORT, default_url=platform_home_url(STATE.active_platform()))
    threading.Thread(target=STATE.scheduler_loop, daemon=True).start()
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    return server, url


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} localhost web console.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    server, url = start_server(args.host, args.port, open_browser=not args.no_browser)
    print(f"{APP_NAME}: {url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STATE.stop_event.set()
        server.server_close()


if __name__ == "__main__":
    main()
