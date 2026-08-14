from __future__ import annotations

import json
import re
import time
from urllib import error, request

from ...environment import get_env_value
from .browser import append_runtime_log
from .constants import DEFAULT_MAIMAI_MATCH_REQUIREMENTS, RUNTIME_DIR


class MaimaiAiMatcherMixin:
    def analyze_current_page_candidates(self, results: list[dict], config: dict) -> dict:
        requirements = self.clean_config_value(config.get("match_requirements")) or DEFAULT_MAIMAI_MATCH_REQUIREMENTS
        api_key = self.clean_config_value(config.get("deepseek_api_key")) or get_env_value("DEEPSEEK_API_KEY")
        model = self.clean_config_value(config.get("deepseek_model")) or "deepseek-chat"

        if not results:
            summary = {"processed": 0, "matched": 0, "results": []}
            self.save_batch_summary(summary["results"], summary)
            return summary

        self.emit("maimai_ai_start", f"正在把本页 {len(results)} 人一次性发给 DeepSeek")
        if not api_key:
            raise RuntimeError("请在UI填写 DeepSeek API Key，或设置环境变量 DEEPSEEK_API_KEY。")

        candidates = [self.build_candidate_payload(item) for item in results]
        payload = {
            "model": model.strip() or "deepseek-chat",
            "messages": self.build_match_messages(candidates, requirements),
            "temperature": 0.1,
            "stream": False,
        }

        try:
            body = self.urlopen_with_retry(
                request.Request(
                    "https://api.deepseek.com/chat/completions",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "Connection": "close",
                    },
                    method="POST",
                ),
                retries=5,
                timeout=60,
            )
            response = json.loads(body)
            content = response["choices"][0]["message"]["content"]
            parsed = self.parse_ai_json(content)
            batch_result = self.merge_ai_results(results, parsed, requirements, payload, content)
            self.emit_candidate_results(batch_result["results"])
            self.emit(
                "maimai_ai_done",
                f"DeepSeek 批量分析完成：处理 {batch_result['processed']} 人，匹配 {batch_result['matched']} 人",
                batch_result,
            )
            self.save_batch_summary(batch_result["results"], batch_result)
            return batch_result
        except Exception as exc:
            append_runtime_log(f"maimai batch ai failed: {exc}")
            batch_result = self.build_fallback_batch_result(results, requirements, payload, str(exc))
            self.emit_candidate_results(batch_result["results"])
            self.emit(
                "maimai_ai_failed",
                f"DeepSeek 批量分析失败，已回退为空匹配：{exc}",
                batch_result,
            )
            self.save_batch_summary(batch_result["results"], batch_result)
            return batch_result

    def build_candidate_payload(self, item: dict) -> dict:
        return {
            "page_candidate_index": item.get("page_candidate_index"),
            "name": item.get("name", ""),
            "drawer_index": item.get("drawer_index"),
            "list_summary": self.limit_text(self.summary_text(item.get("list_summary", "")), 240),
            "basic_info": self.limit_text(item.get("basic_info", ""), 700),
            "expectation": self.limit_text(item.get("expectation", ""), 700),
            "work_experience": self.limit_text(item.get("work_experience", ""), 1800),
            "education_experience": self.limit_text(item.get("education_experience", ""), 900),
            "project_experience": self.limit_text(item.get("project_experience", ""), 900),
            "career_tags": self.limit_text(item.get("career_tags", ""), 500),
            "more_info": self.limit_text(item.get("more_info", ""), 500),
            "resume_text": self.limit_text(item.get("resume_text", ""), 2600),
        }

    @staticmethod
    def limit_text(text: str, max_chars: int) -> str:
        value = str(text or "")
        return value if len(value) <= max_chars else value[:max_chars] + "\n...[截断]"

    def build_match_messages(self, candidates: list[dict], requirements: str) -> list[dict]:
        system_prompt = (
            "你是严谨的招聘候选人批量评估助手。你要读懂招聘方用自然语言写的要求，"
            "先把口语化描述转成可执行的招聘判断标准，再把硬性条件和软性偏好分开判断。"
            "你必须一次性评估整页候选人，按招聘判断标准判断每个人是否匹配。"
            "只依据输入内容，不要补充不存在的信息，不要输出 Markdown。"
            "信息不足时标记 unknown，并降低 score。"
            "只有候选人明确满足核心硬性要求、岗位方向和经验强相关、没有明显风险时，"
            "match 才能为 true 且 next_action=communicate。"
            "你只能返回一个 JSON 对象。"
        )
        user_prompt = f"""
招聘方口语化描述：
{requirements}

请先把这段口语化描述理解成招聘判断标准，例如：
- “要有销售经验”意味着需要在工作经历、职位名称、职责或业绩里看到真实销售相关证据。
- “最好做过医疗/交通/金融”属于行业偏好；如果用户语气是“必须”，才作为硬性条件。
- “不要太频繁跳槽”“薪资别太离谱”“杭州优先”这类表达要转成风险、偏好或硬性条件。
- 城市、薪资、职位方向和年限如果与要求明显冲突，要作为风险或拒绝理由。

候选人列表(JSON)：
{json.dumps(candidates, ensure_ascii=False, indent=2)}

请严格返回如下 JSON：
{{
  "results": [
    {{
      "page_candidate_index": 1,
      "match": true,
      "score": 85,
      "decision": "pass",
      "reason": "一句话说明最终判断",
      "must_have_result": [
        {{"requirement": "硬性条件", "status": "met/not_met/unknown", "evidence": "简历证据"}}
      ],
      "strengths": ["匹配点"],
      "risks": ["风险点"],
      "next_action": "communicate"
    }}
  ]
}}

规则：
1. 按 page_candidate_index 一一对应返回，顺序保持一致。
2. 只返回 JSON，不要代码块，不要解释。
3. decision 只能是 pass、reject、uncertain。
4. match=false 时 next_action 一律填 skip。
5. 如果某个硬性条件明显不满足，match 必须为 false。
6. score 必须是 0 到 100 的整数；pass 通常 70-100，uncertain 通常 40-69，reject 通常 0-39。
"""
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def merge_ai_results(
        self,
        results: list[dict],
        parsed: dict,
        requirements: str,
        payload: dict,
        raw_response: str,
    ) -> dict:
        normalized = self.normalize_ai_results(parsed, results)
        matched = sum(1 for item in normalized if item.get("match"))
        batch_result = {
            "status": "batch_completed",
            "processed": len(normalized),
            "matched": matched,
            "results": normalized,
        }
        self.append_ai_match_log(results, requirements, payload, raw_response, batch_result)
        return batch_result

    def build_fallback_batch_result(
        self,
        results: list[dict],
        requirements: str,
        payload: dict,
        error_message: str,
    ) -> dict:
        fallback_results = []
        for item in results:
            decision = self.default_ai_decision(error_message)
            item = dict(item)
            item.update(decision)
            item["ai_match"] = decision
            fallback_results.append(item)
        batch_result = {
            "status": "batch_completed",
            "processed": len(fallback_results),
            "matched": 0,
            "results": fallback_results,
            "ai_error": error_message,
        }
        self.append_ai_match_log(results, requirements, payload, {"error": error_message}, batch_result)
        return batch_result

    def normalize_ai_results(self, parsed: dict, results: list[dict]) -> list[dict]:
        items = parsed.get("results") if isinstance(parsed, dict) else parsed
        if not isinstance(items, list):
            items = []
        by_index: dict[int, dict] = {}
        sequential: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            sequential.append(item)
            index = self.safe_int(item.get("page_candidate_index"))
            if index is not None:
                by_index[index] = item

        normalized_results: list[dict] = []
        for offset, item in enumerate(results, start=1):
            ai_item = by_index.get(self.safe_int(item.get("page_candidate_index")) or offset)
            if ai_item is None and offset - 1 < len(sequential):
                ai_item = sequential[offset - 1]
            decision = self.normalize_single_result(item, ai_item)
            normalized_results.append(decision)
        return normalized_results

    def normalize_single_result(self, item: dict, ai_item: dict | None) -> dict:
        ai_item = ai_item if isinstance(ai_item, dict) else {}
        decision = self.default_ai_decision()
        decision.update(
            {
                "page_candidate_index": item.get("page_candidate_index"),
                "page_number": item.get("page_number"),
                "global_candidate_index": item.get("global_candidate_index"),
                "drawer_index": item.get("drawer_index"),
                "name": item.get("name", ""),
                "list_summary": self.summary_text(item.get("list_summary", "")),
                "basic_info": item.get("basic_info", ""),
                "expectation": item.get("expectation", ""),
                "work_experience": item.get("work_experience", ""),
                "education_experience": item.get("education_experience", ""),
                "project_experience": item.get("project_experience", ""),
                "career_tags": item.get("career_tags", ""),
                "more_info": item.get("more_info", ""),
                "resume_text": item.get("resume_text", ""),
                "match": bool(ai_item.get("match")),
                "score": self.normalize_score(ai_item.get("score"), bool(ai_item.get("match")), ai_item.get("decision")),
                "decision": str(ai_item.get("decision") or ("pass" if ai_item.get("match") else "reject")),
                "reason": str(ai_item.get("reason") or ""),
                "must_have_result": ai_item.get("must_have_result") if isinstance(ai_item.get("must_have_result"), list) else [],
                "strengths": ai_item.get("strengths") if isinstance(ai_item.get("strengths"), list) else [],
                "risks": ai_item.get("risks") if isinstance(ai_item.get("risks"), list) else [],
                "next_action": str(ai_item.get("next_action") or ("communicate" if ai_item.get("match") else "skip")),
            }
        )
        decision["ai_match"] = {
            "match": decision["match"],
            "score": decision["score"],
            "decision": decision["decision"],
            "reason": decision["reason"],
            "must_have_result": decision["must_have_result"],
            "strengths": decision["strengths"],
            "risks": decision["risks"],
            "next_action": decision["next_action"],
        }
        if ai_item.get("raw_response"):
            decision["ai_match"]["raw_response"] = ai_item.get("raw_response")
        if ai_item.get("ai_error"):
            decision["ai_error"] = ai_item.get("ai_error")
        return decision

    def emit_candidate_results(self, results: list[dict]) -> None:
        for item in results:
            index = item.get("global_candidate_index") or item.get("page_candidate_index") or item.get("index") or ""
            page_number = item.get("page_number")
            name = item.get("name") or ""
            page_text = f"第 {page_number} 页，" if page_number else ""
            self.emit(
                "candidate_result",
                f"{page_text}第 {index} 个脉脉候选人 AI 结果：{'匹配' if item.get('match') else '不匹配'}，{item.get('score', 0)}分，{item.get('reason', '')}",
                item,
            )

    def default_ai_decision(self, error_message: str = "") -> dict:
        decision = {
            "match": False,
            "score": 0,
            "decision": "reject",
            "reason": "AI 分析失败，已按不匹配处理" if not error_message else f"AI 分析失败：{error_message}",
            "must_have_result": [],
            "strengths": [],
            "risks": [],
            "next_action": "skip",
        }
        if error_message:
            decision["ai_error"] = error_message
        return decision

    @staticmethod
    def summary_text(value) -> str:
        if isinstance(value, dict):
            return str(value.get("summary") or value.get("name") or "")
        return str(value or "")

    def append_ai_match_log(
        self,
        candidates: list[dict],
        requirements: str,
        payload: dict,
        raw_response: dict | str,
        batch_result: dict,
    ) -> None:
        output_path = RUNTIME_DIR / "maimai_ai_match_logs.json"
        logs = []
        if output_path.exists():
            try:
                logs = json.loads(output_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                logs = []
        logs.append(
            {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "requirements": requirements,
                "candidate_count": len(candidates),
                "payload": payload,
                "raw_response": raw_response,
                "batch_result": batch_result,
            }
        )
        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(logs, file, ensure_ascii=False, indent=2)

    def save_batch_summary(self, results: list[dict], summary: dict | None = None) -> None:
        batch_summary = summary or {
            "processed": len(results),
            "matched": sum(1 for item in results if item.get("match")),
            "results": results,
        }
        output_path = RUNTIME_DIR / "candidate_batch_summary.json"
        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(batch_summary, file, ensure_ascii=False, indent=2)

    @staticmethod
    def safe_int(value) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def normalize_score(cls, value, matched: bool = False, decision: str | None = None) -> int:
        score = cls.safe_int(value)
        if score is None:
            match = re.search(r"\d+", str(value or ""))
            score = int(match.group(0)) if match else None
        if score is None or score <= 0:
            normalized_decision = str(decision or "").strip().lower()
            if matched or normalized_decision == "pass":
                score = 75
            elif normalized_decision == "uncertain":
                score = 50
            else:
                score = 0
        return max(0, min(100, int(score)))

    @staticmethod
    def urlopen_with_retry(req: request.Request, retries: int = 5, timeout: int = 60) -> str:
        last_error = None
        for attempt in range(1, retries + 1):
            try:
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
            if match:
                return json.loads(match.group(0))
            match = re.search(r"\[\s*\{.*\}\s*\]", text, flags=re.S)
            if match:
                return {"results": json.loads(match.group(0))}
            raise RuntimeError(f"AI 没有返回可解析 JSON：{content}")
