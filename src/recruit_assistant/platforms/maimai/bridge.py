# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import importlib
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable


Logger = Callable[[str], None] | None
MAIMAI_URL = "https://maimai.cn/ent/v41/recruit/talents?pid=&tab=1"


def workspace_root() -> Path:
    if hasattr(sys, "_MEIPASS"):
        bundle_root = Path(sys._MEIPASS)
        if (bundle_root / "src" / "maimai_auto").exists():
            return bundle_root
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if exe_dir.name.lower() == "dist" or exe_dir.parent.name.lower() == "dist":
            return exe_dir.parents[1]
        return exe_dir.parent
    return Path(__file__).resolve().parents[3]


def maimai_root() -> Path:
    bundled_root = Path(__file__).resolve().parent / "maimai"
    if bundled_root.exists():
        return bundled_root
    if hasattr(sys, "_MEIPASS"):
        bundle_root = Path(sys._MEIPASS)
        if (bundle_root / "src" / "maimai_auto").exists():
            return bundle_root
    maimai_dir_name = "".join(chr(code) for code in (0x8109, 0x8109, 0x81EA, 0x52A8, 0x5316))
    candidates = [
        workspace_root() / maimai_dir_name,
        Path.cwd().resolve().parent / maimai_dir_name,
        Path.cwd().resolve() / maimai_dir_name,
    ]
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                exe_dir.parent / maimai_dir_name,
                exe_dir.parent.parent / maimai_dir_name,
                exe_dir / maimai_dir_name,
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def ensure_maimai_path() -> None:
    root = maimai_root()
    if not root.exists():
        raise RuntimeError(f"Maimai project directory not found: {root}")
    for path in (root, root / "src"):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)


class CallbackWriter:
    def __init__(self, callback: Logger) -> None:
        self.callback = callback
        self.buffer = ""

    def write(self, value: str) -> int:
        self.buffer += value
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            line = line.strip()
            if line and self.callback:
                self.callback(line)
        return len(value)

    def flush(self) -> None:
        line = self.buffer.strip()
        self.buffer = ""
        if line and self.callback:
            self.callback(line)


def _log(callback: Logger, message: str) -> None:
    if callback:
        callback(message)


def _run_with_capture(callback: Logger, func, *args, **kwargs):
    if not callback:
        return func(*args, **kwargs)
    writer = CallbackWriter(callback)
    with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
        result = func(*args, **kwargs)
    writer.flush()
    return result


def load_modules() -> dict:
    ensure_maimai_path()
    return {
        "config": importlib.import_module("src.maimai_auto.config"),
        "matching": importlib.import_module("src.maimai_auto.matching"),
        "message_monitor": importlib.import_module("src.maimai_auto.message_monitor"),
        "contacted": importlib.import_module("src.maimai_auto.contacted_candidates"),
        "legacy": importlib.import_module("src.maimai_auto.legacy"),
    }


def configure_maimai_port(modules: dict, port: int) -> None:
    port = int(port)
    modules["config"].DEFAULT_DEBUG_PORT = port if hasattr(modules["config"], "DEFAULT_DEBUG_PORT") else port
    try:
        importlib.import_module("src.maimai_auto.paths").DEFAULT_DEBUG_PORT = port
        importlib.import_module("src.maimai_auto.browser_connect").DEFAULT_DEBUG_PORT = port
        importlib.import_module("src.maimai_auto.message_monitor").DEFAULT_DEBUG_PORT = port
    except Exception:
        pass
    for loader_name in ("load_search_module", "load_resume_extract_batch_module", "load_chat_flow_module"):
        try:
            modules["legacy"].__dict__[loader_name].cache_clear()
        except Exception:
            pass


