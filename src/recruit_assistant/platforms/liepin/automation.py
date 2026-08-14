from __future__ import annotations

import argparse

from .ai_matcher import AiMatcherMixin
from .browser import connect_chromium_page
from .candidates import CandidateMixin
from .communication import CommunicationMixin
from .constants import (
    CHAT_URL,
    DEFAULT_BROWSER_PORT,
    DEFAULT_MATCH_REQUIREMENTS,
    JOB_MANAGER_URL,
    SEARCH_URL,
)
from .filters import FilterMixin
from .job_manager import JobManagerMixin
from .models import BatchProgress, SearchFilters
from .workflow import WorkflowMixin


__all__ = [
    "CHAT_URL",
    "DEFAULT_BROWSER_PORT",
    "DEFAULT_MATCH_REQUIREMENTS",
    "JOB_MANAGER_URL",
    "SEARCH_URL",
    "LiepinSearchPage",
    "SearchFilters",
    "connect_chromium_page",
]


class LiepinSearchPage(
    WorkflowMixin,
    JobManagerMixin,
    CandidateMixin,
    AiMatcherMixin,
    CommunicationMixin,
    FilterMixin,
):
    """Facade for the complete Liepin recruitment workflow."""

    def __init__(self, port: int = DEFAULT_BROWSER_PORT, progress_callback=None, stop_event=None) -> None:
        self.page = connect_chromium_page(search_url=SEARCH_URL, port=port)
        self.progress = BatchProgress(progress_callback, stop_event)

    def check_stopped(self) -> None:
        self.progress.check_stopped()


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
    parser.add_argument("--age-min", default="")
    parser.add_argument("--age-max", default="")
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
            age_min=args.age_min.strip(),
            age_max=args.age_max.strip(),
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
