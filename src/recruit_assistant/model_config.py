from __future__ import annotations

from dataclasses import dataclass

from .environment import get_env_value


AI_PROVIDER_COMPANY = "company"
AI_PROVIDER_DEEPSEEK = "deepseek"
DEFAULT_AI_PROVIDER = AI_PROVIDER_COMPANY

COMPANY_MODEL_BASE_URL = get_env_value(
    "COMPANY_MODEL_BASE_URL",
    "http://172.20.13.3:9094/v1",
).rstrip("/")
COMPANY_MODEL_API_KEY = get_env_value(
    "COMPANY_MODEL_API_KEY",
    "",
).strip()
DEFAULT_COMPANY_MODEL = get_env_value("COMPANY_MODEL", "minimax").strip() or "minimax"

DEEPSEEK_BASE_URL = get_env_value(
    "DEEPSEEK_BASE_URL",
    "https://api.deepseek.com",
).rstrip("/")
DEEPSEEK_API_KEY = get_env_value("DEEPSEEK_API_KEY", "").strip()
DEFAULT_DEEPSEEK_MODEL = get_env_value("DEEPSEEK_MODEL", "deepseek-chat").strip() or "deepseek-chat"


@dataclass(frozen=True)
class ModelEndpoint:
    provider: str
    display_name: str
    base_url: str
    api_key: str
    model: str
    use_json_response_format: bool = False

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url}/chat/completions"


def normalize_ai_provider(value, model: str = "") -> str:
    provider = str(value or "").strip().lower()
    if provider in {AI_PROVIDER_COMPANY, AI_PROVIDER_DEEPSEEK}:
        return provider
    if str(model or "").strip().lower().startswith("deepseek"):
        return AI_PROVIDER_DEEPSEEK
    return DEFAULT_AI_PROVIDER


def normalize_model_for_provider(provider: str, model: str = "") -> str:
    normalized_provider = normalize_ai_provider(provider, model)
    value = str(model or "").strip()
    if normalized_provider == AI_PROVIDER_DEEPSEEK:
        return DEFAULT_DEEPSEEK_MODEL if not value or value == DEFAULT_COMPANY_MODEL else value
    return DEFAULT_COMPANY_MODEL if not value or value.startswith("deepseek-") else value


def resolve_model_endpoint(
    provider: str,
    model: str = "",
    deepseek_api_key: str = "",
    company_api_key: str = "",
) -> ModelEndpoint:
    normalized_provider = normalize_ai_provider(provider, model)
    normalized_model = normalize_model_for_provider(normalized_provider, model)
    if normalized_provider == AI_PROVIDER_DEEPSEEK:
        return ModelEndpoint(
            provider=normalized_provider,
            display_name="DeepSeek",
            base_url=DEEPSEEK_BASE_URL,
            api_key=(deepseek_api_key or DEEPSEEK_API_KEY).strip(),
            model=normalized_model,
        )
    return ModelEndpoint(
        provider=normalized_provider,
        display_name="公司模型",
        base_url=COMPANY_MODEL_BASE_URL,
        api_key=(company_api_key or COMPANY_MODEL_API_KEY).strip(),
        model=normalized_model,
        use_json_response_format=True,
    )


def messages_for_endpoint(messages: list[dict], endpoint: ModelEndpoint) -> list[dict]:
    copied = [dict(message) for message in messages]
    if endpoint.provider == AI_PROVIDER_COMPANY and copied:
        copied[0]["content"] = (
            str(copied[0].get("content") or "")
            + "输出字段必须相互一致；如果 reason 或 risks 已指出核心硬性冲突，match 必须为 false。"
        )
    return copied