def config_to_settings(config: dict):
    modules = load_modules()
    SearchSettings = modules["config"].SearchSettings
    settings = SearchSettings.from_dict(
        {
            "keyword": config.get("maimai_keyword", ""),
            "keyword_mode": config.get("maimai_keyword_mode", "所有"),
            "city": config.get("maimai_city", "无"),
            "education": config.get("maimai_education", "无"),
            "education_extra": config.get("maimai_education_extra", "无"),
            "work_years": config.get("maimai_work_years", "无"),
            "graduation_year": config.get("maimai_graduation_year", "无"),
            "companies": config.get("maimai_companies", ""),
            "gender": config.get("maimai_gender", "无"),
            "page_limit": config.get("maimai_page_limit", 1),
            "ai_requirement_text": config.get("maimai_ai_requirement_text", ""),
            "greeting": config.get("maimai_greeting", ""),
            "actual_send": config.get("maimai_actual_send", False),
            "deepseek_api_key": config.get("deepseek_api_key", ""),
            "deepseek_base_url": config.get("maimai_deepseek_base_url", "https://api.deepseek.com"),
        }
    )
    return settings


def maimai_options() -> dict:
    modules = load_modules()
    cfg = modules["config"]
    return {
        "work_years": list(cfg.WORK_YEAR_OPTIONS),
        "graduation_year": list(cfg.GRADUATION_YEAR_OPTIONS),
        "education": list(cfg.EDUCATION_OPTIONS),
        "education_extra": list(cfg.EDUCATION_EXTRA_OPTIONS),
        "gender": list(cfg.GENDER_OPTIONS),
        "keyword_mode": list(cfg.KEYWORD_MODE_OPTIONS),
    }


def load_matches() -> dict:
    modules = load_modules()
    return modules["matching"].load_match_results()


def load_contacted() -> dict:
    modules = load_modules()
    return modules["contacted"].load_contacted_candidates()


def load_followup() -> dict:
    modules = load_modules()
    return modules["message_monitor"].load_state()


FOLLOWUP_LOCK = threading.Lock()
FOLLOWUP_THREAD: threading.Thread | None = None
FOLLOWUP_ACTIVE = False


def start_followup_daemon(callback: Logger = None) -> bool:
    global FOLLOWUP_THREAD, FOLLOWUP_ACTIVE
    modules = load_modules()
    with FOLLOWUP_LOCK:
        if FOLLOWUP_THREAD and FOLLOWUP_THREAD.is_alive():
            _log(callback, "Maimai follow-up thread is already running.")
            return False

        def worker() -> None:
            global FOLLOWUP_ACTIVE
            FOLLOWUP_ACTIVE = True
            try:
                _run_with_capture(callback, modules["message_monitor"].run_message_followup, 5.0, False, None, False)
            except Exception as exc:
                _log(callback, f"Maimai follow-up failed: {exc}")
            finally:
                FOLLOWUP_ACTIVE = False

        FOLLOWUP_THREAD = threading.Thread(target=worker, daemon=True, name="maimai-followup")
        FOLLOWUP_THREAD.start()
        return True


def is_followup_running() -> bool:
    return bool(FOLLOWUP_ACTIVE and FOLLOWUP_THREAD and FOLLOWUP_THREAD.is_alive())


