from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from recruit_assistant.environment import parse_env_file
from recruit_assistant.app_backend import normalize_config, normalize_platform, platform_home_url
from recruit_assistant.platforms.liepin.ai_matcher import AiMatcherMixin
from recruit_assistant.platforms.liepin.browser import same_site_url
from recruit_assistant.platforms.liepin.constants import DEFAULT_MATCH_REQUIREMENTS, RUNTIME_DIR
from recruit_assistant.platforms.liepin.filters import FilterMixin
from recruit_assistant.platforms.liepin.models import BatchProgress, SearchFilters
from recruit_assistant.platforms.maimai.ai_matcher import MaimaiAiMatcherMixin
from recruit_assistant.platforms.maimai.constants import DEFAULT_MAIMAI_MATCH_REQUIREMENTS, MAIMAI_TALENTS_URL


class LiepinCoreTests(unittest.TestCase):
    def test_runtime_directory_is_at_repository_root(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        self.assertEqual(RUNTIME_DIR, repository_root / "runtime")

    def test_same_site_url_ignores_www_prefix(self) -> None:
        self.assertTrue(same_site_url("https://www.liepin.com/a", "https://liepin.com/b"))
        self.assertFalse(same_site_url("https://liepin.com", "https://example.com"))

    def test_search_filter_defaults_are_safe(self) -> None:
        filters = SearchFilters()
        self.assertEqual(filters.candidate_limit, 1)
        self.assertTrue(filters.auto_communicate)
        self.assertFalse(filters.request_phone_after_communicate)

    def test_platform_normalization_supports_liepin_and_maimai(self) -> None:
        self.assertEqual(normalize_platform("liepin"), "liepin")
        self.assertEqual(normalize_platform("maimai"), "maimai")
        self.assertEqual(normalize_platform("unknown"), "liepin")

    def test_maimai_default_filters_are_available(self) -> None:
        config = normalize_config({"platform": "maimai"})
        self.assertEqual(config["platform"], "maimai")
        self.assertIn("maimai_city", config)
        self.assertIn("maimai_education", config)
        self.assertIn("maimai_experience", config)
        self.assertIn("maimai_graduation_year", config)
        self.assertIn("maimai_company", config)
        self.assertIn("maimai_keywords", config)
        self.assertIn("maimai_keyword_mode", config)
        self.assertIn("maimai_gender", config)
        self.assertIn("maimai_age_min", config)
        self.assertIn("maimai_age_max", config)
        self.assertEqual(config["maimai_page_limit"], 1)
        self.assertFalse(config["maimai_auto_communicate"])
        self.assertEqual(platform_home_url("maimai"), MAIMAI_TALENTS_URL)

    def test_default_match_requirements_keep_chinese_text(self) -> None:
        self.assertIn("通用要求：", DEFAULT_MATCH_REQUIREMENTS)
        self.assertIn("候选人的过往经历", DEFAULT_MATCH_REQUIREMENTS)
        self.assertNotIn("?" * 3, DEFAULT_MATCH_REQUIREMENTS)


    def test_maimai_default_match_requirements_are_separate(self) -> None:
        self.assertIn("本次搜索关键词", DEFAULT_MAIMAI_MATCH_REQUIREMENTS)
        self.assertNotIn("开聊职位", DEFAULT_MAIMAI_MATCH_REQUIREMENTS)

    def test_liepin_and_maimai_ai_prompts_keep_different_shapes(self) -> None:
        requirements = "要有大客户销售经验，最好做过医疗行业"
        liepin_messages = AiMatcherMixin().build_match_messages(
            {"basic": {"name": "张三"}, "selected_chat_job": {}},
            requirements,
        )
        maimai_messages = MaimaiAiMatcherMixin().build_match_messages(
            [{"page_candidate_index": 1, "name": "张三"}],
            requirements,
        )
        self.assertIn('"match"', liepin_messages[1]["content"])
        self.assertNotIn('"results"', liepin_messages[1]["content"])
        self.assertIn('"results"', maimai_messages[1]["content"])
        self.assertIn("page_candidate_index", maimai_messages[1]["content"])

    def test_batch_progress_emits_structured_event(self) -> None:
        events = []
        progress = BatchProgress(events.append)
        progress.emit("candidate", "loaded", {"index": 1})
        self.assertEqual(
            events,
            [{"event": "candidate", "message": "loaded", "data": {"index": 1}}],
        )

    def test_batch_progress_honors_stop_event(self) -> None:
        stop_event = threading.Event()
        stop_event.set()
        progress = BatchProgress(stop_event=stop_event)
        with self.assertRaisesRegex(RuntimeError, "任务已停止"):
            progress.check_stopped()

    def test_multi_value_normalization(self) -> None:
        values = FilterMixin.split_multi_values("本科，硕士、博士; 本科")
        self.assertEqual(values, ["本科", "硕士", "博士", "本科"])

    def test_ai_json_parser_accepts_markdown_fence(self) -> None:
        payload = {"match": True, "score": 88}
        content = "```json\n" + json.dumps(payload) + "\n```"
        self.assertEqual(AiMatcherMixin.parse_ai_json(content), payload)

    def test_env_file_parser_handles_comments_and_quotes(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('# comment\nDEEPSEEK_API_KEY="test-key"\nEMPTY=\n', encoding="utf-8")
            self.assertEqual(
                parse_env_file(path),
                {"DEEPSEEK_API_KEY": "test-key", "EMPTY": ""},
            )


if __name__ == "__main__":
    unittest.main()
