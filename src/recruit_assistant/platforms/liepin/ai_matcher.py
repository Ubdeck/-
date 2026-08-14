from __future__ import annotations

import json
import re
import time
from urllib import error, request

from ...environment import get_env_value
from .browser import append_runtime_log
from .constants import DEFAULT_MATCH_REQUIREMENTS, RUNTIME_DIR
from .models import SearchFilters


class AiMatcherMixin:
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
        key = (api_key or get_env_value("DEEPSEEK_API_KEY") or "").strip()
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