def run_pipeline(config: dict, callback: Logger = None) -> dict:
    modules = load_modules()
    configure_maimai_port(modules, int(config.get("maimai_port") or config.get("port") or 9225))
    settings = config_to_settings(config)
    legacy = modules["legacy"]
    matching = modules["matching"]
    contacted = modules["contacted"]
    message_monitor = modules["message_monitor"]

    search_module = legacy.load_search_module()
    batch_module = legacy.load_resume_extract_batch_module()
    chat_module = legacy.load_chat_flow_module()

    matching.reset_match_results()
    contacted.reset_contacted_candidates()
    message_monitor.reset_state()

    _log(callback, "Start Maimai candidate search.")
    _run_with_capture(callback, search_module.run_candidate_search, settings.to_search_config())
    page = batch_module.connect_page()

    last_match_result = {"matched_candidates": [], "rejected_candidates": [], "summary": ""}
    target_pages = max(1, int(settings.page_limit))
    candidate_limit = int(config.get("maimai_candidate_limit") or 0)
    candidate_limit_arg = candidate_limit if candidate_limit > 0 else None
    processed_pages = 0

    while processed_pages < target_pages:
        page_number = processed_pages + 1
        _log(callback, f"Start Maimai page {page_number}/{target_pages}.")
        page_candidates = _run_with_capture(callback, batch_module.extract_current_page, page_number, candidate_limit_arg, page)
        current_count = len(page_candidates or [])
        if current_count <= 0:
            raise RuntimeError(f"Maimai page {page_number}: no candidates extracted.")

        last_match_result = matching.match_candidates(settings)
        matched = last_match_result.get("matched_candidates", [])
        _log(callback, f"Maimai page {page_number}: AI match complete, matched {len(matched)} candidates.")

        if matched:
            _run_with_capture(callback, chat_module.run_chat_flow_test, settings.greeting, settings.actual_send, page_number, page)
        else:
            _log(callback, f"Maimai page {page_number}: no matched candidates, skip chat.")

        processed_pages += 1
        if processed_pages >= target_pages:
            break
        if not _run_with_capture(callback, batch_module.goto_next_page, page):
            raise RuntimeError(f"Maimai page {page_number}: failed to go to next page.")

    if settings.actual_send and bool(config.get("maimai_followup_after_send", True)):
        start_followup_daemon(callback)
        _log(callback, "Maimai follow-up started.")
    elif not settings.actual_send:
        _log(callback, "Maimai test mode: messages were not sent and follow-up was not started.")

    contacted_payload = contacted.load_contacted_candidates()
    return {
        "platform": "maimai",
        "processed_pages": processed_pages,
        "matched": len(last_match_result.get("matched_candidates", [])),
        "matches": last_match_result,
        "contacted": contacted_payload,
        "settings": asdict(settings),
    }


def run_pipeline_subprocess(config: dict, callback: Logger = None, timeout: int = 600, stop_event=None) -> dict:
    config = dict(config or {})
    timeout = max(60, int(config.get("maimai_timeout_seconds") or timeout))
    temp_dir = Path(tempfile.gettempdir()) / "liepin-maimai-worker"
    temp_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{os.getpid()}-{int(time.time() * 1000)}"
    config_path = temp_dir / f"config-{stamp}.json"
    result_path = temp_dir / f"result-{stamp}.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    cmd = [
        sys.executable,
        "-m",
        "recruit_assistant.platforms.maimai_worker",
        str(config_path),
        str(result_path),
    ]
    env = os.environ.copy()
    package_src_dir = str(Path(__file__).resolve().parents[1])
    maimai_dir = str(maimai_root())
    env["PYTHONPATH"] = os.pathsep.join(
        path for path in (package_src_dir, maimai_dir, env.get("PYTHONPATH", "")) if path
    )

    _log(callback, f"Maimai worker started, timeout {timeout}s.")
    process = subprocess.Popen(
        cmd,
        cwd=str(Path(__file__).resolve().parents[2]),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    started_at = time.time()
    assert process.stdout is not None
    output_queue: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        try:
            for raw_line in process.stdout:
                output_queue.put(raw_line)
        finally:
            output_queue.put(None)

    reader = threading.Thread(target=read_output, daemon=True, name="maimai-worker-output")
    reader.start()
    output_closed = False
    last_heartbeat = started_at
    while True:
        while True:
            try:
                line = output_queue.get_nowait()
            except queue.Empty:
                break
            if line is None:
                output_closed = True
                continue
            if line:
                _log(callback, line.rstrip())
        if process.poll() is not None and output_closed:
            break
        if stop_event is not None and stop_event.is_set():
            process.kill()
            try:
                process.wait(timeout=5)
            except Exception:
                pass
            raise RuntimeError("Task stopped.")
        if time.time() - started_at > timeout:
            process.kill()
            try:
                process.wait(timeout=5)
            except Exception:
                pass
            raise TimeoutError(f"Maimai workflow did not finish within {timeout}s and was stopped.")
        if time.time() - last_heartbeat >= 15:
            _log(callback, "Maimai worker is still running; waiting for browser operation to return...")
            last_heartbeat = time.time()
        time.sleep(0.1)

    if result_path.exists():
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        if payload.get("ok"):
            return payload.get("result") or {}
        raise RuntimeError(payload.get("error") or "Maimai worker failed.")

    if process.returncode:
        raise RuntimeError(f"Maimai worker exited with code {process.returncode}.")
    raise RuntimeError("Maimai worker did not return a result file.")

