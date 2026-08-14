from __future__ import annotations

import json
import time

from .constants import (
    ACTIVE_STATUS_TITLE,
    COMPANY_PLACEHOLDER,
    CURRENT_CITY_TITLE,
    CURRENT_FUNCTION_TITLE,
    CURRENT_INDUSTRY_TITLE,
    EDUCATION_TITLE,
    EXPECTED_CITY_TITLE,
    EXPECTED_FUNCTION_TITLE,
    EXPECTED_INDUSTRY_TITLE,
    EXPERIENCE_TITLE,
    GENDER_REQUIREMENT_TITLE,
    GRADUATION_YEAR_TITLE,
    JOB_HOP_FREQUENCY_TITLE,
    JOB_PLACEHOLDER,
    JOB_STATUS_TITLE,
    KEYWORDS_PLACEHOLDER,
    LANGUAGE_REQUIREMENT_TITLE,
    RECRUITMENT_TYPE_TITLE,
    RUNTIME_DIR,
    SCHOOL_TYPE_TITLE,
    SEARCH_URL,
)
from .models import SearchFilters


class WorkflowMixin:
    def open(self) -> None:
        self.check_stopped()
        self.page.get(SEARCH_URL)
        self.wait_for_input(KEYWORDS_PLACEHOLDER)
        self.wait_for_input(JOB_PLACEHOLDER)
        self.wait_for_input(COMPANY_PLACEHOLDER)

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
        if filters.age_min or filters.age_max:
            self.check_stopped()
            self.select_custom_age_range(filters.age_min, filters.age_max)
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
