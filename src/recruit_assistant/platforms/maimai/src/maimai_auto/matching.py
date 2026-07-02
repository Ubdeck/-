import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import DEFAULT_AI_PROMPT, SearchSettings, load_settings
from .paths import bundle_root, runtime_root


DEFAULT_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
CHUNK_SIZE = 10

PROMPT_REWRITE_SYSTEM = (
    "你是资深猎头与招聘提示词优化专家。"
    "用户给你的只是口语化、零散的招聘要求，你需要把它重写成一份专业、清晰、可执行的候选人筛选提示词。"
    "改写要求："
    "1. 保留用户的硬性要求、软性要求、加分项、风险点。"
    "2. 如果用户表达模糊，请自动补成适合 AI 执行筛选的明确表述。"
    "3. 像“必须”“一定要”“不要”“不能”“排除”“不考虑”这类说法，要明确区分成硬性要求或排除项。"
    "4. 不要臆造用户没提过的具体公司、学校、奖项、技术栈。"
    '5. 直接输出 JSON，格式为 {"professional_prompt":"...","hard_requirements":["..."],"preferred_requirements":["..."],"exclusions":["..."]}，不要输出别的内容。'
)


def env_path() -> Path:
    candidates = [
        runtime_root() / ".env",
        bundle_root() / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def current_candidates_path() -> Path:
    runtime_candidate = runtime_root() / "current_page_candidates.json"
    if runtime_candidate.exists():
        return runtime_candidate
    return bundle_root() / "current_page_candidates.json"


def matched_candidates_path() -> Path:
    return runtime_root() / "matched_candidates.json"


def reset_match_results() -> None:
    payload = {
        "matched_candidates": [],
        "rejected_candidates": [],
        "summary": "",
        "used_ai_prompt": "",
        "raw_ai_requirement_text": "",
    }
    matched_candidates_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_env(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def compact_candidate(candidate: dict) -> dict:
    return {
        "list_index": candidate.get("list_index"),
        "name": candidate.get("name"),
        "recent_activity": candidate.get("recent_activity"),
        "location": candidate.get("location"),
        "work_years": candidate.get("work_years"),
        "degree": candidate.get("degree"),
        "gender": candidate.get("gender"),
        "expectation": candidate.get("expectation", {}),
        "work_experience": [
            {
                "company": item.get("company"),
                "role": item.get("role"),
                "dates": item.get("dates"),
                "details": item.get("details", [])[:4],
            }
            for item in candidate.get("work_experience", [])[:3]
        ],
        "education_experience": [
            {
                "school": item.get("school"),
                "degree_major": item.get("degree_major"),
                "dates": item.get("dates"),
                "details": item.get("details", [])[:2],
            }
            for item in candidate.get("education_experience", [])[:3]
        ],
        "career_tags": candidate.get("career_tags", [])[:12],
    }


def chunk_list(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def build_requirements(settings: SearchSettings) -> dict:
    data = settings.to_search_config()
    if settings.ai_requirement_text.strip():
        data["ai_requirement_text"] = settings.ai_requirement_text.strip()
    return data


def normalize_requirement_list(items) -> list[str]:
    if not isinstance(items, list):
        return []
    normalized = []
    seen = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def build_hard_constraint_text(settings: SearchSettings) -> str:
    filters = settings.to_search_config().get("filters", {})
    lines = [
        "硬性筛选规则：",
        "1. 只要命中任何一条硬性不符合，就必须放入 rejected_candidates，不允许因为整体不错而放入 matched_candidates。",
        "2. 不能把硬性条件当作“尽量匹配”“可放宽条件”处理。",
    ]

    cities = [item for item in filters.get("城市地区", []) if item]
    if cities:
        city_text = "、".join(cities)
        lines.append(
            f"3. 城市是硬条件：候选人的期望工作地、期望地区、当前城市、现居地中，必须明确包含 {city_text}；"
            "如果不包含，就直接淘汰。"
        )

    education_items = [item for item in filters.get("学历要求", []) if item and item != "不限"]
    if education_items:
        lines.append(f"4. 学历是硬条件：必须满足 {'、'.join(education_items)}，不满足就淘汰。")

    work_year_items = [item for item in filters.get("工作年限", []) if item and item != "不限"]
    if work_year_items:
        lines.append(f"5. 工作年限是硬条件：必须满足 {'、'.join(work_year_items)}，不满足就淘汰。")

    company_items = [item for item in filters.get("就职公司", []) if item]
    if company_items:
        lines.append(
            f"6. 就职公司是硬条件：候选人的经历中必须明确出现以下公司之一：{'、'.join(company_items)}；否则淘汰。"
        )

    gender_items = [item for item in filters.get("性别", []) if item]
    if gender_items:
        lines.append(f"7. 性别是硬条件：必须是 {'、'.join(gender_items)}，否则淘汰。")

    lines.append("8. 只有在以上硬条件全部满足后，才允许再根据关键词、岗位方向、亮点经历等做进一步匹配评分。")
    return "\n".join(lines)


def build_ai_requirement_spec(raw_text: str, rewrite_result: dict | None = None) -> dict:
    rewrite_result = rewrite_result or {}
    hard_requirements = normalize_requirement_list(rewrite_result.get("hard_requirements"))
    preferred_requirements = normalize_requirement_list(rewrite_result.get("preferred_requirements"))
    exclusions = normalize_requirement_list(rewrite_result.get("exclusions"))

    raw = str(raw_text or "").strip()
    if raw:
        pieces = re.split(r"[；;。\n]+", raw)
        for piece in pieces:
            text = str(piece or "").strip(" ，,、")
            if not text:
                continue
            if any(keyword in text for keyword in ["一定要", "必须", "务必", "只要", "硬性", "必须有", "一定得"]):
                if text not in hard_requirements:
                    hard_requirements.append(text)
            if any(keyword in text for keyword in ["不要", "不能", "排除", "剔除", "不考虑", "不接受"]):
                if text not in exclusions:
                    exclusions.append(text)
            if any(keyword in text for keyword in ["优先", "加分", "最好", "更倾向"]):
                if text not in preferred_requirements:
                    preferred_requirements.append(text)

    return {
        "hard_requirements": hard_requirements,
        "preferred_requirements": preferred_requirements,
        "exclusions": exclusions,
    }


def build_prompt(
    settings: SearchSettings,
    candidates: list[dict],
    effective_prompt: str,
    ai_requirement_spec: dict,
) -> str:
    payload = {
        "search_requirements": build_requirements(settings),
        "hard_constraints": build_hard_constraint_text(settings),
        "ai_requirement_spec": ai_requirement_spec,
        "candidates": [compact_candidate(item) for item in candidates],
        "output_schema": {
            "matched_candidates": [
                {
                    "list_index": 1,
                    "name": "候选人姓名",
                    "match_score": 95,
                    "reason": "简短中文原因",
                }
            ],
            "rejected_candidates": [
                {
                    "list_index": 2,
                    "name": "候选人姓名",
                    "reason": "简短中文原因",
                }
            ],
            "summary": "整体结论",
        },
    }
    instructions = effective_prompt.strip() or DEFAULT_AI_PROMPT
    return (
        instructions
        + "\n\n请严格执行下面的硬性筛选规则与 AI 硬要求："
        + "\n1. 只要 search_requirements / hard_constraints / ai_requirement_spec.hard_requirements / ai_requirement_spec.exclusions 中任意一条不满足，就必须淘汰。"
        + "\n2. 对于“必须”“一定要”“不要”“不能”“排除”类要求，绝对不允许放宽。"
        + "\n3. 只有硬性条件全部满足后，才允许参考 ai_requirement_spec.preferred_requirements 做排序和评分。"
        + "\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _post_json(base_url: str, api_key: str, body: dict, timeout: int = 120) -> dict:
    url = base_url.rstrip("/") + "/chat/completions"
    request = urllib.request.Request(
        url=url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept-Encoding": "identity",
            "Connection": "close",
        },
        method="POST",
    )
    last_error = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
            return json.loads(raw, strict=False)
        except http.client.IncompleteRead as exc:
            last_error = exc
        except json.JSONDecodeError as exc:
            last_error = exc
        except urllib.error.URLError as exc:
            last_error = exc
        if attempt < 3:
            time.sleep(1.5 * attempt)
    raise RuntimeError(f"DeepSeek 请求失败，重试 3 次后仍未成功：{last_error}")


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def _extract_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def _extract_json_string_value(text: str, key: str) -> str:
    pattern = rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"'
    match = re.search(pattern, text, flags=re.S)
    if not match:
        return ""
    raw = match.group(1)
    try:
        return json.loads(f'"{raw}"')
    except Exception:
        return raw.replace('\\"', '"').replace("\\n", "\n").replace("\\r", "\r")


def _extract_array_slice(text: str, key: str) -> str:
    marker = f'"{key}"'
    start = text.find(marker)
    if start == -1:
        return ""
    start = text.find("[", start)
    if start == -1:
        return ""

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return text[start:]


def _parse_objects_from_array_text(array_text: str) -> list[dict]:
    if not array_text:
        return []

    items = []
    depth = 0
    in_string = False
    escape = False
    obj_start = -1

    for index, char in enumerate(array_text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue

        if char == "{":
            if depth == 0:
                obj_start = index
            depth += 1
            continue

        if char == "}":
            if depth <= 0:
                continue
            depth -= 1
            if depth == 0 and obj_start != -1:
                chunk = array_text[obj_start : index + 1]
                try:
                    item = json.loads(chunk, strict=False)
                except Exception:
                    item = None
                if isinstance(item, dict):
                    items.append(item)
                obj_start = -1

    return items


def _salvage_candidate_result(text: str) -> dict | None:
    cleaned = _extract_json_object(_strip_code_fence(text))
    matched = _parse_objects_from_array_text(_extract_array_slice(cleaned, "matched_candidates"))
    rejected = _parse_objects_from_array_text(_extract_array_slice(cleaned, "rejected_candidates"))
    summary = _extract_json_string_value(cleaned, "summary")

    if not matched and not rejected and not summary:
        return None

    return {
        "matched_candidates": matched,
        "rejected_candidates": rejected,
        "summary": summary or f"AI 返回格式异常，已尽量恢复结果：通过 {len(matched)} 人，未通过 {len(rejected)} 人。",
    }


def parse_json_loose(text: str, context: str) -> dict:
    cleaned = _extract_json_object(_strip_code_fence(text))
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError as exc:
        salvaged = _salvage_candidate_result(text)
        if salvaged is not None:
            return salvaged
        preview = cleaned[:300].replace("\n", "\\n").replace("\r", "\\r")
        raise RuntimeError(f"{context} 解析失败：{exc}。返回片段：{preview}")


def call_deepseek(base_url: str, api_key: str, prompt: str) -> dict:
    body = {
        "model": DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": "你是一个严谨的招聘筛选助手，必须输出可解析 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    return _post_json(base_url, api_key, body)


def generate_ai_screening_prompt(base_url: str, api_key: str, requirement_text: str) -> dict:
    raw_requirement = requirement_text.strip()
    if not raw_requirement:
        return {
            "professional_prompt": DEFAULT_AI_PROMPT,
            "hard_requirements": [],
            "preferred_requirements": [],
            "exclusions": [],
        }

    body = {
        "model": DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": PROMPT_REWRITE_SYSTEM},
            {
                "role": "user",
                "content": (
                    "请把下面这些招聘要求整理成更专业的候选人筛选提示词，"
                    "并拆分出必须满足、优先考虑、明确排除三类规则：\n\n"
                    f"{raw_requirement}"
                ),
            },
        ],
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    result = _post_json(base_url, api_key, body)
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    if not content:
        return {
            "professional_prompt": DEFAULT_AI_PROMPT + "\n\n补充筛选要求：\n" + raw_requirement,
            "hard_requirements": [],
            "preferred_requirements": [],
            "exclusions": [],
        }
    data = parse_json_loose(content, "AI 筛选提示词生成结果")
    professional_prompt = str(data.get("professional_prompt", "") or "").strip()
    if not professional_prompt:
        professional_prompt = raw_requirement
    return {
        "professional_prompt": DEFAULT_AI_PROMPT + "\n\n补充筛选要求：\n" + professional_prompt,
        "hard_requirements": normalize_requirement_list(data.get("hard_requirements")),
        "preferred_requirements": normalize_requirement_list(data.get("preferred_requirements")),
        "exclusions": normalize_requirement_list(data.get("exclusions")),
    }


def merge_results(parts: list[dict]) -> dict:
    merged = {
        "matched_candidates": [],
        "rejected_candidates": [],
        "summary": "",
    }
    for part in parts:
        merged["matched_candidates"].extend(part.get("matched_candidates", []))
        merged["rejected_candidates"].extend(part.get("rejected_candidates", []))
    merged["matched_candidates"].sort(key=lambda item: int(item.get("list_index", 0) or 0))
    merged["rejected_candidates"].sort(key=lambda item: int(item.get("list_index", 0) or 0))
    merged["summary"] = (
        f"共处理 {len(merged['matched_candidates']) + len(merged['rejected_candidates'])} 位候选人，"
        f"符合 {len(merged['matched_candidates'])} 位。"
    )
    return merged


def enrich_result_candidates(result: dict, candidates: list[dict]) -> dict:
    lookup = {int(item.get("list_index", 0)): item for item in candidates if item.get("list_index")}
    enriched_matched = []
    enriched_rejected = []

    for item in result.get("matched_candidates", []):
        index = int(item.get("list_index", 0) or 0)
        source = lookup.get(index, {})
        expectation = source.get("expectation", {})
        enriched_matched.append(
            {
                "list_index": index,
                "page_number": int(source.get("page_number", 1) or 1),
                "page_list_index": int(source.get("page_list_index", index) or index),
                "name": item.get("name") or source.get("name") or "",
                "match_score": int(item.get("match_score", 0) or 0),
                "reason": item.get("reason", ""),
                "location": source.get("location", ""),
                "recent_activity": source.get("recent_activity", ""),
                "work_years": source.get("work_years", ""),
                "degree": source.get("degree", ""),
                "gender": source.get("gender", ""),
                "target_role": expectation.get("target_role", ""),
                "expected_salary": expectation.get("salary", ""),
                "companies": [exp.get("company", "") for exp in source.get("work_experience", [])[:2] if exp.get("company")],
                "career_tags": source.get("career_tags", [])[:6],
            }
        )

    for item in result.get("rejected_candidates", []):
        index = int(item.get("list_index", 0) or 0)
        source = lookup.get(index, {})
        expectation = source.get("expectation", {})
        enriched_rejected.append(
            {
                "list_index": index,
                "page_number": int(source.get("page_number", 1) or 1),
                "page_list_index": int(source.get("page_list_index", index) or index),
                "name": item.get("name") or source.get("name") or "",
                "reason": item.get("reason", ""),
                "location": source.get("location", ""),
                "recent_activity": source.get("recent_activity", ""),
                "work_years": source.get("work_years", ""),
                "degree": source.get("degree", ""),
                "gender": source.get("gender", ""),
                "target_role": expectation.get("target_role", ""),
                "expected_salary": expectation.get("salary", ""),
                "companies": [exp.get("company", "") for exp in source.get("work_experience", [])[:2] if exp.get("company")],
                "career_tags": source.get("career_tags", [])[:6],
            }
        )

    result["matched_candidates"] = enriched_matched
    result["rejected_candidates"] = enriched_rejected
    return result


def load_match_results() -> dict:
    path = matched_candidates_path()
    if not path.exists():
        return {
            "matched_candidates": [],
            "rejected_candidates": [],
            "summary": "",
            "used_ai_prompt": "",
            "raw_ai_requirement_text": "",
        }
    return json.loads(path.read_text(encoding="utf-8"), strict=False)


def merge_accumulated_results(existing: dict, current: dict) -> dict:
    matched_map = {}
    rejected_map = {}

    for item in existing.get("matched_candidates", []):
        key = (int(item.get("page_number", 0) or 0), int(item.get("page_list_index", item.get("list_index", 0)) or 0))
        matched_map[key] = item
    for item in current.get("matched_candidates", []):
        key = (int(item.get("page_number", 0) or 0), int(item.get("page_list_index", item.get("list_index", 0)) or 0))
        matched_map[key] = item
        rejected_map.pop(key, None)

    for item in existing.get("rejected_candidates", []):
        key = (int(item.get("page_number", 0) or 0), int(item.get("page_list_index", item.get("list_index", 0)) or 0))
        if key not in matched_map:
            rejected_map[key] = item
    for item in current.get("rejected_candidates", []):
        key = (int(item.get("page_number", 0) or 0), int(item.get("page_list_index", item.get("list_index", 0)) or 0))
        if key not in matched_map:
            rejected_map[key] = item

    accumulated_matched = sorted(
        matched_map.values(),
        key=lambda item: (
            int(item.get("page_number", 0) or 0),
            int(item.get("page_list_index", item.get("list_index", 0)) or 0),
        ),
    )
    accumulated_rejected = sorted(
        rejected_map.values(),
        key=lambda item: (
            int(item.get("page_number", 0) or 0),
            int(item.get("page_list_index", item.get("list_index", 0)) or 0),
        ),
    )

    current_page = 0
    if current.get("matched_candidates"):
        current_page = int(current["matched_candidates"][0].get("page_number", 0) or 0)
    elif current.get("rejected_candidates"):
        current_page = int(current["rejected_candidates"][0].get("page_number", 0) or 0)

    current_page_total = len(current.get("matched_candidates", [])) + len(current.get("rejected_candidates", []))
    current_page_passed = len(current.get("matched_candidates", []))

    return {
        "matched_candidates": accumulated_matched,
        "rejected_candidates": accumulated_rejected,
        "summary": (
            f"当前已累计 {len(accumulated_matched)} 位通过候选人。"
            f" 第 {current_page} 页本次处理 {current_page_total} 位，通过 {current_page_passed} 位。"
        ),
        "used_ai_prompt": current.get("used_ai_prompt", existing.get("used_ai_prompt", "")),
        "raw_ai_requirement_text": current.get("raw_ai_requirement_text", existing.get("raw_ai_requirement_text", "")),
    }


def _parse_group_result_with_retry(
    base_url: str,
    api_key: str,
    settings: SearchSettings,
    group: list[dict],
    effective_prompt: str,
    ai_requirement_spec: dict,
    idx: int,
    total: int,
) -> dict:
    last_error = None
    prompt = build_prompt(settings, group, effective_prompt, ai_requirement_spec)

    for attempt in range(1, 3):
        result = call_deepseek(base_url, api_key, prompt)
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not content:
            last_error = RuntimeError(f"DeepSeek 返回内容为空，分组 {idx}/{total}。")
        else:
            try:
                return parse_json_loose(content, f"候选人匹配结果分组 {idx}/{total}")
            except RuntimeError as exc:
                last_error = exc
                salvaged = _salvage_candidate_result(content)
                if salvaged is not None:
                    return salvaged

        if attempt == 1:
            prompt = (
                prompt
                + "\n\n再次强调：只允许返回一个完整、可解析的 JSON 对象。"
                + "必须严格服从 ai_requirement_spec 中的 hard_requirements 与 exclusions。"
            )
            time.sleep(1.0)

    raise last_error or RuntimeError(f"候选人匹配结果分组 {idx}/{total} 解析失败。")


def match_candidates(settings: SearchSettings) -> dict:
    env = load_env(env_path())
    persisted = load_settings()
    api_key = (
        settings.deepseek_api_key
        or persisted.deepseek_api_key
        or env.get("DEEPSEEK_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
    )
    base_url = (
        settings.deepseek_base_url
        or persisted.deepseek_base_url
        or env.get("DEEPSEEK_BASE_URL")
        or os.getenv("DEEPSEEK_BASE_URL")
        or "https://api.deepseek.com"
    )
    if not api_key:
        raise RuntimeError("未找到 DEEPSEEK_API_KEY。")

    candidates_file = current_candidates_path()
    if not candidates_file.exists():
        raise FileNotFoundError(f"未找到候选人数据文件：{candidates_file}")

    rewrite_result = generate_ai_screening_prompt(base_url, api_key, settings.ai_requirement_text)
    effective_prompt = rewrite_result.get("professional_prompt", DEFAULT_AI_PROMPT)
    ai_requirement_spec = build_ai_requirement_spec(settings.ai_requirement_text, rewrite_result)
    candidates = json.loads(candidates_file.read_text(encoding="utf-8"), strict=False)
    candidate_groups = chunk_list(candidates, CHUNK_SIZE)

    parts = []
    for idx, group in enumerate(candidate_groups, start=1):
        parts.append(
            _parse_group_result_with_retry(
                base_url,
                api_key,
                settings,
                group,
                effective_prompt,
                ai_requirement_spec,
                idx,
                len(candidate_groups),
            )
        )

    merged = merge_results(parts)
    merged = enrich_result_candidates(merged, candidates)
    merged["used_ai_prompt"] = effective_prompt
    merged["raw_ai_requirement_text"] = settings.ai_requirement_text.strip()

    existing = load_match_results()
    accumulated = merge_accumulated_results(existing, merged)
    matched_candidates_path().write_text(json.dumps(accumulated, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged
