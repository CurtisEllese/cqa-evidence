"""Gemini adapter with one shared retry/repair budget and no score arithmetic."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable

import httpx2
from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from contracts.v1 import AdapterResult, ErrorBody, EvaluatorInput, ModelOutput, Provenance, validate_output
from .prompt import PROMPT_VERSION, SYSTEM_INSTRUCTION


@dataclass(frozen=True)
class EvaluatorSettings:
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float = 60
    min_interval_seconds: float = 5
    max_output_tokens: int = 8192

    def __post_init__(self):
        if not self.api_key.strip() or not self.model.strip():
            raise ValueError("GEMINI_API_KEY and LLM_MODEL must be configured on the server")
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


@dataclass(frozen=True)
class AttemptEvent:
    """Safe telemetry: no key, prompt, response, quote, or customer identifiers."""
    request_fingerprint: str
    attempt: int
    outcome: str
    elapsed_seconds: float
    input_tokens: int | None
    output_tokens: int | None


class EvaluationFailure(Exception):
    def __init__(self, error: ErrorBody, attempts: tuple[AttemptEvent, ...] = ()):
        super().__init__(error.message)
        self.error = error
        self.attempts = attempts


def failure(code, message, *, retryable=False, retry_after=None, details=()):
    return EvaluationFailure(ErrorBody(code=code, message=message, retryable=retryable,
                                      retry_after_seconds=retry_after, details=list(details)))


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


def request_fingerprint(request: EvaluatorInput, settings: EvaluatorSettings) -> str:
    # Hash actual full inputs, not just an untrusted caller-provided content_hash.
    payload = {"input": request.model_dump(mode="json"), "provider": "gemini",
               "model": settings.model, "system": SYSTEM_INSTRUCTION,
               "schema": ModelOutput.model_json_schema(), "max_output_tokens": settings.max_output_tokens}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


class GeminiEvaluator:
    """Create ONCE per app process/event loop; close on app shutdown.

    This object owns up to TWO provider calls per evaluate(), including repair/retry.
    Backend must not wrap evaluate() in another automatic retry loop.
    The returned output can need evidence review: backend MUST call validate_output.
    """
    def __init__(self, settings: EvaluatorSettings, *, client=None,
                 on_attempt: Callable[[AttemptEvent], None] | None = None):
        self.settings = settings
        self._owned = client is None
        self._client = client if client is not None else genai.Client(
            api_key=settings.api_key,
            http_options=types.HttpOptions(timeout=int(settings.timeout_seconds * 1000),
                                          retry_options=types.HttpRetryOptions(attempts=1)),
        ).aio
        self._lock = asyncio.Lock()
        self._last_start = float("-inf")
        self._on_attempt = on_attempt

    async def aclose(self):
        if self._owned:
            await self._client.aclose()

    async def evaluate(self, request: EvaluatorInput) -> AdapterResult:
        # Validate and detach mutable caller objects before the first await.
        snapshot = EvaluatorInput.model_validate_json(request.model_dump_json())
        if snapshot.prompt_version != PROMPT_VERSION:
            raise failure("INVALID_REQUEST", f"Expected prompt_version={PROMPT_VERSION}")
        async with self._lock:
            return await self._evaluate(snapshot)

    async def _evaluate(self, request):
        fingerprint = request_fingerprint(request, self.settings)
        # Only text is submitted; audio_url is not sent to a provider or dereferenced.
        payload = request.model_dump(mode="json")
        payload["conversation"].pop("audio_url", None)
        contents = json.dumps(payload, ensure_ascii=False)
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_json_schema=ModelOutput.model_json_schema(),
            max_output_tokens=self.settings.max_output_tokens,
        )
        events = []
        total_in = total_out = 0
        feedback = None
        for number in (1, 2):
            delay = self.settings.min_interval_seconds - (time.monotonic() - self._last_start)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_start = start = time.monotonic()
            in_tokens = out_tokens = None
            problem = None
            result = None
            try:
                prompt = contents if feedback is None else contents + "\nVALIDATION_FEEDBACK:\n" + feedback
                async with asyncio.timeout(self.settings.timeout_seconds):
                    response = await self._client.models.generate_content(
                        model=self.settings.model, contents=prompt, config=config)
                usage = response.usage_metadata
                if usage is not None:
                    in_tokens = usage.prompt_token_count
                    # Include reasoning/thinking tokens, not only visible candidate text.
                    out_tokens = ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0))
                total_in += in_tokens or 0
                total_out += out_tokens or 0
                result = self._parse(response, request)
                outcome = validate_output(result, request.checklist, request.conversation).status
            except EvaluationFailure as exc:
                problem = exc
                outcome = exc.error.code
            except ValidationError:
                problem = failure("MODEL_OUTPUT_INVALID", "Model response does not match the required JSON schema")
                outcome = problem.error.code
            except errors.APIError as exc:
                status = exc.code
                after = retry_delay(exc)
                if status == 429:
                    problem = failure("PROVIDER_RATE_LIMIT", "Gemini quota or rate limit reached",
                                      retryable=True, retry_after=after)
                else:
                    temporary = status in (408, 500, 502, 503, 504)
                    problem = failure("PROVIDER_UNAVAILABLE", "Gemini request failed; check server configuration or retry later",
                                      retryable=temporary, retry_after=after)
                outcome = problem.error.code
            except (TimeoutError, httpx2.TransportError):
                problem = failure("PROVIDER_UNAVAILABLE", "Gemini timed out or could not be reached", retryable=True)
                outcome = problem.error.code
            event = AttemptEvent(fingerprint, number, outcome, round(time.monotonic() - start, 3), in_tokens, out_tokens)
            events.append(event)
            if self._on_attempt is not None:
                self._on_attempt(event)
            if problem is None:
                return AdapterResult(output=result, provenance=Provenance(
                    provider="gemini", model=response.model_version or self.settings.model,
                    prompt_version=request.prompt_version, knowledge_version=request.knowledge_version,
                    input_tokens=total_in, output_tokens=total_out, cost_usd=None))
            can_retry = problem.error.retryable or problem.error.code == "MODEL_OUTPUT_INVALID"
            after = problem.error.retry_after_seconds
            if number == 2 or not can_retry or (after is not None and after > 30):
                problem.attempts = tuple(events)
                raise problem from None
            if after is not None and after > 0:
                await asyncio.sleep(after)
            if problem.error.code == "MODEL_OUTPUT_INVALID":
                feedback = json.dumps({"instruction": "Re-evaluate the original data and return the complete corrected JSON.",
                                       "errors": [i.model_dump() for i in problem.error.details]}, ensure_ascii=False)
        raise AssertionError("unreachable")

    @staticmethod
    def _parse(response, request):
        prompt_feedback = response.prompt_feedback
        if prompt_feedback and prompt_feedback.block_reason and str(prompt_feedback.block_reason).split(".")[-1] != "BLOCK_REASON_UNSPECIFIED":
            raise failure("MODEL_REFUSAL", "Gemini declined this request")
        candidates = response.candidates or []
        if len(candidates) != 1:
            raise failure("MODEL_OUTPUT_INVALID", "Gemini did not return one complete candidate")
        reason = candidates[0].finish_reason
        reason = getattr(reason, "value", reason)
        if reason in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"):
            raise failure("MODEL_REFUSAL", "Gemini declined this request")
        if reason != "STOP":
            raise failure("MODEL_OUTPUT_INVALID", "Gemini response was incomplete")
        parts = candidates[0].content.parts if candidates[0].content else []
        text_parts = [p.text for p in parts or [] if p.text and not p.thought]
        if not text_parts:
            raise failure("MODEL_OUTPUT_INVALID", "Gemini returned no JSON text")
        output = ModelOutput.model_validate_json("".join(text_parts))
        report = validate_output(output, request.checklist, request.conversation)
        if report.status == "invalid":
            raise failure("MODEL_OUTPUT_INVALID", "Model decisions violate the checklist", details=report.errors)
        return output
