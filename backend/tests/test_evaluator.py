"""Worker boundary regression tests: synthetic adapters, no network or real datasets."""
import asyncio
from copy import deepcopy
import json

import pytest

from backend.contracts.v2 import ModelResponse, to_wire_evaluation, to_wire_state
from backend.domain import initial_checklist
from backend.evaluator import AdapterFailure, EvaluationService
from backend.repository import Conflict, Repository


@pytest.fixture
def repo(tmp_path):
    repository = Repository(tmp_path / "evaluator.sqlite")
    repository.import_data([{
        "conversation_id": "worker-test", "channel": "agent chat", "language": "RU", "audio_url": None,
        "transcript": [
            {"timestamp": "00:00", "speaker": "client", "text": "Синтетический запрос."},
            {"timestamp": "00:10", "speaker": "operator", "text": "Здравствуйте! Я тестовый оператор. Чем могу помочь?"},
            {"timestamp": "00:15", "speaker": "client", "text": "Спасибо, вопрос закрыт."},
        ],
    }])
    return repository


def output(request):
    values = {"greeting": "1", "discovery": None, "identification": None, "consultation": "15", "empathy": "1", "dialogue": "7", "closing": "1", "gross-errors": "0"}
    return {"schema_version": "2.0", "results": [
        {"criterionId": criterion.id, "value": values[criterion.id], "explanation": "Синтетическое обоснование для теста воркера.",
         "applicability_reason": "Запрос не требует дополнительной проверки." if values[criterion.id] is None else None,
         "evidence": [{"kind": "not_applicable" if values[criterion.id] is None else "message", "message_id": request.messages[1].id, "quote": "Здравствуйте!"}]}
        for criterion in request.checklist.criteria
    ]}


def execute(repo, adapter, *, timeout=1):
    service = EvaluationService(repo, adapter, timeout=timeout)
    job = service.create("worker-test")
    asyncio.run(service.run(job["id"]))
    return service, service.get(job["id"])


def wire(repo, result):
    conversation = repo.state()["data"]["conversations"][0]
    return to_wire_evaluation(result, messages=conversation["transcript"])


def stored_evaluation_count(repo):
    with repo.connect() as db:
        return db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]


def test_valid_model_response_is_persisted_and_scored(repo):
    calls = []

    async def adapter(request):
        calls.append(request)
        return ModelResponse.model_validate(output(request))

    service, result = execute(repo, adapter)
    assert result["status"] == "completed" and result["error"] is None
    assert len(calls) == 1 and stored_evaluation_count(repo) == 1
    assert calls[0].language == "RU" and calls[0].channel == "agent chat"
    assert [message.message_index for message in calls[0].messages] == [0, 1, 2]
    assert wire(repo, result)["score"]["total"] == "85.38"
    state = to_wire_state(repo.state())
    assert state["data"]["conversations"][0]["scores"]["final"]["total"] == "85.38"
    assert all(evidence["verification_status"] == "verified" for result in result["results"] for evidence in result["evidence"])
    assert service.get(result["id"])["id"] == result["id"]


@pytest.mark.parametrize("failure", ["missing", "duplicate", "unknown_criterion", "outside_scale", "forbidden_na", "missing_na_reason", "invalid_json", "model_score"])
def test_structural_errors_allow_only_one_repair_then_fail_without_score(repo, failure):
    requests = []

    async def adapter(request):
        requests.append(request)
        response = output(request)
        if failure == "missing":
            response["results"].pop()
        elif failure == "duplicate":
            response["results"][1]["criterionId"] = response["results"][0]["criterionId"]
        elif failure == "unknown_criterion":
            response["results"][0]["criterionId"] = "not-in-checklist"
        elif failure == "outside_scale":
            response["results"][3]["value"] = "12"
        elif failure == "forbidden_na":
            response["results"][0].update(value=None, applicability_reason="Reason supplied but N/A is forbidden.")
        elif failure == "missing_na_reason":
            response["results"][1]["applicability_reason"] = None
        elif failure == "invalid_json":
            return "{ invalid JSON"
        elif failure == "model_score":
            response["score"] = "100"
        return response

    _, result = execute(repo, adapter)
    assert len(requests) == 2
    assert requests[0].repair_feedback is None
    assert requests[1].repair_feedback
    assert result["status"] == "failed_validation" and result["results"] == []
    assert result["error"]["code"] == "MODEL_OUTPUT_INVALID"
    assert wire(repo, result)["score"] is None
    assert stored_evaluation_count(repo) == 0


