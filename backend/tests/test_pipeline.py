"""Synthetic adapters exercise the application boundary, never production inference."""
import asyncio
import copy
import itertools
import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, strategies as st

from backend.app import create_app
from backend.application.evaluations import EvaluationService, InvalidOutput, score
from backend.application.ports import ProviderFailure
from backend.domain.scoring import CriterionDefinition, ScoringEngine, ScaleStrategy
from backend.infrastructure.evaluations import SQLiteEvaluations, checklist_from_ui
from backend.domain import initial_checklist
from backend.repository import Conflict, Repository
from contracts.v1 import AdapterResult, Checklist, ErrorResponse, EvaluationJob, ModelOutput, Provenance


VALUES = [1, None, None, 15, 1, 7, 1, 0]


def record(identifier="synthetic-kz"):
    return {"conversation_id": identifier, "language": "KZ", "channel": "agent chat", "audio_url": None,
            "transcript": [{"timestamp": "00:00", "speaker": "system", "text": "Идентификация подтверждена"},
                           {"timestamp": "00:01", "speaker": "bot", "text": "Сәлеметсіз бе!"},
                           {"timestamp": "00:02", "speaker": "client", "text": "Көмектесіңізші"},
                           {"timestamp": "00:03", "speaker": "operator", "text": "Сәлеметсіз бе! Мен Дана."}]}


def output_for(request, values=None):
    values = VALUES if values is None else values
    return {"schema_version": "1.0", "results": [
        {"criterion_id": criterion.id, "value": value, "explanation": "Синтетическое экспертное решение.",
         "na_reason": "Условие неприменимости выполнено в тестовом примере." if value is None else None,
         "evidence": {"kind": "not_applicable" if value is None else "message",
                      "message_ids": [request.conversation.transcript[0].id], "start_message_id": None,
                      "end_message_id": None, "quote": None}}
        for criterion, value in zip(request.checklist.criteria, values)]}


class SyntheticAdapter:
    def __init__(self, mutate=None):
        self.mutate = mutate
        self.requests = []

    async def evaluate(self, request):
        self.requests.append(request.model_copy(deep=True))
        output = output_for(request)
        if self.mutate:
            self.mutate(output)
        return AdapterResult(output=ModelOutput.model_validate(output), provenance=Provenance(
            provider="test-only", model="synthetic", prompt_version=request.prompt_version,
            knowledge_version=request.knowledge_version, input_tokens=0, output_tokens=0, cost_usd=None))


@pytest.fixture
def repository(tmp_path):
    source = Repository(tmp_path / "test.sqlite")
    source.import_data(json.dumps([record()]))
    return source, SQLiteEvaluations(source)


def input_for(store):
    return store.request("synthetic-kz", "cqa-official", 1, "", "v1", "none")


def test_control_example_and_sections(repository):
    _, store = repository
    request = input_for(store)
    result = score(request, ModelOutput.model_validate(output_for(request)), ScoringEngine())
    assert result.total_score == "85.38"
    assert result.provisional_total_score is None
    assert result.critical_error is False
    assert {r.criterion_id: r.evidence.quote for r in result.results}["greeting"] == "Идентификация подтверждена"
    assert all(s.score is None or Decimal(0) <= Decimal(s.score) <= Decimal(100) for s in result.sections)
    assert all(isinstance(r.weight, str) for r in result.results)


@pytest.mark.parametrize("kind,code", [
    ("na", "NA_FORBIDDEN"), ("step", "VALUE_NOT_ALLOWED"), ("missing", "CRITERION_COVERAGE"),
    ("extra", "UNKNOWN_CRITERION"), ("duplicate", "CRITERION_COVERAGE"), ("na_reason", "NA_REASON_MISSING"),
])
def test_structural_errors_block_scores(repository, kind, code):
    request = input_for(repository[1])
    output = output_for(request)
    if kind == "na": output["results"][0]["value"] = None
    if kind == "step": output["results"][3]["value"] = 12
    if kind == "missing": output["results"].pop()
    if kind == "extra": output["results"].append({**output["results"][0], "criterion_id": "not-in-rubric"})
    if kind == "duplicate": output["results"].append(output["results"][0])
    if kind == "na_reason": output["results"][1]["na_reason"] = None
    with pytest.raises(InvalidOutput) as error:
        score(request, ModelOutput.model_validate(output), ScoringEngine())
    assert code in {issue.code for issue in error.value.issues}


