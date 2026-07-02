from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib import error, request

from DrissionPage import ChromiumOptions, ChromiumPage


SEARCH_URL = "https://lpt.liepin.com/search"
JOB_MANAGER_URL = "https://lpt.liepin.com/job/manager"
CHAT_URL = "https://lpt.liepin.com/chat/im"
DEFAULT_MATCH_REQUIREMENTS = """通用要求：
1. 候选人的过往经历必须和开聊职位方向相关，优先看最近 5 年工作经历。
2. 销售岗位优先要求有企业端销售、行业客户、大客户、渠道或团队管理经验。
3. 城市、薪资、行业方向明显不匹配时不要通过。
4. 关键信息缺失时保持谨慎，不要为了通过而脑补简历中没有的信息。
5. 只有明确值得发起沟通时才返回 match=true。"""
DEFAULT_BROWSER_PORT = 9225


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


APP_DIR = get_app_dir()
RUNTIME_DIR = APP_DIR / "runtime"


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
        current_host = re.sub(r"^www\\.", "", current.split("//", 1)[-1].split("/", 1)[0])
        target_host = re.sub(r"^www\\.", "", target.split("//", 1)[-1].split("/", 1)[0])
        return bool(current_host and target_host and current_host == target_host)
    except Exception:
        return current.startswith(target)

KEYWORDS_PLACEHOLDER = "\u641c\u804c\u4f4d/\u516c\u53f8/\u884c\u4e1a\u7b49\uff08\u4e2d\u6587\u7528\u7a7a\u683c\u9694\u5f00\uff0c\u82f1\u6587\u7528\u9017\u53f7\u9694\u5f00\uff09"
JOB_PLACEHOLDER = "\u641c\u7d22\u804c\u4f4d"
COMPANY_PLACEHOLDER = "\u641c\u7d22\u516c\u53f8"
CITY_SEARCH_PLACEHOLDER = "\u641c\u7d22\u57ce\u5e02"

CONFIRM_TEXT = "\u786e\u5b9a"
CITY_CONFIRM_TEXT = "\u786e\u8ba4"
OTHER_TEXT = "\u5176\u4ed6"
CURRENT_CITY_TITLE = "\u76ee\u524d\u57ce\u5e02"
EXPECTED_CITY_TITLE = "\u671f\u671b\u57ce\u5e02"
EXPERIENCE_TITLE = "\u7ecf\u9a8c"
EDUCATION_TITLE = "\u6559\u80b2\u7ecf\u5386"
RECRUITMENT_TYPE_TITLE = "\u7edf\u62db\u8981\u6c42"
SCHOOL_TYPE_TITLE = "\u9662\u6821\u8981\u6c42"
ACTIVE_STATUS_TITLE = "\u6d3b\u8dc3\u72b6\u6001"
JOB_STATUS_TITLE = "\u6c42\u804c\u72b6\u6001"
JOB_HOP_FREQUENCY_TITLE = "\u8df3\u69fd\u9891\u7387"
AGE_REQUIREMENT_TITLE = "\u5e74\u9f84\u8981\u6c42"
GENDER_REQUIREMENT_TITLE = "\u6027\u522b\u8981\u6c42"
LANGUAGE_REQUIREMENT_TITLE = "\u8bed\u8a00\u8981\u6c42"
GRADUATION_YEAR_TITLE = "\u6bd5\u4e1a\u5e74\u4efd"
CURRENT_INDUSTRY_TITLE = "\u5f53\u524d\u884c\u4e1a"
EXPECTED_INDUSTRY_TITLE = "\u671f\u671b\u884c\u4e1a"
INDUSTRY_MODAL_TITLE = "\u8bf7\u9009\u62e9\u884c\u4e1a"
INDUSTRY_SEARCH_PLACEHOLDER = "\u8bf7\u8f93\u5165\u884c\u4e1a\u5173\u952e\u8bcd"
CURRENT_FUNCTION_TITLE = "\u5f53\u524d\u804c\u80fd"
EXPECTED_FUNCTION_TITLE = "\u671f\u671b\u804c\u80fd"
FUNCTION_MODAL_TITLE = "\u8bf7\u9009\u62e9\u804c\u4f4d\u7c7b\u522b"
FUNCTION_SEARCH_PLACEHOLDER = "\u8bf7\u8f93\u5165\u804c\u4f4d\u540d\u79f0\u641c\u7d22"
MORE_CONDITIONS_TEXT = "\u66f4\u591a\u6761\u4ef6"
AI_FILL_TEXTS = ("\u586b\u5165", "\u63d2\u5165", "\u586b\u5165\u590d\u5408\u5173\u952e\u8bcd")


@dataclass
class SearchFilters:
    selected_chat_job: dict | None = None
    match_requirements: str = ""
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    auto_communicate: bool = True
    request_resume_after_communicate: bool = True
    request_phone_after_communicate: bool = False
    candidate_limit: int = 1
    keywords: str = ""
    job_name: str = ""
    company_name: str = ""
    current_city: str = ""
    expected_city: str = ""
    experience: str = ""
    education: str = ""
    recruitment_type: str = ""
    school_types: str = ""
    active_status: str = ""
    job_status: str = ""
    job_hop_frequency: str = ""
    age_requirement: str = ""
    gender_requirement: str = ""
    language_requirement: str = ""
    graduation_year: str = ""
    current_industries: str = ""
    expected_industries: str = ""
    current_functions: str = ""
    expected_functions: str = ""
    use_keywords_ai_words: bool = False
    use_job_ai_words: bool = False
    use_company_ai_words: bool = False


class BatchProgress:
    def __init__(self, callback=None, stop_event=None) -> None:
        self.callback = callback
        self.stop_event = stop_event

    def check_stopped(self) -> None:
        if self.stop_event is not None and self.stop_event.is_set():
            raise RuntimeError("任务已停止。")

    def emit(self, event: str, message: str, data: dict | None = None) -> None:
        self.check_stopped()
        if self.callback:
            self.callback({"event": event, "message": message, "data": data or {}})


class LiepinSearchPage:
    """DrissionPage helpers for the Liepin search page.

    The methods use DOM anchors such as placeholder text, visible modal text,
    row titles, and button labels. No screen-coordinate clicking is used.
    """

    def __init__(self, port: int = DEFAULT_BROWSER_PORT, progress_callback=None, stop_event=None) -> None:
        self.page = connect_chromium_page(search_url=SEARCH_URL, port=port)
        self.progress = BatchProgress(progress_callback, stop_event)

    def check_stopped(self) -> None:
        self.progress.check_stopped()

    def open(self) -> None:
        self.check_stopped()
        self.page.get(SEARCH_URL)
        self.wait_for_input(KEYWORDS_PLACEHOLDER)
        self.wait_for_input(JOB_PLACEHOLDER)
        self.wait_for_input(COMPANY_PLACEHOLDER)

    def fetch_job_list(self) -> list[dict]:
        self.check_stopped()
        self.page.get(JOB_MANAGER_URL)
        self.wait_for_job_cards()
        max_page = self.get_job_manager_page_count()
        jobs: list[dict] = []
        seen: set[str] = set()

        for page_number in range(1, max_page + 1):
            self.check_stopped()
            self.go_to_job_manager_page(page_number)
            for job in self.extract_current_job_cards():
                key = job.get("job_id") or job.get("href") or job.get("title")
                if key and key not in seen:
                    seen.add(key)
                    jobs.append(job)
        self.save_job_list(jobs)
        return jobs

    def wait_for_job_cards(self, timeout: int = 15) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            loaded = self.page.run_js(
                """
                const body = document.body ? document.body.innerText || '' : '';
                return document.querySelectorAll('a[class*=jobTitle]').length > 0
                  || body.includes('暂无数据')
                  || body.includes('暂无职位');
                """
            )
            if loaded:
                return
            time.sleep(0.4)
        raise RuntimeError("Job manager list did not load.")

    def get_job_manager_page_count(self) -> int:
        count = self.page.run_js(
            """
            const nums = Array.from(document.querySelectorAll('li[class*=pagination-item]'))
              .map(ele => Number(ele.getAttribute('title') || ele.innerText || ''))
              .filter(Number.isFinite);
            return nums.length ? Math.max(...nums) : 1;
            """
        )
        return max(int(count or 1), 1)

    def go_to_job_manager_page(self, page_number: int) -> None:
        result = self.page.run_js(
            """
            const pageNumber = String(arguments[0]);
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0
                && rect.height > 0
                && style.display !== 'none'
                && style.visibility !== 'hidden';
            };
            const current = document.querySelector('li[class*=pagination-item-active]');
            if (current && (current.getAttribute('title') || current.innerText || '').trim() === pageNumber) {
              return {ok: true, already: true};
            }
            const item = Array.from(document.querySelectorAll('li[class*=pagination-item]'))
              .find(ele => visible(ele) && (ele.getAttribute('title') || ele.innerText || '').trim() === pageNumber);
            if (!item) return {ok: false, reason: 'pagination item not found'};
            item.scrollIntoView({block: 'center', inline: 'nearest'});
            for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
              item.dispatchEvent(new MouseEvent(name, {
                bubbles: true,
                cancelable: true,
                composed: true,
                view: window,
              }));
            }
            return {ok: true};
            """,
            page_number,
        )
        if not result or not result.get("ok"):
            raise RuntimeError(f"Could not open job manager page {page_number}: {result}")

        deadline = time.time() + 10
        while time.time() < deadline:
            active = self.page.run_js(
                """
                const item = document.querySelector('li[class*=pagination-item-active]');
                return item ? (item.getAttribute('title') || item.innerText || '').trim() : '';
                """
            )
            if str(active) == str(page_number):
                self.wait_for_job_cards()
                return
            time.sleep(0.3)
        raise RuntimeError(f"Job manager page {page_number} did not become active.")

    def extract_current_job_cards(self) -> list[dict]:
        jobs = self.page.run_js(
            """
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0
                && rect.height > 0
                && style.display !== 'none'
                && style.visibility !== 'hidden';
            };
            const cleanText = value => String(value || '')
              .replace(/\\u00a0/g, ' ')
              .replace(/[ \\t]+/g, ' ')
              .trim();
            const getJobId = href => {
              try {
                const url = new URL(href, location.href);
                return url.searchParams.get('ejob_id') || url.searchParams.get('job_id') || '';
              } catch {
                return '';
              }
            };
            const findCard = link => {
              let node = link;
              for (let depth = 0; node && depth < 8; depth += 1, node = node.parentElement) {
                const cls = String(node.className || '');
                const text = node.innerText || '';
                if (cls.includes('jobCardWrap') || (text.includes('沟通中') && text.includes('待看/收到简历'))) {
                  return node;
                }
              }
              return link.parentElement;
            };
            return Array.from(document.querySelectorAll('a[class*=jobTitle]'))
              .filter(visible)
              .map(link => {
                const card = findCard(link);
                const titleInfo = card && card.querySelector('[class*=jobTitleInfo]');
                const infoLines = ((titleInfo || card || link).innerText || '')
                  .split('\\n')
                  .map(cleanText)
                  .filter(Boolean);
                const cardLines = ((card || link).innerText || '')
                  .split('\\n')
                  .map(cleanText)
                  .filter(Boolean);
                const title = cleanText(link.getAttribute('title') || link.innerText || link.textContent);
                const titleIndex = infoLines.indexOf(title);
                const afterTitle = titleIndex >= 0 ? infoLines.slice(titleIndex + 1) : infoLines.slice(1);
                const href = link.href || '';
                const communicateIndex = cardLines.indexOf('沟通中');
                const receivedIndex = cardLines.indexOf('待看/收到简历');
                const label = [title, afterTitle[0], afterTitle[1]]
                  .filter(Boolean)
                  .join(' | ');
                return {
                  title,
                  label,
                  city: afterTitle[0] || '',
                  salary: afterTitle[1] || '',
                  refreshed_at: afterTitle[2] || '',
                  communicate_count: communicateIndex > 0 ? cardLines[communicateIndex - 1] : '',
                  resume_count: receivedIndex > 1 ? `${cardLines[receivedIndex - 2] || ''}${cardLines[receivedIndex - 1] || ''}` : '',
                  job_id: getJobId(href),
                  href,
                  raw_lines: cardLines,
                };
              })
              .filter(job => job.title);
            """
        )
        return jobs or []

    def save_job_list(self, jobs: list[dict], path: str = "liepin_jobs.json") -> None:
        output_path = Path(path)
        if not output_path.is_absolute():
            output_path = RUNTIME_DIR / output_path
        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(jobs, file, ensure_ascii=False, indent=2)

    def apply_filters(self, filters: SearchFilters) -> dict:
        self.check_stopped()
        if filters.job_name:
            self.fill_filter_input(JOB_PLACEHOLDER, filters.job_name)
            if filters.use_job_ai_words:
                self.insert_ai_words_for_input(JOB_PLACEHOLDER)

        if filters.company_name:
            self.fill_filter_input(COMPANY_PLACEHOLDER, filters.company_name)
            if filters.use_company_ai_words:
                self.insert_ai_words_for_input(COMPANY_PLACEHOLDER)

        if filters.current_city:
            self.select_city(CURRENT_CITY_TITLE, filters.current_city)

        if filters.expected_city:
            self.select_city(EXPECTED_CITY_TITLE, filters.expected_city)

        if filters.experience:
            self.click_row_option(EXPERIENCE_TITLE, filters.experience)

        if filters.education:
            for education in self.split_multi_values(filters.education):
                self.click_row_option(EDUCATION_TITLE, education)
        if filters.recruitment_type:
            self.select_dropdown_option(RECRUITMENT_TYPE_TITLE, filters.recruitment_type)
        if filters.school_types:
            for school_type in self.split_multi_values(filters.school_types):
                self.select_dropdown_option(SCHOOL_TYPE_TITLE, school_type, keep_open=True)
            self.close_open_dropdown()

        dropdown_filters = [
            (ACTIVE_STATUS_TITLE, filters.active_status),
            (JOB_STATUS_TITLE, filters.job_status),
            (JOB_HOP_FREQUENCY_TITLE, filters.job_hop_frequency),
            (AGE_REQUIREMENT_TITLE, filters.age_requirement),
            (GENDER_REQUIREMENT_TITLE, filters.gender_requirement),
            (LANGUAGE_REQUIREMENT_TITLE, filters.language_requirement),
            (GRADUATION_YEAR_TITLE, filters.graduation_year),
        ]
        industry_filters = [
            (CURRENT_INDUSTRY_TITLE, filters.current_industries),
            (EXPECTED_INDUSTRY_TITLE, filters.expected_industries),
        ]
        function_filters = [
            (CURRENT_FUNCTION_TITLE, filters.current_functions),
            (EXPECTED_FUNCTION_TITLE, filters.expected_functions),
        ]
        if any(value for _title, value in dropdown_filters + industry_filters + function_filters):
            self.ensure_more_conditions_expanded()
        for title, value in dropdown_filters:
            self.check_stopped()
            if value:
                self.select_dropdown_option(title, value)
        for title, value in industry_filters:
            self.check_stopped()
            if value:
                self.select_industry_modal(title, value)
        for title, value in function_filters:
            self.check_stopped()
            if value:
                self.select_function_modal(title, value)

        if filters.keywords:
            self.fill_filter_input(KEYWORDS_PLACEHOLDER, filters.keywords)
            if filters.use_keywords_ai_words:
                self.insert_ai_words_for_input(KEYWORDS_PLACEHOLDER)

        self.click_search_button()
        self.check_stopped()
        self.open_first_candidate()
        return self.process_candidate_batch(filters)

    def process_candidate_batch(self, filters: SearchFilters) -> dict:
        limit = max(int(filters.candidate_limit or 1), 1)
        self.check_stopped()
        self.ensure_candidate_detail_open()
        results: list[dict] = []
        should_request_contacts = filters.request_resume_after_communicate or filters.request_phone_after_communicate
        for index in range(limit):
            self.check_stopped()
            self.progress.emit("candidate_start", f"开始处理第 {index + 1}/{limit} 个候选人")
            profile = self.save_candidate_profile(
                {
                    "selected_chat_job": filters.selected_chat_job,
                    "batch_index": index + 1,
                    "batch_limit": limit,
                }
            )
            self.progress.emit(
                "candidate_profile",
                (
                    f"第 {index + 1}/{limit} 个：{profile.get('basic', {}).get('name', '')}，"
                    f"{profile.get('job_intention', {}).get('position', '')}，"
                    f"{profile.get('basic', {}).get('location', '')}"
                ),
                {
                    "index": index + 1,
                    "name": profile.get("basic", {}).get("name", ""),
                    "job_position": profile.get("job_intention", {}).get("position", ""),
                    "location": profile.get("basic", {}).get("location", ""),
                },
            )
            decision = self.decide_candidate_match(profile, filters)
            time.sleep(0.6)
            profile["ai_match"] = decision
            self.append_batch_candidate(profile)
            self.progress.emit(
                "ai_decision",
                (
                    f"第 {index + 1}/{limit} 个AI结果："
                    f"{'匹配' if decision.get('match') else '不匹配'}，"
                    f"{decision.get('score', 0)}分，{decision.get('reason', '')}"
                ),
                decision,
            )

            if decision.get("match") and filters.auto_communicate:
                self.progress.emit("communicate_start", f"第 {index + 1}/{limit} 个匹配，开始点击立即沟通")
                try:
                    communicate_result = self.auto_open_communicate(filters.selected_chat_job)
                    decision["communicate_status"] = communicate_result.get("status", "done")
                    if communicate_result.get("status") == "already_communicated":
                        decision["communicate_note"] = "页面显示继续沟通，说明此前已沟通过，本次跳过开聊。"
                        self.progress.emit(
                            "communicate_done",
                            f"第 {index + 1}/{limit} 个已沟通过，页面显示继续沟通，本次跳过开聊",
                        )
                    else:
                        self.progress.emit("communicate_done", f"第 {index + 1}/{limit} 个已完成职位选择和确认")
                except Exception as exc:
                    decision["communicate_status"] = "failed"
                    decision["communicate_error"] = str(exc)
                    self.progress.emit(
                        "communicate_failed",
                        f"第 {index + 1}/{limit} 个沟通失败：{exc}",
                        {"error": str(exc)},
                    )

            result_item = {
                "index": index + 1,
                "name": profile.get("basic", {}).get("name", ""),
                "location": profile.get("basic", {}).get("location", ""),
                "job_position": profile.get("job_intention", {}).get("position", ""),
                "job_cities": profile.get("job_intention", {}).get("cities", ""),
                "match": bool(decision.get("match")),
                "score": decision.get("score", 0),
                "decision": decision.get("decision", ""),
                "reason": decision.get("communicate_error") or decision.get("reason", ""),
                "communicate_note": decision.get("communicate_note", ""),
                "communicate_status": decision.get("communicate_status", ""),
                "strengths": decision.get("strengths", []),
                "risks": decision.get("risks", []),
                "must_have_result": decision.get("must_have_result", []),
            }
            if decision.get("communicate_status") == "done" and should_request_contacts:
                contact_results = self.request_contacts_from_continue_chat(
                    index=index + 1,
                    request_resume=filters.request_resume_after_communicate,
                    request_phone=filters.request_phone_after_communicate,
                )
                if filters.request_resume_after_communicate:
                    resume = contact_results.get("resume") or {}
                    result_item["resume_request_status"] = resume.get("status", "unknown")
                    result_item["resume_request_note"] = resume.get("message", "")
                if filters.request_phone_after_communicate:
                    phone = contact_results.get("phone") or {}
                    result_item["phone_request_status"] = phone.get("status", "unknown")
                    result_item["phone_request_note"] = phone.get("message", "")
            elif decision.get("communicate_status") == "already_communicated":
                self.progress.emit(
                    "resume_request_skip",
                    f"第 {index + 1}/{limit} 个此前已沟通过，本轮不索要联系方式",
                    {"index": index + 1, "status": "already_communicated"},
                )
            results.append(result_item)
            self.progress.emit("candidate_result", f"第 {index + 1}/{limit} 个已写入列表", result_item)
            self.save_batch_summary(results)

            if index < limit - 1:
                self.progress.emit("next_candidate", f"切换到第 {index + 2}/{limit} 个候选人")
                self.go_to_next_candidate_for_batch()

        summary = {
            "processed": len(results),
            "matched": sum(1 for item in results if item.get("match")),
            "results": results,
        }
        self.save_batch_summary(results)
        return summary

    def save_batch_summary(self, results: list[dict]) -> None:
        summary = {
            "processed": len(results),
            "matched": sum(1 for item in results if item.get("match")),
            "results": results,
        }
        summary_path = RUNTIME_DIR / "candidate_batch_summary.json"
        with open(summary_path, "w", encoding="utf-8") as file:
            json.dump(summary, file, ensure_ascii=False, indent=2)

    def request_contacts_after_batch(
        self,
        targets: list[dict],
        results: list[dict],
        started_at: datetime,
        request_resume: bool = True,
        request_phone: bool = False,
    ) -> None:
        actions = []
        if request_phone:
            actions.append("phone")
        if request_resume:
            actions.append("resume")
        if not actions:
            return

        start_minute = started_at.hour * 60 + started_at.minute
        self.progress.emit(
            "resume_request_start",
            f"开始进入消息页，处理 {started_at.strftime('%H:%M')} 之后的新会话：{len(targets)} 个",
        )
        self.page.get(CHAT_URL)
        self.wait_for_chat_page()
        self.reset_chat_list_scroll()

        cards = self.collect_recent_chat_cards(start_minute, max_count=len(targets))
        processed_count = 0
        for opened in cards[: len(targets)]:
            clicked = self.open_chat_card_by_signature(opened.get("signature", ""))
            if not clicked.get("ok"):
                self.progress.emit(
                    "resume_request_done",
                    f"消息卡片打开失败，跳过：{opened.get('time', '')} {opened.get('title', '')}",
                    {"card": opened, "error": clicked},
                )
                continue

            target = targets[processed_count]
            index = target.get("index")
            self.progress.emit(
                "resume_request",
                f"正在处理第 {index} 个已沟通会话：{opened.get('time', '')} {opened.get('title', '')}",
                {"index": index, "card": opened},
            )
            time.sleep(0.8)

            action_results: dict[str, dict] = {}
            for action in actions:
                try:
                    action_results[action] = self.request_chat_action_in_current_chat(action)
                except Exception as exc:
                    action_results[action] = {"status": "failed", "message": str(exc)}

            for item in results:
                if item.get("index") == index:
                    if request_resume:
                        resume = action_results.get("resume") or {}
                        item["resume_request_status"] = resume.get("status", "unknown")
                        item["resume_request_note"] = resume.get("message", "")
                    if request_phone:
                        phone = action_results.get("phone") or {}
                        item["phone_request_status"] = phone.get("status", "unknown")
                        item["phone_request_note"] = phone.get("message", "")
                    break

            message_parts = []
            if request_phone:
                phone = action_results.get("phone") or {}
                message_parts.append(f"电话：{self.contact_status_text(phone.get('status', 'unknown'))}")
            if request_resume:
                resume = action_results.get("resume") or {}
                message_parts.append(f"简历：{self.contact_status_text(resume.get('status', 'unknown'))}")
            self.progress.emit(
                "resume_request_done",
                f"第 {index} 个会话处理结果：{'；'.join(message_parts)}",
                {"index": index, "actions": action_results},
            )
            self.save_batch_summary(results)
            processed_count += 1

        if processed_count < len(targets):
            self.progress.emit(
                "resume_request_done",
                f"消息页只处理到 {processed_count}/{len(targets)} 个本轮开始后的会话，其余未处理",
                {"processed": processed_count, "expected": len(targets), "cards": cards},
            )

    def request_contacts_from_continue_chat(
        self,
        index: int,
        request_resume: bool = True,
        request_phone: bool = False,
    ) -> dict[str, dict]:
        actions = []
        if request_phone:
            actions.append("phone")
        if request_resume:
            actions.append("resume")
        if not actions:
            return {}

        self.progress.emit("resume_request_start", f"第 {index} 个沟通成功，打开继续沟通小窗索要联系方式")
        action_results: dict[str, dict] = {}
        try:
            opened = self.open_continue_chat_panel()
            if not opened.get("ok"):
                raise RuntimeError(str(opened))
            time.sleep(0.8)
            for action in actions:
                try:
                    action_results[action] = self.request_chat_action_in_current_chat(action)
                except Exception as exc:
                    action_results[action] = {"status": "failed", "message": str(exc)}
                if (action_results.get(action) or {}).get("status") == "failed":
                    self.dismiss_chat_confirm_overlay()
            return action_results
        except Exception as exc:
            failed = {"status": "failed", "message": str(exc)}
            for action in actions:
                action_results.setdefault(action, failed)
            return action_results
        finally:
            closed = self.close_continue_chat_panel()
            message_parts = []
            if request_phone:
                phone = action_results.get("phone") or {}
                message_parts.append(f"电话：{self.contact_status_text(phone.get('status', 'unknown'))}")
            if request_resume:
                resume = action_results.get("resume") or {}
                message_parts.append(f"简历：{self.contact_status_text(resume.get('status', 'unknown'))}")
            close_note = "" if closed.get("ok") else f"；关闭小窗失败：{closed.get('reason', closed)}"
            self.progress.emit(
                "resume_request_done",
                f"第 {index} 个小窗处理结果：{'；'.join(message_parts)}{close_note}",
                {"index": index, "actions": action_results, "close": closed},
            )

    def dismiss_chat_confirm_overlay(self) -> None:
        try:
            self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const textOf = ele => clean(ele.innerText || ele.textContent);
                const compact = value => clean(value).replace(/\\s+/g, '');
                const scopes = Array.from(document.querySelectorAll('.ant-im-modal, .ant-lpt-modal, [role=dialog], [class*=modal], .ant-im-popover, .ant-popover, [class*=popover], [class*=Popconfirm]'))
                  .filter(visible)
                  .filter(ele => /确定|确认|索要|获取/.test(textOf(ele)));
                for (const scope of scopes) {
                  const cancel = Array.from(scope.querySelectorAll('button, [role=button], a, span, div'))
                    .filter(visible)
                    .find(ele => /^(取消|关闭|×|x|X)$/.test(compact(textOf(ele))));
                  if (cancel) {
                    for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                      cancel.dispatchEvent(new MouseEvent(name, {bubbles: true, cancelable: true, composed: true, view: window}));
                    }
                    return {ok: true, clicked: textOf(cancel)};
                  }
                }
                document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape', bubbles: true, cancelable: true}));
                document.dispatchEvent(new KeyboardEvent('keyup', {key: 'Escape', code: 'Escape', bubbles: true, cancelable: true}));
                return {ok: true, escaped: true};
                """
            )
            time.sleep(0.4)
        except Exception:
            pass

    def open_continue_chat_panel(self, timeout: int = 12) -> dict:
        deadline = time.time() + timeout
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && !ele.disabled;
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const textOf = ele => clean(ele.innerText || ele.textContent);
                const drawerLeft = () => {
                  const drawer = Array.from(document.querySelectorAll('.ant-im-drawer-content-wrapper, .ant-im-drawer'))
                    .filter(visible)
                    .map(ele => ele.getBoundingClientRect())
                    .filter(rect => rect.width > 0 && rect.left > window.innerWidth * 0.55)
                    .sort((a, b) => a.left - b.left)[0];
                  return drawer ? drawer.left : window.innerWidth + 1;
                };
                const currentResumeActionPanel = () => {
                  const rightLimit = drawerLeft();
                  const candidates = Array.from(document.querySelectorAll('.xpath-wrap-resume-download, [class*=operation]'))
                    .filter(visible)
                    .filter(ele => {
                      const rect = ele.getBoundingClientRect();
                      const text = textOf(ele);
                      return text.includes('觉得TA还不错')
                        && rect.left > window.innerWidth * 0.45
                        && rect.right < rightLimit - 8
                        && rect.width >= 180
                        && rect.width <= 420
                        && rect.height >= 80;
                    })
                    .sort((a, b) => {
                      const ac = String(a.className || '');
                      const bc = String(b.className || '');
                      const aDirect = ac.includes('xpath-wrap-resume-download') ? 0 : 1;
                      const bDirect = bc.includes('xpath-wrap-resume-download') ? 0 : 1;
                      if (aDirect !== bDirect) return aDirect - bDirect;
                      return (a.getBoundingClientRect().height * a.getBoundingClientRect().width)
                        - (b.getBoundingClientRect().height * b.getBoundingClientRect().width);
                    });
                  return candidates[0] || null;
                };
                const chatPanel = Array.from(document.querySelectorAll('.chatwin-action'))
                  .filter(visible)
                  .map(ele => {
                    const direct = ele.closest('.im-ui-basic-chat-modal, .ant-im-modal');
                    if (direct && visible(direct)) return direct;
                    let panel = ele;
                    for (let depth = 0; panel && depth < 8; depth += 1, panel = panel.parentElement) {
                      const text = textOf(panel);
                      const rect = panel.getBoundingClientRect();
                      if (text.includes('沟通职位') && rect.width >= 420 && rect.height >= 360) {
                        return panel;
                      }
                    }
                    return null;
                  })
                  .filter(Boolean)[0];
                if (chatPanel) return {ok: true, already_open: true};

                const clickableOf = ele => {
                  let node = ele;
                  for (let depth = 0; node && depth < 8; depth += 1, node = node.parentElement) {
                    const tag = node.tagName;
                    const role = node.getAttribute('role') || '';
                    const cls = String(node.className || '');
                    const style = getComputedStyle(node);
                    if (tag === 'BUTTON' || tag === 'A' || role === 'button' || cls.includes('btn') || cls.includes('Btn') || cls.includes('button') || cls.includes('Button') || style.cursor === 'pointer') {
                      return node;
                    }
                  }
                  return ele;
                };
                const panel = currentResumeActionPanel();
                if (!panel) return {ok: false, reason: '当前简历右侧操作卡片未找到'};
                const candidates = Array.from(panel.querySelectorAll('*'))
                  .filter(visible)
                  .map(ele => ({source: ele, target: clickableOf(ele), text: textOf(ele)}))
                  .filter(item => item.text === '继续沟通' || item.text.includes('继续沟通'))
                  .filter(item => visible(item.target) && panel.contains(item.target));
                candidates.sort((a, b) => {
                  const aExact = a.text === '继续沟通' ? 0 : 1;
                  const bExact = b.text === '继续沟通' ? 0 : 1;
                  if (aExact !== bExact) return aExact - bExact;
                  const aOpenIm = String(a.target.className || '').includes('xpath-open-im-btn') ? 0 : 1;
                  const bOpenIm = String(b.target.className || '').includes('xpath-open-im-btn') ? 0 : 1;
                  if (aOpenIm !== bOpenIm) return aOpenIm - bOpenIm;
                  const aButton = a.target.tagName === 'BUTTON' ? 0 : 1;
                  const bButton = b.target.tagName === 'BUTTON' ? 0 : 1;
                  if (aButton !== bButton) return aButton - bButton;
                  const ar = a.target.getBoundingClientRect();
                  const br = b.target.getBoundingClientRect();
                  return bRectLeft(br) - bRectLeft(ar);
                });
                function bRectLeft(rect) { return rect.left; }
                const button = candidates[0] && candidates[0].target;
                if (!button) return {ok: false, reason: '继续沟通按钮未找到'};
                button.scrollIntoView({block: 'center', inline: 'nearest'});
                for (const name of ['mouseover', 'pointerover', 'pointerenter', 'mouseenter', 'pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  button.dispatchEvent(new MouseEvent(name, {bubbles: true, cancelable: true, composed: true, view: window}));
                }
                return {ok: false, clicked: true, reason: '已点击继续沟通，等待小窗打开'};
                """
            )
            if last_result and last_result.get("ok"):
                return last_result
            time.sleep(0.4)
        return {"ok": False, "reason": str(last_result or "继续沟通小窗未打开")}

    def close_continue_chat_panel(self, timeout: int = 5) -> dict:
        deadline = time.time() + timeout
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const textOf = ele => clean(ele.innerText || ele.textContent);
                const panels = Array.from(document.querySelectorAll('.chatwin-action'))
                  .filter(visible)
                  .map(ele => {
                    const direct = ele.closest('.im-ui-basic-chat-modal, .ant-im-modal');
                    if (direct && visible(direct)) return direct;
                    let panel = ele;
                    for (let depth = 0; panel && depth < 8; depth += 1, panel = panel.parentElement) {
                      const text = textOf(panel);
                      const rect = panel.getBoundingClientRect();
                      if (text.includes('沟通职位') && rect.width >= 420 && rect.height >= 360) {
                        return panel;
                      }
                    }
                    return null;
                  })
                  .filter(Boolean);
                const panel = panels[0];
                if (!panel) return {ok: true, already_closed: true};
                const panelRect = panel.getBoundingClientRect();
                const closeCandidates = Array.from(panel.querySelectorAll('button, [role=button], i, svg, span, div'))
                  .filter(visible)
                  .map(ele => ({ele, text: textOf(ele), rect: ele.getBoundingClientRect(), cls: String(ele.className || '')}))
                  .filter(item => {
                    const aria = item.ele.getAttribute('aria-label') || item.ele.getAttribute('title') || '';
                    const nearTopRight = item.rect.top <= panelRect.top + 90 && item.rect.left >= panelRect.right - 90;
                    return nearTopRight && (
                      item.text === '×'
                      || item.text === 'x'
                      || item.text === 'X'
                      || aria.includes('关闭')
                      || aria.toLowerCase().includes('close')
                      || item.cls.includes('close')
                      || item.cls.includes('Close')
                    );
                  })
                  .sort((a, b) => {
                    const ar = a.rect;
                    const br = b.rect;
                    const aScore = Math.abs(ar.right - panelRect.right) + Math.abs(ar.top - panelRect.top);
                    const bScore = Math.abs(br.right - panelRect.right) + Math.abs(br.top - panelRect.top);
                    return aScore - bScore;
                  });
                let close = closeCandidates[0] && closeCandidates[0].ele;
                if (!close) {
                  close = document.elementFromPoint(panelRect.right - 36, panelRect.top + 36);
                }
                if (!close) return {ok: false, reason: '关闭按钮未找到'};
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  close.dispatchEvent(new MouseEvent(name, {bubbles: true, cancelable: true, composed: true, view: window}));
                }
                return {ok: false, clicked: true, reason: '已点击关闭小窗'};
                """
            )
            if last_result and last_result.get("ok"):
                return last_result
            time.sleep(0.3)
        return {"ok": False, "reason": str(last_result or "关闭小窗失败")}

    def collect_recent_chat_cards(self, start_minute: int, max_count: int) -> list[dict]:
        return self.page.run_js(
            """
            const startMinute = Number(arguments[0] || 0);
            const maxCount = Number(arguments[1] || 1);
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
            const minuteOf = text => {
              const m = String(text || '').match(/(^|[^0-9])([01]?[0-9]|2[0-3]):([0-5][0-9])([^0-9]|$)/);
              if (!m) return null;
              return Number(m[2]) * 60 + Number(m[3]);
            };
            const ignoredTexts = ['求职者投递', '批量处理', '昨日您主动沟通', '全部职位', '消息筛选'];
            const badCardText = text => ignoredTexts.some(item => text.includes(item));
            const cards = [];
            const seen = new Set();
            const readVisibleCards = () => {
              for (const ele of Array.from(document.querySelectorAll('.im-ui-contact-list-item')).filter(visible)) {
                const text = clean(ele.innerText || ele.textContent);
                const minute = minuteOf(text);
                if (minute === null || minute < startMinute || badCardText(text)) continue;
                const time = (text.match(/([01]?[0-9]|2[0-3]):([0-5][0-9])/) || [''])[0];
                const title = clean(text.split(time)[0] || text).slice(0, 40);
                const signature = `${time}|${title}`;
                if (seen.has(signature)) continue;
                seen.add(signature);
                cards.push({ok: true, signature, time, title, text: text.slice(0, 160), minute});
              }
            };
            readVisibleCards();
            const scrollBox = Array.from(document.querySelectorAll('.im-ui-contacts-wrap, aside *, *'))
              .filter(visible)
              .filter(ele => {
                const rect = ele.getBoundingClientRect();
                const style = getComputedStyle(ele);
                return rect.left < Math.min(520, window.innerWidth * 0.45)
                  && rect.width >= 250
                  && rect.width <= 360
                  && rect.height > 240
                  && ele.scrollHeight > ele.clientHeight + 20
                  && /(auto|scroll)/.test(style.overflowY);
              })
              .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))[0];
            let guard = 0;
            while (scrollBox && cards.length < maxCount && guard < 12) {
              const before = scrollBox.scrollTop;
              scrollBox.scrollTop += Math.max(160, scrollBox.clientHeight * 0.75);
              readVisibleCards();
              guard += 1;
              if (scrollBox.scrollTop === before || scrollBox.scrollTop + scrollBox.clientHeight >= scrollBox.scrollHeight - 8) break;
            }
            return cards.sort((a, b) => b.minute - a.minute);
            """,
            start_minute,
            max_count,
        ) or []

    def open_chat_card_by_signature(self, signature: str) -> dict:
        return self.page.run_js(
            """
            const signature = String(arguments[0] || '');
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
            const signatureOf = ele => {
              const text = clean(ele.innerText || ele.textContent);
              const time = (text.match(/([01]?[0-9]|2[0-3]):([0-5][0-9])/) || [''])[0];
              const title = clean(text.split(time)[0] || text).slice(0, 40);
              return `${time}|${title}`;
            };
            const findCard = () => Array.from(document.querySelectorAll('.im-ui-contact-list-item'))
              .filter(visible)
              .find(ele => signatureOf(ele) === signature);
            let card = findCard();
            if (!card) {
              const scrollBox = Array.from(document.querySelectorAll('.im-ui-contacts-wrap, aside *, *'))
                .filter(visible)
                .filter(ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.left < Math.min(520, window.innerWidth * 0.45)
                    && rect.width >= 250
                    && rect.width <= 360
                    && rect.height > 240
                    && ele.scrollHeight > ele.clientHeight + 20
                    && /(auto|scroll)/.test(style.overflowY);
                })
                .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))[0];
              if (scrollBox) {
                scrollBox.scrollTop = 0;
                for (let i = 0; i < 18 && !card; i += 1) {
                  card = findCard();
                  if (card) break;
                  scrollBox.scrollTop += Math.max(160, scrollBox.clientHeight * 0.75);
                }
              }
            }
            if (!card) return {ok: false, reason: 'card not found', signature};
            card.scrollIntoView({block: 'center', inline: 'nearest'});
            for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
              card.dispatchEvent(new MouseEvent(name, {bubbles: true, cancelable: true, composed: true, view: window}));
            }
            return {ok: true, signature};
            """,
            signature,
        ) or {"ok": False, "reason": "open card script failed"}

    def open_next_recent_chat_card(self, start_minute: int, processed_signatures: set[str]) -> dict:
        processed_json = json.dumps(list(processed_signatures), ensure_ascii=False)
        return self.page.run_js(
            """
            const startMinute = Number(arguments[0] || 0);
            let processedItems = [];
            try {
              processedItems = JSON.parse(arguments[1] || '[]');
            } catch {
              processedItems = [];
            }
            const processed = new Set(processedItems);
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
            const minuteOf = text => {
              const m = String(text || '').match(/(^|[^0-9])([01]?[0-9]|2[0-3]):([0-5][0-9])([^0-9]|$)/);
              if (!m) return null;
              return Number(m[2]) * 60 + Number(m[3]);
            };
            const inLeftArea = ele => {
              const rect = ele.getBoundingClientRect();
              const cls = String(ele.className || '');
              if (cls.includes('im-ui-contact-list-item') || cls.includes('im-ui-contact-info')) {
                return rect.left >= 150 && rect.left < Math.min(520, window.innerWidth * 0.45)
                  && rect.width >= 220
                  && rect.width <= 340
                  && rect.height >= 55
                  && rect.height <= 95;
              }
              return rect.left < Math.min(620, window.innerWidth * 0.48)
                && rect.width >= 180
                && rect.width <= Math.max(680, window.innerWidth * 0.55)
                && rect.height >= 36
                && rect.height <= 180;
            };
            const ignoredTexts = ['求职者投递', '批量处理', '昨日您主动沟通', '全部职位', '消息筛选'];
            const badCardText = text => ignoredTexts.some(item => text.includes(item));
            const primaryCards = Array.from(document.querySelectorAll('.im-ui-contact-list-item'))
              .filter(visible)
              .map(ele => {
                const text = clean(ele.innerText || ele.textContent);
                const minute = minuteOf(text);
                const rect = ele.getBoundingClientRect();
                const time = (text.match(/([01]?[0-9]|2[0-3]):([0-5][0-9])/) || [''])[0];
                const title = clean(text.split(time)[0] || text).slice(0, 40);
                const signature = `${time}|${title}`;
                return {ele, text, minute, time, title, signature, top: rect.top};
              })
              .filter(item => item.minute !== null)
              .filter(item => item.minute >= startMinute)
              .filter(item => !badCardText(item.text))
              .filter(item => !processed.has(item.signature))
              .sort((a, b) => b.minute - a.minute || a.top - b.top);
            const primaryCard = primaryCards[0];
            if (primaryCard) {
              primaryCard.ele.scrollIntoView({block: 'center', inline: 'nearest'});
              for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                primaryCard.ele.dispatchEvent(new MouseEvent(name, {
                  bubbles: true,
                  cancelable: true,
                  composed: true,
                  view: window,
                }));
              }
              return {ok: true, signature: primaryCard.signature, time: primaryCard.time, title: primaryCard.title, text: primaryCard.text.slice(0, 160)};
            }
            if (document.querySelectorAll('.im-ui-contact-list-item').length > 0) {
              return {
                ok: false,
                reason: 'no recent chat card found',
                debug: {
                  startMinute,
                  processed: Array.from(processed),
                  total: document.querySelectorAll('.im-ui-contact-list-item').length,
                  samples: Array.from(document.querySelectorAll('.im-ui-contact-list-item')).slice(0, 8).map(ele => {
                    const text = clean(ele.innerText || ele.textContent);
                    return {text: text.slice(0, 120), minute: minuteOf(text), ignored: badCardText(text)};
                  }),
                },
              };
            }
            const resolveCard = ele => {
              const direct = ele.closest('.im-ui-contact-list-item, .im-ui-contact-info');
              if (direct) return direct;
              for (let node = ele; node && node !== document.body; node = node.parentElement) {
                const rect = node.getBoundingClientRect();
                const cls = String(node.className || '');
                if ((cls.includes('contact') || cls.includes('item') || cls.includes('card') || cls.includes('session') || cls.includes('conversation'))
                  && rect.left >= 150 && rect.left < Math.min(520, window.innerWidth * 0.45)
                  && rect.width >= 220 && rect.width <= 360 && rect.height >= 55 && rect.height <= 120) {
                  return node;
                }
              }
              return ele;
            };
            const primaryNodes = Array.from(document.querySelectorAll('.im-ui-contact-list-item'));
            const fallbackNodes = Array.from(document.querySelectorAll('.im-ui-contact-info, li, [class*=item], [class*=card], [class*=session], [class*=conversation], [class*=list] > div'));
            const rawNodes = primaryNodes.length ? primaryNodes : fallbackNodes;
            const uniqueNodes = Array.from(new Set(rawNodes.map(resolveCard)));
            const cardNodes = uniqueNodes
              .filter(visible)
              .filter(inLeftArea)
              .map(ele => {
                const text = clean(ele.innerText || ele.textContent);
                const minute = minuteOf(text);
                const rect = ele.getBoundingClientRect();
                const time = (text.match(/([01]?[0-9]|2[0-3]):([0-5][0-9])/) || [''])[0];
                const title = clean(text.split(time)[0] || text).slice(0, 40);
                const signature = `${time}|${title}`;
                return {ele, text, minute, time, title, signature, top: rect.top};
              })
              .filter(item => item.minute !== null)
              .filter(item => item.minute >= startMinute)
              .filter(item => !badCardText(item.text))
              .filter(item => !processed.has(item.signature))
              .sort((a, b) => b.minute - a.minute || a.top - b.top);
            const card = cardNodes[0];
            if (card) {
              card.ele.scrollIntoView({block: 'center', inline: 'nearest'});
              for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                card.ele.dispatchEvent(new MouseEvent(name, {
                  bubbles: true,
                  cancelable: true,
                  composed: true,
                  view: window,
                }));
              }
              return {ok: true, signature: card.signature, time: card.time, title: card.title, text: card.text.slice(0, 160)};
            }
            const scrollBox = Array.from(document.querySelectorAll('*'))
              .filter(visible)
              .filter(ele => {
                const rect = ele.getBoundingClientRect();
                const style = getComputedStyle(ele);
                return rect.left < Math.min(620, window.innerWidth * 0.48)
                  && rect.width >= 220
                  && rect.width <= 520
                  && rect.height > 240
                  && ele.scrollHeight > ele.clientHeight + 20
                  && /(auto|scroll)/.test(style.overflowY);
              })
              .sort((a, b) => {
                const ar = a.getBoundingClientRect();
                const br = b.getBoundingClientRect();
                return (br.height * br.width) - (ar.height * ar.width);
              })[0];
            if (scrollBox && scrollBox.scrollTop + scrollBox.clientHeight < scrollBox.scrollHeight - 8) {
              scrollBox.scrollTop += Math.max(180, scrollBox.clientHeight * 0.75);
              return {ok: false, scrolled: true};
            }
            return {ok: false, reason: 'no recent chat card found'};
            """,
            start_minute,
            processed_json,
        ) or {"ok": False, "reason": "open chat card script failed"}

    def reset_chat_list_scroll(self) -> None:
        self.page.run_js(
            """
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const boxes = Array.from(document.querySelectorAll('.im-ui-contacts-wrap, aside *, *'))
              .filter(visible)
              .filter(ele => {
                const rect = ele.getBoundingClientRect();
                const style = getComputedStyle(ele);
                return rect.left < Math.min(520, window.innerWidth * 0.45)
                  && rect.width >= 250
                  && rect.width <= 360
                  && rect.height > 240
                  && ele.scrollHeight > ele.clientHeight + 20
                  && /(auto|scroll)/.test(style.overflowY);
              })
              .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
            for (const box of boxes.slice(0, 3)) {
              box.scrollTop = 0;
            }
            return boxes.length;
            """
        )
        time.sleep(0.8)

    def request_chat_action_in_current_chat(self, action: str, timeout: int = 14) -> dict:
        action = "phone" if action == "phone" else "resume"
        deadline = time.time() + timeout
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const action = arguments[0];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const textOf = ele => clean(ele.innerText || ele.textContent);
                const compact = value => clean(value).replace(/\\s+/g, '');
                const actionName = action === 'phone' ? '电话' : '简历';
                const config = action === 'phone'
                  ? {askText: '索要手机', availableRe: /手机号|手机|电话|查看手机|查看电话|电话已获取|手机已获取/, modalRe: /确定向对方索要(手机|电话)吗|确定.*(索要|获取).*(手机|电话)/}
                  : {askRe: /索要简历/, availableRe: /看简历|查看简历|简历已获取/, modalRe: /确定向对方索要简历吗|确定.*索要.*简历/};
                if (action === 'resume') config.askText = '索要简历';
                const clickEle = ele => {
                  ele.scrollIntoView({block: 'center', inline: 'nearest'});
                  for (const name of ['mouseover', 'pointerover', 'pointerenter', 'mouseenter', 'pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    ele.dispatchEvent(new MouseEvent(name, {bubbles: true, cancelable: true, composed: true, view: window}));
                  }
                };
                const dialog = Array.from(document.querySelectorAll('.ant-im-modal, .ant-lpt-modal, [role=dialog], [class*=modal]'))
                  .filter(visible)
                  .find(ele => config.modalRe.test(textOf(ele)) || (textOf(ele).includes('确定向对方索要') && textOf(ele).includes(actionName)));
                const confirmScope = dialog || Array.from(document.querySelectorAll('.ant-im-popover, .ant-popover, [class*=popover], [class*=Popconfirm]'))
                  .filter(visible)
                  .find(ele => /确定|确认/.test(textOf(ele)) && (textOf(ele).includes(actionName) || textOf(ele).includes('索要') || textOf(ele).length <= 120));
                if (confirmScope) {
                  const isConfirmButton = ele => {
                    const text = compact(textOf(ele));
                    if (!text || text.length > 12) return false;
                    if (/^(确定|确认|确定索要|确认索要|确定获取|确认获取)$/.test(text)) return true;
                    return text.includes('确定') || text.includes('确认');
                  };
                  const button = Array.from(confirmScope.querySelectorAll('button, [role=button], a, span, div'))
                    .filter(visible)
                    .filter(isConfirmButton)
                    .sort((a, b) => {
                      const ar = a.getBoundingClientRect();
                      const br = b.getBoundingClientRect();
                      const rank = ele => {
                        const tag = ele.tagName;
                        const cls = String(ele.className || '');
                        if (tag === 'BUTTON') return 0;
                        if (ele.getAttribute('role') === 'button') return 1;
                        if (cls.includes('btn') || cls.includes('Btn') || cls.includes('button') || cls.includes('Button')) return 2;
                        return 3;
                      };
                      const rankDiff = rank(a) - rank(b);
                      if (rankDiff) return rankDiff;
                      const aPrimary = /primary|danger|confirm|ok/i.test(String(a.className || '')) ? 0 : 1;
                      const bPrimary = /primary|danger|confirm|ok/i.test(String(b.className || '')) ? 0 : 1;
                      if (aPrimary !== bPrimary) return aPrimary - bPrimary;
                      return (ar.width * ar.height) - (br.width * br.height);
                    })[0];
                  if (!button) {
                    const debugButtons = Array.from(confirmScope.querySelectorAll('button, [role=button], a, span, div'))
                      .filter(visible)
                      .map(ele => ({tag: ele.tagName, text: textOf(ele).slice(0, 40), cls: String(ele.className || '').slice(0, 80)}))
                      .filter(item => item.text)
                      .slice(0, 20);
                    return {ok: false, reason: `${actionName}确认按钮未找到`, scope_text: textOf(confirmScope).slice(0, 160), buttons: debugButtons};
                  }
                  clickEle(button);
                  return {ok: false, clicked: true, confirming: true, reason: `已点击${actionName}确认`};
                }
                const toolbar = Array.from(document.querySelectorAll('.chatwin-action'))
                  .filter(visible)
                  .sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width)[0];
                if (!toolbar) return {ok: false, status: 'not_found', reason: '底部工具栏未找到'};
                const all = Array.from(toolbar.querySelectorAll('*')).filter(visible);
                const isAskText = ele => compact(textOf(ele)) === config.askText;
                const actionButtonSelector = action === 'phone' ? '.im-ui-action-button.action-phone' : '.im-ui-action-button.action-resume';
                const actionButtons = Array.from(toolbar.querySelectorAll(actionButtonSelector)).filter(visible);
                const actionButton = actionButtons
                  .find(ele => textOf(ele).includes(config.askText))
                  || actionButtons.find(ele => compact(textOf(ele)) === config.askText);
                const pendingButton = actionButtons.find(ele => /索要中|已索要|等待/.test(textOf(ele)));
                if (pendingButton) {
                  return {ok: true, status: 'already_requested', message: `${actionName}已索要，当前为：${textOf(pendingButton).slice(0, 24)}`};
                }
                const isAvailableText = ele => {
                  const text = textOf(ele);
                  const compactText = compact(text);
                  if (action === 'phone' && /^(手机号|手机|电话|查看手机|查看电话)$/.test(compactText)) return true;
                  if (action === 'resume' && /^(看简历|查看简历)$/.test(compactText)) return true;
                  if (action === 'phone') return false;
                  if (action === 'resume') return false;
                  if (!config.availableRe.test(text)) return false;
                  return !isAskText(ele);
                };
                const available = all
                  .filter(isAvailableText)
                  .filter(ele => textOf(ele).length <= 30)
                  .sort((a, b) => textOf(a).length - textOf(b).length)[0];
                if (available) return {ok: true, status: 'already_available', message: `${actionName}已可查看：${textOf(available).slice(0, 24)}`};
                const exactAction = actionButton;
                const ask = exactAction || all
                  .filter(isAskText)
                  .filter(ele => textOf(ele).length <= 30)
                  .sort((a, b) => {
                    return textOf(a).length - textOf(b).length;
                  })[0];
                if (!ask) return {ok: true, status: 'not_needed', message: `底部不是${config.askText}，跳过`};
                let button = ask;
                for (let node = ask, depth = 0; node && depth < 6; node = node.parentElement, depth += 1) {
                  const style = getComputedStyle(node);
                  const tag = node.tagName;
                  if (tag === 'BUTTON' || tag === 'A' || node.getAttribute('role') === 'button' || style.cursor === 'pointer') {
                    button = node;
                    break;
                  }
                }
                clickEle(button);
                return {ok: false, clicked: true, reason: `已点击${actionName}按钮，等待确认或状态变化`, clicked_text: textOf(button), clicked_class: String(button.className || '')};
                """,
                action,
            )
            if last_result and last_result.get("ok"):
                return last_result
            if last_result and last_result.get("confirming"):
                time.sleep(1.0)
                return {"status": "requested", "message": str(last_result.get("reason") or "已确认")}
            if last_result and last_result.get("clicked"):
                time.sleep(0.9)
                continue
            time.sleep(0.35)
        return {"status": "failed", "message": str(last_result or "按钮未找到")}

    @staticmethod
    def contact_status_text(status: str) -> str:
        return {
            "requested": "已索要",
            "already_requested": "已索要",
            "already_available": "已可查看",
            "not_found": "未找到会话",
            "failed": "失败",
            "unknown": "未知",
        }.get(status, status or "未知")

    def request_resumes_after_batch(self, targets: list[dict], results: list[dict]) -> None:
        self.progress.emit("resume_request_start", f"开始进入消息页索要简历：{len(targets)} 人")
        self.page.get(CHAT_URL)
        self.wait_for_chat_page()
        for target in targets:
            index = target.get("index")
            name = target.get("name", "")
            self.progress.emit("resume_request", f"正在处理第 {index} 个候选人的消息会话：{name}")
            status = ""
            note = ""
            try:
                opened = self.open_candidate_chat(target)
                if not opened.get("ok"):
                    status = "not_found"
                    note = opened.get("reason", "未找到消息卡片")
                else:
                    action = self.request_resume_in_current_chat(target)
                    status = action.get("status", "unknown")
                    note = action.get("message", "")
            except Exception as exc:
                status = "failed"
                note = str(exc)
            for item in results:
                if item.get("index") == index:
                    item["resume_request_status"] = status
                    item["resume_request_note"] = note
                    break
            self.progress.emit(
                "resume_request_done",
                f"第 {index} 个索要简历结果：{self.resume_status_text(status)}，{note}",
                {"index": index, "status": status, "note": note},
            )
            self.save_batch_summary(results)

    @staticmethod
    def resume_status_text(status: str) -> str:
        return {
            "requested": "已索要",
            "already_requested": "已索要",
            "already_available": "已可查看",
            "not_found": "未找到会话",
            "failed": "失败",
            "unknown": "未知",
        }.get(status, status or "未知")

    def wait_for_chat_page(self, timeout: int = 18) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            ok = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const body = document.body.innerText || '';
                const hasChatShell = body.includes('消息') && body.includes('全部职位');
                const hasChatList = Array.from(document.querySelectorAll('*'))
                  .some(ele => visible(ele) && (ele.innerText || '').includes('求职者投递'));
                const hasContactCards = document.querySelectorAll('.im-ui-contact-list-item').length > 0;
                return hasContactCards || hasChatList || (hasChatShell && body.includes('暂无'));
                """
            )
            if ok:
                return
            time.sleep(0.4)
        raise RuntimeError("消息页未加载完成。")

    def open_candidate_chat(self, target: dict, timeout: int = 18) -> dict:
        deadline = time.time() + timeout
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const target = arguments[0] || {};
                const rawName = String(target.name || '').trim();
                const normalizeName = value => String(value || '')
                  .replace(/（.*?）/g, '')
                  .replace(/\\(.*?\\)/g, '')
                  .replace(/先生|女士|TA设置了姓名保护|（|）|\\(|\\)/g, '')
                  .replace(/\\s+/g, '')
                  .trim();
                const nameTokens = Array.from(new Set([
                  rawName,
                  rawName.replace(/（.*?）/g, '').trim(),
                  rawName.replace(/\\(.*?\\)/g, '').trim(),
                  rawName.replace(/先生|女士|TA设置了姓名保护|（|）|\\(|\\)/g, '').trim(),
                  normalizeName(rawName),
                ].map(item => String(item || '').trim()).filter(Boolean)));
                const strictNameTokens = nameTokens.filter(token => token.length >= 2 && !/TA设置|姓名保护/.test(token));
                const chatJob = target.chat_job || {};
                const jobTitle = String(chatJob.title || '').trim();
                const jobTokens = jobTitle.split('-').filter(part => part && part.length >= 2);
                const position = String(target.job_position || '').trim();
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const compact = value => clean(value).replace(/\\s+/g, '');
                if (!strictNameTokens.length) {
                  return {ok: false, reason: 'candidate name is empty or protected; refuse to request resume blindly'};
                }
                const leftArea = ele => {
                  const rect = ele.getBoundingClientRect();
                  return rect.left < Math.min(620, window.innerWidth * 0.45);
                };
                const likelyChatCard = ele => {
                  const rect = ele.getBoundingClientRect();
                  const text = clean(ele.innerText || ele.textContent);
                  if (!leftArea(ele) || rect.height < 36 || rect.height > 180 || rect.width < 180 || rect.width > 620) return false;
                  if (!text || text.length > 320) return false;
                  return strictNameTokens.some(token => compact(text).includes(compact(token)));
                };
                const scoreCard = ele => {
                  const text = clean(ele.innerText || ele.textContent);
                  if (!likelyChatCard(ele)) return -1;
                  let score = 0;
                  if (strictNameTokens.some(token => compact(text).includes(compact(token)))) score += 100;
                  if (jobTitle && text.includes(jobTitle)) score += 8;
                  for (const token of jobTokens) {
                    if (text.includes(token)) score += 2;
                  }
                  if (position && text.includes(position)) score += 2;
                  if (/\\d{1,2}:\\d{2}/.test(text)) score += 1;
                  if (text.includes('求职者投递')) score -= 5;
                  return score;
                };
                const cards = Array.from(document.querySelectorAll('li, [class*=item], [class*=card], [class*=session], [class*=conversation]'))
                  .filter(visible)
                  .map(ele => ({ele, score: scoreCard(ele), text: clean(ele.innerText || ele.textContent)}))
                  .filter(item => item.score >= 100)
                  .sort((a, b) => b.score - a.score || a.ele.getBoundingClientRect().top - b.ele.getBoundingClientRect().top);
                const best = cards[0];
                if (!best) {
                  const scrollBox = Array.from(document.querySelectorAll('*'))
                    .filter(ele => {
                      const rect = ele.getBoundingClientRect();
                      const style = getComputedStyle(ele);
                      return rect.width >= 250 && rect.width <= 460 && rect.height > 250
                        && ele.scrollHeight > ele.clientHeight + 20 && /(auto|scroll)/.test(style.overflowY);
                    })
                    .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))[0];
                  if (scrollBox && scrollBox.scrollTop + scrollBox.clientHeight < scrollBox.scrollHeight - 5) {
                    scrollBox.scrollTop += Math.max(180, scrollBox.clientHeight * 0.8);
                    return {ok: false, waiting: true, reason: 'scrolling chat list'};
                  }
                  return {ok: false, reason: 'candidate chat card not found by name', tokens: strictNameTokens, job_tokens: jobTokens};
                }
                best.ele.scrollIntoView({block: 'center', inline: 'nearest'});
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  best.ele.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                return {ok: true, score: best.score, text: best.text.slice(0, 160), tokens: strictNameTokens};
                """,
                target,
            )
            if last_result and last_result.get("ok"):
                time.sleep(0.8)
                verified = self.current_chat_matches_target(target)
                if verified.get("ok"):
                    last_result["verified"] = verified
                    return last_result
                last_result = {
                    "ok": False,
                    "reason": "clicked chat card but active chat did not match target",
                    "clicked": last_result,
                    "verified": verified,
                }
            time.sleep(0.4)
        return last_result or {"ok": False, "reason": "candidate chat card not found"}

    def current_chat_matches_target(self, target: dict) -> dict:
        return self.page.run_js(
            """
            const target = arguments[0] || {};
            const rawName = String(target.name || '').trim();
            const normalizeName = value => String(value || '')
              .replace(/（.*?）/g, '')
              .replace(/\\(.*?\\)/g, '')
              .replace(/先生|女士|TA设置了姓名保护|（|）|\\(|\\)/g, '')
              .replace(/\\s+/g, '')
              .trim();
            const tokens = Array.from(new Set([
              rawName,
              normalizeName(rawName),
            ].map(item => String(item || '').trim()).filter(item => item.length >= 2 && !/TA设置|姓名保护/.test(item))));
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
            const compact = value => clean(value).replace(/\\s+/g, '');
            if (!tokens.length) return {ok: false, reason: 'candidate name is empty or protected'};
            const rightPanes = Array.from(document.querySelectorAll('[class*=chat], [class*=im], [class*=message], [class*=conversation], main, section, body'))
              .filter(visible)
              .filter(ele => {
                const rect = ele.getBoundingClientRect();
                return rect.left > Math.min(260, window.innerWidth * 0.18) && rect.width > 360 && rect.height > 240;
              })
              .sort((a, b) => {
                const ar = a.getBoundingClientRect();
                const br = b.getBoundingClientRect();
                return (br.width * br.height) - (ar.width * ar.height);
              });
            const scope = rightPanes[0] || document.body;
            const text = compact(scope.innerText || scope.textContent);
            return {
              ok: tokens.some(token => text.includes(compact(token))),
              tokens,
              sample: clean(scope.innerText || scope.textContent).slice(0, 180),
            };
            """,
            target,
        ) or {"ok": False, "reason": "chat verify failed"}

    def request_resume_in_current_chat(self, target: dict, timeout: int = 12) -> dict:
        matched = self.current_chat_matches_target(target)
        if not matched.get("ok"):
            return {
                "status": "failed",
                "message": f"当前聊天窗口不是目标候选人，停止索要简历：{matched}",
            }
        deadline = time.time() + timeout
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const textOf = ele => clean(ele.innerText || ele.textContent);
                const compactTextOf = ele => textOf(ele).replace(/\\s+/g, '');
                const rightPanes = Array.from(document.querySelectorAll('[class*=chat], [class*=im], [class*=message], [class*=conversation], main, section, body'))
                  .filter(visible)
                  .filter(ele => {
                    const rect = ele.getBoundingClientRect();
                    return rect.left > Math.min(260, window.innerWidth * 0.18) && rect.width > 360 && rect.height > 240;
                  })
                  .sort((a, b) => {
                    const ar = a.getBoundingClientRect();
                    const br = b.getBoundingClientRect();
                    return (br.width * br.height) - (ar.width * ar.height);
                  });
                const chatScope = rightPanes[0] || document.body;
                const all = Array.from(chatScope.querySelectorAll('*')).filter(visible);
                const lowerToolbar = ele => ele.getBoundingClientRect().top > window.innerHeight * 0.55;
                const confirmResumeRequest = () => {
                  const dialogs = Array.from(document.querySelectorAll('.ant-im-modal, .ant-lpt-modal, [role=dialog], [class*=modal]'))
                    .filter(visible)
                    .filter(ele => textOf(ele).includes('确定向对方索要简历吗'));
                  const dialog = dialogs[0];
                  if (!dialog) return null;
                  const button = Array.from(dialog.querySelectorAll('button, [role=button], a, span, div'))
                    .filter(visible)
                    .filter(ele => compactTextOf(ele) === '确定')
                    .sort((a, b) => {
                      const ar = a.getBoundingClientRect();
                      const br = b.getBoundingClientRect();
                      return (ar.width * ar.height) - (br.width * br.height);
                    })[0];
                  if (!button) return {ok: false, reason: '索要简历确认按钮未找到'};
                  for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    button.dispatchEvent(new MouseEvent(name, {
                      bubbles: true,
                      cancelable: true,
                      composed: true,
                      view: window,
                    }));
                  }
                  return {ok: false, clicked: true, confirming: true, reason: '已点击索要简历确认'};
                };
                const confirmResult = confirmResumeRequest();
                if (confirmResult) return confirmResult;
                const resumeAvailable = () => all
                  .filter(ele => lowerToolbar(ele) && /看简历|查看简历/.test(textOf(ele)))
                  .sort((a, b) => textOf(a).length - textOf(b).length)[0];
                const resumeView = resumeAvailable();
                if (resumeView) {
                  return {ok: true, status: 'already_available', message: `按钮已是${textOf(resumeView).slice(0, 20)}`};
                }
                const ask = all
                  .filter(lowerToolbar)
                  .filter(ele =>
                    String(ele.className || '').includes('action-resume')
                    || textOf(ele) === '索要简历'
                    || textOf(ele).includes('索要简历')
                  )
                  .filter(ele => textOf(ele).length <= 40)
                  .sort((a, b) => {
                    const aClass = String(a.className || '').includes('action-resume') ? 0 : 1;
                    const bClass = String(b.className || '').includes('action-resume') ? 0 : 1;
                    if (aClass !== bClass) return aClass - bClass;
                    const ar = a.getBoundingClientRect();
                    const br = b.getBoundingClientRect();
                    const aBottom = window.innerHeight - ar.bottom;
                    const bBottom = window.innerHeight - br.bottom;
                    return aBottom - bBottom || textOf(a).length - textOf(b).length;
                  })[0];
                if (!ask) return {ok: false, reason: '索要简历按钮未找到'};
                let button = ask;
                for (let depth = 0; button && depth < 6; depth += 1, button = button.parentElement) {
                  const style = getComputedStyle(button);
                  const tag = button.tagName;
                  const cls = String(button.className || '');
                  if (cls.includes('action-resume')) break;
                  if (tag === 'BUTTON' || tag === 'A' || button.getAttribute('role') === 'button' || style.cursor === 'pointer') {
                    break;
                  }
                }
                if (!button) button = ask;
                button.scrollIntoView({block: 'center', inline: 'nearest'});
                for (const name of ['mouseover', 'pointerover', 'pointerenter', 'mouseenter', 'pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  button.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                return {ok: false, clicked: true, reason: '已点击索要简历，等待状态变化', clicked_text: textOf(button), clicked_class: String(button.className || '')};
                """
            )
            if last_result and last_result.get("ok"):
                time.sleep(0.8)
                return last_result
            if last_result and last_result.get("clicked"):
                time.sleep(1.0)
                verify = self.page.run_js(
                    """
                    const visible = ele => {
                      const rect = ele.getBoundingClientRect();
                      const style = getComputedStyle(ele);
                      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                    const lowerToolbar = ele => ele.getBoundingClientRect().top > window.innerHeight * 0.55;
                    const all = Array.from(document.querySelectorAll('*')).filter(visible);
                    const view = all
                      .filter(ele => lowerToolbar(ele) && /看简历|查看简历/.test(clean(ele.innerText || ele.textContent)))
                      .sort((a, b) => clean(a.innerText || a.textContent).length - clean(b.innerText || b.textContent).length)[0];
                    if (view) return {ok: true, status: 'requested', message: `已索要简历，当前为${clean(view.innerText || view.textContent).slice(0, 20)}`};
                    return {ok: false};
                    """
                )
                if verify and verify.get("ok"):
                    return verify
                confirm_after_click = self.page.run_js(
                    """
                    const visible = ele => {
                      const rect = ele.getBoundingClientRect();
                      const style = getComputedStyle(ele);
                      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                    const textOf = ele => clean(ele.innerText || ele.textContent);
                    const compactTextOf = ele => textOf(ele).replace(/\\s+/g, '');
                    const dialog = Array.from(document.querySelectorAll('.ant-im-modal, .ant-lpt-modal, [role=dialog], [class*=modal]'))
                      .filter(visible)
                      .find(ele => textOf(ele).includes('确定向对方索要简历吗'));
                    if (!dialog) return {ok: false};
                    const button = Array.from(dialog.querySelectorAll('button, [role=button], a, span, div'))
                      .filter(visible)
                      .filter(ele => compactTextOf(ele) === '确定')
                      .sort((a, b) => {
                        const ar = a.getBoundingClientRect();
                        const br = b.getBoundingClientRect();
                        return (ar.width * ar.height) - (br.width * br.height);
                      })[0];
                    if (!button) return {ok: false, reason: 'confirm button not found'};
                    for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                      button.dispatchEvent(new MouseEvent(name, {
                        bubbles: true,
                        cancelable: true,
                        composed: true,
                        view: window,
                      }));
                    }
                    return {ok: true, status: 'requested', message: '已确认索要简历'};
                    """
                )
                if confirm_after_click and confirm_after_click.get("ok"):
                    time.sleep(1.0)
                    return confirm_after_click
            if last_result and last_result.get("confirming"):
                time.sleep(1.0)
                return {"ok": True, "status": "requested", "message": "已确认索要简历"}
            time.sleep(0.4)
        return {"status": "failed", "message": str(last_result or "索要简历按钮未找到")}

    def ensure_candidate_detail_open(self) -> None:
        has_detail = self.page.run_js(
            """
            const resume = document.querySelector('.xpath-resume-body')
              || document.querySelector('.resume-detail-content-body');
            const text = resume ? (resume.innerText || resume.textContent || '').trim() : '';
            return text.length > 0 && (
              text.includes('查看大图')
              || text.includes('Resume update time')
              || text.includes('求职意向')
              || text.includes('Job Seeking Intention')
            );
            """
        )
        if not has_detail:
            self.progress.emit("candidate_open", "当前不在候选人详情页，自动打开第一个候选人")
            self.open_first_candidate()

    def append_batch_candidate(self, profile: dict, path: str = "candidate_batch_profiles.json") -> None:
        output_path = Path(path)
        if not output_path.is_absolute():
            output_path = RUNTIME_DIR / output_path
        profiles = []
        if output_path.exists():
            try:
                profiles = json.loads(output_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                profiles = []
        profiles.append(profile)
        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(profiles, file, ensure_ascii=False, indent=2)

    def go_to_next_candidate_for_batch(self) -> None:
        if self.has_candidate_turn_next():
            self.click_next_candidate()
            return
        self.open_next_unread_candidate_from_list()

    def process_current_candidate(self, filters: SearchFilters) -> dict:
        profile = self.save_candidate_profile({"selected_chat_job": filters.selected_chat_job})
        decision = self.decide_candidate_match(profile, filters)
        profile_path = RUNTIME_DIR / "candidate_profile.json"
        with open(profile_path, "w", encoding="utf-8") as file:
            json.dump(profile, file, ensure_ascii=False, indent=2)
        if decision.get("match") and filters.auto_communicate:
            communicate_result = self.auto_open_communicate(filters.selected_chat_job)
            decision["communicate_status"] = communicate_result.get("status", "done")
            if communicate_result.get("status") == "already_communicated":
                decision["communicate_note"] = "页面显示继续沟通，说明此前已沟通过，本次跳过开聊。"
            return decision
        return decision

    def fill_filter_input(self, placeholder: str, value: str) -> None:
        input_ele = self.wait_for_input(placeholder)
        input_ele.click()
        input_ele.clear()
        input_ele.input(value)
        self.wait_until_input_value(placeholder, value)

    def click_search_button(self) -> None:
        result = self.page.run_js(
            """
            const searchText = arguments[0];
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0
                && rect.height > 0
                && style.display !== 'none'
                && style.visibility !== 'hidden'
                && !ele.disabled;
            };
            const clean = ele => (ele.innerText || ele.textContent || '')
              .trim()
              .replace(/\\s+/g, ' ');
            const searchBars = Array.from(document.querySelectorAll('[class*=searchBar]'))
              .filter(visible)
              .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
            const scopes = searchBars.length ? searchBars : [document.body];
            for (const scope of scopes) {
              const button = Array.from(scope.querySelectorAll('button'))
                .find(ele => visible(ele) && clean(ele) === searchText);
              if (button) {
                button.scrollIntoView({block: 'center', inline: 'nearest'});
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  button.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                return {ok: true};
              }
            }
            return {ok: false, reason: 'search button not found'};
            """,
            "\u641c\u7d22",
        )
        if not result or not result.get("ok"):
            raise RuntimeError(f"Could not click Search button: {result}")
        time.sleep(1.2)

    def open_first_candidate(self) -> None:
        self.wait_for_search_results()
        deadline = time.time() + 12
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '')
                  .trim()
                  .replace(/\\s+/g, ' ');
                const hasAnyResult = Array.from(document.querySelectorAll('.xpath-resume-card'))
                  .some(visible);
                if (!hasAnyResult) {
                  const body = (document.body && document.body.innerText) || '';
                  if (/暂无|没有找到|请设置搜索条件进行搜索/.test(body)) {
                    return {ok: false, reason: 'no candidate results'};
                  }
                  return {ok: false, reason: 'candidate cards not rendered yet'};
                }
                const cards = Array.from(document.querySelectorAll('.xpath-resume-card'))
                  .filter(visible)
                  .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
                const card = cards[0];
                if (!card) return {ok: false, reason: 'candidate card not found'};
                card.scrollIntoView({block: 'center', inline: 'nearest'});
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  card.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                return {ok: true, text: clean(card).slice(0, 120)};
                """
            )
            if last_result and last_result.get("ok"):
                self.wait_for_candidate_preview()
                return
            if last_result and last_result.get("reason") == "no candidate results":
                raise RuntimeError("没有搜索到候选人，请放宽筛选条件后再试。")
            time.sleep(0.4)
        raise RuntimeError(f"Could not open first candidate: {last_result}")

    def click_next_candidate(self) -> None:
        before_signature = self.get_candidate_signature()
        result = self.page.run_js(
            """
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0
                && rect.height > 0
                && style.display !== 'none'
                && style.visibility !== 'hidden';
            };
            const modal = Array.from(document.querySelectorAll('.ant-lpt-modal,[role=dialog],.ant-lpt-drawer'))
              .filter(visible)
              .find(ele => (ele.innerText || '').includes('查看大图') || (ele.innerText || '').includes('Resume update time'));
            const scope = modal || document.body;
            const directNext = Array.from(scope.querySelectorAll('.button-turn-next'))
              .filter(visible)
              .sort((a, b) => b.getBoundingClientRect().left - a.getBoundingClientRect().left)[0];
            const candidates = directNext ? [directNext] : Array.from(scope.querySelectorAll('[aria-label=right], .antlpticon-right'))
              .filter(visible)
              .map(ele => {
                let target = ele;
                for (let depth = 0; target && depth < 6; depth += 1, target = target.parentElement) {
                  const style = getComputedStyle(target);
                  if (
                    target.tagName === 'BUTTON'
                    || target.getAttribute('role') === 'button'
                    || String(target.className || '').includes('arrow')
                    || style.cursor === 'pointer'
                  ) {
                    return target;
                  }
                }
                return ele;
              })
              .filter(visible)
              .sort((a, b) => b.getBoundingClientRect().left - a.getBoundingClientRect().left);
            const button = candidates[0];
            if (!button) return {ok: false, reason: 'next candidate button not found'};
            button.scrollIntoView({block: 'center', inline: 'nearest'});
            for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
              button.dispatchEvent(new MouseEvent(name, {
                bubbles: true,
                cancelable: true,
                composed: true,
                view: window,
              }));
            }
            return {ok: true};
            """
        )
        if not result or not result.get("ok"):
            raise RuntimeError(f"Could not click next candidate: {result}")
        self.wait_for_candidate_change(before_signature)

    def has_candidate_turn_next(self) -> bool:
        return bool(
            self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                return Array.from(document.querySelectorAll('.button-turn-next')).some(visible);
                """
            )
        )

    def open_next_unread_candidate_from_list(self) -> None:
        result = self.page.run_js(
            """
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0
                && rect.height > 0
                && style.display !== 'none'
                && style.visibility !== 'hidden';
            };
            const clean = ele => (ele.innerText || ele.textContent || '')
              .trim()
              .replace(/\\s+/g, ' ');
            const cards = Array.from(document.querySelectorAll('.xpath-resume-card'))
              .filter(visible)
              .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
            const unread = cards.find(card => !String(card.className || '').includes('read') && !clean(card).includes('已查看'));
            const card = unread || cards[0];
            if (!card) return {ok: false, reason: 'candidate card not found'};
            card.scrollIntoView({block: 'center', inline: 'nearest'});
            for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
              card.dispatchEvent(new MouseEvent(name, {
                bubbles: true,
                cancelable: true,
                composed: true,
                view: window,
              }));
            }
            return {ok: true, text: clean(card).slice(0, 120)};
            """
        )
        if not result or not result.get("ok"):
            raise RuntimeError(f"Could not open next candidate from list: {result}")
        self.wait_for_candidate_preview()

    def get_candidate_signature(self) -> str:
        signature = self.page.run_js(
            """
            const resume = document.querySelector('.xpath-resume-body')
              || document.querySelector('.resume-detail-content-body')
              || Array.from(document.querySelectorAll('.ant-lpt-modal,[role=dialog]'))
                .find(ele => (ele.innerText || '').includes('查看大图') || (ele.innerText || '').includes('Resume update time'));
            const text = resume ? (resume.innerText || resume.textContent || '').trim() : '';
            return text.slice(0, 800);
            """
        )
        return str(signature or "")

    def wait_for_candidate_change(self, before_signature: str, timeout: int = 15) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self.wait_for_candidate_profile_text(timeout=2)
            except RuntimeError:
                time.sleep(0.3)
                continue
            current = self.get_candidate_signature()
            if current and current != before_signature:
                return
            time.sleep(0.4)
        raise RuntimeError("Next candidate did not load.")

    def wait_for_search_results(self, timeout: int = 15) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const body = document.body.innerText || '';
                const hasResumeCard = Array.from(document.querySelectorAll('.xpath-resume-card'))
                  .some(visible);
                const hasCardAction = Array.from(document.querySelectorAll('button'))
                  .some(ele => visible(ele) && (ele.innerText || '').trim().replace(/\\s+/g, ' ') === '立即沟通');
                return hasResumeCard || hasCardAction || body.includes('共有') && body.includes('份简历');
                """
            )
            if found:
                return
            time.sleep(0.4)
        raise RuntimeError("Search results did not load.")

    def wait_for_candidate_preview(self, timeout: int = 12) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            opened = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const body = document.body.innerText || '';
                const hasPreviewUrl = location.hash.includes('preview');
                const hasPreviewPanel = body.includes('觉得TA还不错')
                  || Array.from(document.querySelectorAll('[class*=drawer], [class*=modal]'))
                    .some(ele => visible(ele) && (ele.innerText || '').includes('觉得TA还不错'));
                return hasPreviewUrl || hasPreviewPanel;
                """
            )
            if opened:
                return
            time.sleep(0.3)
        raise RuntimeError("Candidate preview did not open.")

    def wait_for_candidate_profile_text(self, timeout: int = 15) -> None:
        deadline = time.time() + timeout
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const resume = document.querySelector('.xpath-resume-body')
                  || document.querySelector('.resume-detail-content-body');
                const text = resume ? (resume.innerText || resume.textContent || '').trim() : '';
                const hasCoreResume = (
                  text.includes('求职意向')
                  || text.includes('工作经历')
                  || text.includes('教育经历')
                  || text.includes('简历编号')
                  || text.includes('Job intention')
                  || text.includes('Work Experience')
                  || text.includes('Education Experience')
                  || text.includes('Resume update time')
                  || text.includes('Years old')
                  || text.includes('Years of work experience')
                );
                return {
                  ok: text.length >= 200 && hasCoreResume,
                  has_resume: !!resume,
                  text_length: text.length,
                  url: window.location.href,
                  sample: text.slice(0, 120),
                };
                """
            )
            if last_result and last_result.get("ok"):
                return
            time.sleep(0.4)
        raise RuntimeError(f"Candidate profile text did not load: {last_result}")

    def extract_candidate_profile(self) -> dict:
        self.wait_for_candidate_profile_text()
        profile = self.page.run_js(
            """
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0
                && rect.height > 0
                && style.display !== 'none'
                && style.visibility !== 'hidden';
            };
            const cleanText = value => String(value || '')
              .replace(/\\u00a0/g, ' ')
              .replace(/[ \\t]+/g, ' ')
              .trim();
            const resume = document.querySelector('.xpath-resume-body')
              || document.querySelector('.resume-detail-content-body');
            if (!resume) return {ok: false, reason: 'resume detail body not found'};

            let rawLines = (resume.innerText || '')
              .split('\\n')
              .map(cleanText)
              .filter(Boolean);
            const profileStart = rawLines.findIndex(line => line === '查看大图');
            if (profileStart > 0) {
              rawLines = rawLines.slice(profileStart);
            }
            if (!rawLines.length) {
              return {
                ok: false,
                reason: 'resume detail text is empty',
                url: window.location.href,
              };
            }
            const text = rawLines.join('\\n');
            const indexOf = label => rawLines.findIndex(line => line === label);
            const firstIndexOfAny = (labels, start = 0) => {
              const indexes = labels
                .map(label => rawLines.findIndex((line, index) => index >= start && line === label))
                .filter(index => index >= 0);
              return indexes.length ? Math.min(...indexes) : -1;
            };
            const sliceSection = (title, nextTitles) => {
              const start = indexOf(title);
              if (start < 0) return [];
              const end = firstIndexOfAny(nextTitles, start + 1);
              return rawLines.slice(start + 1, end >= 0 ? end : rawLines.length)
                .filter(line => !/^查看全部\\d+个$/.test(line));
            };
            const first = predicate => rawLines.find(predicate) || '';
            const last = array => array.length ? array[array.length - 1] : '';
            const intentStart = firstIndexOfAny(['求职意向', 'Job Seeking Intention']);
            const beforeIntent = rawLines.slice(0, intentStart >= 0 ? intentStart : rawLines.length);
            const isHeaderNoise = line => ['查看大图', '展开'].includes(line)
              || /活跃$/.test(line)
              || line === '在线'
              || /^Online$/i.test(line)
              || line === '发起意向沟通'
              || line.includes('高端人才')
              || line.startsWith('附件简历');
            const cleanHeader = beforeIntent.filter(line => !isHeaderNoise(line));
            const name = cleanHeader.find(line => !line.startsWith('更新简历时间：')) || '';
            const updatedAt = (first(line => line.startsWith('更新简历时间：') || line.startsWith('Resume update time：')) || '')
              .replace('更新简历时间：', '')
              .replace('Resume update time：', '');
            const age = first(line => /\\d+\\s*岁/.test(line) || /\\d+\\s*Years? old/i.test(line));
            const workYears = first(line =>
              /^工作/.test(line)
                || /^\\d+年$/.test(line)
                || /^\\d+年\\d+个月$/.test(line)
                || /Years? of work experience/i.test(line)
            );
            const currentSalary = first(line => /\\d+\\s*[kK].*薪|\\d+\\s*-\\s*\\d+\\s*[kK]|\\d+\\s*[kK].*Months/i.test(line));
            const educationSummary = first(line => line.includes('·'));
            const jobStatus = first(line => /在职|离职|新机会|找工作|暂无跳槽|On job|seeking|new job/i.test(line));
            const residence = cleanHeader.find(line =>
              line
                && line !== name
                && line !== age
                && line !== workYears
                && line !== currentSalary
                && line !== educationSummary
                && line !== jobStatus
                && !isHeaderNoise(line)
                && !line.startsWith('更新简历时间：')
                && !line.startsWith('Resume update time：')
                && !line.startsWith('查看全部')
                && line.length <= 40
            ) || '';
            const selfEvaluation = last(beforeIntent.filter(line =>
              line.length > 20
                && line !== educationSummary
                && !line.startsWith('更新简历时间：')
            ));

            const sectionTitles = [
              '求职意向', 'Job Seeking Intention',
              '工作经历', 'Work Experience',
              '项目经历', 'Project Experience',
              '教育经历', 'Education Experience',
              '资格证书', '语言能力', '附件简历', '简历编号', 'Resume No.', '觉得TA还不错：'
            ];
            const sliceFirstSection = (titles, nextTitles) => {
              for (const title of titles) {
                const lines = sliceSection(title, nextTitles.filter(item => !titles.includes(item)));
                if (lines.length) return lines;
              }
              return [];
            };
            const jobIntentLines = sliceFirstSection(['求职意向', 'Job Seeking Intention'], sectionTitles);
            const workLines = sliceFirstSection(['工作经历', 'Work Experience'], sectionTitles);
            const projectLines = sliceFirstSection(['项目经历', 'Project Experience'], sectionTitles);
            const educationLines = sliceFirstSection(['教育经历', 'Education Experience'], sectionTitles);
            const certificateLines = sliceSection('资格证书', sectionTitles.filter(title => title !== '资格证书'));
            const languageLines = sliceSection('语言能力', sectionTitles.filter(title => title !== '语言能力'));
            const resumeNoIndex = indexOf('简历编号');
            const resumeNo = resumeNoIndex >= 0 ? (rawLines[resumeNoIndex + 1] || '').replace(/^:\\s*/, '') : '';

            const splitJobIntent = lines => ({
              position: lines[0] || '',
              salary: lines[1] || '',
              cities: lines[2] || '',
              industry: lines[3] || '',
              raw_lines: lines,
            });

            return {
              ok: true,
              url: window.location.href,
              extracted_at: new Date().toISOString(),
              basic: {
                name,
                updated_at: updatedAt,
                location: residence,
                work_years: workYears,
                age,
                job_status: jobStatus,
                current_salary: currentSalary,
                education_summary: educationSummary,
                self_evaluation: selfEvaluation,
              },
              job_intention: splitJobIntent(jobIntentLines),
              work_experience: {
                raw_lines: workLines,
                raw_text: workLines.join('\\n'),
              },
              project_experience: {
                raw_lines: projectLines,
                raw_text: projectLines.join('\\n'),
              },
              education_experience: {
                raw_lines: educationLines,
                raw_text: educationLines.join('\\n'),
              },
              certificates: certificateLines,
              languages: languageLines,
              resume_no: resumeNo,
              raw_lines: rawLines,
              raw_text: text,
            };
            """
        )
        if not profile or not profile.get("ok"):
            raise RuntimeError(f"Could not extract candidate profile: {profile}")
        return profile

    def save_candidate_profile(self, metadata: dict | None = None, path: str = "candidate_profile.json") -> dict:
        if isinstance(metadata, str):
            path = metadata
            metadata = None
        profile = self.extract_candidate_profile()
        if metadata:
            profile.update(metadata)
        output_path = Path(path)
        if not output_path.is_absolute():
            output_path = RUNTIME_DIR / output_path
        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(profile, file, ensure_ascii=False, indent=2)
        return profile

    def decide_candidate_match(self, profile: dict, filters: SearchFilters) -> dict:
        requirements = filters.match_requirements.strip() or DEFAULT_MATCH_REQUIREMENTS

        try:
            decision = self.ask_deepseek_for_match(
                profile=profile,
                requirements=requirements,
                api_key=filters.deepseek_api_key,
                model=filters.deepseek_model,
            )
        except Exception as exc:
            decision = {
                "match": False,
                "score": 0,
                "decision": "reject",
                "reason": f"AI 判断失败，已跳过当前候选人：{exc}",
                "next_action": "skip",
                "ai_error": str(exc),
            }
            self.append_ai_match_log(profile, requirements, {"messages": []}, decision)
        profile["ai_match"] = decision
        return decision

    def ask_deepseek_for_match(
        self,
        profile: dict,
        requirements: str,
        api_key: str = "",
        model: str = "deepseek-chat",
    ) -> dict:
        key = (api_key or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
        if not key:
            raise RuntimeError("请在UI填写 DeepSeek API Key，或设置环境变量 DEEPSEEK_API_KEY。")

        messages = self.build_match_messages(profile, requirements)
        payload = {
            "model": model.strip() or "deepseek-chat",
            "messages": messages,
            "temperature": 0.1,
            "stream": False,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            "https://api.deepseek.com/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Connection": "close",
            },
            method="POST",
        )
        body = self.urlopen_with_retry(req, retries=5, timeout=60)

        result = json.loads(body)
        content = result["choices"][0]["message"]["content"]
        decision = self.parse_ai_json(content)
        decision.setdefault("match", False)
        decision.setdefault("score", 0)
        decision.setdefault("decision", "reject")
        decision.setdefault("reason", "")
        decision.setdefault("next_action", "communicate" if decision.get("match") else "skip")
        decision["raw_response"] = content
        self.append_ai_match_log(profile, requirements, payload, decision)
        return decision

    def urlopen_with_retry(self, req: request.Request, retries: int = 5, timeout: int = 60) -> str:
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                # Avoid exhausting Windows ephemeral ports during batch runs.
                if attempt == 1:
                    time.sleep(0.8)
                with request.urlopen(req, timeout=timeout) as resp:
                    return resp.read().decode("utf-8")
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code not in {408, 409, 425, 429, 500, 502, 503, 504}:
                    raise RuntimeError(f"DeepSeek API 请求失败：HTTP {exc.code} {detail}") from exc
                last_error = RuntimeError(f"DeepSeek API 请求失败：HTTP {exc.code} {detail}")
            except error.URLError as exc:
                last_error = RuntimeError(f"DeepSeek API 请求失败：{exc}")
            except OSError as exc:
                last_error = RuntimeError(f"DeepSeek API 请求失败：{exc}")

            if attempt < retries:
                delay = min(2 ** attempt, 20)
                append_runtime_log(f"DeepSeek retry attempt={attempt} delay={delay}s error={last_error}")
                time.sleep(delay)

        raise last_error or RuntimeError("DeepSeek API 请求失败：unknown error")

    def append_ai_match_log(self, profile: dict, requirements: str, payload: dict, decision: dict) -> None:
        output_path = RUNTIME_DIR / "ai_match_logs.json"
        logs = []
        if output_path.exists():
            try:
                logs = json.loads(output_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                logs = []
        logs.append(
            {
                "batch_index": profile.get("batch_index"),
                "candidate": {
                    "name": profile.get("basic", {}).get("name", ""),
                    "location": profile.get("basic", {}).get("location", ""),
                    "work_years": profile.get("basic", {}).get("work_years", ""),
                    "age": profile.get("basic", {}).get("age", ""),
                    "job_position": profile.get("job_intention", {}).get("position", ""),
                    "job_salary": profile.get("job_intention", {}).get("salary", ""),
                    "job_cities": profile.get("job_intention", {}).get("cities", ""),
                    "education_summary": profile.get("basic", {}).get("education_summary", ""),
                },
                "selected_chat_job": profile.get("selected_chat_job"),
                "requirements": requirements,
                "prompt": payload.get("messages", []),
                "decision": decision,
            }
        )
        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(logs, file, ensure_ascii=False, indent=2)

    def build_match_messages(self, profile: dict, requirements: str) -> list[dict]:
        selected_job = profile.get("selected_chat_job") or {}
        compact_profile = {
            "开聊职位": {
                "职位名称": selected_job.get("title", ""),
                "城市": selected_job.get("city", ""),
                "薪资": selected_job.get("salary", ""),
                "职位ID": selected_job.get("job_id", ""),
            },
            "候选人基础信息": profile.get("basic", {}),
            "候选人求职意向": profile.get("job_intention", {}),
            "工作经历": self.limit_text(profile.get("work_experience", {}).get("raw_text", ""), 9000),
            "项目经历": self.limit_text(profile.get("project_experience", {}).get("raw_text", ""), 3000),
            "教育经历": self.limit_text(profile.get("education_experience", {}).get("raw_text", ""), 2500),
            "证书": profile.get("certificates", []),
            "语言": profile.get("languages", []),
            "原始简历摘要": self.limit_text(profile.get("raw_text", ""), 12000),
        }
        system_prompt = (
            "你是严谨的招聘匹配评估助手。你要读懂招聘方用自然语言写的要求，"
            "先把口语化描述转成可执行的招聘判断标准，再把硬性条件和软性偏好分开判断。"
            "不要为了通过而脑补简历里没有的信息；"
            "信息缺失时要标记为 unknown，并降低通过概率。只有候选人明确满足核心硬性要求、"
            "岗位方向和经验强相关、没有明显风险时，match 才能为 true。"
            "你只能返回一个 JSON 对象，不要返回 Markdown，不要解释 JSON 之外的文字。"
        )
        user_prompt = f"""
招聘方口语化描述：
{requirements}

请先把这段口语化描述理解成招聘判断标准，例如：
- “要有销售经验”意味着需要在工作经历、职位名称、职责或业绩里看到真实销售相关证据。
- “最好做过医疗/交通/金融”属于行业偏好；如果用户语气是“必须”，才作为硬性条件。
- 城市、薪资、职位方向和年限如果与开聊职位明显冲突，要作为风险或拒绝理由。

请根据下面的候选人信息判断是否值得自动点击“立即沟通”。

候选人与职位信息 JSON：
{json.dumps(compact_profile, ensure_ascii=False, indent=2)}

请严格返回如下 JSON 结构：
{{
  "match": true 或 false,
  "score": 0到100的整数,
  "decision": "pass" 或 "reject" 或 "uncertain",
  "reason": "一句话说明最终判断",
  "must_have_result": [
    {{"requirement": "硬性条件", "status": "met/not_met/unknown", "evidence": "简历证据"}}
  ],
  "strengths": ["匹配亮点"],
  "risks": ["不匹配或信息不足风险"],
  "next_action": "communicate 或 skip"
}}

判定规则：
1. 硬性条件只要有一个明确不满足，match=false。
2. 关键信息缺失但无法确认满足时，decision=uncertain 且 match=false。
3. 候选人方向、行业、年限、城市、薪资意向与要求明显偏离时，match=false。
4. 只有你愿意让系统自动点“立即沟通”时，match=true 且 next_action="communicate"。
"""
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    @staticmethod
    def limit_text(text: str, max_chars: int) -> str:
        value = str(text or "")
        return value if len(value) <= max_chars else value[:max_chars] + "\n...[已截断]"

    @staticmethod
    def parse_ai_json(content: str) -> dict:
        text = str(content or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.S)
            if not match:
                raise RuntimeError(f"AI没有返回可解析JSON：{content}")
            return json.loads(match.group(0))

    def auto_open_communicate(self, selected_job: dict | None) -> dict:
        button_result = self.click_immediate_communicate()
        if button_result.get("status") == "already_communicated":
            return button_result
        self.select_chat_job(selected_job)
        self.confirm_chat_job()
        return {"status": "done"}

    def click_immediate_communicate(self) -> dict:
        target_text = "立即沟通"
        already_text = "继续沟通"
        deadline = time.time() + 12
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const targetText = arguments[0];
                const alreadyText = arguments[1];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && !ele.disabled;
                };
                const clean = ele => (ele.innerText || ele.textContent || '')
                  .trim()
                  .replace(/\\s+/g, ' ');
                const cleanText = value => String(value || '').trim().replace(/\\s+/g, ' ');
                const drawerLeft = () => {
                  const drawer = Array.from(document.querySelectorAll('.ant-im-drawer-content-wrapper, .ant-im-drawer'))
                    .filter(visible)
                    .map(ele => ele.getBoundingClientRect())
                    .filter(rect => rect.width > 0 && rect.left > window.innerWidth * 0.55)
                    .sort((a, b) => a.left - b.left)[0];
                  return drawer ? drawer.left : window.innerWidth + 1;
                };
                const currentResumeActionPanel = () => {
                  const rightLimit = drawerLeft();
                  const candidates = Array.from(document.querySelectorAll('.xpath-wrap-resume-download, [class*=operation]'))
                    .filter(visible)
                    .filter(ele => {
                      const rect = ele.getBoundingClientRect();
                      const text = cleanText(ele.innerText || ele.textContent);
                      return text.includes('觉得TA还不错')
                        && rect.left > window.innerWidth * 0.45
                        && rect.right < rightLimit - 8
                        && rect.width >= 180
                        && rect.width <= 420
                        && rect.height >= 80;
                    })
                    .sort((a, b) => {
                      const ac = String(a.className || '');
                      const bc = String(b.className || '');
                      const aDirect = ac.includes('xpath-wrap-resume-download') ? 0 : 1;
                      const bDirect = bc.includes('xpath-wrap-resume-download') ? 0 : 1;
                      if (aDirect !== bDirect) return aDirect - bDirect;
                      return (a.getBoundingClientRect().height * a.getBoundingClientRect().width)
                        - (b.getBoundingClientRect().height * b.getBoundingClientRect().width);
                    });
                  return candidates[0] || null;
                };
                const clickableOf = ele => {
                  let node = ele;
                  for (let depth = 0; node && depth < 8; depth += 1, node = node.parentElement) {
                    const tag = node.tagName;
                    const role = node.getAttribute('role') || '';
                    const cls = String(node.className || '');
                    const style = getComputedStyle(node);
                    if (
                      tag === 'BUTTON'
                      || tag === 'A'
                      || role === 'button'
                      || cls.includes('btn')
                      || cls.includes('Btn')
                      || cls.includes('button')
                      || cls.includes('Button')
                      || style.cursor === 'pointer'
                    ) {
                      return node;
                    }
                  }
                  return ele;
                };
                const ancestorText = (ele, depthLimit = 8) => {
                  let node = ele;
                  const texts = [];
                  for (let depth = 0; node && depth < depthLimit; depth += 1, node = node.parentElement) {
                    texts.push(clean(node));
                  }
                  return texts.join(' ');
                };
                const inCardList = ele => {
                  let node = ele;
                  for (let depth = 0; node && depth < 8; depth += 1, node = node.parentElement) {
                    const cls = String(node.className || '');
                    if (cls.includes('xpath-resume-card') || cls.includes('resumeCard')) return true;
                  }
                  return false;
                };
                const panel = currentResumeActionPanel();
                if (!panel) return {ok: false, reason: '当前简历右侧操作卡片未找到'};
                const candidates = Array.from(panel.querySelectorAll('*'))
                  .filter(visible)
                  .map(ele => ({source: ele, text: clean(ele), target: clickableOf(ele)}))
                  .filter(item => item.text === targetText || item.text.includes(targetText) || item.text === alreadyText || item.text.includes(alreadyText))
                  .filter(item => item.text.length <= 200 || clean(item.target).length <= 200)
                  .filter(item => visible(item.target));
                const alreadyCandidates = candidates
                  .filter(item => item.text === alreadyText || item.text.includes(alreadyText))
                  .filter(item => panel.contains(item.target));
                if (alreadyCandidates.length) {
                  alreadyCandidates.sort((a, b) => {
                    const aExact = a.text === alreadyText ? 0 : 1;
                    const bExact = b.text === alreadyText ? 0 : 1;
                    if (aExact !== bExact) return aExact - bExact;
                    return b.target.getBoundingClientRect().left - a.target.getBoundingClientRect().left;
                  });
                  return {ok: true, status: 'already_communicated', text: clean(alreadyCandidates[0].target)};
                }
                candidates.sort((a, b) => {
                  const aExact = a.text === targetText ? 0 : 1;
                  const bExact = b.text === targetText ? 0 : 1;
                  if (aExact !== bExact) return aExact - bExact;
                  const aOpenIm = String(a.target.className || '').includes('xpath-open-im-btn') ? 0 : 1;
                  const bOpenIm = String(b.target.className || '').includes('xpath-open-im-btn') ? 0 : 1;
                  if (aOpenIm !== bOpenIm) return aOpenIm - bOpenIm;
                  const clickableRank = ele => {
                    if (ele.tagName === 'BUTTON') return 0;
                    if (ele.tagName === 'A' || ele.getAttribute('role') === 'button') return 1;
                    return 2;
                  };
                  const aClickable = clickableRank(a.target);
                  const bClickable = clickableRank(b.target);
                  if (aClickable !== bClickable) return aClickable - bClickable;
                  const aList = inCardList(a.target) ? 1 : 0;
                  const bList = inCardList(b.target) ? 1 : 0;
                  if (aList !== bList) return aList - bList;
                  const aRect = a.target.getBoundingClientRect();
                  const bRect = b.target.getBoundingClientRect();
                  const aArea = aRect.width * aRect.height;
                  const bArea = bRect.width * bRect.height;
                  if (Math.abs(aArea - bArea) > 1) return aArea - bArea;
                  return bRect.left - aRect.left;
                });
                const button = candidates[0] && candidates[0].target;
                if (!button) return {ok: false, reason: `${targetText}按钮未找到`};
                button.scrollIntoView({block: 'center', inline: 'nearest'});
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  button.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                return {ok: true, text: clean(button)};
                """,
                target_text,
                already_text,
            )
            if last_result and last_result.get("ok"):
                if last_result.get("status") == "already_communicated":
                    return last_result
                self.wait_for_chat_job_modal()
                return {"status": "done", "button_text": last_result.get("text", "")}
            time.sleep(0.4)
        raise RuntimeError(f"无法点击立即沟通：{last_result}")

    def wait_for_chat_job_modal(self, timeout: int = 12) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            opened = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                return Array.from(document.querySelectorAll('.ant-lpt-modal,[role=dialog],.ant-lpt-drawer'))
                  .some(ele => visible(ele) && (ele.innerText || '').includes('请选择开聊职位'));
                """
            )
            if opened:
                return
            time.sleep(0.3)
        raise RuntimeError("开聊职位弹窗未出现。")

    def select_chat_job(self, selected_job: dict | None) -> None:
        if not selected_job or not selected_job.get("title"):
            raise RuntimeError("没有选中的开聊职位，无法继续沟通。")
        title = selected_job.get("title", "")
        salary = selected_job.get("salary", "")
        city = selected_job.get("city", "")
        self.progress.emit("chat_job_select", f"选择开聊职位：{title}")
        city_token = city.split("-")[0] if city else ""
        title_parts = [part for part in title.split("-") if part and part != city_token]
        search_queries = []
        for query in [
            title,
            "-".join(title_parts),
            " ".join(part for part in title_parts if len(part) >= 2),
            city_token,
            "",
        ]:
            query = str(query or "").strip()
            if query not in search_queries:
                search_queries.append(query)

        deadline = time.time() + 30
        result = None
        input_done = False
        scroll_round = 0
        query_index = 0
        while time.time() < deadline:
            search_query = search_queries[min(query_index, len(search_queries) - 1)]
            result = self.page.run_js(
                """
                const [title, salary, city, inputDone, scrollRound, searchQuery] = arguments;
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const clean = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const modal = Array.from(document.querySelectorAll('.ant-lpt-modal,[role=dialog],.ant-lpt-drawer'))
                  .filter(visible)
                  .find(ele => (ele.innerText || '').includes('请选择开聊职位'));
                if (!modal) return {ok: false, reason: 'modal not found'};

                const input = Array.from(modal.querySelectorAll('input'))
                  .find(ele => visible(ele) && (ele.placeholder || '').includes('搜索职位'));
                if (input && !inputDone) {
                  input.focus();
                  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                  setter.call(input, '');
                  input.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'deleteContentBackward', data: null}));
                  setter.call(input, searchQuery);
                  input.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: searchQuery}));
                  input.dispatchEvent(new Event('change', {bubbles: true}));
                  input.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: 'Enter'}));
                  Array.from(modal.querySelectorAll('*')).forEach(ele => {
                    if (ele.scrollHeight > ele.clientHeight + 20) ele.scrollTop = 0;
                  });
                  return {ok: false, waiting: true, reason: 'waiting for filtered jobs'};
                }

                const scrollBox = Array.from(modal.querySelectorAll('*'))
                  .filter(ele => {
                    const style = getComputedStyle(ele);
                    const rect = ele.getBoundingClientRect();
                    return rect.height > 120
                      && rect.width > 200
                      && ele.scrollHeight > ele.clientHeight + 20
                      && /(auto|scroll)/.test(style.overflowY);
                  })
                  .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))[0];
                if (scrollBox && scrollRound > 0) {
                  scrollBox.scrollTop = Math.min(
                    scrollBox.scrollHeight,
                    scrollBox.scrollTop + Math.max(120, scrollBox.clientHeight * 0.8)
                  );
                }

                const listItems = Array.from(modal.querySelectorAll('li, [class*=jobName], [class*=jobItem], [class*=jobCard], [class*=item]'))
                  .filter(visible)
                  .map(ele => {
                    const card = ele.closest('li') || ele;
                    return {ele: card, text: clean(card.innerText || card.textContent)};
                  })
                  .filter((item, index, arr) =>
                    item.text
                    && item.text.length >= 4
                    && arr.findIndex(other => other.ele === item.ele) === index
                  );
                const visibleJobs = listItems.map(item => item.text.slice(0, 120));
                let matched = listItems.find(item => item.text.includes(title));
                if (!matched) {
                  const cityToken = city ? city.split('-')[0] : '';
                  matched = listItems.find(item =>
                    (!salary || salary === '薪资面议' || item.text.includes(salary))
                    && (!cityToken || item.text.includes(cityToken))
                    && title.split('-').some(part => part.length >= 2 && item.text.includes(part))
                  );
                }
                if (!matched) {
                  const atBottom = !scrollBox || scrollBox.scrollTop + scrollBox.clientHeight >= scrollBox.scrollHeight - 5;
                  return {
                    ok: false,
                    waiting: !atBottom,
                    reason: atBottom ? 'job title not found in modal' : 'scrolling job list',
                    title,
                    search_query: searchQuery,
                    visible_jobs: visibleJobs,
                    scroll_top: scrollBox ? scrollBox.scrollTop : null,
                    scroll_height: scrollBox ? scrollBox.scrollHeight : null,
                  };
                }

                let card = matched.ele.closest('li') || matched.ele;
                for (let depth = 0; card && depth < 6; depth += 1, card = card.parentElement) {
                  const text = clean(card.innerText || card.textContent);
                  const rect = card.getBoundingClientRect();
                  if (
                    (text.includes(title) || text.includes(matched.text))
                    && (!salary || salary === '薪资面议' || text.includes(salary))
                    && (!city || text.includes(city.split('-')[0]))
                    && rect.height >= 50
                  ) {
                    break;
                  }
                }
                if (!card) card = matched.ele;
                card.scrollIntoView({block: 'center', inline: 'nearest'});
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  card.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                return {ok: true, text: clean(card.innerText || card.textContent).slice(0, 160)};
                """,
                title,
                salary,
                city,
                input_done,
                scroll_round,
                search_query,
            )
            if result and result.get("ok"):
                break
            input_done = True
            if result:
                if result.get("waiting") and result.get("reason") == "waiting for filtered jobs":
                    self.progress.emit(
                        "chat_job_search",
                        f"已在开聊职位弹窗输入职位关键词：{search_query or '全部职位'}",
                        result,
                    )
                    scroll_round += 1
                    time.sleep(1.1)
                    continue
                if result.get("reason") == "job title not found in modal" and query_index < len(search_queries) - 1:
                    self.progress.emit(
                        "chat_job_search",
                        f"开聊职位未命中，切换关键词再试：{search_queries[query_index + 1] or '全部职位'}",
                        result,
                    )
                    query_index += 1
                    input_done = False
                    scroll_round = 0
                    time.sleep(0.6)
                    continue
                self.progress.emit(
                    "chat_job_search",
                    f"查找开聊职位中：{result.get('reason', '')}；可见职位：{'; '.join(result.get('visible_jobs', [])[:3])}",
                    result,
                )
            if result and result.get("waiting") is False:
                break
            scroll_round += 1
            time.sleep(0.4)
        if not result or not result.get("ok"):
            raise RuntimeError(f"无法选择开聊职位：{result}")
        time.sleep(0.4)

    def confirm_chat_job(self) -> None:
        deadline = time.time() + 10
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '')
                  .trim()
                  .replace(/\\s+/g, ' ');
                const modal = Array.from(document.querySelectorAll('.ant-lpt-modal,[role=dialog],.ant-lpt-drawer'))
                  .filter(visible)
                  .find(ele => (ele.innerText || '').includes('请选择开聊职位'));
                if (!modal) return {ok: true, alreadyClosed: true};
                const button = Array.from(modal.querySelectorAll('button'))
                  .filter(visible)
                  .find(ele => clean(ele) === '确认');
                if (!button) return {ok: false, reason: 'confirm button not found'};
                if (button.disabled || button.className.includes('disabled')) {
                  return {ok: false, reason: 'confirm button disabled'};
                }
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  button.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                return {ok: true};
                """
            )
            if last_result and last_result.get("ok"):
                return
            time.sleep(0.4)
        raise RuntimeError(f"无法确认开聊职位：{last_result}")

    def insert_ai_words_for_input(self, placeholder: str) -> None:
        deadline = time.time() + 8
        result = None
        while time.time() < deadline:
            result = self.page.run_js(
                """
                const placeholder = arguments[0];
                const input = Array.from(document.querySelectorAll('input[placeholder]'))
                  .find(ele => ele.placeholder === placeholder);
                if (!input) return {ok: false, reason: 'input not found'};

                const scopes = [];
                let node = input;
                for (let depth = 0; node && depth < 8; depth += 1, node = node.parentElement) {
                  scopes.push(node);
                }

                for (const scope of scopes) {
                  const btn = scope.querySelector('[class*=aiBtn]');
                  if (btn) {
                    for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                      btn.dispatchEvent(new MouseEvent(name, {
                        bubbles: true,
                        cancelable: true,
                        composed: true,
                        view: window,
                      }));
                    }
                    return {ok: true};
                  }
                }
                return {ok: false, reason: 'ai button not found near input'};
                """,
                placeholder,
            )
            if result and result.get("ok"):
                break
            time.sleep(0.3)
        if not result or not result.get("ok"):
            raise RuntimeError(f"AI fill button unavailable: {placeholder}, {result}")

        self.click_ai_fill_button()

    def click_ai_fill_button(self) -> None:
        deadline = time.time() + 15
        last_result = None
        while time.time() < deadline:
            result = self.page.run_js(
                """
                const fillTexts = arguments[0].split('|');
                const popovers = Array.from(document.querySelectorAll('.ant-lpt-popover'))
                  .filter(ele => {
                    const rect = ele.getBoundingClientRect();
                    return rect.width > 0
                      && rect.height > 0
                      && (ele.innerText || '').includes('AI');
                  });
                const popover = popovers[popovers.length - 1];
                if (!popover) return {ok: false, reason: 'popover not visible'};
                const popoverText = (popover.innerText || popover.textContent || '').trim().replace(/\\s+/g, ' ');
                if (popoverText.includes('正在扩展中')) {
                  return {ok: false, waiting: true, reason: 'AI keywords still generating'};
                }
                const buttons = Array.from(popover.querySelectorAll('button'))
                  .map(ele => ({
                    ele,
                    text: (ele.innerText || '').trim().replace(/\\s+/g, ' '),
                    disabled: !!ele.disabled || ele.getAttribute('aria-disabled') === 'true' || ele.className.includes('disabled'),
                  }));
                const button = (buttons.find(item => item.text === '填入复合关键词')
                  || buttons.find(item => fillTexts.includes(item.text))
                  || {});
                if (!button.ele) return {ok: false, reason: 'fill button not visible'};
                if (button.disabled) return {ok: false, waiting: true, reason: 'fill button disabled'};
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  button.ele.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                return {ok: true, text: button.text};
                """,
                "|".join(AI_FILL_TEXTS),
            )
            last_result = result
            if result and result.get("ok"):
                time.sleep(1.0)
                return
            time.sleep(0.5)
        raise RuntimeError(f"AI fill popover did not close after clicking fill: {last_result}")

    def select_city(self, row_title: str, city: str) -> None:
        self.progress.emit("city_select", f"正在选择{row_title}：{city}")
        self.open_city_modal(row_title)

        input_ele = self.wait_for_input(CITY_SEARCH_PLACEHOLDER)
        input_ele.click()
        input_ele.clear()
        input_ele.input(city)

        self.click_first_city_result(city)
        self.wait_until_city_selected(city)
        self.click_city_modal_confirm()
        if self.is_city_visible_in_row(row_title, city):
            self.progress.emit("city_select", f"{row_title}已显示：{city}")
        else:
            self.progress.emit(
                "city_select",
                f"{row_title}已确认：{city}（页面未在筛选行回显，已按弹窗确认成功继续）",
            )

    def open_city_modal(self, row_title: str) -> None:
        xpath = (
            f'xpath://span[normalize-space()="{row_title}"]'
            f'/ancestor::div[.//label[normalize-space()="{OTHER_TEXT}"]][1]'
            f'//label[normalize-space()="{OTHER_TEXT}"]'
        )
        other = self.page.ele(xpath, timeout=8)
        if not other:
            raise RuntimeError(f"Could not find city Other option: {row_title}")
        other.click()
        self.wait_for_input(CITY_SEARCH_PLACEHOLDER)

    def click_first_city_result(self, city: str) -> None:
        suggest = self.page.ele(
            f'xpath://div[contains(@class,"city-modal")]'
            f'//div[contains(@class,"suggest-list")]//li[contains(.,"{city}")]',
            timeout=5,
        )
        if suggest:
            suggest.click()
            time.sleep(0.8)
            return

        deadline = time.time() + 10
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const city = arguments[0];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  return rect.width > 0 && rect.height > 0;
                };
                const clean = ele => (ele.innerText || ele.value || '').trim().replace(/\\s+/g, ' ');
                const modal = document.querySelector('.city-modal');
                if (!modal || !visible(modal)) return {ok: false, reason: 'modal not visible'};

                const suggestItems = Array.from(modal.querySelectorAll('.suggest-list li'))
                  .filter(ele => visible(ele) && clean(ele).includes(city));
                const buckets = suggestItems.length ? [suggestItems[0]] : [modal];

                for (const bucket of buckets) {
                  if (bucket.matches && bucket.matches('.suggest-list li')) {
                    for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                      bucket.dispatchEvent(new MouseEvent(name, {
                        bubbles: true,
                        cancelable: true,
                        composed: true,
                        view: window,
                      }));
                    }
                    return {ok: true, text: clean(bucket)};
                  }
                  const candidates = Array.from(bucket.querySelectorAll('li,button,label,span,div'))
                    .filter(ele => {
                      const text = clean(ele);
                      if (!visible(ele) || !text.includes(city)) return false;
                      if (text.length > 40) return false;
                      if (ele.tagName === 'INPUT') return false;
                      if (ele.closest('.antd-lp-city-header')) return false;
                      return true;
                    })
                    .sort((a, b) => {
                      const aText = clean(a);
                      const bText = clean(b);
                      const aExact = aText === city ? 0 : 1;
                      const bExact = bText === city ? 0 : 1;
                      return aExact - bExact || aText.length - bText.length;
                    });
                  const target = candidates[0];
                  if (target) {
                    for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                      target.dispatchEvent(new MouseEvent(name, {
                        bubbles: true,
                        cancelable: true,
                        composed: true,
                        view: window,
                      }));
                    }
                    return {ok: true, text: clean(target)};
                  }
                }
                return {ok: false, reason: 'city result not found'};
                """,
                city,
            )
            if last_result and last_result.get("ok"):
                time.sleep(0.6)
                return
            time.sleep(0.3)
        raise RuntimeError(f"City search result not found: {city}, {last_result}")

    def wait_until_city_selected(self, city: str, timeout: int = 5) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            selected = self.page.run_js(
                """
                const city = arguments[0];
                const modal = document.querySelector('.city-modal');
                if (!modal) return false;
                const text = (modal.innerText || '').trim().replace(/\\s+/g, ' ');
                return text.includes(city) && /已选\\s*[（(][1-5]\\//.test(text);
                """,
                city,
            )
            if selected:
                return
            time.sleep(0.2)
        raise RuntimeError(f"City was not added to selected list: {city}")

    def click_city_modal_confirm(self) -> None:
        deadline = time.time() + 10
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const confirmText = arguments[0];
                const modal = document.querySelector('.city-modal');
                if (!modal) return {ok: false, reason: 'modal not found'};
                const button = Array.from(modal.querySelectorAll('button'))
                  .find(ele => {
                    const rect = ele.getBoundingClientRect();
                    return rect.width > 0
                      && rect.height > 0
                      && (ele.innerText || '').trim().replace(/\\s+/g, ' ') === confirmText
                      && !ele.disabled
                      && !String(ele.className).includes('disabled');
                  });
                if (!button) return {ok: false, reason: 'confirm button not ready'};
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  button.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                return {ok: true};
                """,
                CITY_CONFIRM_TEXT,
            )
            if last_result and last_result.get("ok"):
                self.wait_until_city_modal_closed()
                return
            time.sleep(0.3)
        raise RuntimeError(f"Could not confirm city modal: {last_result}")

    def is_city_visible_in_row(self, row_title: str, city: str, timeout: int = 2) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            ok = self.page.run_js(
                """
                const rowTitle = arguments[0];
                const city = arguments[1];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  return rect.width > 0 && rect.height > 0;
                };
                const clean = ele => (ele.innerText || '').trim().replace(/\\s+/g, ' ');
                const compact = value => String(value || '').replace(/\\s+/g, '');
                const title = Array.from(document.querySelectorAll('span, div'))
                  .find(ele => visible(ele) && compact(clean(ele)) === compact(rowTitle));
                let row = title;
                while (row && row !== document.body) {
                  const text = clean(row);
                  if (text.includes(city)) return true;
                  const parent = row.parentElement;
                  if (!parent) break;
                  const parentText = clean(parent);
                  const siblingTitles = Array.from(parent.querySelectorAll('span, div'))
                    .filter(ele => visible(ele) && /城市$/.test(compact(clean(ele))));
                  if (siblingTitles.length > 1 || parentText.length > 300) break;
                  row = parent;
                }
                return !!row && (row.innerText || '').includes(city);
                """,
                row_title,
                city,
            )
            if ok:
                return True
            time.sleep(0.2)
        return False

    def wait_until_city_modal_closed(self, timeout: int = 5) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            visible = self.page.run_js(
                """
                return Array.from(document.querySelectorAll('.city-modal'))
                  .some(ele => {
                    const rect = ele.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                  });
                """
            )
            if not visible:
                return
            time.sleep(0.2)
        raise RuntimeError("City modal did not close.")

    def select_industry_modal(self, row_title: str, industries: str) -> None:
        values = self.split_multi_values(industries)[:5]
        if not values:
            return
        self.progress.emit("industry_select", f"正在选择{row_title}：{', '.join(values)}")
        self.close_open_dropdown()
        self.open_industry_modal(row_title)
        for value in values:
            self.search_and_click_industry(value)
        self.click_industry_modal_confirm()
        self.progress.emit("industry_select", f"{row_title}已确认：{', '.join(values)}")

    def select_function_modal(self, row_title: str, functions: str) -> None:
        values = self.split_multi_values(functions)[:5]
        if not values:
            return
        self.progress.emit("function_select", f"正在选择{row_title}：{', '.join(values)}")
        self.close_open_dropdown()
        self.open_function_modal(row_title)
        for value in values:
            self.search_and_click_function(value)
        self.click_function_modal_confirm()
        self.progress.emit("function_select", f"{row_title}已确认：{', '.join(values)}")

    def open_function_modal(self, row_title: str) -> None:
        deadline = time.time() + 8
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const rowTitle = arguments[0];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '').trim().replace(/\\s+/g, ' ');
                const compact = value => String(value || '').replace(/\\s+/g, '');
                const fireClick = ele => {
                  ele.scrollIntoView({block: 'center', inline: 'nearest'});
                  for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    ele.dispatchEvent(new MouseEvent(name, {
                      bubbles: true,
                      cancelable: true,
                      composed: true,
                      view: window,
                    }));
                  }
                };
                const exactSelects = Array.from(document.querySelectorAll('.ant-lpt-select, [class*=select]'))
                  .filter(ele => visible(ele) && compact(clean(ele)) === compact(rowTitle));
                let target = exactSelects[0];
                if (!target) {
                  const title = Array.from(document.querySelectorAll('span, div, label'))
                    .find(ele => visible(ele) && compact(clean(ele)) === compact(rowTitle));
                  if (title) {
                    let row = title.closest('[class*=wrap], [class*=filter], [class*=item]') || title.parentElement;
                    for (let depth = 0; row && depth < 5; depth += 1, row = row.parentElement) {
                      const text = clean(row);
                      if (!text.includes(rowTitle)) continue;
                      const candidates = Array.from(row.querySelectorAll('.ant-lpt-select, [class*=select], button, label, span, div'))
                        .filter(ele => visible(ele) && ele !== title)
                        .filter(ele => {
                          const text = clean(ele);
                          const cls = String(ele.className || '').toLowerCase();
                          const cursor = getComputedStyle(ele).cursor;
                          return cls.includes('select')
                            || cursor === 'pointer'
                            || compact(text) === compact(rowTitle);
                        })
                        .sort((a, b) => {
                          const score = ele => {
                            const cls = String(ele.className || '').toLowerCase();
                            const text = compact(clean(ele));
                            if (cls.includes('select')) return 0;
                            if (text === compact(rowTitle)) return 1;
                            if (getComputedStyle(ele).cursor === 'pointer') return 2;
                            return 9;
                          };
                          const ar = a.getBoundingClientRect();
                          const br = b.getBoundingClientRect();
                          return score(a) - score(b) || (ar.width * ar.height) - (br.width * br.height);
                        });
                      if (candidates[0]) {
                        target = candidates[0];
                        break;
                      }
                      if (text.length > 500) break;
                    }
                  }
                }
                if (!target) return {ok: false, reason: 'function trigger not found'};
                fireClick(target);
                return {ok: true, text: clean(target)};
                """,
                row_title,
            )
            if last_result and last_result.get("ok"):
                try:
                    self.wait_for_function_modal(timeout=3)
                    return
                except RuntimeError:
                    pass
            time.sleep(0.3)
        raise RuntimeError(f"Could not open function modal: {row_title}, {last_result}")

    def wait_for_function_modal(self, timeout: int = 8) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = self.page.run_js(
                """
                const modalTitle = arguments[0];
                const placeholder = arguments[1];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const modals = Array.from(document.querySelectorAll(
                  '.ant-lpt-modal, .ant-modal, [role=dialog], [class*=modal], [class*=Modal]'
                )).filter(visible);
                return modals.some(ele => (ele.innerText || '').includes(modalTitle))
                  || Array.from(document.querySelectorAll('input[placeholder]'))
                    .some(ele => visible(ele) && ele.placeholder === placeholder);
                """,
                FUNCTION_MODAL_TITLE,
                FUNCTION_SEARCH_PLACEHOLDER,
            )
            if found:
                return
            time.sleep(0.2)
        raise RuntimeError("Function modal did not open.")

    def search_and_click_function(self, function_name: str) -> None:
        deadline = time.time() + 10
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const functionName = arguments[0];
                const modalTitle = arguments[1];
                const placeholder = arguments[2];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || ele.value || '')
                  .trim()
                  .replace(/\\s+/g, ' ');
                const compact = value => String(value || '').replace(/\\s+/g, '');
                const fireClick = ele => {
                  ele.scrollIntoView({block: 'center', inline: 'nearest'});
                  for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    ele.dispatchEvent(new MouseEvent(name, {
                      bubbles: true,
                      cancelable: true,
                      composed: true,
                      view: window,
                    }));
                  }
                };
                const findModal = () => {
                  const modals = Array.from(document.querySelectorAll(
                    '.ant-lpt-modal, .ant-modal, [role=dialog], [class*=modal], [class*=Modal]'
                  )).filter(visible);
                  return modals.find(ele => (ele.innerText || '').includes(modalTitle))
                    || modals.find(ele => ele.querySelector(`input[placeholder="${placeholder}"]`))
                    || null;
                };
                const modal = findModal();
                if (!modal) return {ok: false, reason: 'function modal not found'};
                const checked = Array.from(modal.querySelectorAll('span, button, label, li, div'))
                  .filter(ele => visible(ele))
                  .find(ele => compact(clean(ele)) === compact(functionName)
                    && /checked|selected|active/.test(String(ele.className || '').toLowerCase()));
                if (checked) return {ok: true, text: clean(checked), mode: 'already_selected'};
                const findOption = () => Array.from(modal.querySelectorAll('button, label, li, span, div'))
                  .filter(ele => visible(ele))
                  .map(ele => ({ele, text: clean(ele), rect: ele.getBoundingClientRect()}))
                  .filter(item => {
                    if (!item.text || item.ele.tagName === 'INPUT') return false;
                    if (item.text.includes('已选') || item.text.includes('确认') || item.text.includes('取消')) return false;
                    if (item.text.length > Math.max(36, functionName.length + 18)) return false;
                    return compact(item.text) === compact(functionName) || item.text.includes(functionName);
                  })
                  .sort((a, b) => {
                    const aExact = compact(a.text) === compact(functionName) ? 0 : 1;
                    const bExact = compact(b.text) === compact(functionName) ? 0 : 1;
                    const aArea = a.rect.width * a.rect.height;
                    const bArea = b.rect.width * b.rect.height;
                    return aExact - bExact || a.text.length - b.text.length || aArea - bArea;
                  });
                let candidates = findOption();
                let target = candidates[0] && candidates[0].ele;
                if (target) {
                  fireClick(target);
                  return {ok: true, text: clean(target), mode: 'visible_option'};
                }
                const input = Array.from(modal.querySelectorAll('input[placeholder]'))
                  .find(ele => visible(ele) && ele.placeholder === placeholder);
                if (input) {
                  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                  setter.call(input, functionName);
                  input.dispatchEvent(new Event('input', {bubbles: true}));
                  input.dispatchEvent(new Event('change', {bubbles: true}));
                }
                candidates = findOption();
                target = candidates[0] && candidates[0].ele;
                if (!target) return {ok: false, reason: 'function option not found'};
                fireClick(target);
                return {ok: true, text: clean(target), mode: 'search_fallback'};
                """,
                function_name,
                FUNCTION_MODAL_TITLE,
                FUNCTION_SEARCH_PLACEHOLDER,
            )
            if last_result and last_result.get("ok"):
                time.sleep(0.4)
                return
            time.sleep(0.35)
        raise RuntimeError(f"Function option not found: {function_name}, {last_result}")

    def click_function_modal_confirm(self) -> None:
        self.click_modal_confirm_by_title(FUNCTION_MODAL_TITLE, "Function")

    def open_industry_modal(self, row_title: str) -> None:
        deadline = time.time() + 8
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const rowTitle = arguments[0];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '')
                  .trim()
                  .replace(/\\s+/g, ' ');
                const compact = value => String(value || '').replace(/\\s+/g, '');
                const fireClick = ele => {
                  ele.scrollIntoView({block: 'center', inline: 'nearest'});
                  for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    ele.dispatchEvent(new MouseEvent(name, {
                      bubbles: true,
                      cancelable: true,
                      composed: true,
                      view: window,
                    }));
                  }
                };

                const exactSelects = Array.from(document.querySelectorAll('.ant-lpt-select, [class*=select]'))
                  .filter(ele => visible(ele) && compact(clean(ele)) === compact(rowTitle));
                let target = exactSelects[0];

                if (!target) {
                  const title = Array.from(document.querySelectorAll('span, div, label'))
                    .find(ele => visible(ele) && compact(clean(ele)) === compact(rowTitle));
                  if (title) {
                    let row = title.closest('[class*=wrap], [class*=filter], [class*=item]') || title.parentElement;
                    for (let depth = 0; row && depth < 5; depth += 1, row = row.parentElement) {
                      const text = clean(row);
                      if (!text.includes(rowTitle)) continue;
                      const candidates = Array.from(row.querySelectorAll('.ant-lpt-select, [class*=select], button, label, span, div'))
                        .filter(ele => visible(ele) && ele !== title)
                        .filter(ele => {
                          const text = clean(ele);
                          const cls = String(ele.className || '').toLowerCase();
                          const cursor = getComputedStyle(ele).cursor;
                          return cls.includes('select')
                            || cursor === 'pointer'
                            || compact(text) === compact(rowTitle);
                        })
                        .sort((a, b) => {
                          const score = ele => {
                            const cls = String(ele.className || '').toLowerCase();
                            const text = compact(clean(ele));
                            if (cls.includes('select')) return 0;
                            if (text === compact(rowTitle)) return 1;
                            if (getComputedStyle(ele).cursor === 'pointer') return 2;
                            return 9;
                          };
                          const ar = a.getBoundingClientRect();
                          const br = b.getBoundingClientRect();
                          return score(a) - score(b) || (ar.width * ar.height) - (br.width * br.height);
                        });
                      if (candidates[0]) {
                        target = candidates[0];
                        break;
                      }
                      if (text.length > 500) break;
                    }
                  }
                }

                if (!target) return {ok: false, reason: 'industry trigger not found'};
                fireClick(target);
                return {ok: true, text: clean(target)};
                """,
                row_title,
            )
            if last_result and last_result.get("ok"):
                try:
                    self.wait_for_industry_modal(timeout=3)
                    return
                except RuntimeError:
                    pass
            time.sleep(0.3)
        raise RuntimeError(f"Could not open industry modal: {row_title}, {last_result}")

    def wait_for_industry_modal(self, timeout: int = 8) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = self.page.run_js(
                """
                const modalTitle = arguments[0];
                const placeholder = arguments[1];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const modals = Array.from(document.querySelectorAll(
                  '.ant-lpt-modal, .ant-modal, [role=dialog], [class*=modal], [class*=Modal]'
                )).filter(visible);
                return modals.some(ele => (ele.innerText || '').includes(modalTitle))
                  || Array.from(document.querySelectorAll('input[placeholder]'))
                    .some(ele => visible(ele) && ele.placeholder === placeholder);
                """,
                INDUSTRY_MODAL_TITLE,
                INDUSTRY_SEARCH_PLACEHOLDER,
            )
            if found:
                return
            time.sleep(0.2)
        raise RuntimeError("Industry modal did not open.")

    def search_and_click_industry(self, industry: str) -> None:
        deadline = time.time() + 10
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const industry = arguments[0];
                const modalTitle = arguments[1];
                const placeholder = arguments[2];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || ele.value || '')
                  .trim()
                  .replace(/\\s+/g, ' ');
                const compact = value => String(value || '').replace(/\\s+/g, '');
                const fireClick = ele => {
                  ele.scrollIntoView({block: 'center', inline: 'nearest'});
                  for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    ele.dispatchEvent(new MouseEvent(name, {
                      bubbles: true,
                      cancelable: true,
                      composed: true,
                      view: window,
                    }));
                  }
                };
                const findModal = () => {
                  const modals = Array.from(document.querySelectorAll(
                    '.ant-lpt-modal, .ant-modal, [role=dialog], [class*=modal], [class*=Modal]'
                  )).filter(visible);
                  return modals.find(ele => (ele.innerText || '').includes(modalTitle))
                    || modals.find(ele => ele.querySelector(`input[placeholder="${placeholder}"]`))
                    || null;
                };
                const modal = findModal();
                if (!modal) return {ok: false, reason: 'industry modal not found'};

                const checked = Array.from(modal.querySelectorAll('span, button, label, li, div'))
                  .filter(ele => visible(ele))
                  .find(ele => compact(clean(ele)) === compact(industry)
                    && /checked|selected|active/.test(String(ele.className || '').toLowerCase()));
                if (checked) return {ok: true, text: clean(checked), mode: 'already_selected'};

                const findOption = () => Array.from(modal.querySelectorAll('button, label, li, span, div'))
                  .filter(ele => visible(ele))
                  .map(ele => ({ele, text: clean(ele), rect: ele.getBoundingClientRect()}))
                  .filter(item => {
                    if (!item.text || item.ele.tagName === 'INPUT') return false;
                    if (item.text.includes('已选') || item.text.includes('确认') || item.text.includes('取消')) return false;
                    if (item.text.length > Math.max(36, industry.length + 18)) return false;
                    return compact(item.text) === compact(industry) || item.text.includes(industry);
                  })
                  .sort((a, b) => {
                    const aExact = compact(a.text) === compact(industry) ? 0 : 1;
                    const bExact = compact(b.text) === compact(industry) ? 0 : 1;
                    const aArea = a.rect.width * a.rect.height;
                    const bArea = b.rect.width * b.rect.height;
                    return aExact - bExact || a.text.length - b.text.length || aArea - bArea;
                  });
                let candidates = findOption();
                let target = candidates[0] && candidates[0].ele;
                if (target) {
                  fireClick(target);
                  return {ok: true, text: clean(target), mode: 'visible_option'};
                }

                const input = Array.from(modal.querySelectorAll('input[placeholder]'))
                  .find(ele => visible(ele) && ele.placeholder === placeholder);
                if (input) {
                  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                  setter.call(input, industry);
                  input.dispatchEvent(new Event('input', {bubbles: true}));
                  input.dispatchEvent(new Event('change', {bubbles: true}));
                }
                candidates = findOption();
                target = candidates[0] && candidates[0].ele;
                if (!target) return {ok: false, reason: 'industry option not found'};
                fireClick(target);
                return {ok: true, text: clean(target), mode: 'search_fallback'};
                """,
                industry,
                INDUSTRY_MODAL_TITLE,
                INDUSTRY_SEARCH_PLACEHOLDER,
            )
            if last_result and last_result.get("ok"):
                time.sleep(0.4)
                return
            time.sleep(0.35)
        raise RuntimeError(f"Industry option not found: {industry}, {last_result}")

    def click_industry_modal_confirm(self) -> None:
        self.click_modal_confirm_by_title(INDUSTRY_MODAL_TITLE, "Industry")

    def click_modal_confirm_by_title(self, modal_title: str, label: str) -> None:
        deadline = time.time() + 10
        last_result = None
        confirm_texts_json = json.dumps([CONFIRM_TEXT, CITY_CONFIRM_TEXT], ensure_ascii=False)
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const modalTitle = arguments[0];
                const confirmTexts = JSON.parse(arguments[1]);
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
                };
                const clean = ele => (ele.innerText || ele.textContent || '')
                  .trim()
                  .replace(/\\s+/g, ' ');
                const compact = value => String(value || '').replace(/\\s+/g, '');
                const modals = Array.from(document.querySelectorAll(
                  '.ant-lpt-modal, .ant-modal, [role=dialog], [class*=modal], [class*=Modal]'
                )).filter(visible);
                const modal = modals.find(ele => (ele.innerText || '').includes(modalTitle)) || modals[modals.length - 1];
                if (!modal) return {ok: false, reason: 'industry modal not found'};
                const exact = ele => confirmTexts.some(text => compact(clean(ele)) === compact(text));
                const button = Array.from(modal.querySelectorAll('button'))
                  .filter(ele => visible(ele) && !ele.disabled)
                  .find(exact)
                  || Array.from(modal.querySelectorAll('span, div'))
                    .filter(ele => visible(ele))
                    .map(ele => exact(ele) ? (ele.closest('button') || ele) : null)
                    .find(Boolean);
                if (!button) return {ok: false, reason: 'confirm button not found'};
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  button.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                if (typeof button.click === 'function') button.click();
                return {ok: true};
                """,
                modal_title,
                confirm_texts_json,
            )
            if last_result and last_result.get("ok"):
                self.wait_until_modal_closed(modal_title, label)
                return
            time.sleep(0.3)
        raise RuntimeError(f"Could not confirm {label.lower()} modal: {last_result}")

    def wait_until_industry_modal_closed(self, timeout: int = 5) -> None:
        self.wait_until_modal_closed(INDUSTRY_MODAL_TITLE, "Industry", timeout=timeout)

    def wait_until_modal_closed(self, modal_title: str, label: str, timeout: int = 5) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            visible = self.page.run_js(
                """
                const modalTitle = arguments[0];
                const modals = Array.from(document.querySelectorAll(
                  '.ant-lpt-modal, .ant-modal, [role=dialog], [class*=modal], [class*=Modal]'
                ));
                return modals.some(ele => {
                  const rect = ele.getBoundingClientRect();
                  const style = getComputedStyle(ele);
                  return rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && (ele.innerText || '').includes(modalTitle);
                });
                """,
                modal_title,
            )
            if not visible:
                return
            time.sleep(0.2)
        raise RuntimeError(f"{label} modal did not close.")

    def click_row_option(self, row_title: str, option_text: str) -> None:
        result = self.page.run_js(
            """
            const rowTitle = arguments[0];
            const optionText = arguments[1];
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              return rect.width > 0 && rect.height > 0;
            };
            const clean = ele => (ele.innerText || '').trim().replace(/\\s+/g, ' ');
            const compact = value => String(value || '').replace(/\\s+/g, '');
            const title = Array.from(document.querySelectorAll('span'))
              .find(ele => compact(clean(ele)) === compact(rowTitle) && visible(ele));
            if (!title) return {ok: false, reason: 'row title not found'};
            const row = title.closest('[class*=wrap]');
            if (!row) return {ok: false, reason: 'row wrapper not found'};
            const target = Array.from(row.querySelectorAll('label, span, div'))
              .find(ele => clean(ele) === optionText && visible(ele));
            if (!target) return {ok: false, reason: 'option not found'};
            for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
              target.dispatchEvent(new MouseEvent(name, {
                bubbles: true,
                cancelable: true,
                composed: true,
                view: window,
              }));
            }
            return {ok: true};
            """,
            row_title,
            option_text,
        )
        if not result or not result.get("ok"):
            raise RuntimeError(f"Could not click row option: {row_title} -> {option_text}, {result}")

    def ensure_more_conditions_expanded(self) -> None:
        if self.is_more_conditions_expanded():
            return

        result = self.page.run_js(
            """
            const moreText = arguments[0];
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              const style = getComputedStyle(ele);
              return rect.width > 0
                && rect.height > 0
                && style.display !== 'none'
                && style.visibility !== 'hidden';
            };
            const clean = ele => (ele.innerText || ele.textContent || '')
              .trim()
              .replace(/\\s+/g, ' ');
            const buttons = Array.from(document.querySelectorAll('span, button, div'))
              .filter(ele => visible(ele)
                && clean(ele) === moreText
                && getComputedStyle(ele).cursor === 'pointer');
            const button = buttons[buttons.length - 1];
            if (!button) return {ok: false, reason: 'more button not found'};
            button.scrollIntoView({block: 'center', inline: 'nearest'});
            for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
              button.dispatchEvent(new MouseEvent(name, {
                bubbles: true,
                cancelable: true,
                composed: true,
                view: window,
              }));
            }
            return {ok: true};
            """,
            MORE_CONDITIONS_TEXT,
        )
        if not result or not result.get("ok"):
            raise RuntimeError(f"Could not click More Conditions button: {result}")

        deadline = time.time() + 5
        while time.time() < deadline:
            if self.is_more_conditions_expanded():
                return
            time.sleep(0.2)
        raise RuntimeError("More conditions did not expand.")

    def is_more_conditions_expanded(self) -> bool:
        return bool(
            self.page.run_js(
                """
                const clean = ele => (ele.innerText || '').trim().replace(/\\s+/g, ' ');
                const compact = value => String(value || '').replace(/\\s+/g, '');
                const otherTitle = Array.from(document.querySelectorAll('span'))
                  .find(ele => compact(clean(ele)) === '其他筛选');
                const row = otherTitle && otherTitle.closest('[class*=wrap]');
                if (!row) return false;
                return row.getBoundingClientRect().height > 60;
                """
            )
        )

    def select_dropdown_option(self, dropdown_title: str, option_text: str, keep_open: bool = False) -> None:
        if keep_open:
            if not self.has_visible_dropdown():
                self.open_dropdown(dropdown_title)
        else:
            self.close_open_dropdown()
            self.open_dropdown(dropdown_title)
        self.click_dropdown_option(option_text)
        if not keep_open:
            time.sleep(0.3)

    def open_dropdown(self, dropdown_title: str) -> None:
        result = self.page.run_js(
            """
            const title = arguments[0];
            const schoolTitle = arguments[1];
            const recruitmentTitle = arguments[2];
            const educationTitle = arguments[3];
            const visible = ele => {
              const rect = ele.getBoundingClientRect();
              return rect.width > 0 && rect.height > 0;
            };
            const clean = ele => (ele.innerText || '').trim().replace(/\\s+/g, ' ');
            const compact = value => String(value || '').replace(/\\s+/g, '');
            let candidates = Array.from(document.querySelectorAll('.ant-lpt-select, [class*=select]'))
              .filter(ele => visible(ele) && clean(ele) === title);

            if (!candidates.length && (title === schoolTitle || title === recruitmentTitle)) {
              const educationLabel = Array.from(document.querySelectorAll('span'))
                .find(ele => compact(clean(ele)) === compact(educationTitle) && visible(ele));
              const row = educationLabel && educationLabel.closest('[class*=wrap]');
              if (row) {
                const selects = Array.from(row.querySelectorAll('.ant-lpt-select')).filter(visible);
                candidates = title === schoolTitle
                  ? selects.filter(ele => String(ele.className).includes('multiple'))
                  : selects.filter(ele => !String(ele.className).includes('multiple'));
              }
            }

            candidates = candidates.sort((a, b) => {
              const ar = a.getBoundingClientRect();
              const br = b.getBoundingClientRect();
              return (ar.width * ar.height) - (br.width * br.height);
            });
            const target = candidates[0];
            if (!target) return {ok: false, reason: 'dropdown not found'};
            for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
              target.dispatchEvent(new MouseEvent(name, {
                bubbles: true,
                cancelable: true,
                composed: true,
                view: window,
              }));
            }
            return {ok: true};
            """,
            dropdown_title,
            SCHOOL_TYPE_TITLE,
            RECRUITMENT_TYPE_TITLE,
            EDUCATION_TITLE,
        )
        if not result or not result.get("ok"):
            raise RuntimeError(f"Could not open dropdown: {dropdown_title}, {result}")

    def has_visible_dropdown(self) -> bool:
        return bool(
            self.page.run_js(
                """
                return Array.from(document.querySelectorAll('.ant-lpt-select-dropdown:not(.ant-lpt-select-dropdown-hidden)'))
                  .some(ele => {
                    const rect = ele.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                  });
                """
            )
        )

    def click_dropdown_option(self, option_text: str) -> None:
        deadline = time.time() + 8
        last_result = None
        while time.time() < deadline:
            last_result = self.page.run_js(
                """
                const optionText = arguments[0];
                const visible = ele => {
                  const rect = ele.getBoundingClientRect();
                  return rect.width > 0 && rect.height > 0;
                };
                const clean = ele => (ele.innerText || '').trim().replace(/\\s+/g, ' ');
                const dropdowns = Array.from(document.querySelectorAll(
                  '.ant-lpt-select-dropdown:not(.ant-lpt-select-dropdown-hidden)'
                )).filter(visible);
                const dropdown = dropdowns[dropdowns.length - 1];
                if (!dropdown) return {ok: false, reason: 'dropdown panel not visible'};
                const options = Array.from(dropdown.querySelectorAll('[class*=option], li, div, span'))
                  .filter(ele => visible(ele) && clean(ele) === optionText)
                  .sort((a, b) => {
                    const ar = a.getBoundingClientRect();
                    const br = b.getBoundingClientRect();
                    return (ar.width * ar.height) - (br.width * br.height);
                  });
                const target = options[0];
                if (!target) return {ok: false, reason: 'option not found'};
                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                  target.dispatchEvent(new MouseEvent(name, {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                  }));
                }
                return {ok: true};
                """,
                option_text,
            )
            if last_result and last_result.get("ok"):
                time.sleep(0.4)
                return
            time.sleep(0.2)
        raise RuntimeError(f"Could not click dropdown option: {option_text}, {last_result}")

    def close_open_dropdown(self) -> None:
        self.page.run_js(
            """
            const target = document.querySelector('.searchBarBox--IpmLs') || document.body;
            for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
              target.dispatchEvent(new MouseEvent(name, {
                bubbles: true,
                cancelable: true,
                composed: true,
                view: window,
              }));
            }
            """
        )
        time.sleep(0.3)

    @staticmethod
    def split_multi_values(value: str) -> list[str]:
        for sep in ["，", "、", ";", "；", "|", "\n"]:
            value = value.replace(sep, ",")
        return [item.strip() for item in value.split(",") if item.strip()]

    def has_visible_ai_popover(self) -> bool:
        return bool(
            self.page.run_js(
                """
                return Array.from(document.querySelectorAll('.ant-lpt-popover'))
                  .some(ele => {
                    const rect = ele.getBoundingClientRect();
                    return rect.width > 0
                      && rect.height > 0
                      && (ele.innerText || '').includes('AI');
                  });
                """
            )
        )

    def wait_for_input(self, placeholder: str, timeout: int = 10):
        return self.page.ele(f'xpath://input[@placeholder="{placeholder}"]', timeout=timeout)

    def wait_until_input_value(self, placeholder: str, expected: str, timeout: int = 5) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = self.page.run_js(
                """
                const placeholder = arguments[0];
                const input = Array.from(document.querySelectorAll('input[placeholder]'))
                  .find(ele => ele.placeholder === placeholder);
                return input ? input.value : null;
                """,
                placeholder,
            )
            if value == expected:
                return
            time.sleep(0.2)
        raise RuntimeError(f"Input did not take effect: {placeholder} -> {expected}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill Liepin search filters.")
    parser.add_argument("--port", type=int, default=DEFAULT_BROWSER_PORT)
    parser.add_argument("--keywords", default="")
    parser.add_argument("--job", default="")
    parser.add_argument("--company", default="")
    parser.add_argument("--current-city", default="")
    parser.add_argument("--expected-city", default="")
    parser.add_argument("--experience", default="")
    parser.add_argument("--education", default="")
    parser.add_argument("--recruitment-type", default="")
    parser.add_argument("--school-types", default="")
    parser.add_argument("--active-status", default="")
    parser.add_argument("--job-status", default="")
    parser.add_argument("--job-hop-frequency", default="")
    parser.add_argument("--age-requirement", default="")
    parser.add_argument("--gender-requirement", default="")
    parser.add_argument("--language-requirement", default="")
    parser.add_argument("--graduation-year", default="")
    parser.add_argument("--current-industries", default="")
    parser.add_argument("--expected-industries", default="")
    parser.add_argument("--current-functions", default="")
    parser.add_argument("--expected-functions", default="")
    parser.add_argument("--keywords-ai", action="store_true")
    parser.add_argument("--job-ai", action="store_true")
    parser.add_argument("--company-ai", action="store_true")
    parser.add_argument("--match-requirements", default="")
    parser.add_argument("--deepseek-api-key", default="")
    parser.add_argument("--deepseek-model", default="deepseek-chat")
    parser.add_argument("--no-auto-communicate", action="store_true")
    parser.add_argument("--candidate-limit", type=int, default=1)
    args = parser.parse_args()

    search_page = LiepinSearchPage(port=args.port)
    search_page.open()
    search_page.apply_filters(
        SearchFilters(
            match_requirements=args.match_requirements.strip(),
            deepseek_api_key=args.deepseek_api_key.strip(),
            deepseek_model=args.deepseek_model.strip(),
            auto_communicate=not args.no_auto_communicate,
            candidate_limit=args.candidate_limit,
            keywords=args.keywords.strip(),
            job_name=args.job.strip(),
            company_name=args.company.strip(),
            current_city=args.current_city.strip(),
            expected_city=args.expected_city.strip(),
            experience=args.experience.strip(),
            education=args.education.strip(),
            recruitment_type=args.recruitment_type.strip(),
            school_types=args.school_types.strip(),
            active_status=args.active_status.strip(),
            job_status=args.job_status.strip(),
            job_hop_frequency=args.job_hop_frequency.strip(),
            age_requirement=args.age_requirement.strip(),
            gender_requirement=args.gender_requirement.strip(),
            language_requirement=args.language_requirement.strip(),
            graduation_year=args.graduation_year.strip(),
            current_industries=args.current_industries.strip(),
            expected_industries=args.expected_industries.strip(),
            current_functions=args.current_functions.strip(),
            expected_functions=args.expected_functions.strip(),
            use_keywords_ai_words=args.keywords_ai,
            use_job_ai_words=args.job_ai,
            use_company_ai_words=args.company_ai,
        )
    )
    print(f"Done: {search_page.page.url}")


if __name__ == "__main__":
    main()