def test_repaired_output_uses_second_attempt_and_can_complete(repo):
    calls = []

    async def adapter(request):
        calls.append(request)
        response = output(request)
        if len(calls) == 1:
            response["results"][3]["value"] = "12"
        return response

    _, result = execute(repo, adapter)
    assert len(calls) == 2 and result["status"] == "completed"
    assert any(issue.code == "VALUE_NOT_ALLOWED" for issue in calls[1].repair_feedback)
    assert wire(repo, result)["score"]["total"] == "85.38"


@pytest.mark.parametrize("failure", ["pydantic", "json"])
def test_typed_adapter_parse_exceptions_are_repaired_once(repo, failure):
    requests = []

    async def adapter(request):
        requests.append(request)
        if len(requests) == 1:
            if failure == "pydantic":
                return ModelResponse.model_validate({"schema_version": "2.0", "results": "invalid"})
            return json.loads("{invalid provider JSON")
        return output(request)

    _, result = execute(repo, adapter)
    assert len(requests) == 2 and requests[1].repair_feedback
    assert result["status"] == "completed" and wire(repo, result)["score"]["total"] == "85.38"


@pytest.mark.parametrize("failure", ["pydantic", "json"])
def test_typed_adapter_repeated_parse_errors_stop_after_two_calls(repo, failure):
    calls = 0

    async def adapter(request):
        nonlocal calls
        calls += 1
        if failure == "pydantic":
            return ModelResponse.model_validate({"schema_version": "2.0", "results": "invalid"})
        return json.loads("{invalid provider JSON")

    _, result = execute(repo, adapter)
    assert calls == 2 and result["status"] == "failed_validation"
    assert result["error"]["code"] == "MODEL_OUTPUT_INVALID" and stored_evaluation_count(repo) == 0


@pytest.mark.parametrize("failure,reason", [("unknown_message", "MESSAGE_NOT_FOUND"), ("bad_quote", "QUOTE_MISMATCH"), ("missing", "EVIDENCE_MISSING"), ("reverse_range", "RANGE_REVERSED")])
def test_unverifiable_evidence_requires_review_and_has_no_numeric_score(repo, failure, reason):
    calls = []

    async def adapter(request):
        calls.append(request)
        response = output(request)
        target = response["results"][0]
        if failure == "unknown_message":
            target["evidence"] = [{"message_id": "invented-message", "quote": None}]
        elif failure == "bad_quote":
            target["evidence"][0]["quote"] = "A quote never present in the transcript."
        elif failure == "missing":
            target["evidence"] = []
        else:
            target["evidence"] = [{"kind": "absence", "start_message_id": request.messages[2].id, "end_message_id": request.messages[0].id}]
        return response

    _, result = execute(repo, adapter)
    assert result["status"] == "needs_review"
    assert len(calls) <= 2 and stored_evaluation_count(repo) == 0
    assert wire(repo, result)["score"] is None
    candidate = to_wire_state(repo.state())["data"]["conversations"][0]
    assert candidate["evaluation"]["status"] == "needs_review" and not candidate.get("scores")
    if failure != "missing":
        evidence = result["results"][0]["evidence"][0]
        assert evidence["verification_status"] == "unverified" and evidence["reason"] == reason
    else:
        assert result["results"][0]["evidence"] == []


