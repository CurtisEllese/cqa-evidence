import asyncio
import json
from pathlib import Path

import httpx
import pytest

from backend.contracts.v2 import EvaluationRequest, ModelResponse
from backend.evaluator import AdapterFailure, EvaluationService, inspect_output, load_adapter
from backend.repository import Repository
from evaluator.openai_v2 import OpenAIV2Evaluator, UsageEvent, write_usage_event, openai_schema
from evaluator.settings import OpenAISettings


def request():
    path = Path(__file__).parents[1] / "evaluator/fixtures/synthetic_v2.json"
    raw = json.loads(path.read_text())[0]["request"]
    raw["knowledgeFacts"] = []  # legacy fixture facts have no confirmed applicability
    return EvaluationRequest.model_validate(raw)


def answer():
    req = request()
    return {"schema_version": "2.0", "results": [
        {"criterionId": c.id, "value": "0", "explanation": "Synthetic test answer",
         "applicability_reason": None, "evidence": [{"kind": "absence",
         "start_message_id": req.messages[0].id, "end_message_id": req.messages[-1].id}]}
        for c in req.checklist.criteria]}


def envelope(text=None):
    return {"model": "configured-model-snapshot", "status": "completed",
            "usage": {"input_tokens": 40, "output_tokens": 20},
            "output": [{"type": "reasoning", "summary": []},
                       {"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": text or json.dumps(answer())}]}]}


class Fake:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def handle(self, req):
        self.calls.append(req)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def judge(self, **kwargs):
        return OpenAIV2Evaluator(
            OpenAISettings("test-secret", "configured-model", min_interval_seconds=0),
            transport=httpx.MockTransport(self.handle), **kwargs)


def test_request_contract_strict_schema_and_safe_usage():
    fake = Fake(httpx.Response(200, json=envelope()))
    events = []
    result = asyncio.run(fake.judge(on_attempt=events.append).evaluate(request()))
    assert isinstance(result, ModelResponse)
    sent = fake.calls[0]
    payload = json.loads(sent.content)
    assert str(sent.url) == "https://api.openai.com/v1/responses"
    assert sent.headers["authorization"] == "Bearer test-secret"
    assert payload["store"] is False
    expected = request().model_dump(mode="json")
    # v2 cannot prove the conversation/entity/date scope of a legacy fact.
    expected["knowledgeFacts"] = []
    assert json.loads(payload["input"][0]["content"]) == expected
    assert payload["text"]["format"]["strict"] is True
    schema = payload["text"]["format"]["schema"]
    assert schema == openai_schema(ModelResponse.model_json_schema())
    for node in [schema, *schema["$defs"].values()]:
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
    assert events[0].output_tokens == 20
    assert events[0].model == "configured-model-snapshot"
    assert "test-secret" not in str(events)


@pytest.mark.parametrize("status,retryable,code", [
    (401, False, "PROVIDER_UNAVAILABLE"), (403, False, "PROVIDER_UNAVAILABLE"),
    (404, False, "PROVIDER_UNAVAILABLE"), (429, True, "PROVIDER_RATE_LIMIT"),
    (503, True, "PROVIDER_UNAVAILABLE"), (302, False, "PROVIDER_UNAVAILABLE")])
def test_http_failure_never_becomes_score_or_internal_retry(status, retryable, code):
    fake = Fake(httpx.Response(status, json={"error": {"message": "secret customer data"}},
                               headers={"Location": "https://other.example"}))
    with pytest.raises(AdapterFailure) as error:
        asyncio.run(fake.judge().evaluate(request()))
    assert error.value.code == code and error.value.retryable == retryable
    assert len(fake.calls) == 1
    assert "customer" not in str(error.value)


@pytest.mark.parametrize("headers,body", [
    ({"Retry-After": "120"}, {"error": {}}),
    ({}, {"error": {"code": "insufficient_quota"}})])
