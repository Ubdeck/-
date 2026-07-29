# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
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

from . import browser, contacts, matching, paths, phone_exchange
from .automation import candidates, communication, resume, search
from .settings import (
    EDUCATION_EXTRA_OPTIONS,
    EDUCATION_OPTIONS,
    GENDER_OPTIONS,
    GRADUATION_YEAR_OPTIONS,
    KEYWORD_MODE_OPTIONS,
    WORK_YEAR_OPTIONS,
    SearchSettings,
)


Logger = Callable[[str], None] | None
MAIMAI_URL = "https://maimai.cn/ent/v41/recruit/talents?pid=&tab=1"


def workspace_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[4]


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


def configure_maimai_port(port: int) -> None:
    port = int(port)
    for module in (paths, browser, phone_exchange, search, resume):
        module.DEFAULT_DEBUG_PORT = port


def config_to_settings(config: dict):
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
    return {
        "work_years": list(WORK_YEAR_OPTIONS),
        "graduation_year": list(GRADUATION_YEAR_OPTIONS),
        "education": list(EDUCATION_OPTIONS),
        "education_extra": list(EDUCATION_EXTRA_OPTIONS),
        "gender": list(GENDER_OPTIONS),
        "keyword_mode": list(KEYWORD_MODE_OPTIONS),
    }


def load_matches() -> dict:
    return matching.load_match_results()


def load_contacted() -> dict:
    return contacts.load_contacted_candidates()


def run_pipeline(config: dict, callback: Logger = None) -> dict:
    configure_maimai_port(int(config.get("maimai_port") or config.get("port") or 9225))
    settings = config_to_settings(config)

    matching.reset_match_results()
    contacts.reset_contacted_candidates()

    _log(callback, "Start Maimai candidate search.")
    _run_with_capture(callback, search.run_candidate_search, settings.to_search_config())
    page = candidates.connect_page()

    last_match_result = {"matched_candidates": [], "rejected_candidates": [], "summary": ""}
    target_pages = max(1, int(settings.page_limit))
    candidate_limit = int(config.get("maimai_candidate_limit") or 0)
    candidate_limit_arg = candidate_limit if candidate_limit > 0 else None
    processed_pages = 0

    while processed_pages < target_pages:
        page_number = processed_pages + 1
        _log(callback, f"Start Maimai page {page_number}/{target_pages}.")
        page_candidates = None
        for extract_attempt in range(1, 4):
            try:
                page_candidates = _run_with_capture(
                    callback,
                    candidates.extract_current_page,
                    page_number,
                    candidate_limit_arg,
                    page,
                )
                break
            except Exception as exc:
                if exc.__class__.__name__ != "ContextLostError" or extract_attempt >= 3:
                    raise
                _log(
                    callback,
                    f"Maimai page {page_number}: page was still refreshing; "
                    f"retry extraction {extract_attempt}/3.",
                )
                time.sleep(1.5)
        current_count = len(page_candidates or [])
        if current_count <= 0:
            raise RuntimeError(f"Maimai page {page_number}: no candidates extracted.")

        last_match_result = matching.match_candidates(settings)
        matched = last_match_result.get("matched_candidates", [])
        _log(callback, f"Maimai page {page_number}: AI match complete, matched {len(matched)} candidates.")

        if matched:
            _run_with_capture(
                callback,
                communication.run_chat_flow_test,
                settings.greeting,
                settings.actual_send,
                page_number,
                page,
            )
        else:
            _log(callback, f"Maimai page {page_number}: no matched candidates, skip chat.")

        processed_pages += 1
        if processed_pages >= target_pages:
            break
        if not _run_with_capture(callback, candidates.goto_next_page, page):
            raise RuntimeError(f"Maimai page {page_number}: failed to go to next page.")

    if not settings.actual_send:
        _log(callback, "Maimai test mode: messages were not sent and follow-up was not started.")
    else:
        _log(callback, "Maimai configured pages completed; pipeline finished without message monitoring.")

    contacted_payload = contacts.load_contacted_candidates()
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

    if getattr(sys, "frozen", False):
        cmd = [
            sys.executable,
            "--maimai-worker",
            str(config_path),
            str(result_path),
        ]
    else:
        cmd = [
            sys.executable,
            "-m",
            "recruit_assistant.platforms.maimai.worker",
            str(config_path),
            str(result_path),
        ]
    env = os.environ.copy()
    package_src_dir = str(Path(__file__).resolve().parents[3])
    env["PYTHONPATH"] = os.pathsep.join(
        path for path in (package_src_dir, env.get("PYTHONPATH", "")) if path
    )

    worker_cwd = workspace_root()
    if not worker_cwd.exists():
        worker_cwd = Path(__file__).resolve().parents[3]

    _log(callback, f"Maimai worker started, timeout {timeout}s.")
    process = subprocess.Popen(
        cmd,
        cwd=str(worker_cwd),
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
