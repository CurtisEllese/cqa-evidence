"""HTTP evaluation lifecycle tests with injected synthetic async adapters only."""
from concurrent.futures import ThreadPoolExecutor
import asyncio
import json
import threading

from fastapi.testclient import TestClient
import pytest

from backend.app import create_app
from backend.contracts.v2 import ApiError, ConversationExport, EvaluationResult, Health, WorkspaceState
from backend.evaluator import AdapterFailure


def model_output(request):
    values = {"greeting": "1", "discovery": None, "identification": None, "consultation": "15", "empathy": "1", "dialogue": "7", "closing": "1", "gross-errors": "0"}
    return {"schema_version": "2.0", "results": [{
        "criterionId": criterion.id, "value": values[criterion.id], "explanation": "Синтетическое обоснование API-теста.",
        "applicability_reason": "Дополнительная проверка не требуется." if values[criterion.id] is None else None,
        "evidence": [{"kind": "not_applicable" if values[criterion.id] is None else "message", "message_id": request.messages[1].id, "quote": "Здравствуйте!"}],
    } for criterion in request.checklist.criteria]}


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.delenv("EVALUATOR_ADAPTER", raising=False)
    records = [{
        "conversation_id": f"api-worker-test-{index}", "language": "RU", "channel": "agent chat", "audio_url": None,
        "transcript": [
            {"speaker": "client", "timestamp": "00:00", "text": "Только синтетический вопрос для тестов."},
            {"speaker": "operator", "timestamp": "00:10", "text": "Здравствуйте! Синтетический ответ оператора."},
        ],
    } for index in range(65)]
    cache = tmp_path / "synthetic-cache.json"
    cache.write_text(json.dumps({"fetchedAt": "2026-11-07T12:00:00Z", "payload": {
        "isSuccess": True, "data": {"dataset_group_id": "DEV", "total_records": 65, "records": records},
    }}, ensure_ascii=False), encoding="utf-8")
    return tmp_path / "evaluation-api.sqlite", cache


def app_for(paths, adapter, timeout=1):
    database, cache = paths
    return create_app(database, cache, organizer_url="", evaluator=adapter, evaluation_timeout=timeout)


def start(client, conversation="api-worker-test-0"):
    response = client.post("/api/evaluations", json={"schema_version": "2.0", "conversationId": conversation})
    assert response.status_code == 202, response.text
    result = EvaluationResult.model_validate(response.json())
    assert result.status == "queued" and result.score is None and result.error is None
    return result.id


def poll(client, identifier):
    response = client.get(f"/api/evaluations/{identifier}")
    assert response.status_code == 200, response.text
    return EvaluationResult.model_validate(response.json())


def test_post_202_poll_completed_workspace_and_exports_are_v2(paths):
    calls = []

    async def adapter(request):
        calls.append(request)
        return model_output(request)

    with TestClient(app_for(paths, adapter)) as client:
        assert Health.model_validate(client.get("/api/health").json()).evaluationAvailable is True
        identifier = start(client)
        result = poll(client, identifier)
        assert result.status == "completed" and result.score.total == "85.38"
        assert len(calls) == 1
        state = WorkspaceState.model_validate(client.get("/api/state").json())
        conversation = state.data.conversations[0]
        assert conversation.evaluation.id == identifier
        assert conversation.scores.final.total == "85.38"
        assert conversation.transcript[0].message_index == 0
        exported = client.get(f"/api/evaluations/{identifier}/export")
        assert EvaluationResult.model_validate(exported.json()).score.total == "85.38"
        assert "attachment" in exported.headers["content-disposition"]
        exported_conversation = client.get("/api/conversations/api-worker-test-0/export")
        assert ConversationExport.model_validate(exported_conversation.json()).conversation.evaluation.id == identifier


@pytest.mark.parametrize("failure", ["quote", "message"])
def test_unverified_evidence_is_reviewable_without_score_in_poll_or_workspace(paths, failure):
    async def adapter(request):
        response = model_output(request)
        evidence = response["results"][0]["evidence"][0]
        if failure == "quote":
            evidence["quote"] = "This text does not exist."
        else:
            evidence.update(message_id="unknown-message", quote=None)
        return response

    with TestClient(app_for(paths, adapter)) as client:
        result = poll(client, start(client))
        assert result.status == "needs_review" and result.score is None
        assert result.results[0].evidence[0].verification_status == "unverified"
        workspace = client.get("/api/state").json()
        conversation = workspace["data"]["conversations"][0]
        assert conversation["evaluation"]["status"] == "needs_review"
        assert conversation["evaluation"]["score"] is None and not conversation.get("scores")
        assert WorkspaceState.model_validate(workspace)


