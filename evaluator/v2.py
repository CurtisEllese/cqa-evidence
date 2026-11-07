"""Active v2 adapter: exactly ONE provider request; backend owns retry/repair."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from weakref import WeakKeyDictionary

import httpx2
from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from backend.contracts.v2 import EvaluationRequest, ModelResponse
from backend.evaluator import AdapterFailure
from .settings import EvaluatorSettings, retry_delay

PROMPT_VERSION = "gemini-cqa-v2-wire2-na1"
from .prompt import SYSTEM, provider_schema


@dataclass(frozen=True)
class UsageEvent:
    fingerprint: str
    prompt_version: str
    model: str
    outcome: str
    seconds: float
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: None = None


def write_usage_event(event):
    """Persist safe usage independently of the response DTO; never lose a paid result on log failure."""
    path=Path(os.getenv('GEMINI_USAGE_LOG',str(Path(__file__).resolve().parents[1]/'local-data/gemini-usage.jsonl')))
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
        with os.fdopen(fd,'a',encoding='utf-8') as stream:
            stream.write(json.dumps(asdict(event),ensure_ascii=False)+'\n')
    except OSError:
        logging.getLogger(__name__).warning('Unable to persist Gemini usage event')


class GeminiV2Evaluator:
    def __init__(self, settings: EvaluatorSettings, *, client=None, on_attempt=None):
        self.settings = settings
        self.client = client
        self.on_attempt = on_attempt
        self.lock = asyncio.Lock()
        self.last_start = float("-inf")

    async def evaluate(self, request: EvaluationRequest) -> ModelResponse:
        snapshot = EvaluationRequest.model_validate_json(request.model_dump_json())
        async with self.lock:
            delay = self.settings.min_interval_seconds - (time.monotonic() - self.last_start)
            if delay > 0: await asyncio.sleep(delay)
            self.last_start = started = time.monotonic()
            fingerprint = hashlib.sha256((snapshot.model_dump_json(exclude={'repair_feedback'}) + SYSTEM + self.settings.model
                + json.dumps(provider_schema(ModelResponse.model_json_schema()),sort_keys=True)
                + str(self.settings.max_output_tokens)).encode()).hexdigest()
            outcome, incoming, outgoing, model = "cancelled_or_failed", None, None, self.settings.model
            try:
                config = types.GenerateContentConfig(system_instruction=SYSTEM,
                    response_mime_type="application/json", response_json_schema=provider_schema(ModelResponse.model_json_schema()),
                    max_output_tokens=self.settings.max_output_tokens)
                async with asyncio.timeout(self.settings.timeout_seconds):
                    if self.client is not None:
                        response = await self.client.models.generate_content(
                            model=self.settings.model, contents=snapshot.model_dump_json(), config=config)
                    else:
                        # Per-call context closes HTTP resources even on cancellation; no lifespan hook required.
                        async with genai.Client(api_key=self.settings.api_key, http_options=types.HttpOptions(
                            timeout=int(self.settings.timeout_seconds * 1000),
                            retry_options=types.HttpRetryOptions(attempts=1))).aio as client:
                            response = await client.models.generate_content(
                                model=self.settings.model, contents=snapshot.model_dump_json(), config=config)
                model = response.model_version or self.settings.model
                if response.usage_metadata:
                    u = response.usage_metadata
                    incoming = u.prompt_token_count
                    outgoing = (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)
                feedback = response.prompt_feedback
                if feedback and feedback.block_reason and getattr(feedback.block_reason, "value", feedback.block_reason) != "BLOCK_REASON_UNSPECIFIED":
                    raise AdapterFailure("MODEL_REFUSAL")
                candidates = response.candidates or []
                if len(candidates) != 1:
                    raise json.JSONDecodeError("Missing candidate", "", 0)
                reason = getattr(candidates[0].finish_reason, "value", candidates[0].finish_reason)
                if reason in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"):
                    raise AdapterFailure("MODEL_REFUSAL")
                if reason != "STOP":
                    raise json.JSONDecodeError("Incomplete response", "", 0)
                parts = candidates[0].content.parts if candidates[0].content else []
                text = "".join(p.text for p in parts or [] if p.text and not p.thought)
                result = ModelResponse.model_validate_json(text)
                outcome = "parsed"  # Evidence/scale verification is exclusively backend's responsibility.
                return result
            except errors.APIError as exc:
                code = "PROVIDER_RATE_LIMIT" if exc.code == 429 else "PROVIDER_UNAVAILABLE"
                after = retry_delay(exc)
                retryable = exc.code in (408, 429, 500, 502, 503, 504)
                # Backend caps retry_after at 30: never retry earlier than a longer provider hint.
                if after is not None and after > 30: retryable = False
                outcome = code
                raise AdapterFailure(code, retryable=retryable, retry_after=after or 0) from None
            except (TimeoutError, httpx2.TransportError):
                outcome = "PROVIDER_UNAVAILABLE"
                raise AdapterFailure("PROVIDER_UNAVAILABLE", retryable=True) from None
            except AdapterFailure as exc:
                outcome = exc.code
                raise
            except (ValidationError, json.JSONDecodeError):
                outcome = "MODEL_OUTPUT_INVALID"
                # Worker explicitly handles these as structural failures and supplies repair_feedback.
                raise json.JSONDecodeError("Invalid structured model response", "", 0) from None
            finally:
                event = UsageEvent(fingerprint, PROMPT_VERSION, model, outcome,
                                   round(time.monotonic() - started, 3), incoming, outgoing)
                logging.getLogger(__name__).info("gemini_attempt %s", json.dumps(asdict(event)))
                if self.on_attempt is not None: self.on_attempt(event)


_instances = WeakKeyDictionary()


async def evaluate(request: EvaluationRequest) -> ModelResponse:
    """EVALUATOR_ADAPTER=evaluator.v2:evaluate. One adapter instance per event loop."""
    loop = asyncio.get_running_loop()
    if loop not in _instances:
        try:
            # Under backend's 60s deadline, with a serialized provider queue.
            settings = replace(EvaluatorSettings.from_env(), timeout_seconds=45)
        except ValueError:
            raise AdapterFailure("PROVIDER_UNAVAILABLE", retryable=False) from None
        _instances[loop] = GeminiV2Evaluator(settings,on_attempt=write_usage_event)
    return await _instances[loop].evaluate(request)