def test_verified_inclusive_range_uses_real_source_text(repo):
    async def adapter(request):
        response = output(request)
        response["results"][0]["evidence"] = [{"kind": "absence", "start_message_id": request.messages[0].id, "end_message_id": request.messages[2].id}]
        return response

    _, result = execute(repo, adapter)
    evidence = result["results"][0]["evidence"][0]
    assert result["status"] == "completed"
    assert evidence["source_text"] == "Синтетический запрос.\nЗдравствуйте! Я тестовый оператор. Чем могу помочь?\nСпасибо, вопрос закрыт."
    assert result["results"][0]["evidenceIds"] == ["worker-test-m1", "worker-test-m2", "worker-test-m3"]


def test_evidence_message_outside_its_checked_range_is_unverified(repo):
    async def adapter(request):
        response = output(request)
        response["results"][0]["evidence"] = [{
            "kind": "absence", "start_message_id": request.messages[0].id, "end_message_id": request.messages[0].id,
            "message_id": request.messages[1].id, "quote": "Здравствуйте!",
        }]
        return response

    _, result = execute(repo, adapter)
    assert result["status"] == "needs_review"
    assert result["results"][0]["evidence"][0]["reason"] == "MESSAGE_OUTSIDE_RANGE"
    assert wire(repo, result)["score"] is None
    assert stored_evaluation_count(repo) == 0


def test_retryable_provider_failure_recovers_with_total_two_calls(repo):
    calls = []

    async def adapter(request):
        calls.append(request)
        if len(calls) == 1:
            raise AdapterFailure("PROVIDER_RATE_LIMIT", retryable=True, retry_after=0)
        return output(request)

    _, result = execute(repo, adapter)
    assert len(calls) == 2 and result["status"] == "completed"


