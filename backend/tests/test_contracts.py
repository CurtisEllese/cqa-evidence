"""Contract tests exercise the wire boundary without provider calls or application startup."""
from copy import deepcopy
from decimal import Decimal
import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.contracts.v2 import (
    ApiError, Checklist, Criterion, DecideAppealRequest, EvaluationRequest, EvaluationResult,
    ModelResponse, RefreshRequest, Score, StartEvaluationRequest, WorkspaceState, decimal_text,
    from_wire_checklist, internal_number, to_wire_checklist, to_wire_evaluation,
    to_wire_score, to_wire_state,
)
from backend.contracts.v2.models import MODELS
from backend.domain import initial_checklist, normalize_import, score_evaluation


def legacy_conversation():
    conversation = normalize_import([{
        "conversation_id": "contract-test", "channel": "agent chat", "language": "RU", "audio_url": None,
        "transcript": [
            {"speaker": "client", "timestamp": "00:00", "text": "Тестовый вопрос."},
            {"speaker": "operator", "timestamp": "00:10", "text": "Здравствуйте! Тестовый ответ."},
        ],
    }])[0]
    conversation["contentHash"] = "a" * 64
    return conversation


def legacy_evaluation():
    checklist = initial_checklist()
    values = [1, None, None, 15, 1, 7, 1, 0]
    return {
        "id": "contract-evaluation", "source": "api", "createdAt": "2026-11-07T12:00:00+05:00", "checklist": checklist,
        "results": [{"criterionId": criterion["id"], "value": value, "explanation": "Синтетическое обоснование для теста контракта.", "evidenceIds": ["contract-test-m2"], "critical": False} for criterion, value in zip(checklist["criteria"], values)],
    }


def wire_model_response():
    return {"schema_version": "2.0", "results": [{
        "criterionId": "greeting", "value": "1", "explanation": "Оператор представился.", "applicability_reason": None,
        "evidence": [{"message_id": "contract-test-m2", "quote": "Здравствуйте!"}],
    }]}


def wire_evaluation():
    conversation = legacy_conversation()
    return to_wire_evaluation(legacy_evaluation(), conversation["id"], conversation["contentHash"], conversation["transcript"])


def test_schema_version_required_and_v1_never_silently_accepted():
    for payload in [{"conversationId": "x"}, {"schema_version": "1.0", "conversationId": "x"}, {"schema_version": 2, "conversationId": "x"}]:
        with pytest.raises(ValidationError):
            StartEvaluationRequest.model_validate(payload)
    assert StartEvaluationRequest.model_validate({"schema_version": "2.0", "conversationId": "x"}).conversationId == "x"


def test_refresh_group_defaults_to_dev_and_preserves_organizer_identifier():
    assert RefreshRequest.model_validate({"schema_version": "2.0"}).groupId == "DEV"
    request = RefreshRequest.model_validate({"schema_version": "2.0", "groupId": "  бақылау / final + 10?  "})
    assert request.groupId == "бақылау / final + 10?"


@pytest.mark.parametrize("group", [None, 10, True, [], "", " \t\n", "x" * 513])
def test_refresh_rejects_invalid_group_without_coercion(group):
    with pytest.raises(ValidationError):
        RefreshRequest.model_validate({"schema_version": "2.0", "groupId": group})


def test_all_structured_models_forbid_unknown_fields():
    for model in MODELS.values():
        assert model.model_config["extra"] == "forbid"
        assert model.model_config["strict"] is True
        assert model.model_json_schema()["additionalProperties"] is False
    response = wire_model_response()
    response["results"][0]["verification_status"] = "verified"
    with pytest.raises(ValidationError):
        ModelResponse.model_validate(response)


@pytest.mark.parametrize("field,value", [("score", "100"), ("checklist", {}), ("id", "model-owned-id"), ("createdAt", "now"), ("status", "completed")])
def test_model_cannot_supply_server_owned_fields(field, value):
    response = wire_model_response()
    response[field] = value
    with pytest.raises(ValidationError):
        ModelResponse.model_validate(response)


@pytest.mark.parametrize("value", [15, 15.5, True, "1e2", "1,5", "-1", ".5", "01", "1.", "0.1234567", "NaN", "Infinity", " 1", "1 "])
def test_decimal_input_types_and_syntax_are_strict(value):
    criterion = to_wire_checklist(initial_checklist())["criteria"][0]
    criterion["weight"] = value
    with pytest.raises(ValidationError):
        Criterion.model_validate(criterion)