def test_long_retry_hint_and_empty_quota_are_not_retried(headers, body):
    fake = Fake(httpx.Response(429, headers=headers, json=body))
    with pytest.raises(AdapterFailure) as error:
        asyncio.run(fake.judge().evaluate(request()))
    assert not error.value.retryable


@pytest.mark.parametrize("body", [envelope("bad"), {"status": "incomplete"},
    {"status": "completed", "output": []}, [],
    {"status": "completed", "output": [None]}])
def test_invalid_and_incomplete_output_raise_repairable_error(body):
    fake = Fake(httpx.Response(200, json=body))
    with pytest.raises(json.JSONDecodeError):
        asyncio.run(fake.judge().evaluate(request()))
    assert len(fake.calls) == 1


def test_refusal_is_not_repaired_as_json():
    body = envelope()
    body["output"][1]["content"] = [{"type": "refusal", "refusal": "Private text"}]
    fake = Fake(httpx.Response(200, json=body))
    with pytest.raises(AdapterFailure) as error:
        asyncio.run(fake.judge().evaluate(request()))
    assert error.value.code == "MODEL_REFUSAL" and not error.value.retryable


def test_timeout_is_safe_and_retryable():
    fake = Fake(httpx.ReadTimeout("Secret provider context"))
    with pytest.raises(AdapterFailure) as error:
        asyncio.run(fake.judge().evaluate(request()))
    assert error.value.retryable and "Secret" not in str(error.value)


def test_bad_evidence_still_goes_through_backend_guard():
    body = answer()
    body["results"][0]["evidence"][0]["start_message_id"] = "missing"
    fake = Fake(httpx.Response(200, json=envelope(json.dumps(body))))
    result = asyncio.run(fake.judge().evaluate(request()))
    _, errors, reviews = inspect_output(result, request())
    assert not errors and reviews


def test_worker_owns_two_attempts_and_passes_repair_feedback(tmp_path):
    req = request()
    repo = Repository(tmp_path / "openai.sqlite")
    repo.import_data([{"conversation_id": req.conversationId, "language": req.language,
        "channel": req.channel, "audio_url": None, "transcript": [
            {"speaker": m.speaker, "timestamp": m.timestamp, "text": m.text} for m in req.messages]}])
    fake = Fake(httpx.Response(200, json=envelope("invalid")), httpx.Response(200, json=envelope()))
    service = EvaluationService(repo, fake.judge().evaluate, timeout=5)
    job = service.create(req.conversationId)
    asyncio.run(service.run(job["id"]))
    assert service.get(job["id"])["status"] == "completed"
    assert len(fake.calls) == 2
    second = json.loads(json.loads(fake.calls[1].content)["input"][0]["content"])
    assert second["repair_feedback"]
    with repo.connect() as db:
        assert db.execute("select count(*) from evaluations").fetchone()[0] == 1


def test_entrypoint_requires_key_without_fallback(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    callback = load_adapter("evaluator.provider:evaluate")
    with pytest.raises(AdapterFailure):
        asyncio.run(callback(request()))
    monkeypatch.setenv("LLM_PROVIDER", "typo")
    with pytest.raises(AdapterFailure):
        asyncio.run(callback(request()))


def test_safe_usage_journal(tmp_path, monkeypatch):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("OPENAI_USAGE_LOG", str(path))
    write_usage_event(UsageEvent("hash", "prompt", "model", "parsed", 0.2, 40, 20))
    row = json.loads(path.read_text())
    assert row["provider"] == "openai"
    assert row["cost_usd"] is None
    assert not {"api_key", "messages", "input", "response"} & set(row)


def test_provider_schema_rejects_quoted_null_and_accepts_json_null():
    import jsonschema
    schema = openai_schema(ModelResponse.model_json_schema())
    data = ModelResponse.model_validate(answer()).model_dump(mode="json")
    data["results"][0]["value"] = "null"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, schema)
    data["results"][0]["value"] = None
    data["results"][0]["applicability_reason"] = "Not applicable in this synthetic case"
    jsonschema.validate(data, schema)