def test_retry_and_repair_share_one_budget_instead_of_multiplying_calls(repo):
    calls = 0

    async def adapter(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise AdapterFailure("PROVIDER_UNAVAILABLE", retryable=True)
        response = output(request)
        response["results"].pop()
        return response

    _, result = execute(repo, adapter)
    assert calls == 2 and result["status"] == "failed_validation"
    assert stored_evaluation_count(repo) == 0


def test_nonretryable_refusal_stops_after_one_call(repo):
    calls = 0

    async def adapter(request):
        nonlocal calls
        calls += 1
        raise AdapterFailure("MODEL_REFUSAL", retryable=False)

    _, result = execute(repo, adapter)
    assert calls == 1 and result["status"] == "failed"
    assert result["error"]["code"] == "MODEL_REFUSAL"
    assert wire(repo, result)["score"] is None


def test_timeout_is_bounded_retried_once_and_never_becomes_zero_score(repo):
    calls = 0

    async def adapter(request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(10)

    _, result = execute(repo, adapter, timeout=0.005)
    assert calls == 2 and result["status"] == "failed"
    assert result["error"]["code"] == "PROVIDER_TIMEOUT" and result["error"]["retryable"] is True
    assert wire(repo, result)["score"] is None
    assert stored_evaluation_count(repo) == 0


def test_unexpected_adapter_error_never_leaks_provider_secrets(repo):
    async def adapter(request):
        raise RuntimeError("https://provider.invalid?api_key=SECRET-TOKEN private response")

    _, result = execute(repo, adapter)
    assert result["status"] == "failed" and result["error"]["code"] == "EVALUATOR_ERROR"
    assert "SECRET-TOKEN" not in json.dumps(result)
    assert stored_evaluation_count(repo) == 0


def test_adapter_mutations_cannot_change_authoritative_evidence_snapshot(repo):
    async def adapter(request):
        response = output(request)
        request.messages[1].text = "Invented mutable adapter text"
        response["results"][0]["evidence"][0]["quote"] = request.messages[1].text
        request.checklist.criteria[0].weight = "999"
        return response

    _, result = execute(repo, adapter)
    assert result["status"] == "needs_review"
    assert result["checklist"]["criteria"][0]["weight"] == 10
    assert result["results"][0]["evidence"][0]["reason"] == "QUOTE_MISMATCH"
    assert repo.state()["data"]["conversations"][0]["transcript"][1]["text"].startswith("Здравствуйте!")


def test_checklist_update_during_call_preserves_saved_original_snapshot(repo):
    async def adapter(request):
        changed = initial_checklist()
        changed["criteria"][0]["weight"] = 30
        repo.save_checklist(changed, 1)
        return output(request)

    _, result = execute(repo, adapter)
    assert result["status"] == "completed"
    assert result["checklist"]["version"] == 1
    assert result["checklist"]["criteria"][0]["weight"] == 10
    assert repo.state()["data"]["checklist"]["version"] == 2
    assert wire(repo, result)["score"]["total"] == "85.38"


def test_transcript_change_during_call_rejects_stale_results(repo):
    async def adapter(request):
        response = output(request)
        conversation = deepcopy(repo.state()["data"]["conversations"][0])
        conversation["transcript"][0]["text"] = "Changed while awaiting model"
        with repo.connect(write=True) as db:
            repo.put_conversations(db, [conversation], "manual")
        return response

    _, result = execute(repo, adapter)
    assert result["status"] == "failed_validation" and result["error"]["code"] == "INPUT_CHANGED"
    assert stored_evaluation_count(repo) == 0


@pytest.mark.parametrize("status", ["queued", "running"])
def test_restart_recovery_marks_unfinished_jobs_retryable(repo, status):
    service = EvaluationService(repo)
    job = service.create("worker-test")
    if status == "running":
        service.update(job["id"], "running")
    recovered = EvaluationService(repo)
    recovered.recover()
    result = recovered.get(job["id"])
    assert result["status"] == "retryable"
    assert result["error"]["code"] == "WORKER_INTERRUPTED" and result["error"]["retryable"] is True
    assert wire(repo, result)["score"] is None
    assert recovered.create("worker-test")["status"] == "queued"


def test_duplicate_pending_job_rejected_and_completed_recovery_untouched(repo):
    service = EvaluationService(repo)
    service.create("worker-test")
    with pytest.raises(Conflict):
        service.create("worker-test")
    service.recover()

    async def adapter(request):
        return output(request)

    service, result = execute(repo, adapter)
    before = deepcopy(result)
    service.recover()
    assert service.get(result["id"]) == before


def test_long_provider_backoff_is_not_shortened_or_retried_immediately(repo):
    calls = 0

    async def adapter(request):
        nonlocal calls
        calls += 1
        raise AdapterFailure("PROVIDER_RATE_LIMIT", retryable=True, retry_after=60)

    _, result = execute(repo, adapter)
    assert calls == 1 and result["status"] == "failed"
    assert result["error"]["code"] == "PROVIDER_RATE_LIMIT" and result["error"]["retryable"] is True


def test_provider_failure_is_counted_in_persisted_shared_attempt_budget(repo):
    calls = 0

    async def adapter(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise AdapterFailure("PROVIDER_UNAVAILABLE", retryable=True)
        return output(request)

    _, result = execute(repo, adapter)
    assert result["status"] == "completed" and calls == 2
    with repo.connect() as db:
        attempts = db.execute("SELECT attempt,output,issues FROM evaluation_attempts WHERE job_id=? ORDER BY attempt", (result["id"],)).fetchall()
    assert [row["attempt"] for row in attempts] == [1, 2]
    assert attempts[0]["output"] is None
    assert json.loads(attempts[0]["issues"])[0]["code"] == "PROVIDER_UNAVAILABLE"
    assert json.loads(attempts[1]["output"])["schema_version"] == "2.0"


def test_different_dialogues_share_one_provider_slot(repo):
    second = deepcopy(repo.state()["data"]["conversations"][0])
    second["id"] = "worker-test-second"
    repo.import_data([second])
    active = maximum = 0

    async def adapter(request):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        active -= 1
        return output(request)

    async def run_both():
        service = EvaluationService(repo, adapter)
        jobs = [service.create(identifier) for identifier in ("worker-test", "worker-test-second")]
        await asyncio.gather(*(service.run(job["id"]) for job in jobs))
        assert all(service.get(job["id"])["status"] == "completed" for job in jobs)

    asyncio.run(run_both())
    assert maximum == 1