def test_decimal_scale_uniqueness_is_numeric_not_lexical():
    criterion = to_wire_checklist(initial_checklist())["criteria"][3]
    criterion["allowedValues"] = ["0", "5", "5.0", "20"]
    with pytest.raises(ValidationError, match="unique"):
        Criterion.model_validate(criterion)


@pytest.mark.parametrize("patch", [
    {"type": "binary"}, {"weight": "1000000.000001"}, {"max": "0"},
    {"allowedValues": ["0", "21", "20"]}, {"allowedValues": ["5", "20"]},
    {"allowNA": True, "naCondition": None},
])
def test_criterion_semantic_constraints(patch):
    criterion = to_wire_checklist(initial_checklist())["criteria"][3]
    criterion.update(patch)
    with pytest.raises(ValidationError):
        Criterion.model_validate(criterion)


@pytest.mark.parametrize("patch", [{"weight": "1"}, {"max": "2", "allowedValues": ["0", "2"]}, {"allowNA": True, "naCondition": "example"}])
def test_flag_invariants(patch):
    criterion = to_wire_checklist(initial_checklist())["criteria"][-1]
    criterion.update(patch)
    with pytest.raises(ValidationError):
        Criterion.model_validate(criterion)


def test_exact_decimal_roundtrip_and_legacy_enum_translation():
    legacy = initial_checklist()
    wire = to_wire_checklist(legacy)
    assert wire["schema_version"] == "2.0"
    assert wire["criteria"][0]["type"] == "yes_no"
    assert wire["criteria"][0]["weight"] == "10"
    assert wire["criteria"][3]["max"] == "20"
    assert wire["criteria"][3]["allowedValues"] == ["0", "5", "10", "15", "20"]
    assert from_wire_checklist(wire) == legacy
    wire["criteria"][0]["weight"] = "999999.123456"
    changed = from_wire_checklist(wire)
    assert Decimal(str(changed["criteria"][0]["weight"])) == Decimal("999999.123456")
    assert to_wire_checklist(changed)["criteria"][0]["weight"] == "999999.123456"
    with pytest.raises(ValueError, match="round-trip"):
        internal_number("0.12345678901234567890123456789")


def test_decimal_codec_never_rounds_and_rejects_nonfinite():
    assert decimal_text(1e-6) == "0.000001"
    assert decimal_text(Decimal("100.500000")) == "100.5"
    assert decimal_text(Decimal("0.1234567890123456789")) == "0.1234567890123456789"
    for invalid in [True, float("nan"), float("inf"), -1, None]:
        with pytest.raises(ValueError):
            decimal_text(invalid)


def test_na_requires_explicit_reason_but_null_evidence_is_structurally_retained_for_review():
    response = wire_model_response()
    result = response["results"][0]
    result["value"] = None
    with pytest.raises(ValidationError, match="applicability_reason"):
        ModelResponse.model_validate(response)
    result["applicability_reason"] = "Клиент завершил разговор до запроса."
    result["evidence"] = [{"kind": "not_applicable", "message_id": None, "quote": None}]
    assert ModelResponse.model_validate(response).results[0].value is None


def test_model_evidence_cannot_claim_verification():
    response = wire_model_response()
    response["results"][0]["evidence"][0]["verification_status"] = "verified"
    with pytest.raises(ValidationError):
        ModelResponse.model_validate(response)


def test_range_evidence_schema_preserves_stable_ids():
    response = wire_model_response()
    response["results"][0]["evidence"] = [{"kind": "absence", "start_message_id": "contract-test-m1", "end_message_id": "contract-test-m2", "message_id": None, "quote": None}]
    evidence = ModelResponse.model_validate(response).results[0].evidence[0]
    assert evidence.start_message_id == "contract-test-m1" and evidence.end_message_id == "contract-test-m2"


def test_request_carries_channel_language_stable_ids_and_zero_based_indices():
    conversation = legacy_conversation()
    messages = [{**message, "message_index": index} for index, message in enumerate(conversation["transcript"])]
    payload = {"schema_version": "2.0", "conversationId": conversation["id"], "conversationHash": conversation["contentHash"], "language": "RU", "channel": "agent chat", "checklist": to_wire_checklist(initial_checklist()), "messages": messages, "knowledgeFacts": []}
    request = EvaluationRequest.model_validate(payload)
    assert request.messages[0].message_index == 0
    assert request.messages[0].id == "contract-test-m1"
    payload["messages"][1]["message_index"] = 0
    with pytest.raises(ValidationError, match="unique"):
        EvaluationRequest.model_validate(payload)
    payload["messages"][1]["message_index"] = -1
    with pytest.raises(ValidationError):
        EvaluationRequest.model_validate(payload)