@pytest.mark.parametrize("kind", ["unknown", "quote", "missing", "range", "empty_quote"])
def test_unverified_evidence_never_becomes_confirmed_total(repository, kind):
    request = input_for(repository[1]); output = output_for(request)
    evidence = output["results"][-1]["evidence"]
    if kind == "unknown": evidence["message_ids"] = ["synthetic-kz-m999"]
    if kind == "quote": evidence["quote"] = "Выдуманное доказательство"
    if kind == "empty_quote": evidence["quote"] = " "
    if kind == "missing": evidence["message_ids"] = []
    if kind == "range": evidence.update(kind="absence", message_ids=[], start_message_id="synthetic-kz-m4", end_message_id="synthetic-kz-m1")
    result = score(request, ModelOutput.model_validate(output), ScoringEngine())
    assert result.status == "needs_review" and result.total_score is None
    assert result.provisional_total_score == "85.38" and result.critical_error is None
    assert result.results[-1].evidence_status == "unverified"
    assert result.sections[-1].score is None or any(s.score is None for s in result.sections)


def test_kazakh_quote_exactness_and_bot_system_context(repository):
    request = input_for(repository[1]); output = output_for(request)
    output["results"][0]["evidence"].update(message_ids=["synthetic-kz-m2"], quote="Сәлеметсіз бе!")
    assert score(request, ModelOutput.model_validate(output), ScoringEngine()).status == "completed"
    output["results"][0]["evidence"]["quote"] = "Салеметсиз бе!"
    assert score(request, ModelOutput.model_validate(output), ScoringEngine()).status == "needs_review"


def test_absence_range_inclusive(repository):
    request = input_for(repository[1]); output = output_for(request)
    output["results"][0]["evidence"].update(kind="absence", message_ids=[], start_message_id="synthetic-kz-m1", end_message_id="synthetic-kz-m4")
    assert score(request, ModelOutput.model_validate(output), ScoringEngine()).status == "completed"


def definition(identifier="x", *, kind="scale", weight="1", maximum=20000, steps=(0, 537, 20000), allow_na=True, section="one"):
    return CriterionDefinition(identifier, kind, Decimal(weight), maximum, steps, allow_na, section)


def test_half_up_and_null_denominator():
    engine = ScoringEngine(); c = definition()
    assert engine.calculate((c,), {"x": 537}).total == Decimal("2.69")
    assert engine.calculate((c,), {"x": None}).total is None
    zero = definition(weight="0")
    assert engine.calculate((zero,), {"x": 20000}).total is None


def test_zero_weight_flags_and_nonhundred_weights():
    engine = ScoringEngine()
    criteria = (definition("a", weight="7", maximum=5, steps=(0, 5)),
                definition("b", kind="flag", weight="0", maximum=1, steps=(0, 1)))
    assert engine.calculate(criteria, {"a": 5, "b": 1}).total == Decimal("100.00")
    assert engine.calculate(criteria, {"a": 0, "b": 1}).total == Decimal("0.00")


def test_strategy_registration_is_instance_local():
    engine = ScoringEngine(); engine.register("custom_scale", ScaleStrategy())
    custom = definition(kind="custom_scale")
    assert engine.calculate((custom,), {"x": 20000}).total == Decimal("100.00")
    with pytest.raises(KeyError): ScoringEngine().calculate((custom,), {"x": 20000})


@given(st.lists(st.tuples(st.integers(0, 1_000_000), st.sampled_from([0, 4, 7, 10, None])), min_size=1, max_size=25))
def test_range_determinism_permutation_and_na_properties(rows):
    criteria = tuple(definition(str(i), weight=str(weight), maximum=10, steps=(0, 4, 7, 10)) for i, (weight, _) in enumerate(rows))
    values = {str(i): value for i, (_, value) in enumerate(rows)}
    engine = ScoringEngine(); result = engine.calculate(criteria, values)
    assert result.total is None or Decimal(0) <= result.total <= Decimal(100)
    assert result == engine.calculate(criteria, values)
    assert result == engine.calculate(tuple(reversed(criteria)), values)
    removed = {key: None if int(key) % 2 else value for key, value in values.items()}
    partial = engine.calculate(criteria, removed)
    assert partial.total is None or Decimal(0) <= partial.total <= Decimal(100)
    maximum = engine.calculate(criteria, {c.id: c.maximum for c in criteria})
    assert maximum.total == (Decimal("100.00") if any(c.weight for c in criteria) else None)


def test_version_fingerprints_and_old_input_are_immutable(repository):
    source, store = repository; original = input_for(store)
    new = original.checklist.model_dump()
    new["version"] = 2
    new["criteria"][3].update(max=5, allowed_values=[0, 5])
    store.save_checklist(Checklist.model_validate(new), "2026-11-07T00:00:00Z")
    assert input_for(store).checklist == original.checklist
    changed = store.request("synthetic-kz", "cqa-official", 2, "", "v1", "none")
    assert changed.checklist.criteria[3].allowed_values == [0, 5]
    with source.connect() as db:
        rows = db.execute("SELECT fingerprints FROM checklist_snapshots ORDER BY version").fetchall()
    before, after = [json.loads(row[0]) for row in rows]
    assert [key for key in before if before[key] != after[key]] == ["consultation"]
    new["name"] = "overwrite"
    with pytest.raises(Conflict): store.save_checklist(Checklist.model_validate(new), "later")


