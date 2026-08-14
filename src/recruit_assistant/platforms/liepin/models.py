from __future__ import annotations

from dataclasses import dataclass


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
    age_min: str = ""
    age_max: str = ""
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
