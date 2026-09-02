from __future__ import annotations

import json
import inspect
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from recruit_assistant.environment import parse_env_file
from recruit_assistant.app_backend import AppState, DEFAULT_AI_TEMPLATE_ID, browser_port_candidates, default_ai_templates, new_task, normalize_company_model, normalize_config, normalize_platform, platform_home_url, select_browser_port
from recruit_assistant.model_config import messages_for_endpoint, resolve_model_endpoint
from recruit_assistant.platforms.liepin.ai_matcher import AiMatcherMixin
from recruit_assistant.platforms.liepin.browser import same_site_url
from recruit_assistant.platforms.liepin.constants import DEFAULT_MATCH_REQUIREMENTS, RUNTIME_DIR
from recruit_assistant.platforms.liepin.filters import FilterMixin
from recruit_assistant.platforms.liepin.models import BatchProgress, SearchFilters
from recruit_assistant.platforms.maimai.ai_matcher import MaimaiAiMatcherMixin
from recruit_assistant.platforms.maimai.automation import MaimaiRecruitPage
from recruit_assistant.platforms.maimai.constants import DEFAULT_MAIMAI_MATCH_REQUIREMENTS, MAIMAI_TALENTS_URL
from recruit_assistant.updater import install_downloaded_update, launch_update_installer_and_exit


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

    def test_browser_port_candidates_keep_9225_first(self) -> None:
        candidates = browser_port_candidates()
        self.assertEqual(candidates[0], 9225)
        self.assertIn(9226, candidates)

    def test_browser_port_falls_back_to_ready_debug_endpoint(self) -> None:
        with patch("recruit_assistant.app_backend.debug_browser_ready_stable", side_effect=lambda port, **_kwargs: port == 9226), patch(
            "recruit_assistant.app_backend.is_local_port_open", return_value=True
        ):
            self.assertEqual(select_browser_port(), 9226)

    def test_normalize_config_hides_legacy_custom_browser_port(self) -> None:
        self.assertEqual(normalize_config({"port": 9333})["port"], 9225)

    def test_old_deepseek_model_config_keeps_deepseek_provider(self) -> None:
        self.assertEqual(normalize_company_model("deepseek-chat"), "minimax")
        config = normalize_config({"deepseek_model": "deepseek-chat"})
        self.assertEqual(config["ai_provider"], "deepseek")
        self.assertEqual(config["deepseek_model"], "deepseek-chat")

    def test_ai_provider_selects_separate_endpoints_and_defaults(self) -> None:
        company = resolve_model_endpoint("company", "", company_api_key="company-key")
        deepseek = resolve_model_endpoint("deepseek", "", deepseek_api_key="deepseek-key")
        self.assertEqual(company.model, "minimax")
        self.assertTrue(company.chat_completions_url.endswith("/v1/chat/completions"))
        self.assertTrue(company.use_json_response_format)
        self.assertEqual(deepseek.model, "deepseek-chat")
        self.assertEqual(deepseek.chat_completions_url, "https://api.deepseek.com/chat/completions")
        self.assertFalse(deepseek.use_json_response_format)

    def test_ai_provider_keeps_separate_local_keys(self) -> None:
        config = normalize_config(
            {
                "ai_provider": "company",
                "company_model_api_key": "company-key",
                "deepseek_api_key": "deepseek-key",
            }
        )
        self.assertEqual(config["company_model_api_key"], "company-key")
        self.assertEqual(config["deepseek_api_key"], "deepseek-key")

    def test_company_only_adds_output_consistency_instruction(self) -> None:
        messages = [{"role": "system", "content": "base"}, {"role": "user", "content": "request"}]
        company = resolve_model_endpoint("company", company_api_key="company-key")
        deepseek = resolve_model_endpoint("deepseek", deepseek_api_key="deepseek-key")
        self.assertIn("字段必须相互一致", messages_for_endpoint(messages, company)[0]["content"])
        self.assertEqual(messages_for_endpoint(messages, deepseek), messages)
        self.assertEqual(messages[0]["content"], "base")

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

    def test_maimai_phone_confirmation_accepts_a_timeout(self) -> None:
        signature = inspect.signature(MaimaiRecruitPage.confirm_maimai_phone_request)
        self.assertIn("timeout", signature.parameters)

    def test_maimai_phone_status_recognizes_requested_and_available(self) -> None:
        requested = MaimaiRecruitPage.read_maimai_phone_request_state("您向对方发起了交换手机号的申请", already=False)
        available = MaimaiRecruitPage.read_maimai_phone_request_state("手机号已交换", already=True)
        self.assertEqual(requested["status"], "requested")
        self.assertEqual(available["status"], "already_available")

    def test_maimai_phone_toolbar_states_do_not_trigger_duplicate_exchange(self) -> None:
        class Frame:
            def __init__(self, button_text: str) -> None:
                self.button_text = button_text

            def run_js(self, _script: str):
                return {
                    "ok": True,
                    "state": "already_requested" if self.button_text == "申请中" else "already_available",
                    "text": self.button_text,
                    "message": "toolbar state",
                }

        for button_text, expected_status in (("申请中", "already_requested"), ("拨打电话", "already_available")):
            page = object.__new__(MaimaiRecruitPage)
            page.stop_event = None
            with patch.object(page, "get_maimai_chat_frame", return_value=Frame(button_text)), patch.object(
                page, "read_maimai_active_chat_text", return_value=""
            ):
                result = page.request_phone_in_maimai_chat(object(), timeout=0.1)
            self.assertEqual(result["status"], expected_status)
            self.assertEqual(result["data"]["button"]["text"], button_text)

    def test_maimai_phone_exchange_succeeds_when_button_turns_pending(self) -> None:
        class Frame:
            def run_js(self, _script: str):
                return {
                    "ok": True,
                    "clicked": True,
                    "text": "交换手机",
                    "class_name": "tool normal",
                }

        page = object.__new__(MaimaiRecruitPage)
        page.stop_event = None
        pending = {"status": "already_requested", "message": "按钮显示“申请中”。", "data": {"text": "申请中"}}
        with patch.object(page, "get_maimai_chat_frame", return_value=Frame()), patch.object(
            page, "read_maimai_active_chat_text", return_value=""
        ), patch.object(page, "wait_for_maimai_phone_button_state", return_value=pending):
            result = page.request_phone_in_maimai_chat(object(), timeout=0.1)
        self.assertEqual(result["status"], "requested")
        self.assertIn("按钮已变为“申请中”", result["message"])

    def test_maimai_quick_reply_state_skips_communication(self) -> None:
        class Page:
            def run_js(self, _script: str, *_args):
                return {
                    "ok": True,
                    "status": "quick_reply_skip",
                    "text": "快捷回复",
                    "message": "右侧按钮为“快捷回复”，说明候选人已投递职位，本次不触发立即沟通或交换手机。",
                }

        page = object.__new__(MaimaiRecruitPage)
        page.page = Page()
        page.stop_event = None
        result = page.click_current_candidate_immediate_communicate("方封域", timeout=0.1)
        self.assertEqual(result["status"], "quick_reply_skip")

    def test_maimai_smoke_test_is_hard_bounded_to_five_candidates(self) -> None:
        self.assertEqual(MaimaiRecruitPage.clean_optional_positive_int(5), 5)
        self.assertEqual(max(1, min(int(99 or 5), 5)), 5)

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

    def test_ai_json_parser_ignores_company_model_thinking_block(self) -> None:
        payload = {"match": True, "score": 88}
        content = "<think>内部推理，不属于最终 JSON</think>\n" + json.dumps(payload)
        self.assertEqual(AiMatcherMixin.parse_ai_json(content), payload)

    def test_company_model_response_uses_openai_chat_shape(self) -> None:
        response = {"choices": [{"message": {"content": '{"match":false}'}}]}
        self.assertEqual(AiMatcherMixin.extract_chat_content(response), '{"match":false}')

    def test_env_file_parser_handles_comments_and_quotes(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('# comment\nDEEPSEEK_API_KEY="test-key"\nEMPTY=\n', encoding="utf-8")
            self.assertEqual(
                parse_env_file(path),
                {"DEEPSEEK_API_KEY": "test-key", "EMPTY": ""},
            )

    def test_self_update_installer_replaces_target_without_scripts(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "runtime" / "updates" / "RecruitTool.exe"
            target = root / "招聘工具.exe"
            log_path = source.parent / "install_update.log"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"MZ-new-version")
            target.write_bytes(b"MZ-old-version")
            result = install_downloaded_update(source, target, 999999, log_path)
            self.assertEqual(result, 0)
            self.assertEqual(target.read_bytes(), b"MZ-new-version")
            self.assertEqual(target.with_suffix(".exe.bak").read_bytes(), b"MZ-old-version")
            self.assertFalse((source.parent / "install_update.ps1").exists())
            self.assertFalse((source.parent / "install_update.vbs").exists())

    def test_update_launcher_starts_downloaded_executable_directly(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "updates" / "RecruitTool.exe"
            target = root / "招聘工具.exe"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"MZ-new-version")
            target.write_bytes(b"MZ-old-version")
            with patch("recruit_assistant.updater.current_executable", return_value=target), patch(
                "recruit_assistant.updater.subprocess.Popen"
            ) as popen, patch("recruit_assistant.updater.time.sleep"), patch(
                "recruit_assistant.updater.os._exit", side_effect=SystemExit
            ):
                with self.assertRaises(SystemExit):
                    launch_update_installer_and_exit(source, "招聘工具")
            command = popen.call_args.args[0]
            self.assertEqual(command[0], str(source))
            self.assertIn("--install-update", command)
            self.assertNotIn("powershell", " ".join(command).lower())

    def test_app_state_queues_user_notifications(self) -> None:
        state = AppState()
        state.notify("任务完成", "已处理 2 人。", "success")
        snapshot = state.snapshot()
        self.assertEqual(snapshot["notifications"][-1]["title"], "任务完成")
        self.assertEqual(snapshot["notifications"][-1]["message"], "已处理 2 人。")
        self.assertEqual(snapshot["notifications"][-1]["level"], "success")

    def test_default_ai_templates_include_sales_template(self) -> None:
        templates = default_ai_templates()
        self.assertEqual(templates[0]["id"], DEFAULT_AI_TEMPLATE_ID)
        sales = next(item for item in templates if item["id"] == "sales-screening")
        self.assertIn("大客户", sales["prompt"])

    def test_ai_template_is_applied_at_run_time(self) -> None:
        state = AppState()
        state.data["ai_templates"] = default_ai_templates()
        result = state.apply_ai_template({"ai_template_id": "sales-screening", "match_requirements": "旧提示词"})
        self.assertIn("大客户", result["match_requirements"])

    def test_duplicate_task_is_disabled_and_keeps_template(self) -> None:
        state = AppState()
        source = state.data["tasks"][0]
        source["name"] = "销售筛选"
        source["enabled"] = True
        source["config"]["ai_template_id"] = "sales-screening"
        with patch.object(state, "save"):
            duplicate = state.duplicate_task(source["id"])
        self.assertNotEqual(duplicate["id"], source["id"])
        self.assertEqual(duplicate["name"], "销售筛选 - 副本")
        self.assertFalse(duplicate["enabled"])
        self.assertEqual(duplicate["config"]["ai_template_id"], "sales-screening")

    def test_tasks_keep_independent_selected_chat_jobs(self) -> None:
        state = AppState()
        first_id = state.data["tasks"][0]["id"]
        job_a = {"job_id": "job-a", "title": "销售经理 A", "label": "销售经理 A | 深圳"}
        job_b = {"job_id": "job-b", "title": "销售经理 B", "label": "销售经理 B | 上海"}
        with patch.object(state, "save"):
            first = state.save_task({"id": first_id, "name": "配置 A", "config": {"selected_chat_job": job_a}})
            second = state.save_task({"name": "配置 B", "config": {"selected_chat_job": job_b}})
        self.assertEqual(first["config"]["selected_chat_job"]["job_id"], "job-a")
        self.assertEqual(second["config"]["selected_chat_job"]["job_id"], "job-b")
        self.assertEqual(state.find_task(first_id)["config"]["selected_chat_job"]["job_id"], "job-a")

    def test_scheduled_tasks_are_dispatched_in_order(self) -> None:
        state = AppState()
        first_id = state.data["tasks"][0]["id"]
        second = new_task("第二套配置")
        state.data["tasks"].append(second)
        calls = []
        completed = threading.Event()

        def fake_run(task_id: str, reason: str, wait_for_lock: bool = False) -> None:
            calls.append((task_id, reason, wait_for_lock))
            if len(calls) == 2:
                completed.set()

        with patch.object(state, "run_task", side_effect=fake_run):
            state.scheduled_queue = [(first_id, "定时 09:00"), (second["id"], "定时 09:00")]
            state._dispatch_scheduled_task()
            self.assertTrue(completed.wait(2))

        self.assertEqual([item[0] for item in calls], [first_id, second["id"]])
        self.assertTrue(all(item[2] for item in calls))

    def test_task_failure_creates_interruption_notification(self) -> None:
        state = AppState()
        task = state.data["tasks"][0]
        with patch("recruit_assistant.app_backend.ensure_debug_browser_for_work", return_value=False):
            state.run_task(task["id"], "测试")
        notification = state.snapshot()["notifications"][-1]
        self.assertEqual(notification["title"], "任务中断")
        self.assertIn("浏览器调试端口", notification["message"])
        self.assertEqual(notification["level"], "error")


if __name__ == "__main__":
    unittest.main()