def wait_for(client, job_id):
    import time
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/evaluations/{job_id}")
        assert response.status_code == 200
        job = EvaluationJob.model_validate(response.json())
        if job.status not in ("queued", "running"):
            return job
        time.sleep(0.01)
    raise AssertionError("job did not complete")


def test_full_http_pipeline_restart_and_idempotence(tmp_path):
    adapter = SyntheticAdapter(); path = tmp_path / "service.sqlite"
    app = create_app(path, tmp_path / "no-cache", organizer_url="", evaluator=adapter)
    with TestClient(app) as client:
        assert client.post("/api/import", json={"text": json.dumps([record()])}).status_code == 200
        request = {"conversation_id": "synthetic-kz", "checklist_id": "cqa-official", "checklist_version": 1}
        accepted = client.post("/api/evaluations", json=request)
        assert accepted.status_code == 202 and accepted.json()["status"] == "queued"
        job = wait_for(client, accepted.json()["id"])
        assert job.status == "completed" and job.evaluation.final.total_score == "85.38"
        repeated = wait_for(client, client.post("/api/evaluations", json=request).json()["id"])
        assert repeated.evaluation.id == job.evaluation.id
        state = client.get("/api/state").json()
        assert state["data"]["conversations"][0]["wire_evaluation"]["final"]["total_score"] == "85.38"
        with app.state.repo.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM evaluation_snapshots").fetchone()[0] == 1
            assert db.execute("SELECT engine_version FROM evaluation_snapshots").fetchone()[0] == "decimal-v1.0"
        assert [m.id for m in adapter.requests[0].conversation.transcript] == [f"synthetic-kz-m{i}" for i in range(1, 5)]
        assert client.get(f"/api/evaluations/{job.id}/export").json()["evaluation"]["original"]["total_score"] == "85.38"
    with TestClient(create_app(path, tmp_path / "no-cache", organizer_url="")) as client:
        assert client.get(f"/api/evaluations/{job.id}").json()["evaluation"]["original"]["total_score"] == "85.38"


@pytest.mark.parametrize("kind,expected", [("step", "failed"), ("evidence", "needs_review"), ("type", "failed")])
def test_pipeline_classifies_bad_model_results(tmp_path, kind, expected):
    def mutate(output):
        if kind == "step": output["results"][3]["value"] = 12
        elif kind == "type": output["results"][0]["value"] = True
        else: output["results"][0]["evidence"]["message_ids"] = ["missing"]
    app = create_app(tmp_path / "db.sqlite", tmp_path / "no-cache", organizer_url="", evaluator=SyntheticAdapter(mutate))
    with TestClient(app) as client:
        client.post("/api/import", json={"text": json.dumps([record()])})
        response = client.post("/api/evaluations", json={"conversation_id": "synthetic-kz", "checklist_id": "cqa-official", "checklist_version": 1})
        job = wait_for(client, response.json()["id"])
        assert job.status == expected
        if expected == "failed":
            assert job.evaluation is None and job.error.error.code == "MODEL_OUTPUT_INVALID"
        else:
            assert job.evaluation.final.total_score is None and job.evaluation.final.provisional_total_score == "85.38"


def test_provider_failure_is_sanitized_and_not_retried(tmp_path):
    class Failing:
        calls = 0
        async def evaluate(self, request):
            self.calls += 1
            raise RuntimeError("secret-value / private transcript")
    adapter = Failing()
    app = create_app(tmp_path / "db.sqlite", tmp_path / "no-cache", organizer_url="", evaluator=adapter)
    with TestClient(app) as client:
        client.post("/api/import", json={"text": json.dumps([record()])})
        response = client.post("/api/evaluations", json={"conversation_id": "synthetic-kz", "checklist_id": "cqa-official", "checklist_version": 1})
        job = wait_for(client, response.json()["id"])
        assert job.status == "failed" and job.evaluation is None and adapter.calls == 1
        assert "secret-value" not in job.model_dump_json()


def test_api_validation_error_contract_and_missing_evaluator(tmp_path):
    app = create_app(tmp_path / "db.sqlite", tmp_path / "none", organizer_url="")
    with TestClient(app) as client:
        response = client.post("/api/evaluations", json={"conversationId": "x", "secret": "not-echoed"})
        assert response.status_code == 422
        ErrorResponse.model_validate(response.json())
        assert "not-echoed" not in response.text
        response = client.post("/api/evaluations", json={"conversation_id": "x", "checklist_id": "cqa-official", "checklist_version": 1})
        assert response.status_code == 503
        assert ErrorResponse.model_validate(response.json()).error.code == "PROVIDER_UNAVAILABLE"


