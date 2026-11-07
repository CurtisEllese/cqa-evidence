import json
from pathlib import Path

import pytest
from hypothesis import given, strategies as st
from pydantic import ValidationError

from contracts.v1 import (
    Checklist, Conversation, Criterion, EvaluationJob, EvaluatorInput,
    ModelOutput, ScoreSnapshot, validate_output,
)


def inputs():
    checklist = Checklist.model_validate({
        "schema_version": "1.0", "id": "demo", "version": 1,
        "name": "Synthetic contract example", "channel": "agent chat",
        "criteria": [{"id": "01", "title": "Greeting", "description": "Operator greets client",
                      "type": "binary", "weight": "10", "max": 1, "allowed_values": [0, 1],
                      "allow_na": False, "na_condition": None, "section": "Contact"}],
    })
    conversation = Conversation.model_validate({
        "id": "demo-en-001", "content_hash": "a" * 64, "language": "EN",
        "channel": "agent chat", "audio_url": None,
        "transcript": [
            {"id": "demo-en-001-m1", "speaker": "bot", "timestamp": "00:00:00", "text": "Welcome"},
            {"id": "demo-en-001-m2", "speaker": "operator", "timestamp": "00:00:01", "text": "Hello, I am Alex."},
        ],
    })
    output = {"schema_version": "1.0", "results": [{
        "criterion_id": "01", "value": 1, "explanation": "The operator greets the client.",
        "na_reason": None, "evidence": {"kind": "message", "message_ids": ["demo-en-001-m2"],
        "start_message_id": None, "end_message_id": None, "quote": "Hello"},
    }]}
    return checklist, conversation, output


def report(output, checklist=None):
    default, conversation, _ = inputs()
    return validate_output(ModelOutput.model_validate(output), checklist or default, conversation)


def test_valid_json_roundtrip_and_exact_quote():
    checklist, conversation, output = inputs()
    parsed = ModelOutput.model_validate_json(json.dumps(output))
    assert validate_output(parsed, checklist, conversation).status == "valid"
    assert parsed.model_dump() == output


@pytest.mark.parametrize("value", [True, "1", 1.0, "yes", "N/A"])
def test_no_coercion_of_decisions(value):
    _, _, output = inputs()
    output["results"][0]["value"] = value
    with pytest.raises(ValidationError):
        ModelOutput.model_validate(output)


@pytest.mark.parametrize("case", ["missing", "duplicate", "unknown", "step", "na"])
def test_hard_errors_block_scoring(case):
    _, _, output = inputs()
    if case == "missing": output["results"] = []
    if case == "duplicate": output["results"] *= 2
    if case == "unknown": output["results"][0]["criterion_id"] = "new"
    if case == "step": output["results"][0]["value"] = 7
    if case == "na": output["results"][0]["value"] = None
    result = report(output)
    assert result.status == "invalid"
    assert result.errors


@pytest.mark.parametrize("case", ["unknown_message", "quote", "empty", "reversed", "partial_range"])
def test_evidence_errors_require_review(case):
    _, _, output = inputs()
    ev = output["results"][0]["evidence"]
    if case == "unknown_message": ev["message_ids"] = ["demo-en-001-m99"]
    if case == "quote": ev["quote"] = "I guarantee profits"
    if case == "empty": ev.update(message_ids=[], quote=None)
    if case == "reversed":
        ev.update(kind="absence", message_ids=[], quote=None,
                  start_message_id="demo-en-001-m2", end_message_id="demo-en-001-m1")
    if case == "partial_range": ev["start_message_id"] = "demo-en-001-m1"
    result = report(output)
    assert result.status == "needs_review"
    assert not result.errors
    assert result.evidence_issues


def test_na_is_valid_only_with_condition_reason_and_reference():
    checklist, _, output = inputs()
    data = checklist.model_dump()
    data["criteria"][0].update(allow_na=True, na_condition="Only when client left before operator joined")
    checklist = Checklist.model_validate(data)
    decision = output["results"][0]
    decision.update(value=None, na_reason="Client left before the operator joined")
    decision["evidence"].update(kind="not_applicable", quote=None)
    assert report(output, checklist).status == "valid"
    decision["na_reason"] = " "
    assert report(output, checklist).status == "invalid"


@given(maximum=st.integers(min_value=2, max_value=100), candidate=st.integers(min_value=-5, max_value=105))
def test_new_scale_uses_config_without_criterion_specific_logic(maximum, candidate):
    checklist, _, output = inputs()
    data = checklist.model_dump()
    data["criteria"][0].update(type="scale", max=maximum, allowed_values=[0, maximum], weight="7.25")
    output["results"][0]["value"] = candidate
    result = report(output, Checklist.model_validate(data))
    assert result.status == ("valid" if candidate in [0, maximum] else "invalid")


def test_ids_include_bot_and_start_at_one():
    _, conversation, _ = inputs()
    data = conversation.model_dump()
    data["transcript"][0]["id"] = "demo-en-001-m0"
    with pytest.raises(ValidationError): Conversation.model_validate(data)


def test_wrong_channel_cannot_reach_adapter():
    checklist, conversation, _ = inputs()
    data = checklist.model_dump()
    data["channel"] = "voice call"
    with pytest.raises(ValidationError):
        EvaluatorInput(conversation=conversation, checklist=Checklist.model_validate(data),
                       knowledge_context="", prompt_version="v1", knowledge_version="none")


def test_flag_cannot_contribute_weight():
    checklist, _, _ = inputs()
    data = checklist.criteria[0].model_dump()
    data.update(type="flag", weight="0")
    Criterion.model_validate(data)
    data["weight"] = "1"
    with pytest.raises(ValidationError): Criterion.model_validate(data)


def test_needs_review_cannot_publish_confirmed_total():
    snapshot = {"status": "needs_review", "results": [], "total_score": None,
                "provisional_total_score": "85.38", "score_unavailable_reason": "unverified_evidence",
                "critical_error": None, "sections": [], "validation_issues": []}
    ScoreSnapshot.model_validate(snapshot)
    snapshot["total_score"] = "85.38"
    with pytest.raises(ValidationError): ScoreSnapshot.model_validate(snapshot)


def test_failed_job_must_expose_error():
    with pytest.raises(ValidationError):
        EvaluationJob(schema_version="1.0", id="job-1", status="failed", evaluation=None, error=None)


def test_published_schemas_are_current():
    from contracts.v1 import Evaluation, ErrorResponse
    for name, cls in {"checklist": Checklist, "model-output": ModelOutput, "evaluation": Evaluation,
                      "error": ErrorResponse, "evaluation-job": EvaluationJob, "evaluator-input": EvaluatorInput}.items():
        expected = cls.model_json_schema()
        expected["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        actual = json.loads((Path(__file__).parents[1] / "contracts" / "schemas" / f"{name}.schema.json").read_text())
        assert actual == expected