def test_legacy_evaluation_conversion_extracts_source_and_keeps_decimal_score():
    wire = wire_evaluation()
    assert wire["status"] == "completed" and wire["score"]["total"] == "85.38"
    assert wire["results"][0]["evidence"][0]["source_text"] == "Здравствуйте! Тестовый ответ."
    assert wire["results"][0]["evidence"][0]["verification_status"] == "verified"
    assert "evidenceIds" not in wire["results"][0]
    assert wire["results"][1]["applicability_reason"]
    assert EvaluationResult.model_validate(wire).source == "api"


def test_unknown_legacy_evidence_never_produces_completed_score():
    item = legacy_evaluation()
    item["results"][0]["evidenceIds"] = ["missing"]
    conversation = legacy_conversation()
    wire = to_wire_evaluation(item, conversation["id"], conversation["contentHash"], conversation["transcript"])
    assert wire["status"] == "needs_review" and wire["score"] is None
    assert wire["results"][0]["evidence"][0]["verification_status"] == "unverified"


def test_needs_review_cannot_expose_score_and_completed_requires_verified_evidence():
    wire = wire_evaluation()
    wire["status"] = "needs_review"
    with pytest.raises(ValidationError, match="score"):
        EvaluationResult.model_validate(wire)
    wire = wire_evaluation()
    wire["results"][0]["evidence"][0].update(verification_status="unverified", reason="Quote mismatch")
    with pytest.raises(ValidationError, match="verified"):
        EvaluationResult.model_validate(wire)
    wire = wire_evaluation()
    wire["score"] = None
    with pytest.raises(ValidationError, match="score"):
        EvaluationResult.model_validate(wire)


def test_confirmed_score_rejects_unverified_evidence_and_accepts_large_aggregates():
    conversation = legacy_conversation()
    score = to_wire_score(score_evaluation(legacy_evaluation()), conversation["transcript"])
    score["earned"] = "1500000.1234567890123456789"
    score["possible"] = "2000000"
    assert Score.model_validate(score).earned == "1500000.1234567890123456789"
    score["results"][0]["evidence"][0].update(verification_status="unverified", reason="Missing evidence")
    with pytest.raises(ValidationError, match="verified"):
        Score.model_validate(score)


def test_state_codec_is_explicit_idempotent_and_immutable():
    conversation = legacy_conversation()
    conversation["evaluation"] = legacy_evaluation()
    score = score_evaluation(conversation["evaluation"])
    conversation["scores"] = {"original": score, "final": score, "sections": {}}
    state = {"data": {"conversations": [conversation], "checklist": initial_checklist(), "appeals": []}, "dataset": None, "evaluationAvailable": False, "revision": 3, "imported": 1}
    before = deepcopy(state)
    wire = to_wire_state(state)
    assert state == before
    assert wire["schema_version"] == "2.0" and wire["imported"] == 1
    assert wire["data"]["conversations"][0]["transcript"][0]["message_index"] == 0
    assert wire["data"]["conversations"][0]["scores"]["final"]["total"] == "85.38"
    assert WorkspaceState.model_validate(wire).revision == 3
    assert to_wire_state(wire) == wire


def test_decision_explicit_na_distinguished_from_missing_value():
    with pytest.raises(ValidationError, match="newValue"):
        DecideAppealRequest.model_validate({"schema_version": "2.0", "status": "accepted"})
    decision = DecideAppealRequest.model_validate({"schema_version": "2.0", "status": "accepted", "newValue": None})
    assert decision.newValue is None
    with pytest.raises(ValidationError):
        DecideAppealRequest.model_validate({"schema_version": "2.0", "status": "accepted", "newValue": 1})


def test_api_error_shape_is_flat_and_versioned():
    error = ApiError.model_validate({"schema_version": "2.0", "code": "INVALID_REQUEST", "message": "Проверьте поля запроса.", "details": [{"code": "FIELD", "path": "/checklist", "message": "Нет чек-листа."}], "request_id": "test-request", "retryable": False})
    assert error.details[0].path == "/checklist"


def test_generated_types_and_schemas_match_authoritative_models():
    path = Path(__file__).resolve().parents[2] / "scripts" / "generate_contracts.py"
    spec = importlib.util.spec_from_file_location("contract_generator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for artifact, expected in module.artifacts().items():
        assert artifact.read_text(encoding="utf-8") == expected, f"Regenerate {artifact}"