def test_interrupted_job_recovery(repository):
    source, store = repository; request = input_for(store)
    store.create_job(EvaluationJob(schema_version="1.0", id="interrupted", status="running", evaluation=None, error=None), request, "2026-11-07T00:00:00Z")
    service = EvaluationService(store, None, lambda: "2026-11-07T00:00:00Z", lambda: "test-id")
    service.recover()
    job = store.get_job("interrupted")
    assert job.status == "failed" and job.error.error.retryable and job.evaluation is None


def test_changed_input_keeps_original_job_and_transcript(tmp_path):
    app = create_app(tmp_path / "db.sqlite", tmp_path / "none", organizer_url="", evaluator=SyntheticAdapter())
    with TestClient(app) as client:
        client.post("/api/import", json={"text": json.dumps([record()])})
        accepted = client.post("/api/evaluations", json={"conversation_id": "synthetic-kz", "checklist_id": "cqa-official", "checklist_version": 1})
        job = wait_for(client, accepted.json()["id"])
        source = app.state.repo
        from backend.domain import normalize_import
        changed = record(); changed["transcript"][0]["text"] = "Изменённое системное сообщение"
        with source.connect(write=True) as db:
            source.put_conversations(db, normalize_import([changed]), "manual")
        assert "wire_evaluation" not in client.get("/api/state").json()["data"]["conversations"][0]
        assert client.get(f"/api/evaluations/{job.id}").json()["evaluation"] == job.evaluation.model_dump()
        with source.connect() as db:
            archived = json.loads(db.execute("SELECT body FROM transcript_snapshots WHERE content_hash=?", (job.evaluation.conversation_hash,)).fetchone()[0])
        assert archived["transcript"][0]["text"] == record()["transcript"][0]["text"]


def test_timeout_and_provider_rate_limit_preserve_error_codes(tmp_path):
    class RateLimited:
        async def evaluate(self, request):
            raise ProviderFailure("PROVIDER_RATE_LIMIT", 3)
    app = create_app(tmp_path / "db.sqlite", tmp_path / "none", organizer_url="", evaluator=RateLimited())
    with TestClient(app) as client:
        client.post("/api/import", json={"text": json.dumps([record()])})
        job = wait_for(client, client.post("/api/evaluations", json={"conversation_id": "synthetic-kz", "checklist_id": "cqa-official", "checklist_version": 1}).json()["id"])
        assert job.error.error.code == "PROVIDER_RATE_LIMIT" and job.error.error.retry_after_seconds == 3


def test_empty_applied_set_has_null_total_in_contract(repository):
    request = input_for(repository[1]); body = request.model_dump()
    body["checklist"]["criteria"] = [body["checklist"]["criteria"][1]]
    from contracts.v1 import EvaluatorInput
    request = EvaluatorInput.model_validate(body)
    snapshot = score(request, ModelOutput.model_validate(output_for(request, [None])), ScoringEngine())
    assert snapshot.total_score is None and snapshot.score_unavailable_reason == "no_applicable_weight"


def test_api_saves_new_scale_and_delivers_it_to_adapter(tmp_path):
    class MaximumAdapter(SyntheticAdapter):
        async def evaluate(self, request):
            self.requests.append(request.model_copy(deep=True))
            return AdapterResult(output=ModelOutput.model_validate(output_for(request, [c.max for c in request.checklist.criteria])),
                provenance=Provenance(provider="test-only",model="maximum",prompt_version="v1",knowledge_version="none",input_tokens=0,output_tokens=0,cost_usd=None))
    adapter = MaximumAdapter()
    app = create_app(tmp_path / "db.sqlite", tmp_path / "none", organizer_url="", evaluator=adapter)
    with TestClient(app) as client:
        client.post("/api/import", json={"text": json.dumps([record()])})
        checklist = client.get("/api/checklists").json()[0]
        checklist["version"] = 2
        checklist["criteria"][3].update(max=5, allowed_values=[0,5], weight="17.123456")
        response = client.post("/api/checklists/cqa-official/versions", json=checklist)
        assert response.status_code == 200
        job = wait_for(client, client.post("/api/evaluations", json={"conversation_id":"synthetic-kz","checklist_id":"cqa-official","checklist_version":2}).json()["id"])
        assert job.evaluation.final.total_score == "100.00"
        assert adapter.requests[0].checklist.criteria[3].allowed_values == [0,5]
        assert job.evaluation.checklist.criteria[3].weight == "17.123456"
        assert client.get("/api/state").json()["data"]["checklist"]["version"] == 2
