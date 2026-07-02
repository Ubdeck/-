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

from .platforms.liepin import DEFAULT_MATCH_REQUIREMENTS, LiepinSearchPage, SearchFilters, connect_chromium_page
from .platforms import maimai_bridge

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
CONFIG_PATH = RUNTIME_DIR / "liepin_web_config.json"
JOBS_PATH = RUNTIME_DIR / "liepin_jobs.json"
DEFAULT_REQUIREMENTS = DEFAULT_MATCH_REQUIREMENTS
DEFAULT_BROWSER_PORT = 9225
DEFAULT_MAIMAI_BROWSER_PORT = DEFAULT_BROWSER_PORT
SEARCH_URL = "https://lpt.liepin.com/search"
APP_NAME = "招聘软件助手"
STARTUP_LOG_PATH = RUNTIME_DIR / "startup.log"
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
    profile_dir = runtime_dir / f"browser-profile-{port}-{os.getpid()}"
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
    "age_requirement": ["", "不限", "20-25岁", "25-30岁", "30-35岁", "35-40岁", "40岁以上"],
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
        "age_requirement": "",
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
        "deepseek_api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
        "deepseek_model": "deepseek-chat",
        "match_requirements": DEFAULT_REQUIREMENTS,
        "auto_communicate": True,
        "request_resume_after_communicate": True,
        "request_phone_after_communicate": False,
        "candidate_limit": 4,
        "maimai_keyword": "",
        "maimai_port": DEFAULT_MAIMAI_BROWSER_PORT,
        "maimai_keyword_mode": "所有",
        "maimai_city": "无",
        "maimai_education": "无",
        "maimai_education_extra": "无",
        "maimai_work_years": "无",
        "maimai_graduation_year": "无",
        "maimai_companies": "",
        "maimai_gender": "无",
        "maimai_page_limit": 1,
        "maimai_candidate_limit": 0,
        "maimai_ai_requirement_text": "",
        "maimai_greeting": "你好，我对你的简历很感兴趣，方便沟通一下吗？",
        "maimai_actual_send": False,
        "maimai_followup_after_send": True,
        "maimai_deepseek_base_url": "https://api.deepseek.com",
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
        self.open_lock = threading.Lock()
        self.open_seq = 0
        self.logs: list[dict] = []
        self.results: list[dict] = []
        self.running = False
        self.running_task = ""
        self.stop_requested = False
        self.task_stop_event = threading.Event()
        self.stop_event = threading.Event()
        self.data = self.load()

    def load(self) -> dict:
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}
        defaults = default_filter_config()
        data.setdefault("defaults", defaults)
        merged_defaults = defaults | data.get("defaults", {})
        if int(merged_defaults.get("port") or DEFAULT_BROWSER_PORT) in {9223, 9224}:
            merged_defaults["port"] = DEFAULT_BROWSER_PORT
        if int(merged_defaults.get("maimai_port") or DEFAULT_MAIMAI_BROWSER_PORT) in {9223, 9224}:
            merged_defaults["maimai_port"] = DEFAULT_MAIMAI_BROWSER_PORT
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

    def get_jobs(self) -> list[dict]:
        if not JOBS_PATH.exists():
            return []
        try:
            return json.loads(JOBS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

    def snapshot(self) -> dict:
        try:
            maimai_options = maimai_bridge.maimai_options()
        except Exception as exc:
            maimai_options = {
                "work_years": [],
                "graduation_year": [],
                "education": [],
                "education_extra": [],
                "gender": [],
                "keyword_mode": [],
                "error": str(exc),
            }
        with self.lock:
            return {
                "defaults": self.data.get("defaults", {}),
                "tasks": self.data.get("tasks", []),
                "active_task_id": self.data.get("active_task_id", ""),
                "jobs": self.get_jobs(),
                "logs": self.logs[-250:],
                "results": self.results[-200:],
                "running": self.running,
                "running_task": self.running_task,
                "stop_requested": self.stop_requested,
                "options": OPTION_GROUPS,
                "maimai_options": maimai_options,
                "industry_groups": INDUSTRY_GROUPS,
                "function_groups": FUNCTION_GROUPS,
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
                "auto_communicate",
                "request_resume_after_communicate",
                "request_phone_after_communicate",
                "candidate_limit",
                "port",
                "maimai_port",
                "maimai_keyword_mode",
                "maimai_deepseek_base_url",
                "maimai_actual_send",
                "maimai_followup_after_send",
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
        threading.Thread(target=self.refresh_jobs, args=(port,), daemon=True).start()

    def open_platform_async(self, platform: str, port: int) -> None:
        threading.Thread(target=self.open_platform, args=(platform, port), daemon=True).start()

    def open_platform(self, platform: str, port: int, seq: int) -> None:
        platform = "maimai" if str(platform or "").lower() == "maimai" else "liepin"
        target_url = maimai_bridge.MAIMAI_URL if platform == "maimai" else SEARCH_URL
        platform_name = "脉脉" if platform == "maimai" else "猎聘"
        if self.running:
            self.add_log(f"当前有任务正在运行，暂不切换到{platform_name}首页。")
            return
        try:
            self.add_log(f"正在切换当前页面到{platform_name}：{target_url}")
            if not ensure_debug_browser_for_work(port):
                raise RuntimeError(f"浏览器调试端口 {port} 未就绪。")
            page = connect_platform_page(target_url, port)
            self.add_log(f"已打开{platform_name}初始页面：{page.url}")
        except Exception as exc:
            self.add_log(f"打开{platform_name}初始页面失败：{exc}")

    def refresh_jobs(self, port: int) -> None:
        if not self.run_lock.acquire(blocking=False):
            self.add_log("当前有任务正在运行，暂不刷新职位。")
            return
        try:
            with self.lock:
                self.running = True
                self.stop_requested = False
                self.task_stop_event.clear()
                self.running_task = "刷新职位"
            self.add_log("正在获取职位列表...")
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
            platform = config.get("platform", "liepin")
            port = int(config.get("maimai_port") if platform == "maimai" else config.get("port") or DEFAULT_BROWSER_PORT)
            self.add_log(f"正在确认 {port} 浏览器调试端口...")
            if not ensure_debug_browser_for_work(port):
                raise RuntimeError(f"浏览器调试端口 {port} 未就绪，请确认自动化浏览器已打开。")
            if platform == "maimai":
                self.add_log("进入脉脉流程：搜索、提取简历、AI匹配、沟通。")
                result = maimai_bridge.run_pipeline_subprocess(config, self.add_log, stop_event=self.task_stop_event)
                matches = result.get("matches", {})
                rows = []
                for index, item in enumerate(matches.get("matched_candidates", []), start=1):
                    rows.append(
                        {
                            "index": item.get("page_list_index") or item.get("list_index") or index,
                            "name": item.get("name", ""),
                            "job_position": item.get("target_role", ""),
                            "location": item.get("location", ""),
                            "match": True,
                            "score": item.get("match_score", 0),
                            "communicate_status": "sent" if config.get("maimai_actual_send") else "test",
                            "reason": item.get("reason", ""),
                        }
                    )
                for index, item in enumerate(matches.get("rejected_candidates", []), start=1):
                    rows.append(
                        {
                            "index": item.get("page_list_index") or item.get("list_index") or index,
                            "name": item.get("name", ""),
                            "job_position": item.get("target_role", ""),
                            "location": item.get("location", ""),
                            "match": False,
                            "score": item.get("match_score", 0),
                            "communicate_status": "",
                            "reason": item.get("reason", ""),
                        }
                    )
                with self.lock:
                    self.results = rows
                summary = f"脉脉完成：处理 {result.get('processed_pages', 0)} 页，AI通过 {result.get('matched', 0)} 人。"
            else:
                filters, _port = build_filters(config)
                page = LiepinSearchPage(port=port, progress_callback=self.progress, stop_event=self.task_stop_event)
                self.add_log("正在获取职位列表...")
                jobs = page.fetch_job_list()
                if not filters.selected_chat_job and jobs:
                    filters.selected_chat_job = jobs[0]
                self.add_log(f"已获取 {len(jobs)} 个职位，准备进入搜索页。")
                page.open()
                self.add_log("已进入搜索页，开始填入筛选条件并搜索。")
                result = page.apply_filters(filters)
                if result and "results" in result:
                    with self.lock:
                        self.results = result.get("results", [])
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

    def open_platform(self, platform: str, port: int, seq: int) -> None:
        platform = "maimai" if str(platform or "").lower() == "maimai" else "liepin"
        target_url = maimai_bridge.MAIMAI_URL if platform == "maimai" else SEARCH_URL
        platform_name = "脉脉" if platform == "maimai" else "猎聘"
        if self.running:
            self.add_log(f"当前有任务正在运行，暂不切换到{platform_name}首页。")
            return
        if not self.open_lock.acquire(blocking=False):
            self.add_log(f"浏览器正在切换平台，已忽略本次{platform_name}打开请求。")
            return
        try:
            with self.lock:
                if seq != self.open_seq:
                    return
            self.add_log(f"正在打开{platform_name}初始页面：{target_url}")
            if not ensure_debug_browser_for_work(port):
                raise RuntimeError(f"浏览器调试端口 {port} 未就绪。")
            with self.lock:
                if seq != self.open_seq:
                    return
            page = connect_platform_page(target_url, port)
            with self.lock:
                if seq != self.open_seq:
                    return
            self.add_log(f"已打开{platform_name}初始页面：{page.url}")
        except Exception as exc:
            with self.lock:
                if seq == self.open_seq:
                    self.add_log(f"打开{platform_name}初始页面失败：{exc}")
        finally:
            self.open_lock.release()

    def open_platform(self, platform: str, port: int) -> None:
        platform = "maimai" if str(platform or "").lower() == "maimai" else "liepin"
        target_url = maimai_bridge.MAIMAI_URL if platform == "maimai" else SEARCH_URL
        platform_name = "脉脉" if platform == "maimai" else "猎聘"
        if self.running:
            self.add_log(f"当前有任务正在运行，暂不切换到{platform_name}首页。")
            return
        try:
            self.add_log(f"正在打开{platform_name}初始页面：{target_url}")
            if not ensure_debug_browser_for_work(port):
                raise RuntimeError(f"浏览器调试端口 {port} 未就绪。")
            connect_platform_page(target_url, port)
            self.add_log(f"已切换当前页面到{platform_name}。")
        except Exception as exc:
            self.add_log(f"切换当前页面到{platform_name}失败：{exc}")


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
    base["platform"] = "maimai" if str(base.get("platform") or "").strip().lower() == "maimai" else "liepin"
    try:
        base["port"] = int(base.get("port") or DEFAULT_BROWSER_PORT)
    except (TypeError, ValueError):
        base["port"] = DEFAULT_BROWSER_PORT
    if base["port"] in {9223, 9224}:
        base["port"] = DEFAULT_BROWSER_PORT
    try:
        base["candidate_limit"] = max(int(base.get("candidate_limit") or 1), 1)
    except (TypeError, ValueError):
        base["candidate_limit"] = 1
    try:
        base["maimai_page_limit"] = max(int(base.get("maimai_page_limit") or 1), 1)
    except (TypeError, ValueError):
        base["maimai_page_limit"] = 1
    try:
        base["maimai_candidate_limit"] = max(int(base.get("maimai_candidate_limit") or 0), 0)
    except (TypeError, ValueError):
        base["maimai_candidate_limit"] = 0
    try:
        base["maimai_port"] = int(base.get("maimai_port") or DEFAULT_MAIMAI_BROWSER_PORT)
    except (TypeError, ValueError):
        base["maimai_port"] = DEFAULT_MAIMAI_BROWSER_PORT
    if base["maimai_port"] in {9223, 9224}:
        base["maimai_port"] = DEFAULT_MAIMAI_BROWSER_PORT
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
        "request_resume_after_communicate",
        "request_phone_after_communicate",
        "maimai_actual_send",
        "maimai_followup_after_send",
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
        age_requirement=str(cfg.get("age_requirement") or "").strip(),
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
            self.send_text(INDEX_HTML, "text/html; charset=utf-8")
        elif path == "/api/state":
            self.send_json(STATE.snapshot())
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
            STATE.refresh_jobs_async(port)
            self.send_json({"ok": True})
        elif path == "/api/platform/open":
            platform = "maimai" if str(payload.get("platform") or "").lower() == "maimai" else "liepin"
            default_port = DEFAULT_MAIMAI_BROWSER_PORT if platform == "maimai" else DEFAULT_BROWSER_PORT
            port = int(payload.get("port") or default_port)
            STATE.open_platform_async(platform, port)
            self.send_json({"ok": True})
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


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>招聘软件助手</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7fb;
      --panel: #ffffff;
      --panel-soft: #f9fafc;
      --text: #111827;
      --muted: #6b7280;
      --line: #e5e7eb;
      --brand: #2563eb;
      --brand-dark: #1d4ed8;
      --ok: #059669;
      --warn: #d97706;
      --bad: #dc2626;
      --shadow: 0 16px 40px rgba(15, 23, 42, .08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Microsoft YaHei UI", "Segoe UI", system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      letter-spacing: 0;
    }
    .app { min-height: 100vh; display: grid; grid-template-columns: 300px minmax(0, 1fr); }
    aside {
      background: #0f172a;
      color: #e5e7eb;
      padding: 22px;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
    }
    .brand { display: flex; align-items: center; justify-content: space-between; margin-bottom: 22px; }
    .brand h1 { font-size: 20px; margin: 0; }
    .pill { font-size: 12px; border: 1px solid rgba(255,255,255,.18); border-radius: 999px; padding: 5px 9px; color: #bfdbfe; }
    .side-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 18px; }
    button {
      border: 0;
      border-radius: 8px;
      padding: 10px 12px;
      background: var(--brand);
      color: white;
      cursor: pointer;
      font-size: 14px;
      font-weight: 600;
    }
    button:hover { background: var(--brand-dark); }
    button.secondary { background: #eef2ff; color: #1e40af; }
    button.ghost { background: rgba(255,255,255,.08); color: #e5e7eb; }
    button.danger { background: #fee2e2; color: #991b1b; }
    button:disabled { opacity: .55; cursor: not-allowed; }
    .task-list { display: grid; gap: 10px; }
    .task {
      border: 1px solid rgba(255,255,255,.1);
      background: rgba(255,255,255,.06);
      border-radius: 10px;
      padding: 12px;
      cursor: pointer;
    }
    .task.active { border-color: #60a5fa; background: rgba(37,99,235,.25); }
    .task-name { font-weight: 700; margin-bottom: 6px; }
    .task-meta { color: #cbd5e1; font-size: 12px; line-height: 1.7; }
    main { padding: 24px; min-width: 0; }
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      margin-bottom: 18px;
    }
    .topbar h2 { margin: 0; font-size: 24px; }
    .status { color: var(--muted); font-size: 13px; }
    .toolbar { display: flex; gap: 10px; flex-wrap: wrap; }
    .platform-switch {
      display: inline-flex;
      border: 1px solid var(--line);
      background: white;
      border-radius: 8px;
      padding: 4px;
      gap: 4px;
    }
    .platform-switch button {
      min-height: 32px;
      padding: 7px 12px;
      background: transparent;
      color: #334155;
    }
    .platform-switch button.active {
      background: var(--brand);
      color: white;
    }
    .platform-panel.hidden { display: none; }
    .grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 14px; }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      box-shadow: var(--shadow);
      padding: 16px;
    }
    .span-4 { grid-column: span 4; }
    .span-6 { grid-column: span 6; }
    .span-8 { grid-column: span 8; }
    .span-12 { grid-column: span 12; }
    .card h3 { margin: 0 0 14px; font-size: 16px; }
    label { display: block; color: #374151; font-size: 13px; margin-bottom: 6px; }
    input, select, textarea {
      width: 100%;
      border: 1px solid #d1d5db;
      border-radius: 8px;
      padding: 10px 11px;
      background: white;
      color: var(--text);
      font: inherit;
      min-height: 40px;
    }
    textarea { min-height: 126px; resize: vertical; line-height: 1.55; }
    .fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .field.full { grid-column: 1 / -1; }
    .checks { display: flex; gap: 14px; flex-wrap: wrap; align-items: center; min-height: 40px; }
    .check { display: inline-flex; align-items: center; gap: 7px; color: #374151; font-size: 13px; }
    .check input { width: 16px; height: 16px; min-height: 0; }
    .schedule-row { display: grid; grid-template-columns: 1fr 1fr auto; gap: 10px; align-items: end; }
    .time-picker { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: center; }
    .time-list { display: flex; flex-wrap: wrap; gap: 8px; min-height: 40px; align-items: center; margin-top: 10px; }
    .time-chip {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      border: 1px solid #bfdbfe;
      background: #eff6ff;
      color: #1d4ed8;
      border-radius: 999px;
      padding: 7px 10px;
      font-size: 13px;
      font-weight: 700;
    }
    .time-chip button {
      width: 20px;
      height: 20px;
      min-height: 0;
      padding: 0;
      border-radius: 50%;
      background: #dbeafe;
      color: #1e40af;
      line-height: 20px;
      font-size: 14px;
    }
    .hint { color: var(--muted); font-size: 12px; line-height: 1.7; }
    .table-wrap { overflow: auto; border: 1px solid var(--line); border-radius: 8px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; background: white; }
    th, td { border-bottom: 1px solid var(--line); padding: 10px; text-align: left; vertical-align: top; }
    th { background: var(--panel-soft); color: #374151; font-weight: 700; position: sticky; top: 0; }
    .logs {
      height: 260px;
      overflow: auto;
      background: #0b1020;
      color: #dbeafe;
      border-radius: 8px;
      padding: 12px;
      font: 13px/1.55 Consolas, "Microsoft YaHei UI", monospace;
    }
    .log-line { margin-bottom: 5px; }
    .tag { display: inline-block; padding: 2px 7px; border-radius: 999px; font-size: 12px; background: #e0f2fe; color: #075985; }
    .tag.ok { background: #dcfce7; color: #166534; }
    .tag.bad { background: #fee2e2; color: #991b1b; }
    .muted { color: var(--muted); }
    .pick-box {
      border: 1px solid #d1d5db;
      border-radius: 8px;
      min-height: 40px;
      background: white;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      padding: 6px;
    }
    .chip-list { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; min-width: 0; }
    .pick-chip {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      border: 1px solid #bfdbfe;
      background: #eff6ff;
      color: #1e40af;
      border-radius: 999px;
      padding: 5px 8px;
      font-size: 12px;
      max-width: 100%;
    }
    .pick-chip button {
      width: 16px;
      height: 16px;
      min-height: 0;
      padding: 0;
      border-radius: 50%;
      background: #dbeafe;
      color: #1e40af;
      line-height: 16px;
      font-size: 12px;
    }
    .picker-overlay {
      position: fixed;
      inset: 0;
      display: none;
      align-items: center;
      justify-content: center;
      background: rgba(15, 23, 42, .36);
      z-index: 50;
      padding: 18px;
    }
    .picker-overlay.open { display: flex; }
    .industry-modal {
      width: min(980px, 100%);
      max-height: min(720px, calc(100vh - 36px));
      background: white;
      border-radius: 10px;
      box-shadow: 0 28px 90px rgba(15, 23, 42, .24);
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      overflow: hidden;
    }
    .industry-head {
      height: 62px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 22px;
      border-bottom: 1px solid var(--line);
    }
    .industry-head strong { font-size: 18px; }
    .icon-button {
      width: 34px;
      height: 34px;
      min-height: 0;
      padding: 0;
      background: #f3f4f6;
      color: #111827;
      border-radius: 8px;
      font-size: 20px;
      line-height: 34px;
    }
    .industry-body {
      display: grid;
      grid-template-columns: 240px minmax(0, 1fr);
      min-height: 360px;
      overflow: hidden;
    }
    .industry-cats {
      background: #f8fafc;
      border-right: 1px solid var(--line);
      overflow: auto;
      padding: 10px 8px;
    }
    .industry-cat {
      width: 100%;
      min-height: 40px;
      text-align: left;
      background: transparent;
      color: #111827;
      font-weight: 500;
      border-radius: 6px;
      padding: 9px 14px;
    }
    .industry-cat:hover, .industry-cat.active { background: #eef2f7; color: #0f172a; }
    .industry-tags {
      overflow: auto;
      padding: 26px;
      display: flex;
      align-content: flex-start;
      flex-wrap: wrap;
      gap: 14px;
    }
    .industry-tag {
      background: #f1f5f9;
      color: #0f172a;
      border: 1px solid transparent;
      border-radius: 6px;
      min-height: 40px;
      padding: 9px 15px;
      font-weight: 500;
    }
    .industry-tag:hover { background: #e2e8f0; }
    .industry-tag.selected { background: #eff6ff; border-color: #93c5fd; color: #1d4ed8; }
    .industry-foot {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      border-top: 1px solid var(--line);
      padding: 16px 26px;
      min-height: 78px;
    }
    .industry-selected { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; min-width: 0; }
    @media (max-width: 1100px) {
      .app { grid-template-columns: 1fr; }
      aside { position: static; height: auto; }
      .span-4, .span-6, .span-8 { grid-column: span 12; }
    }
    @media (max-width: 760px) {
      main { padding: 14px; }
      .fields, .schedule-row { grid-template-columns: 1fr; }
      .topbar { align-items: stretch; flex-direction: column; }
      .industry-body { grid-template-columns: 1fr; }
      .industry-cats { display: flex; gap: 6px; overflow-x: auto; border-right: 0; border-bottom: 1px solid var(--line); }
      .industry-cat { width: auto; white-space: nowrap; }
    }
  </style>
</head>
<body>
<div class="app">
  <aside>
    <div class="brand">
      <h1>招聘软件助手</h1>
      <span class="pill">localhost</span>
    </div>
    <div class="side-actions">
      <button class="ghost" onclick="newTask()">新建配置</button>
      <button class="ghost" onclick="deleteTask()">删除配置</button>
    </div>
    <div id="taskList" class="task-list"></div>
  </aside>
  <main>
    <div class="topbar">
      <div>
        <h2 id="pageTitle">自动搜索与沟通</h2>
        <div id="status" class="status">正在加载...</div>
      </div>
      <div class="toolbar">
        <div class="platform-switch" aria-label="平台切换">
          <button type="button" id="platform_liepin" onclick="setPlatform('liepin')">猎聘</button>
          <button type="button" id="platform_maimai" onclick="setPlatform('maimai')">脉脉</button>
        </div>
        <button class="secondary" onclick="refreshJobs()">刷新职位</button>
        <button class="secondary" onclick="saveTask()">保存配置</button>
        <button onclick="runTask()">立即运行</button>
        <button id="stopTaskButton" class="danger" onclick="stopTask()" disabled>停止</button>
      </div>
    </div>

    <div class="grid">
      <section class="card span-8">
        <h3>基础筛选</h3>
        <div class="fields">
          <div><label>配置名称</label><input id="taskName" /></div>
          <div><label>浏览器端口</label><input id="port" type="number" min="1" /></div>
        </div>
        <div id="liepinPanel" class="platform-panel fields" style="margin-top:12px">
          <div class="field full"><label>开聊职位</label><select id="selected_chat_job"></select></div>
          <div><label>顶部关键词</label><input id="keywords" /></div>
          <div class="checks"><label class="check"><input id="use_keywords_ai_words" type="checkbox" />关键词 AI 填词</label></div>
          <div><label>职位名称</label><input id="job_name" /></div>
          <div class="checks"><label class="check"><input id="use_job_ai_words" type="checkbox" />职位 AI 填词</label></div>
          <div><label>公司名称</label><input id="company_name" /></div>
          <div class="checks"><label class="check"><input id="use_company_ai_words" type="checkbox" />公司 AI 填词</label></div>
          <div><label>目前城市</label><input id="current_city" /></div>
          <div><label>期望城市</label><input id="expected_city" /></div>
          <div><label>经验</label><select id="experience"></select></div>
          <div><label>教育经历</label><div class="checks" id="education"></div></div>
          <div><label>统招要求</label><select id="recruitment_type"></select></div>
          <div><label>院校要求</label><div class="checks" id="school_types"></div></div>
          <div class="field full">
            <label>当前行业</label>
            <div class="pick-box">
              <div id="current_industries_view" class="chip-list"></div>
              <button type="button" class="secondary" onclick="openIndustryPicker('current')">选择行业</button>
            </div>
          </div>
          <div class="field full">
            <label>期望行业</label>
            <div class="pick-box">
              <div id="expected_industries_view" class="chip-list"></div>
              <button type="button" class="secondary" onclick="openIndustryPicker('expected')">选择行业</button>
            </div>
          </div>
          <div class="field full">
            <label>当前职能</label>
            <div class="pick-box">
              <div id="current_functions_view" class="chip-list"></div>
              <button type="button" class="secondary" onclick="openFunctionPicker('current')">选择职能</button>
            </div>
          </div>
          <div class="field full">
            <label>期望职能</label>
            <div class="pick-box">
              <div id="expected_functions_view" class="chip-list"></div>
              <button type="button" class="secondary" onclick="openFunctionPicker('expected')">选择职能</button>
            </div>
          </div>
        </div>
        <div id="maimaiPanel" class="platform-panel fields hidden" style="margin-top:12px">
          <div><label>关键词</label><input id="maimai_keyword" placeholder="例如：研发、销售、产品经理" /></div>
          <div><label>脉脉浏览器端口</label><input id="maimai_port" type="number" min="1" /></div>
          <div><label>关键词模式</label><select id="maimai_keyword_mode"></select></div>
          <div><label>城市地区</label><input id="maimai_city" placeholder="无则不筛选" /></div>
          <div><label>页数</label><input id="maimai_page_limit" type="number" min="1" /></div>
          <div><label>学历要求</label><select id="maimai_education"></select></div>
          <div><label>学历附加</label><select id="maimai_education_extra"></select></div>
          <div><label>工作年限</label><select id="maimai_work_years"></select></div>
          <div><label>毕业年份</label><select id="maimai_graduation_year"></select></div>
          <div><label>就职公司</label><input id="maimai_companies" placeholder="多个公司用逗号分隔" /></div>
          <div><label>性别</label><select id="maimai_gender"></select></div>
          <div><label>每页候选人数</label><input id="maimai_candidate_limit" type="number" min="0" placeholder="0 为不限制" /></div>
          <div class="field full"><label>问候语</label><input id="maimai_greeting" /></div>
          <div class="field full"><label class="check"><input id="maimai_actual_send" type="checkbox" />实际发送沟通消息</label></div>
          <div class="field full"><label class="check"><input id="maimai_followup_after_send" type="checkbox" />发送后启动消息跟进、交换手机号并监听回复</label></div>
        </div>
      </section>

      <section class="card span-4">
        <h3>定时任务</h3>
        <div class="fields">
          <div class="field full"><label>启用定时</label><label class="check"><input id="enabled" type="checkbox" />到点自动运行这一套配置</label></div>
          <div class="field full">
            <label>运行时间</label>
            <div class="time-picker">
              <input id="timePicker" type="time" step="60" />
              <button type="button" class="secondary" onclick="addScheduleTime()">添加</button>
            </div>
            <div id="timeList" class="time-list"></div>
          </div>
          <div class="field full hint">可以保存多套配置，每套配置可添加多个时间。比如上午两个配置，下午三个配置。</div>
        </div>
      </section>

      <section id="liepinOtherPanel" class="card span-6 platform-panel">
        <h3>其他筛选</h3>
        <div class="fields">
          <div><label>活跃状态</label><select id="active_status"></select></div>
          <div><label>求职状态</label><select id="job_status"></select></div>
          <div><label>跳槽频率</label><select id="job_hop_frequency"></select></div>
          <div><label>年龄要求</label><select id="age_requirement"></select></div>
          <div><label>性别要求</label><select id="gender_requirement"></select></div>
          <div><label>语言要求</label><select id="language_requirement"></select></div>
          <div><label>毕业年份</label><select id="graduation_year"></select></div>
        </div>
      </section>

      <section class="card span-6">
        <h3>AI 匹配与自动沟通</h3>
        <div class="fields">
          <div class="field full"><label>DeepSeek Key</label><input id="deepseek_api_key" type="password" /></div>
          <div><label>模型</label><input id="deepseek_model" /></div>
          <div id="liepinAiPanel" class="field full platform-panel">
            <div class="fields">
              <div><label>处理人数</label><input id="candidate_limit" type="number" min="1" /></div>
              <div class="field full"><label class="check"><input id="auto_communicate" type="checkbox" />AI 判断通过后自动沟通；如果页面是“继续沟通”则跳过</label></div>
              <div class="field full"><label class="check"><input id="request_resume_after_communicate" type="checkbox" />沟通成功后，打开继续沟通小窗索要简历</label></div>
              <div class="field full"><label class="check"><input id="request_phone_after_communicate" type="checkbox" />沟通成功后，打开继续沟通小窗索要电话</label></div>
            </div>
          </div>
          <div id="maimaiAiPanel" class="field full platform-panel hidden">
            <label>DeepSeek Base URL</label>
            <input id="maimai_deepseek_base_url" />
          </div>
          <div id="liepinRequirementPanel" class="field full platform-panel">
            <label>AI筛选要求</label>
            <textarea id="match_requirements" placeholder="例如：要有销售经验，最好做过医疗行业，成都优先，薪资别太离谱。留空则使用默认通用要求。"></textarea>
            <div class="hint">写口语化要求即可，后台会自动整理成严谨提示词发给 AI；留空时使用默认通用要求。</div>
          </div>
          <div id="maimaiRequirementPanel" class="field full platform-panel hidden">
            <label>脉脉 AI 筛选要求</label>
            <textarea id="maimai_ai_requirement_text" placeholder="例如：必须有 B 端销售经验，优先网络安全行业，广州/深圳优先。"></textarea>
            <div class="hint">脉脉会先提取当前页简历，再用这里的要求做 AI 判断；通过的人才会进入沟通流程。</div>
          </div>
        </div>
      </section>

      <section class="card span-12">
        <h3>候选人 AI 评价</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>#</th><th>候选人</th><th>求职/城市</th><th>AI</th><th>分数</th><th>沟通</th><th>理由</th></tr></thead>
            <tbody id="results"></tbody>
          </table>
        </div>
      </section>

      <section class="card span-12">
        <h3>运行日志</h3>
        <div id="logs" class="logs"></div>
      </section>
    </div>
  </main>
</div>

<div id="industryPicker" class="picker-overlay" onclick="closeIndustryPicker(event)">
  <div class="industry-modal" onclick="event.stopPropagation()">
    <div class="industry-head">
      <strong id="industryPickerTitle">请选择行业</strong>
      <button type="button" class="icon-button" onclick="closeIndustryPicker()">×</button>
    </div>
    <div class="industry-body">
      <div id="industryCats" class="industry-cats"></div>
      <div id="industryTags" class="industry-tags"></div>
    </div>
    <div class="industry-foot">
      <div id="industrySelected" class="industry-selected"></div>
      <button type="button" onclick="confirmIndustryPicker()">确认</button>
    </div>
  </div>
</div>

<script>
let state = null;
let activeTaskId = "";
let scheduleTimes = [];
let currentIndustries = [];
let expectedIndustries = [];
let currentFunctions = [];
let expectedFunctions = [];
let currentPlatform = "liepin";
let optionPickerKind = "industry";
let optionPickerTarget = "current";
let optionPickerCategory = "";
let optionPickerDraft = [];
let refreshJobsTimer = null;
const fieldIds = [
  "platform",
  "port", "keywords", "job_name", "company_name", "current_city", "expected_city",
  "experience", "recruitment_type", "active_status", "job_status",
  "job_hop_frequency", "age_requirement", "gender_requirement", "language_requirement",
  "graduation_year", "deepseek_api_key", "deepseek_model", "candidate_limit", "match_requirements",
  "use_keywords_ai_words", "use_job_ai_words", "use_company_ai_words", "auto_communicate",
  "request_resume_after_communicate", "request_phone_after_communicate",
  "maimai_keyword", "maimai_port", "maimai_keyword_mode", "maimai_city", "maimai_education", "maimai_education_extra",
  "maimai_work_years", "maimai_graduation_year", "maimai_companies", "maimai_gender",
  "maimai_page_limit", "maimai_candidate_limit", "maimai_ai_requirement_text", "maimai_greeting", "maimai_actual_send",
  "maimai_followup_after_send", "maimai_deepseek_base_url"
];

function optionHtml(values, selected = "") {
  return values.map(v => `<option value="${escapeHtml(v)}"${v === selected ? " selected" : ""}>${escapeHtml(v || "不设置")}</option>`).join("");
}
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
}
async function api(path, payload) {
  const res = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload || {})});
  return await res.json();
}
async function loadState(keepForm = false) {
  const res = await fetch("/api/state");
  state = await res.json();
  activeTaskId = state.active_task_id || (state.tasks[0] && state.tasks[0].id) || "";
  renderTasks();
  renderResults();
  renderLogs();
  document.getElementById("status").textContent = state.running
    ? (state.stop_requested ? `正在停止：${state.running_task}` : `运行中：${state.running_task}`)
    : "准备就绪";
  const stopButton = document.getElementById("stopTaskButton");
  if (stopButton) {
    stopButton.disabled = !state.running || !!state.stop_requested;
    stopButton.textContent = state.stop_requested ? "正在停止" : "停止";
  }
  if (!keepForm) fillForm(activeTask());
  else renderJobSelect();
}
function activeTask() {
  return (state.tasks || []).find(t => t.id === activeTaskId) || state.tasks[0];
}
function renderTasks() {
  const box = document.getElementById("taskList");
  box.innerHTML = (state.tasks || []).map(task => `
    <div class="task ${task.id === activeTaskId ? "active" : ""}" onclick="selectTask('${task.id}')">
      <div class="task-name">${escapeHtml(task.name)}</div>
      <div class="task-meta">
        ${task.enabled ? "已启用" : "未启用"} · ${(task.times || []).join(", ") || "未设时间"}<br>
        ${escapeHtml(task.last_status || "未运行")}
      </div>
    </div>
  `).join("");
}
function fillSelect(id, selected) {
  document.getElementById(id).innerHTML = optionHtml(state.options[id] || [""], selected || "");
}
function renderCheckboxGroup(id, values, selectedValues = []) {
  const box = document.getElementById(id);
  if (!box) return;
  const selected = Array.isArray(selectedValues) ? selectedValues : String(selectedValues || "").split(/[,，、;]/).map(x => x.trim()).filter(Boolean);
  box.innerHTML = (values || []).filter(Boolean).map(label => {
    const checked = selected.includes(label) ? "checked" : "";
    return `<label class="check"><input type="checkbox" value="${escapeHtml(label)}" ${checked}>${escapeHtml(label)}</label>`;
  }).join("");
}
function fillMaimaiSelect(id, group, selected) {
  const ele = document.getElementById(id);
  if (!ele) return;
  ele.innerHTML = optionHtml((state.maimai_options || {})[group] || [""], selected || "");
}
function renderMaimaiOptions(cfg = {}) {
  fillMaimaiSelect("maimai_keyword_mode", "keyword_mode", cfg.maimai_keyword_mode || "所有");
  fillMaimaiSelect("maimai_education", "education", cfg.maimai_education || "无");
  fillMaimaiSelect("maimai_education_extra", "education_extra", cfg.maimai_education_extra || "无");
  fillMaimaiSelect("maimai_work_years", "work_years", cfg.maimai_work_years || "无");
  fillMaimaiSelect("maimai_graduation_year", "graduation_year", cfg.maimai_graduation_year || "无");
  fillMaimaiSelect("maimai_gender", "gender", cfg.maimai_gender || "无");
}
async function setPlatform(platform) {
  currentPlatform = platform === "maimai" ? "maimai" : "liepin";
  applyPlatformUI();
  const port = currentPlatform === "maimai"
    ? Number(document.getElementById("maimai_port").value || 9225)
    : Number(document.getElementById("port").value || 9225);
  try {
    await api("/api/platform/open", {platform: currentPlatform, port});
    await loadState(true);
  } catch (err) {
    console.error(err);
  }
}
function applyPlatformUI() {
  const isMaimai = currentPlatform === "maimai";
  for (const id of ["liepinPanel", "liepinOtherPanel", "liepinAiPanel", "liepinRequirementPanel"]) {
    document.getElementById(id)?.classList.toggle("hidden", isMaimai);
  }
  for (const id of ["maimaiPanel", "maimaiAiPanel", "maimaiRequirementPanel"]) {
    document.getElementById(id)?.classList.toggle("hidden", !isMaimai);
  }
  document.getElementById("platform_liepin")?.classList.toggle("active", !isMaimai);
  document.getElementById("platform_maimai")?.classList.toggle("active", isMaimai);
  const refreshButton = document.querySelector('.toolbar button[onclick="refreshJobs()"]');
  if (refreshButton) refreshButton.disabled = isMaimai;
  document.getElementById("pageTitle").textContent = (activeTask()?.name || "自动搜索与沟通") + (isMaimai ? " · 脉脉" : " · 猎聘");
}
function renderJobSelect(cfg = null) {
  const jobSelect = document.getElementById("selected_chat_job");
  if (!jobSelect) return;
  const currentLabel = jobSelect.value || "";
  const configLabel = cfg && cfg.selected_chat_job ? formatJobLabel(cfg.selected_chat_job) : "";
  const selectedJobLabel = currentLabel || configLabel;
  jobSelect.innerHTML = `<option value="">自动选择第一个职位</option>` + (state.jobs || []).map(job => {
    const label = formatJobLabel(job);
    return `<option value="${escapeHtml(label)}"${label === selectedJobLabel ? " selected" : ""}>${escapeHtml(label)}</option>`;
  }).join("");
}
function fillForm(task) {
  if (!task) return;
  const cfg = {...state.defaults, ...(task.config || {})};
  currentPlatform = cfg.platform === "maimai" ? "maimai" : "liepin";
  document.getElementById("taskName").value = task.name || "";
  document.getElementById("enabled").checked = !!task.enabled;
  scheduleTimes = normalizeTimes(task.times || []);
  renderScheduleTimes();
  for (const id of Object.keys(state.options || {})) fillSelect(id, cfg[id]);
  renderCheckboxGroup("education", state.options.education || [], cfg.education || []);
  renderMaimaiOptions(cfg);
  renderJobSelect(cfg);
  const schools = ["211", "985", "双一流", "海外留学"];
  document.getElementById("school_types").innerHTML = schools.map(label => {
    const checked = (cfg.school_types || []).includes(label) ? "checked" : "";
    return `<label class="check"><input type="checkbox" value="${label}" ${checked}>${label}</label>`;
  }).join("");
  for (const id of fieldIds) {
    const ele = document.getElementById(id);
    if (!ele) continue;
    if (ele.type === "checkbox") ele.checked = !!cfg[id];
    else if (id === "match_requirements" && (cfg[id] || "").trim() === (state.defaults.match_requirements || "").trim()) ele.value = "";
    else ele.value = cfg[id] ?? "";
  }
  currentIndustries = normalizeIndustries(cfg.current_industries || []);
  expectedIndustries = normalizeIndustries(cfg.expected_industries || []);
  currentFunctions = normalizeIndustries(cfg.current_functions || []);
  expectedFunctions = normalizeIndustries(cfg.expected_functions || []);
  renderOptionViews();
  applyPlatformUI();
}
function readForm() {
  const cfg = {};
  for (const id of fieldIds) {
    const ele = document.getElementById(id);
    if (!ele) continue;
    cfg[id] = ele.type === "checkbox" ? ele.checked : ele.value;
  }
  cfg.port = Number(cfg.port || 9225);
  cfg.platform = currentPlatform;
  cfg.candidate_limit = Number(cfg.candidate_limit || 1);
  cfg.maimai_port = Number(cfg.maimai_port || 9225);
  if (cfg.maimai_port === 9223) cfg.maimai_port = 9225;
  cfg.maimai_page_limit = Number(cfg.maimai_page_limit || 1);
  cfg.maimai_candidate_limit = Number(cfg.maimai_candidate_limit || 0);
  cfg.school_types = Array.from(document.querySelectorAll("#school_types input:checked")).map(x => x.value);
  cfg.education = Array.from(document.querySelectorAll("#education input:checked")).map(x => x.value);
  cfg.current_industries = normalizeIndustries(currentIndustries);
  cfg.expected_industries = normalizeIndustries(expectedIndustries);
  cfg.current_functions = normalizeIndustries(currentFunctions);
  cfg.expected_functions = normalizeIndustries(expectedFunctions);
  const jobLabel = document.getElementById("selected_chat_job").value;
  cfg.selected_chat_job = (state.jobs || []).find(job => formatJobLabel(job) === jobLabel) || null;
  return {
    id: activeTaskId || undefined,
    name: document.getElementById("taskName").value.trim() || "未命名任务",
    enabled: document.getElementById("enabled").checked,
    times: scheduleTimes,
    config: cfg
  };
}
function normalizeIndustries(values) {
  const raw = Array.isArray(values) ? values : String(values || "").split(/[，,、;；]/);
  const result = [];
  for (const item of raw) {
    const value = String(item || "").trim();
    if (value && !result.includes(value)) result.push(value);
  }
  return result.slice(0, 5);
}
function renderOptionViews() {
  renderOptionView("current_industries", currentIndustries, "industry");
  renderOptionView("expected_industries", expectedIndustries, "industry");
  renderOptionView("current_functions", currentFunctions, "function");
  renderOptionView("expected_functions", expectedFunctions, "function");
}
function renderOptionView(id, values, kind) {
  const box = document.getElementById(`${id}_view`);
  if (!box) return;
  box.innerHTML = values.length
    ? values.map(value => `<span class="pick-chip">${escapeHtml(value)}<button type="button" title="删除" onclick="removePickedOption('${kind}', '${id.startsWith("current") ? "current" : "expected"}', '${escapeJs(value)}')">×</button></span>`).join("")
    : `<span class="hint">不设置</span>`;
}
function escapeJs(value) {
  return String(value ?? "").replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}
function removePickedOption(kind, type, value) {
  if (kind === "industry" && type === "current") currentIndustries = currentIndustries.filter(item => item !== value);
  else if (kind === "industry") expectedIndustries = expectedIndustries.filter(item => item !== value);
  else if (type === "current") currentFunctions = currentFunctions.filter(item => item !== value);
  else expectedFunctions = expectedFunctions.filter(item => item !== value);
  renderOptionViews();
}
function openIndustryPicker(type) {
  openOptionPicker("industry", type);
}
function openFunctionPicker(type) {
  openOptionPicker("function", type);
}
function openOptionPicker(kind, type) {
  optionPickerKind = kind;
  optionPickerTarget = type;
  optionPickerDraft = normalizeIndustries(
    kind === "industry"
      ? (type === "current" ? currentIndustries : expectedIndustries)
      : (type === "current" ? currentFunctions : expectedFunctions)
  );
  const categories = Object.keys(getOptionGroups(kind));
  optionPickerCategory = categories[0] || "";
  document.getElementById("industryPickerTitle").textContent = type === "current" ? "请选择当前行业" : "请选择期望行业";
  if (kind === "function") {
    document.getElementById("industryPickerTitle").textContent = type === "current" ? "请选择当前职能" : "请选择期望职能";
  }
  document.getElementById("industryPicker").classList.add("open");
  renderOptionPicker();
}
function closeIndustryPicker(event) {
  if (event && event.target && event.target.id !== "industryPicker") return;
  document.getElementById("industryPicker").classList.remove("open");
}
function getOptionGroups(kind) {
  return kind === "function" ? (state.function_groups || {}) : (state.industry_groups || {});
}
function renderIndustryPicker() {
  renderOptionPicker();
}
function renderOptionPicker() {
  const groups = getOptionGroups(optionPickerKind);
  const categories = Object.keys(groups);
  if (!optionPickerCategory && categories.length) optionPickerCategory = categories[0];
  document.getElementById("industryCats").innerHTML = categories.map(category => (
    `<button type="button" class="industry-cat ${category === optionPickerCategory ? "active" : ""}" onclick="selectIndustryCategory('${escapeJs(category)}')">${escapeHtml(category)}</button>`
  )).join("");
  const values = groups[optionPickerCategory] || [];
  document.getElementById("industryTags").innerHTML = values.map(value => {
    const selected = optionPickerDraft.includes(value);
    return `<button type="button" class="industry-tag ${selected ? "selected" : ""}" onclick="toggleIndustry('${escapeJs(value)}')">${escapeHtml(value)}</button>`;
  }).join("");
  document.getElementById("industrySelected").innerHTML = `
    <span class="muted">已选（${optionPickerDraft.length}/5）</span>
    ${optionPickerDraft.map(value => `<span class="pick-chip">${escapeHtml(value)}<button type="button" title="删除" onclick="toggleIndustry('${escapeJs(value)}')">×</button></span>`).join("")}
  `;
}
function selectIndustryCategory(category) {
  optionPickerCategory = category;
  renderOptionPicker();
}
function toggleIndustry(value) {
  if (optionPickerDraft.includes(value)) {
    optionPickerDraft = optionPickerDraft.filter(item => item !== value);
  } else if (optionPickerDraft.length < 5) {
    optionPickerDraft.push(value);
  }
  renderOptionPicker();
}
function confirmIndustryPicker() {
  if (optionPickerKind === "industry" && optionPickerTarget === "current") currentIndustries = normalizeIndustries(optionPickerDraft);
  else if (optionPickerKind === "industry") expectedIndustries = normalizeIndustries(optionPickerDraft);
  else if (optionPickerTarget === "current") currentFunctions = normalizeIndustries(optionPickerDraft);
  else expectedFunctions = normalizeIndustries(optionPickerDraft);
  renderOptionViews();
  closeIndustryPicker();
}
function normalizeTimes(values) {
  const raw = Array.isArray(values) ? values : String(values || "").split(/[，,;；]/);
  const result = [];
  for (const item of raw) {
    const value = String(item || "").trim();
    if (!/^\d{1,2}:\d{2}$/.test(value)) continue;
    const [h, m] = value.split(":").map(Number);
    if (h < 0 || h > 23 || m < 0 || m > 59) continue;
    const normalized = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
    if (!result.includes(normalized)) result.push(normalized);
  }
  return result.sort();
}
function renderScheduleTimes() {
  const box = document.getElementById("timeList");
  box.innerHTML = scheduleTimes.length
    ? scheduleTimes.map(time => `<span class="time-chip">${escapeHtml(time)}<button type="button" title="删除" onclick="removeScheduleTime('${time}')">×</button></span>`).join("")
    : `<span class="hint">还没有添加运行时间</span>`;
}
function addScheduleTime() {
  const input = document.getElementById("timePicker");
  const value = normalizeTimes([input.value])[0];
  if (!value) return;
  if (!scheduleTimes.includes(value)) scheduleTimes.push(value);
  scheduleTimes = normalizeTimes(scheduleTimes);
  input.value = "";
  renderScheduleTimes();
}
function removeScheduleTime(time) {
  scheduleTimes = scheduleTimes.filter(item => item !== time);
  renderScheduleTimes();
}
function formatJobLabel(job) {
  return job.label || [job.title, job.city, job.salary].filter(Boolean).join(" | ");
}
async function selectTask(id) {
  activeTaskId = id;
  await api("/api/tasks/active", {id});
  await loadState(false);
}
async function newTask() {
  const payload = readForm();
  payload.id = undefined;
  payload.name = "新配置 " + new Date().toLocaleTimeString("zh-CN", {hour12:false}).slice(0,5);
  payload.enabled = false;
  const res = await api("/api/tasks/save", payload);
  activeTaskId = res.task.id;
  await loadState(false);
}
async function deleteTask() {
  if (!activeTaskId || !confirm("确定删除当前配置？")) return;
  await api("/api/tasks/delete", {id: activeTaskId});
  await loadState(false);
}
async function saveTask() {
  const res = await api("/api/tasks/save", readForm());
  activeTaskId = res.task.id;
  await loadState(false);
}
async function runTask() {
  await saveTask();
  await api("/api/tasks/run", {id: activeTaskId});
  setTimeout(() => loadState(true), 500);
}
async function stopTask() {
  const button = document.getElementById("stopTaskButton");
  if (button) {
    button.disabled = true;
    button.textContent = "正在停止";
  }
  await api("/api/tasks/stop", {});
  const startedAt = Date.now();
  const timer = setInterval(async () => {
    await loadState(true);
    if (!state.running || Date.now() - startedAt > 30000) clearInterval(timer);
  }, 1000);
}
async function refreshJobs() {
  const port = Number(document.getElementById("port").value || 9225);
  await api("/api/jobs/refresh", {port});
  if (refreshJobsTimer) clearInterval(refreshJobsTimer);
  const startedAt = Date.now();
  refreshJobsTimer = setInterval(async () => {
    await loadState(true);
    if (!state.running || Date.now() - startedAt > 60000) {
      clearInterval(refreshJobsTimer);
      refreshJobsTimer = null;
      await loadState(true);
    }
  }, 1000);
}
function renderResults() {
  const mapComm = {done:"已确认", already_communicated:"已沟通", failed:"失败", sent:"已发送", test:"测试"};
  const mapResume = {requested:"已索要", already_requested:"已索要", already_available:"已可看", not_found:"未找到会话", failed:"索要失败"};
  const mapPhone = {requested:"电话已索要", already_requested:"电话已索要", already_available:"电话可查看", not_found:"未找到会话", failed:"电话失败"};
  document.getElementById("results").innerHTML = (state.results || []).map(item => `
    <tr>
      <td>${escapeHtml(item.index || "")}</td>
      <td>${escapeHtml(item.name || "")}</td>
      <td>${escapeHtml([item.job_position, item.location || item.job_cities].filter(Boolean).join(" / "))}</td>
      <td><span class="tag ${item.match ? "ok" : "bad"}">${item.match ? "匹配" : "不匹配"}</span></td>
      <td>${escapeHtml(item.score || 0)}</td>
      <td title="${escapeHtml([item.communicate_note, item.phone_request_note, item.resume_request_note].filter(Boolean).join('；'))}">${escapeHtml([mapComm[item.communicate_status] || item.communicate_status || "", mapPhone[item.phone_request_status] || "", mapResume[item.resume_request_status] || ""].filter(Boolean).join(" / "))}</td>
      <td>${escapeHtml(item.reason || "")}</td>
    </tr>
  `).join("");
}
function renderLogs() {
  const box = document.getElementById("logs");
  box.innerHTML = (state.logs || []).map(log => `<div class="log-line"><span class="muted">${escapeHtml(log.time)}</span> ${escapeHtml(log.message)}</div>`).join("");
  box.scrollTop = box.scrollHeight;
}
setInterval(() => loadState(true), 2500);
loadState(false);
</script>
</body>
</html>
"""

def start_server(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> tuple[ThreadingHTTPServer, str]:
    ensure_edge_debugging(DEFAULT_BROWSER_PORT, default_url=SEARCH_URL)
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
