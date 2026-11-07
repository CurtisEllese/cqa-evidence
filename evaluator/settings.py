"""Provider settings and safe retry-delay extraction; no wire-contract dependency."""
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

@dataclass(frozen=True)
class EvaluatorSettings:
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float = 60
    min_interval_seconds: float = 5
    max_output_tokens: int = 8192

    def __post_init__(self):
        if not self.api_key.strip() or not self.model.strip():
            raise ValueError("Provider API key and LLM_MODEL must be configured on the server")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        if not math.isfinite(self.min_interval_seconds) or self.min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be nonnegative and finite")
        if self.max_output_tokens < 256:
            raise ValueError("max_output_tokens must be at least 256")

    @classmethod
    def from_env(cls):
        if os.getenv("LLM_PROVIDER", "gemini") != "gemini":
            raise ValueError("This adapter requires LLM_PROVIDER=gemini")
        return cls(api_key=os.getenv("GEMINI_API_KEY", ""), model=os.getenv("LLM_MODEL", ""))


def retry_delay(exc) -> int | None:
    """Honor both HTTP Retry-After and Google's structured RetryInfo, not error text."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    value = headers.get("retry-after")
    if value:
        try:
            delay = float(value)
        except (ValueError, TypeError):
            try:
                delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError):
                delay = None
        if delay is not None and math.isfinite(delay):
            return max(0, math.ceil(delay))
    payload = getattr(exc, "details", None) or getattr(exc, "response_json", None)
    if isinstance(payload, dict):
        payload = payload.get("error", payload).get("details", [])
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and item.get("@type", "").endswith("RetryInfo"):
                try:
                    delay = float(str(item["retryDelay"]).removesuffix("s"))
                    if math.isfinite(delay):
                        return max(0, math.ceil(delay))
                except (KeyError, ValueError, TypeError):
                    pass
    return None


class OpenAISettings(EvaluatorSettings):
    @classmethod
    def from_env(cls):
        if os.getenv("LLM_PROVIDER", "openai") != "openai":
            raise ValueError("This adapter requires LLM_PROVIDER=openai")
        return cls(api_key=os.getenv("OPENAI_API_KEY", ""),
                   model=os.getenv("LLM_MODEL", "gpt-5.4-mini-2026-03-17"), timeout_seconds=45)