def test_concurrent_start_is_rejected_while_first_job_is_running(paths):
    entered, release = threading.Event(), threading.Event()
    calls = []

    async def adapter(request):
        calls.append(request)
        entered.set()
        await asyncio.to_thread(release.wait, 5)
        return model_output(request)

    app = app_for(paths, adapter, timeout=8)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(client.post, "/api/evaluations", json={"schema_version": "2.0", "conversationId": "api-worker-test-0"})
        try:
            assert entered.wait(5), "First job did not enter its adapter"
            duplicate = client.post("/api/evaluations", json={"schema_version": "2.0", "conversationId": "api-worker-test-0"})
            assert duplicate.status_code == 409
            assert ApiError.model_validate(duplicate.json()).code == "CONFLICT"
            with app.state.repo.connect() as db:
                assert db.execute("SELECT COUNT(*) FROM evaluation_jobs WHERE status IN ('queued','running')").fetchone()[0] == 1
        finally:
            release.set()
        response = first.result(timeout=5)
        assert response.status_code == 202
        assert poll(client, response.json()["id"]).status == "completed"
        assert len(calls) == 1


@pytest.mark.parametrize("status", ["queued", "running"])
def test_restart_recovers_persisted_unfinished_jobs_as_retryable(paths, status):
    async def adapter(request):
        return model_output(request)

    abandoned = app_for(paths, adapter)
    job = abandoned.state.evaluator.create("api-worker-test-0")
    if status == "running":
        abandoned.state.evaluator.update(job["id"], "running")
    with TestClient(app_for(paths, adapter)) as restarted:
        recovered = poll(restarted, job["id"])
        assert recovered.status == "retryable" and recovered.score is None
        assert recovered.error.code == "WORKER_INTERRUPTED" and recovered.error.retryable is True
        new_identifier = start(restarted)
        assert new_identifier != job["id"]
        assert poll(restarted, new_identifier).status == "completed"


def test_completed_result_survives_restart_without_calling_adapter_again(paths):
    calls = []

    async def adapter(request):
        calls.append(request)
        return model_output(request)

    with TestClient(app_for(paths, adapter)) as client:
        identifier = start(client)
        original = poll(client, identifier).model_dump()
    with TestClient(app_for(paths, adapter)) as restarted:
        assert poll(restarted, identifier).model_dump() == original
        assert len(calls) == 1


def test_provider_failure_job_poll_stays_http_200_and_contains_safe_error(paths):
    async def adapter(request):
        raise AdapterFailure("MODEL_REFUSAL", retryable=False)

    with TestClient(app_for(paths, adapter)) as client:
        result = poll(client, start(client))
        assert result.status == "failed" and result.score is None and result.results == []
        assert result.error.code == "MODEL_REFUSAL"
        state = client.get("/api/state").json()
        assert state["data"]["conversations"][0]["evaluation"]["status"] == "failed"
        assert not state["data"]["conversations"][0].get("scores")


def test_structural_failure_two_attempts_and_versioned_request_errors(paths):
    calls = 0

    async def adapter(request):
        nonlocal calls
        calls += 1
        response = model_output(request)
        response["results"][0]["value"] = "17"
        return response

    with TestClient(app_for(paths, adapter)) as client:
        invalid = client.post("/api/evaluations", json={"conversationId": "api-worker-test-0"})
        assert invalid.status_code == 422 and ApiError.model_validate(invalid.json()).code == "INVALID_REQUEST"
        result = poll(client, start(client))
        assert result.status == "failed_validation" and result.score is None
        assert result.error.code == "MODEL_OUTPUT_INVALID" and calls == 2
        missing = client.get("/api/evaluations/nonexistent")
        assert missing.status_code == 404 and ApiError.model_validate(missing.json()).code == "NOT_FOUND"
