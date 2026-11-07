"""Integrated safety path using temporary databases and HTTP mocks only."""
import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from backend.contracts.v2 import EvaluationRequest, ModelResponse, to_wire_evaluation, to_wire_state
from backend.contracts.v2.models import ModelCriterionResult, Score
from backend.evaluator import EvaluationService, inspect_output
from backend.knowledge import KnowledgeContext, ScopedKnowledgeFact, load_knowledge_facts
from backend.repository import Repository
from backend.submission import export_record
from backend.contracts.v2.codecs import to_wire_appeal
from evaluator.openai_v2 import OpenAIV2Evaluator
from evaluator.settings import OpenAISettings


@pytest.fixture
def repo(tmp_path):
    repository = Repository(tmp_path / "review.sqlite")
    repository.import_data([{"conversation_id": "knowledge-case", "channel": "agent chat", "language": "RU", "audio_url": None,
        "transcript": [{"speaker": "client", "timestamp": "00:00", "text": "Синтетический вопрос о продукте."},
                       {"speaker": "operator", "timestamp": "00:10", "text": "Здравствуйте! Я тестовый оператор. Синтетический ответ."}]}])
    return repository


def model_output(request, *, unassessable=False):
    response = {"schema_version": "2.0", "results": []}
    for criterion in request.checklist.criteria:
        missing = unassessable and criterion.id == "consultation"
        response["results"].append({"criterionId": criterion.id,
            "value": None if missing else "0" if criterion.type == "flag" else criterion.allowedValues[-1],
            "explanation": "Synthetic test decision, not a real assessment.", "applicability_reason": None,
            "assessment_status": "unassessable" if missing else "assessed",
            "review_reason": "Не подтверждены условия продукта для даты и юрлица разговора." if missing else None,
            "evidence": [{"kind": "message", "message_id": request.messages[-1].id}]})
    return response


def scoped():
    return ScopedKnowledgeFact(id="synthetic-rule", text="Synthetic applicable fact", source="https://example.invalid/policy",
        source_locator="Synthetic section", source_published_at="2026-01-01", observed_at="2026-11-08",
        entity="Synthetic PLC", jurisdiction="KZ-AIFC", product="Synthetic product", valid_from="2026-01-01", valid_to="2026-03-01",
        validity_evidence="Synthetic test interval", review_status="expert_approved", verified_by="Synthetic expert",
        approval_reference="Synthetic receipt", limits="Test only, not Freedom")


def scope():
    return KnowledgeContext(entity="Synthetic PLC", jurisdiction="KZ-AIFC", product="Synthetic product",
        conversation_date="2026-02-01", date_source="conversation", verified_by="Synthetic owner", verification_reference="Synthetic source")


def run_http(repo, *, unassessable=False, **service_args):
    sent = []
    def handle(http_request):
        payload = json.loads(http_request.content)
        request = EvaluationRequest.model_validate_json(payload["input"][0]["content"])
        sent.append(request)
        return httpx.Response(200, json={"status": "completed", "model": "gpt-6-luna", "usage": {"input_tokens": 1, "output_tokens": 1},
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps(model_output(request, unassessable=unassessable))}]}]})
    adapter = OpenAIV2Evaluator(OpenAISettings("synthetic-key", "gpt-6-luna", min_interval_seconds=0),
                                 transport=httpx.MockTransport(handle))
    service = EvaluationService(repo, adapter.evaluate, **service_args)
    job = service.create("knowledge-case")
    with repo.connect() as db:
        stored = EvaluationRequest.model_validate_json(db.execute("SELECT request FROM evaluation_jobs WHERE id=?", (job["id"],)).fetchone()[0])
    asyncio.run(service.run(job["id"]))
    assert len(sent) == 1
    assert sent[0].model_dump() == stored.model_dump()  # the persisted request is what the provider receives
    return service.get(job["id"]), stored


def test_unknown_factual_criterion_needs_review_no_score_no_export(repo):
    future = scoped()
    from backend.contracts.v2 import KnowledgeFact
    legacy = KnowledgeFact(text="Future synthetic tariff", source=future.source, effective_date="2099-01-01",
                           product=future.product, jurisdiction=future.jurisdiction, verified_by="file-only")
    result, request = run_http(repo, unassessable=True, knowledge_facts=(legacy,))
    assert request.knowledgeFacts == []
    assert request.knowledge_context.status == "excluded"
    assert request.knowledge_context.excluded_fact_count == 1
    assert "legacy_scope_unverified" in request.knowledge_context.reason
    assert result["status"] == "needs_review"
    wire = to_wire_evaluation(result)
    assert wire["score"] is None
    row = next(r for r in wire["results"] if r["criterionId"] == "consultation")
    assert row["assessment_status"] == "unassessable" and row["value"] is None
    assert row["applicability_reason"] is None and row["review_reason"]
    assert row["evidence"][0]["verification_status"] == "verified"
    assert next(r for r in wire["results"] if r["criterionId"] == "greeting")["assessment_status"] == "assessed"
    state = to_wire_state(repo.state())
    assert state["data"]["conversations"][0]["evaluation"]["status"] == "needs_review"
    assert export_record(state["data"]["conversations"][0])["status"] == "error"
    with repo.connect() as db:
        assert db.execute("SELECT count(*) FROM evaluations").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM evaluation_attempts").fetchone()[0] == 1


