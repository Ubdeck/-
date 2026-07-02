import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import bundle_root, runtime_root


NONE_OPTION = "无"

WORK_YEAR_OPTIONS = [
    NONE_OPTION,
    "在校/应届",
    "1年以内",
    "1-3年",
    "3-5年",
    "5-10年",
    "10年以上",
]

GRADUATION_YEAR_OPTIONS = [
    NONE_OPTION,
    "不限",
    "2025届毕业",
    "2026届毕业",
    "2027届毕业",
    "2028届毕业",
    "2029届毕业",
    "2030届毕业",
]

EDUCATION_OPTIONS = [
    NONE_OPTION,
    "专科及以上",
    "本科及以上",
    "硕士及以上",
    "博士",
]

EDUCATION_EXTRA_OPTIONS = [
    NONE_OPTION,
    "不限",
    "只看统招本科",
]

GENDER_OPTIONS = [
    NONE_OPTION,
    "男",
    "女",
]

KEYWORD_MODE_OPTIONS = [
    "所有",
    "任一",
]

DEFAULT_AI_PROMPT = """你是一个招聘筛选助手。请根据给定的搜索要求，严格判断候选人是否适合进入后续沟通名单。
优先标准：
1. 优先保留与关键词和目标岗位明确相关的候选人。
2. 城市地区尽量匹配用户输入，尤其关注候选人的期望工作地。
3. 学历、工作年限、就职公司、性别等显式条件需要重点参考。
4. 如果候选人整体非常匹配，但存在轻微偏差，可以保留，并在原因里说明。
5. 只返回建议继续推进沟通的人选。"""

SECRET_SETTING_FIELDS = {"deepseek_api_key", "deepseek_base_url"}


def to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "on", "yes"}


def normalize_optional(value: str) -> str:
    text = str(value or "").strip()
    return "" if text in {"", NONE_OPTION} else text


