"""Saved checklist selection and immutable expert-fact snapshots; no provider calls."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.app import create_app
from backend.contracts.v2 import ApiError, EvaluationRequest, EvaluationResult
from backend.evaluator import EvaluationService
from backend.knowledge import load_knowledge_facts
from backend.tests.test_api import record
from backend.tests.test_evaluation_api import model_output

FACTS_FIXTURE = Path(__file__).parent / "fixtures/knowledge-facts.synthetic.json"


def wire(**fields):
    return {"schema_version": "2.0", **fields}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("EVALUATOR_ADAPTER", "")
    monkeypatch.delenv("KNOWLEDGE_FACTS_PATH", raising=False)
    calls = []

    async def adapter(request):
        calls.append(request.model_copy(deep=True))
        response = model_output(request)
        if request.checklist.version > 1:
            next(r for r in response["results"] if r["criterionId"] == "consultation")["value"] = "2"
        return response

    paths = tmp_path / "context.sqlite", tmp_path / "missing.json"
    app = create_app(*paths, organizer_url="", evaluator=adapter)
    conversation = record("context-test")
    conversation["transcript"][1]["text"] = "Здравствуйте! Синтетический ответ оператора."
    app.state.repo.import_data([conversation])
    with TestClient(app) as client:
        yield client, app, calls, paths, adapter


def second_version(client):
    checklist = client.get("/api/checklists").json()[0]
    criterion = next(c for c in checklist["criteria"] if c["id"] == "consultation")
    criterion.update(max="5", allowedValues=["0", "2", "5"], description="Новое синтетическое правило и шкала.")
    saved = client.post("/api/checklists/" + checklist["id"] + "/versions", json=wire(checklist=checklist, expectedVersion=1))
    assert saved.status_code == 200


def evaluate(client, **fields):
    response = client.post("/api/evaluations", json=wire(conversationId="context-test", **fields))
    assert response.status_code == 202, response.text
    result = client.get("/api/evaluations/" + response.json()["id"])
    assert result.status_code == 200
    saved = EvaluationResult.model_validate(result.json())
    assert saved.status == "completed", result.text
    return saved


def test_explicit_old_and_new_versions_and_default_reach_adapter_without_altering_history(setup):
    client, app, calls, paths, _ = setup
    original = evaluate(client)
    second_version(client)
    older = evaluate(client, checklistVersion=1)
    newer = evaluate(client, checklistVersion=2)
    latest = evaluate(client)
    explicit_null = evaluate(client, checklistVersion=None)
    assert [r.checklist.version for r in calls] == [1, 1, 2, 2, 2]
    assert older.score.total == original.score.total == "85.38"
    assert newer.score.total == latest.score.total == explicit_null.score.total == "74.62"
    old_scale = next(c for c in calls[1].checklist.criteria if c.id == "consultation")
    new_scale = next(c for c in calls[2].checklist.criteria if c.id == "consultation")
    assert old_scale.allowedValues == ["0", "5", "10", "15", "20"]
    assert new_scale.allowedValues == ["0", "2", "5"]
    assert client.get("/api/evaluations/" + original.id).json() == original.model_dump()
    client.exit_stack.close()  # A restart releases the first backend's single-worker lock.
    with TestClient(create_app(*paths, organizer_url="")) as restarted:
        assert EvaluationResult.model_validate(restarted.get("/api/evaluations/" + older.id).json()) == older
        assert EvaluationResult.model_validate(restarted.get("/api/evaluations/" + newer.id).json()) == newer
    assert len(calls) == 5


@pytest.mark.parametrize("version,status,code", [
    (99, 404, "NOT_FOUND"), (True, 422, "INVALID_REQUEST"), (0, 422, "INVALID_REQUEST"),
    (-1, 422, "INVALID_REQUEST"), (1.0, 422, "INVALID_REQUEST"), ("1", 422, "INVALID_REQUEST"),
])
def test_invalid_or_unknown_version_never_enqueues_or_invokes_model(setup, version, status, code):
    client, app, calls, _, _ = setup
    before = app.state.repo.state()
    response = client.post("/api/evaluations", json=wire(conversationId="context-test", checklistVersion=version))
    assert response.status_code == status
    assert ApiError.model_validate(response.json()).code == code
    assert app.state.repo.state() == before and not calls
    with app.state.repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM evaluation_jobs").fetchone()[0] == 0


def test_version_selection_does_not_bypass_existing_active_job(setup):
    client, app, calls, _, _ = setup
    second_version(client)
    pending = app.state.evaluator.create("context-test", 1)
    response = client.post("/api/evaluations", json=wire(conversationId="context-test", checklistVersion=2))
    assert response.status_code == 409 and not calls
    assert app.state.evaluator.get(pending["id"])["checklist"]["version"] == 1


@pytest.mark.parametrize("decision", [None, "accepted", "rejected"])
def test_version_selection_cannot_bypass_appeal_guard(setup, decision):
    client, app, calls, _, _ = setup
    original = evaluate(client)
    created = client.post("/api/appeals", json=wire(
        conversationId="context-test", evaluationId=original.id, criterionId="gross-errors",
        comment="Синтетическая проверка защиты истории апелляции.",
    ))
    assert created.status_code == 200
    appeal = created.json()["data"]["appeals"][0]
    if decision:
        result = client.post("/api/appeals/" + appeal["id"] + "/decision", json=wire(
            status=decision, newValue="1", decision="Синтетическое решение для проверки истории.",
        ))
        assert result.status_code == 200
    second_version(client)
    before = app.state.repo.state()
    response = client.post("/api/evaluations", json=wire(conversationId="context-test", checklistVersion=2))
    assert response.status_code == 409 and len(calls) == 1
    assert app.state.repo.state() == before
    assert EvaluationResult.model_validate(client.get("/api/evaluations/" + original.id).json()) == original


def test_unscoped_facts_are_excluded_before_snapshot_across_restart(setup, tmp_path, monkeypatch):
    initial_client, _, calls, paths, adapter = setup
    initial_client.exit_stack.close()
    facts_path = tmp_path / "approved-synthetic.json"
    original_facts = json.loads(FACTS_FIXTURE.read_text())
    facts_path.write_text(json.dumps(original_facts, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_FACTS_PATH", str(facts_path))
    app = create_app(*paths, organizer_url="", evaluator=adapter)
    with TestClient(app) as client:
        first = evaluate(client)
        assert calls[-1].knowledgeFacts == []
        assert calls[-1].knowledge_context.status == "excluded"
        assert "legacy_scope_unverified" in calls[-1].knowledge_context.reason
        changed_facts = deepcopy(original_facts)
        changed_facts[0]["text"] = "Обновлённый синтетический факт. Не правило Freedom."
        facts_path.write_text(json.dumps(changed_facts), encoding="utf-8")
        second = evaluate(client)
        assert calls[-1].knowledgeFacts == []
    with TestClient(create_app(*paths, organizer_url="", evaluator=adapter)) as restarted:
        third = evaluate(restarted)
        assert calls[-1].knowledgeFacts == []
        assert EvaluationResult.model_validate(restarted.get("/api/evaluations/" + first.id).json()) == first
        assert EvaluationResult.model_validate(restarted.get("/api/evaluations/" + second.id).json()) == second
        with restarted.app.state.repo.connect() as db:
            for result, expected in ((first, original_facts), (second, original_facts), (third, changed_facts)):
                request = EvaluationRequest.model_validate_json(db.execute(
                    "SELECT request FROM evaluation_jobs WHERE id=?", (result.id,),
                ).fetchone()[0])
                assert request.knowledgeFacts == []
                assert request.knowledge_context.excluded_fact_count == len(expected)
    assert len(calls) == 3


def test_pending_job_and_repair_use_safe_empty_knowledge_snapshot(setup, tmp_path):
    _, app, _, _, _ = setup
    path = tmp_path / "facts.json"
    path.write_bytes(FACTS_FIXTURE.read_bytes())
    facts = load_knowledge_facts(path)
    original = facts[0].model_dump()
    calls = []

    async def adapter(request):
        calls.append([fact.model_dump() for fact in request.knowledgeFacts])
        request.knowledgeFacts.append(facts[0].model_copy(deep=True))
        return "invalid JSON" if len(calls) == 1 else model_output(request)

    service = EvaluationService(app.state.repo, adapter, knowledge_facts=facts)
    job = service.create("context-test", 1)
    facts[0].text = "Caller changed its model object."
    path.write_text("[]")
    asyncio.run(service.run(job["id"]))
    assert service.get(job["id"])["status"] == "completed"
    assert calls == [[], []]
    with app.state.repo.connect() as db:
        snapshot = json.loads(db.execute("SELECT request FROM evaluation_jobs WHERE id=?", (job["id"],)).fetchone()[0])
        assert snapshot["knowledgeFacts"] == []
        assert snapshot["knowledge_context"]["status"] == "excluded"
