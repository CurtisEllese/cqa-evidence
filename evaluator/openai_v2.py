"""Responses API adapter: one HTTP request; backend owns retry and repair."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from weakref import WeakKeyDictionary

import httpx
from pydantic import ValidationError

from backend.contracts.v2 import EvaluationRequest, ModelResponse
from backend.evaluator import AdapterFailure
from .budget import BudgetLedger
from .prompt import SYSTEM
from .settings import OpenAISettings, retry_delay

PROMPT_VERSION = "openai-cqa-v2-wire2-na3"
ENDPOINT = "https://api.openai.com/v1/responses"


def openai_schema(node):
    """Retain decimal patterns: a string 'null' must never satisfy the score schema."""
    if isinstance(node, list):
        return [openai_schema(value) for value in node]
    if not isinstance(node, dict):
        return node
    result = {key: openai_schema(value) for key, value in node.items()
              if key not in ("default", "const")}
    if "const" in node:
        result["enum"] = [node["const"]]
    if result.get("type") == "object":
        result["required"] = list(result.get("properties", {}))
        result["additionalProperties"] = False
    if "Evidence" in result.get("$defs", {}):
        # Choose one reference form instead of filling unrelated optional IDs.
        # The backend still verifies every referenced message and range.
        source = result["$defs"]["Evidence"]
        variants = []
        for kind, ranged in [("message", False), ("absence", True),
                             ("not_applicable", False), ("not_applicable", True)]:
            variant = copy.deepcopy(source)
            props = variant["properties"]
            props["kind"] = {"type": "string", "enum": [kind]}
            props["quote"] = {"type": "null"}
            for field in ("message_id", "start_message_id", "end_message_id"):
                used = (field != "message_id") if ranged else (field == "message_id")
                props[field] = next(x for x in props[field]["anyOf"]
                                    if x.get("type") == ("string" if used else "null"))
            variants.append(variant)
        result["$defs"]["Evidence"] = {"anyOf": variants}
    return result


@dataclass(frozen=True)
class UsageEvent:
    fingerprint: str
    prompt_version: str
    model: str
    outcome: str
    seconds: float
    input_tokens: int | None
    output_tokens: int | None
    provider: str = "openai"
    cost_usd: str | None = None
    attempt_id: str | None = None
    cost_basis: str = "standard_upper_estimate"


def write_usage_event(event):
    path = Path(os.getenv("OPENAI_USAGE_LOG", str(
        Path(__file__).resolve().parents[1] / "local-data/openai-usage.jsonl")))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
    except OSError:
        logging.getLogger(__name__).warning("Unable to persist OpenAI usage event")


class OpenAIV2Evaluator:
    def __init__(self, settings: OpenAISettings, *, transport=None, on_attempt=None, budget=None):
        self.settings = settings
        self.transport = transport
        self.on_attempt = on_attempt
        # MockTransport is an offline test. Every real HTTP client gets a
        # durable ledger, irrespective of its optional reporting callback.
        self.budget = budget if budget is not None else (None if isinstance(transport, httpx.MockTransport) else BudgetLedger.from_env())
        self.lock = asyncio.Lock()
        self.last_start = float("-inf")

    async def evaluate(self, request: EvaluationRequest) -> ModelResponse:
        snapshot = EvaluationRequest.model_validate_json(request.model_dump_json())
        schema = openai_schema(ModelResponse.model_json_schema())
        payload = {
            "model": self.settings.model,
            "instructions": SYSTEM,
            "input": [{"role": "user", "content": snapshot.model_dump_json()}],
            "text": {"format": {"type": "json_schema", "name": "cqa_evaluation",
                                "strict": True, "schema": schema}},
            "max_output_tokens": self.settings.max_output_tokens,
            "store": False,
            "service_tier": "default",
        }
        async with self.lock:
            delay = self.settings.min_interval_seconds - (time.monotonic() - self.last_start)
            if delay > 0:
                await asyncio.sleep(delay)
            self.last_start = started = time.monotonic()
            fingerprint = hashlib.sha256((
                snapshot.model_dump_json(exclude={"repair_feedback"}) + SYSTEM
                + self.settings.model + PROMPT_VERSION + json.dumps(schema, sort_keys=True)
                + str(self.settings.max_output_tokens)).encode()).hexdigest()
            outcome, incoming, outgoing, model = "cancelled_or_failed", None, None, self.settings.model
            attempt_id = self.budget.reserve(payload, fingerprint) if self.budget is not None else None
            try:
                # No hidden retries or redirects; credentials only go to the official endpoint.
                async with asyncio.timeout(self.settings.timeout_seconds):
                    async with httpx.AsyncClient(transport=self.transport,
                                                timeout=self.settings.timeout_seconds) as client:
                        response = await client.post(ENDPOINT, json=payload, headers={
                            "Authorization": f"Bearer {self.settings.api_key}"})
                if response.status_code != 200:
                    code = "PROVIDER_RATE_LIMIT" if response.status_code == 429 else "PROVIDER_UNAVAILABLE"
                    after = retry_delay(SimpleNamespace(response=response))
                    retryable = response.status_code in (408, 429, 500, 502, 503, 504)
                    if response.status_code == 429:
                        try:
                            if response.json().get("error", {}).get("code") == "insufficient_quota":
                                retryable = False
                        except (ValueError, AttributeError):
                            pass
                    if after is not None and after > 30:
                        retryable = False
                    raise AdapterFailure(code, retryable=retryable, retry_after=after or 0)
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError("Invalid envelope")
                model = body.get("model") or model
                usage = body.get("usage") or {}
                incoming, outgoing = usage.get("input_tokens"), usage.get("output_tokens")
                if body.get("status") != "completed":
                    if (body.get("incomplete_details") or {}).get("reason") == "content_filter":
                        raise AdapterFailure("MODEL_REFUSAL")
                    raise ValueError("Incomplete response")
                texts = []
                for item in body.get("output", []):
                    if item.get("type") != "message":
                        continue
                    if item.get("role") != "assistant" or item.get("status") != "completed":
                        raise ValueError("Incomplete message")
                    for part in item.get("content", []):
                        if part.get("type") == "refusal":
                            raise AdapterFailure("MODEL_REFUSAL")
                        if part.get("type") == "output_text":
                            texts.append(part["text"])
                result = ModelResponse.model_validate_json("".join(texts))
                outcome = "parsed"
                return result
            except (TimeoutError, httpx.TransportError):
                outcome = "PROVIDER_UNAVAILABLE"
                raise AdapterFailure(outcome, retryable=True) from None
            except AdapterFailure as exc:
                outcome = exc.code
                raise
            except (ValidationError, ValueError, TypeError, AttributeError, KeyError):
                outcome = "MODEL_OUTPUT_INVALID"
                raise json.JSONDecodeError("Invalid structured model response", "", 0) from None
            finally:
                cost = self.budget.settle(attempt_id, incoming, outgoing, outcome) if attempt_id else None
                event = UsageEvent(fingerprint, PROMPT_VERSION, model, outcome,
                                   round(time.monotonic() - started, 3), incoming, outgoing,
                                   cost_usd=cost, attempt_id=attempt_id)
                logging.getLogger(__name__).info("openai_attempt %s", json.dumps(asdict(event)))
                if self.on_attempt is not None:
                    self.on_attempt(event)


_instances = WeakKeyDictionary()


async def evaluate(request: EvaluationRequest) -> ModelResponse:
    loop = asyncio.get_running_loop()
    if loop not in _instances:
        try:
            settings = OpenAISettings.from_env()
        except ValueError:
            raise AdapterFailure("PROVIDER_UNAVAILABLE", retryable=False) from None
        _instances[loop] = OpenAIV2Evaluator(settings, on_attempt=write_usage_event)
    return await _instances[loop].evaluate(request)