def _load_env_values() -> dict:
    candidates = [
        runtime_root() / ".env",
        bundle_root() / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    for path in candidates:
        try:
            if not path.exists():
                continue
            values = {}
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
            if values:
                return values
        except Exception:
            continue
    return {}


def _split_companies(value: str) -> list[str]:
    normalized = normalize_optional(value).replace("，", ",").replace("、", ",")
    if not normalized:
        return []
    return [item.strip() for item in normalized.split(",") if item.strip()]


@dataclass
class SearchSettings:
    keyword: str = ""
    keyword_mode: str = "所有"
    city: str = NONE_OPTION
    education: str = NONE_OPTION
    education_extra: str = NONE_OPTION
    work_years: str = NONE_OPTION
    graduation_year: str = NONE_OPTION
    companies: str = ""
    gender: str = NONE_OPTION
    page_limit: int = 1
    ai_requirement_text: str = ""
    greeting: str = "你好，我对你的简历很感兴趣，方便沟通一下吗？"
    actual_send: bool = False
    schedule_enabled: bool = False
    schedule_time: str = ""
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"

    def effective_ai_requirement_text(self) -> str:
        text = str(self.ai_requirement_text or "").strip()
        return text or DEFAULT_AI_PROMPT

    def to_search_config(self) -> dict:
        city = normalize_optional(self.city)
        education = normalize_optional(self.education)
        education_extra = normalize_optional(self.education_extra)
        work_years = normalize_optional(self.work_years)
        graduation_year = normalize_optional(self.graduation_year)
        gender = normalize_optional(self.gender)

        work_year_filters = []
        if work_years:
            work_year_filters.append(work_years)
            if work_years == "在校/应届" and graduation_year:
                work_year_filters.append(graduation_year)

        filters = {
            "城市地区": [city] if city else [],
            "学历要求": [item for item in [education, education_extra] if item],
            "工作年限": work_year_filters,
            "就职公司": _split_companies(self.companies),
            "性别": [gender] if gender else [],
        }
        return {
            "keyword": self.keyword.strip(),
            "keyword_mode": self.keyword_mode if self.keyword.strip() else "所有",
            "filters": filters,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SearchSettings":
        env_values = _load_env_values()
        defaults = cls(
            deepseek_api_key=env_values.get("DEEPSEEK_API_KEY", ""),
            deepseek_base_url=env_values.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
        payload = {field: data.get(field, getattr(defaults, field)) for field in cls.__dataclass_fields__}
        legacy_prompt = str(data.get("ai_prompt", "") or "").strip()
        if not payload.get("ai_requirement_text") and legacy_prompt:
            payload["ai_requirement_text"] = legacy_prompt
        payload["actual_send"] = to_bool(data.get("actual_send", data.get("send_chat_message", False)))
        payload["schedule_enabled"] = to_bool(payload.get("schedule_enabled"))
        try:
            payload["page_limit"] = max(1, int(payload.get("page_limit", data.get("max_candidates", defaults.page_limit))))
        except Exception:
            payload["page_limit"] = defaults.page_limit
        for field_name in ("city", "education", "education_extra", "work_years", "graduation_year", "gender"):
            payload[field_name] = payload.get(field_name) or NONE_OPTION
        payload["keyword_mode"] = payload.get("keyword_mode") or "所有"
        payload["schedule_time"] = str(payload.get("schedule_time", "") or "").strip()
        payload["greeting"] = str(payload.get("greeting", defaults.greeting) or defaults.greeting)
        return cls(**payload)


@dataclass
class ScheduleEntry:
    id: str
    time_text: str
    created_at: str
    settings: dict

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduleEntry":
        return cls(
            id=str(data.get("id", "")).strip(),
            time_text=str(data.get("time_text", "")).strip(),
            created_at=str(data.get("created_at", "")).strip(),
            settings=dict(data.get("settings") or {}),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def serialize_settings(settings: SearchSettings) -> dict:
    data = asdict(settings)
    for field_name in SECRET_SETTING_FIELDS:
        data.pop(field_name, None)
    return data


def config_dir() -> Path:
    path = runtime_root() / "config"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return config_dir() / "search_config.json"


def schedules_path() -> Path:
    return config_dir() / "scheduled_tasks.json"


def load_settings() -> SearchSettings:
    path = config_path()
    if not path.exists():
        settings = SearchSettings.from_dict({})
        save_settings(settings)
        return settings
    return SearchSettings.from_dict(json.loads(path.read_text(encoding="utf-8-sig")))


def save_settings(settings: SearchSettings) -> Path:
    path = config_path()
    path.write_text(json.dumps(serialize_settings(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_schedules() -> list[ScheduleEntry]:
    path = schedules_path()
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    items = raw if isinstance(raw, list) else raw.get("items", [])
    schedules = []
    for item in items:
        try:
            entry = ScheduleEntry.from_dict(item)
        except Exception:
            continue
        if entry.id and entry.time_text:
            schedules.append(entry)
    schedules.sort(key=lambda entry: (entry.time_text, entry.created_at, entry.id))
    return schedules


def save_schedules(entries: list[ScheduleEntry]) -> Path:
    path = schedules_path()
    payload = []
    for entry in entries:
        item = entry.to_dict()
        item["settings"] = {
            key: value for key, value in dict(item.get("settings") or {}).items() if key not in SECRET_SETTING_FIELDS
        }
        payload.append(item)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def upsert_schedule(entry: ScheduleEntry) -> list[ScheduleEntry]:
    entries = [item for item in load_schedules() if item.id != entry.id]
    entries.append(entry)
    entries.sort(key=lambda item: (item.time_text, item.created_at, item.id))
    save_schedules(entries)
    return entries


def delete_schedule(schedule_id: str) -> list[ScheduleEntry]:
    entries = [item for item in load_schedules() if item.id != schedule_id]
    save_schedules(entries)
    return entries
