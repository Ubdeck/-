import contextlib
from typing import Callable, Optional

from .config import SearchSettings
from .contacted_candidates import reset_contacted_candidates
from .legacy import load_chat_flow_module, load_resume_extract_batch_module, load_search_module
from .logging_utils import CallbackWriter
from .matching import match_candidates, matched_candidates_path, reset_match_results


Logger = Optional[Callable[[str], None]]


def _log(callback: Logger, message: str):
    if callback:
        callback(message)


def _run_with_capture(callback: Logger, func, *args, **kwargs):
    if callback:
        writer = CallbackWriter(callback)
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            result = func(*args, **kwargs)
        writer.flush()
        return result
    return func(*args, **kwargs)


def run_search(settings: SearchSettings, callback: Logger = None):
    search_module = load_search_module()
    config = settings.to_search_config()
    _log(callback, "开始执行脉脉候选人搜索。")
    _run_with_capture(callback, search_module.run_candidate_search, config)
    _log(callback, "搜索和筛选条件已执行完成。")


def run_deepseek_match(settings: SearchSettings, callback: Logger = None):
    _log(callback, "开始调用 DeepSeek 判断当前页候选人。")
    result = match_candidates(settings)
    matched = result.get("matched_candidates", [])
    _log(callback, f"当前页 AI 匹配完成，通过 {len(matched)} 人。")
    _log(callback, f"匹配结果已写入：{matched_candidates_path()}")
    return result


def run_full_pipeline(settings: SearchSettings, callback: Logger = None):
    batch_module = load_resume_extract_batch_module()
    chat_module = load_chat_flow_module()

    reset_match_results()
    reset_contacted_candidates()
    run_search(settings, callback)
    page = batch_module.connect_page()

    last_match_result = {
        "matched_candidates": [],
        "rejected_candidates": [],
        "summary": "",
    }

    target_pages = max(1, int(settings.page_limit))
    processed_pages = 0

    while processed_pages < target_pages:
        page_number = processed_pages + 1
        _log(callback, f"开始处理第 {page_number}/{target_pages} 页。")

        page_candidates = _run_with_capture(
            callback,
            batch_module.extract_current_page,
            page_number,
            None,
            page,
        )
        current_count = len(page_candidates or [])
        if current_count <= 0:
            raise RuntimeError(f"第 {page_number} 页未提取到候选人，流程中断。")

        _log(callback, f"第 {page_number} 页提取完成，共 {current_count} 人。")

        last_match_result = run_deepseek_match(settings, callback)
        current_matched = len(last_match_result.get("matched_candidates", []))
        _log(callback, f"第 {page_number} 页 AI 通过 {current_matched} 人。")

        if current_matched <= 0:
            _log(callback, f"第 {page_number} 页没有通过人选。")
        else:
            _log(
                callback,
                f"开始处理第 {page_number} 页通过名单，模式：{'实际发送' if settings.actual_send else '测试模式'}。",
            )
            _run_with_capture(
                callback,
                chat_module.run_chat_flow_test,
                settings.greeting,
                settings.actual_send,
                page_number,
                page,
            )
            _log(callback, f"第 {page_number} 页沟通流程完成。")

        processed_pages += 1
        if processed_pages >= target_pages:
            break

        if not _run_with_capture(callback, batch_module.goto_next_page, page):
            raise RuntimeError(
                f"第 {page_number} 页处理完成后，未能成功跳转到下一页。"
                f" 目标共 {target_pages} 页，实际仅完成 {processed_pages} 页。"
            )

    _log(callback, f"已完成设定的 {processed_pages} 页，流程结束。")

    return last_match_result
