import asyncio
import json
from pathlib import Path

import httpx
import pytest

from backend.contracts.v2 import EvaluationRequest, KnowledgeFact
from backend.knowledge import (KnowledgeContext, ScopedKnowledgeFact,
                               load_scoped_knowledge, select_applicable_facts)
from evaluator.openai_v2 import OpenAIV2Evaluator
from evaluator.settings import OpenAISettings
from backend.evaluator import AdapterFailure


def fact(**patch):
    data = dict(id="synthetic", text="Synthetic approved rule", source="https://example.invalid/policy",
                source_locator="test section", source_published_at="2026-01-01", observed_at="2026-11-08",
                entity="Synthetic PLC", jurisdiction="KZ-AIFC", product="Synthetic product",
                valid_from="2026-01-01", valid_to="2026-03-01", validity_evidence="synthetic signed policy interval",
                review_status="expert_approved", verified_by="synthetic expert", approval_reference="synthetic approval",
                limits="Not real Freedom facts")
    data.update(patch)
    return ScopedKnowledgeFact(**data)


def context(**patch):
    data = dict(entity="Synthetic PLC", jurisdiction="KZ-AIFC", product="Synthetic product",
                conversation_date="2026-02-01", date_source="conversation", verified_by="synthetic data owner",
                verification_reference="synthetic metadata")
    data.update(patch)
    return KnowledgeContext(**data)


def test_exact_approved_context_selects_fact_with_limitations():
    selection = select_applicable_facts((fact(),), context())
    assert selection.included_ids == ("synthetic",)
    assert not selection.excluded
    assert "Not real Freedom facts" in selection.facts[0].text


@pytest.mark.parametrize("ctx,reason", [
    (None, "conversation_context_unverified"),
    (context(conversation_date="2025-12-31"), "outside_proven_effective_period"),
    (context(conversation_date="2026-03-02"), "outside_proven_effective_period"),
    (context(entity="Other PLC"), "entity_jurisdiction_or_product_mismatch"),
    (context(jurisdiction="RU"), "entity_jurisdiction_or_product_mismatch"),
    (context(product="Other product"), "entity_jurisdiction_or_product_mismatch"),
])
def test_fail_closed_dates_entity_country_and_product(ctx, reason):
    result = select_applicable_facts((fact(),), ctx)
    assert result.facts == ()
    assert result.excluded == ({"id": "synthetic", "reason": reason},)


@pytest.mark.parametrize("patch", [dict(valid_from=None), dict(valid_to=None), dict(validity_evidence=None),
    dict(approval_reference=None), dict(verified_by=""), dict(valid_from="2026-02-30"),
    dict(valid_to="2025-01-01")])
def test_cannot_approve_without_proven_scope(patch):
    with pytest.raises(ValueError):
        fact(**patch)


def test_import_date_and_unknown_date_cannot_be_conversation_context():
    for patch in (dict(date_source="import"), dict(conversation_date="unknown"), dict(conversation_date="")):
        with pytest.raises(ValueError):
            context(**patch)


def test_source_verified_is_not_expert_approved_and_date_boundaries_inclusive():
    candidate = fact(review_status="source_verified", valid_from=None, valid_to=None)
    assert select_applicable_facts((candidate,), context()).facts == ()
    for boundary in ("2026-01-01", "2026-03-01"):
        assert len(select_applicable_facts((fact(),), context(conversation_date=boundary)).facts) == 1


def test_checked_catalog_has_zero_runtime_approved_facts():
    path = Path(__file__).parents[1] / "docs/product/knowledge-reviewed.json"
    records = load_scoped_knowledge(path)
    assert records
    assert all(record.review_status != "expert_approved" for record in records)
    assert select_applicable_facts(records, None).facts == ()


def test_openai_rejects_detached_legacy_facts_before_http_without_mutating_request():
    raw = json.loads((Path(__file__).parents[1] / "evaluator/fixtures/synthetic_v2.json").read_text(encoding="utf-8"))[0]["request"]
    request = EvaluationRequest.model_validate(raw)
    request.knowledgeFacts = [KnowledgeFact(text="Future tariff must not reach the model", source="https://example.invalid/future",
        effective_date="2099-01-01", product="Unknown", jurisdiction="Unknown", verified_by="approved-file-only")]
    sent = []

    def handle(req):
        sent.append(json.loads(req.content))
        # Invalid response intentionally avoids introducing an unrelated score.
        return httpx.Response(200, json={"status": "incomplete"})

    evaluator = OpenAIV2Evaluator(OpenAISettings("synthetic-key", "gpt-6-luna", min_interval_seconds=0),
                                  transport=httpx.MockTransport(handle))
    with pytest.raises(AdapterFailure, match="KNOWLEDGE_CONTEXT_INVALID"):
        asyncio.run(evaluator.evaluate(request))
    assert sent == []
    assert len(request.knowledgeFacts) == 1