def test_no_knowledge_does_not_block_observable_nonfactual_behavior(repo):
    result, request = run_http(repo)
    assert request.knowledgeFacts == [] and request.knowledge_context.status == "not_configured"
    assert result["status"] == "completed"
    conversation = to_wire_state(repo.state())["data"]["conversations"][0]
    assert conversation["evaluation"]["score"] is not None
    assert export_record(conversation)["status"] == "ok"


def test_verified_applicable_basis_survives_snapshot_and_provider_boundary(repo):
    with repo.connect() as db:
        content_hash = db.execute("SELECT content_hash FROM conversations WHERE id='knowledge-case'").fetchone()[0]
    result, request = run_http(repo, scoped_knowledge_facts=(scoped(),), knowledge_contexts={("knowledge-case", content_hash): scope()})
    assert result["status"] == "completed"
    assert len(request.knowledgeFacts) == 1
    assert request.knowledge_context.status == "scoped_verified"
    assert request.knowledge_context.included_fact_ids == ["synthetic-rule"]


def test_scope_receipt_for_old_conversation_revision_cannot_be_reused(repo):
    result, request = run_http(repo, scoped_knowledge_facts=(scoped(),), knowledge_contexts={("knowledge-case", "old-hash"): scope()})
    assert request.knowledgeFacts == []
    assert request.knowledge_context.status == "excluded"


def test_historical_queued_legacy_facts_are_closed_before_provider(repo):
    from backend.contracts.v2 import KnowledgeFact
    seen = []
    async def adapter(request):
        seen.append(request.model_dump())
        return model_output(request)
    service = EvaluationService(repo, adapter)
    job = service.create("knowledge-case")
    with repo.connect(write=True) as db:
        old = json.loads(db.execute("SELECT request FROM evaluation_jobs WHERE id=?", (job["id"],)).fetchone()[0])
        old.pop("knowledge_context")
        old["knowledgeFacts"] = [KnowledgeFact(text="Legacy fixture", source="synthetic", effective_date="2099-01-01",
            product="unknown", jurisdiction="unknown", verified_by="file only").model_dump()]
        db.execute("UPDATE evaluation_jobs SET request=? WHERE id=?", (json.dumps(old), job["id"]))
    asyncio.run(service.run(job["id"]))
    assert seen[0]["knowledgeFacts"] == []
    with repo.connect() as db:
        saved = json.loads(db.execute("SELECT request FROM evaluation_jobs WHERE id=?", (job["id"],)).fetchone()[0])
    assert saved == seen[0]


def test_unassessable_needs_reason_null_and_non_na_evidence():
    base = {"criterionId": "consultation", "value": None, "explanation": "Need facts", "applicability_reason": None,
            "assessment_status": "unassessable", "review_reason": "Missing source", "evidence": [{"kind": "message", "message_id": "m1"}]}
    assert ModelCriterionResult.model_validate(base).assessment_status == "unassessable"
    for patch in (dict(value="0"), dict(review_reason=None), dict(review_reason=" "), dict(applicability_reason="N/A"),
                  dict(evidence=[{"kind": "not_applicable", "message_id": "m1"}])):
        with pytest.raises(ValueError):
            ModelCriterionResult.model_validate({**base, **patch})


def test_old_completed_records_remain_compatible_without_mutation(repo):
    result, _ = run_http(repo)
    legacy = deepcopy(result)
    for row in legacy["results"]:
        row.pop("assessment_status", None)
        row.pop("review_reason", None)
    before = deepcopy(legacy)
    wire = to_wire_evaluation(legacy)
    assert legacy == before
    assert wire["status"] == "completed" and wire["score"] is not None
    assert all(r["assessment_status"] == "assessed" for r in wire["results"])


def test_unassessable_does_not_bypass_evidence_verification(repo):
    service = EvaluationService(repo)
    job = service.create("knowledge-case")
    with repo.connect() as db:
        request = EvaluationRequest.model_validate_json(db.execute("SELECT request FROM evaluation_jobs WHERE id=?", (job["id"],)).fetchone()[0])
    raw = model_output(request, unassessable=True)
    row = next(r for r in raw["results"] if r["criterionId"] == "consultation")
    row["evidence"][0]["message_id"] = "missing"
    results, errors, reviews = inspect_output(raw, request)
    assert not errors
    assert {r["code"] for r in reviews} >= {"CRITERION_UNASSESSABLE", "MESSAGE_NOT_FOUND"}


def test_appeal_audit_identity_is_preserved_and_never_invented():
    old = {"id": "a", "conversationId": "c", "criterionId": "greeting", "evaluationId": "e",
           "comment": "Synthetic appeal", "status": "pending", "createdAt": "2026-11-08T00:00:00Z", "originalValue": 0}
    converted = to_wire_appeal(old)
    assert converted["createdBy"] is None and converted["decidedBy"] is None
    current = {**old, "createdBy": "actor-A", "decidedBy": "actor-B"}
    converted = to_wire_appeal(current)
    assert converted["createdBy"] == "actor-A" and converted["decidedBy"] == "actor-B"
