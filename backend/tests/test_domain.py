"""Synthetic data belongs only to tests; production starts without generated dialogs."""
from copy import deepcopy
import json

import pytest

from backend.domain import (
    initial_checklist, normalize_import, score_evaluation,
    validate_checklist, validate_evaluation,
)


def record(**changes):
    result = {
        "conversation_id": "test-001", "channel": "agent chat", "language": "RU", "audio_url": None,
        "transcript": [
            {"timestamp": "00:02", "speaker": "operator", "text": "Здравствуйте!"},
            {"timestamp": "00:17", "speaker": "client", "text": " Как найти отчёт?\n"},
        ],
    }
    result.update(changes)
    return result


def voice_record(**changes):
    result = record(channel="voice call", transcript=[
        {"timestamp": "2026-11-07T10:00:05+05:00", "speaker": "operator", "text": "Здравствуйте!"},
        {"timestamp": "2026-11-07T05:02:22Z", "speaker": "client", "text": "Спасибо."},
    ])
    result.update(changes)
    return result


def evaluation(values=None):
    checklist = initial_checklist()
    values = values or [1, None, None, 15, 1, 7, 1, 0]
    return {
        "id": "evaluation-1", "checklist": checklist, "createdAt": "2026-11-07T12:00:00+05:00", "source": "api",
        "results": [{"criterionId": criterion["id"], "value": value, "explanation": "Проверен указанный участок диалога.", "evidenceIds": ["test-001-m1"], "critical": False} for criterion, value in zip(checklist["criteria"], values)],
    }


def appeal(**changes):
    result = {"id": "appeal-1", "evaluationId": "evaluation-1", "conversationId": "test-001", "criterionId": "consultation", "status": "accepted", "newValue": 20, "originalValue": 15}
    result.update(changes)
    return result


def test_official_rubric_fresh_copies():
    checklist = initial_checklist()
    assert len(checklist["criteria"]) == 8
    assert [criterion["weight"] for criterion in checklist["criteria"]] == [10, 15, 20, 20, 15, 15, 5, 0]
    assert checklist["criteria"][3]["allowedValues"] == [0, 5, 10, 15, 20]
    assert checklist["criteria"][5]["allowedValues"] == [0, 4, 7, 10]
    checklist["criteria"][0]["weight"] = 1
    assert initial_checklist()["criteria"][0]["weight"] == 10


def test_official_8538_no_intermediate_rounding():
    score = score_evaluation(evaluation())
    assert {key: score[key] for key in ("earned", "possible", "total", "applicable", "critical")} == {
        "earned": 55.5, "possible": 65, "total": 85.38, "applicable": 5, "critical": False,
    }


def test_flag_separate_and_appeal_semantics():
    original = evaluation()
    flagged = score_evaluation(original, [appeal(criterionId="gross-errors", newValue=1)])
    assert flagged["critical"] is True
    assert flagged["total"] == 85.38
    assert flagged["possible"] == 65
    cleared = score_evaluation(original, [appeal(criterionId="gross-errors", newValue=1), appeal(criterionId="gross-errors", newValue=0)])
    assert cleared["critical"] is False
    original["results"][0]["critical"] = True
    assert score_evaluation(original)["critical"] is False


@pytest.mark.parametrize("changes", [{"status": "pending"}, {"status": "rejected"}, {"evaluationId": "old-evaluation"}])
def test_wrong_version_and_pending_appeals_ignored(changes):
    assert score_evaluation(evaluation(), [appeal(**changes)])["total"] == 85.38


def test_score_snapshot_and_appeals_do_not_mutate_inputs():
    original = evaluation()
    before = deepcopy(original)
    assert score_evaluation(original, [appeal()])["total"] == 93.08
    assert original == before
    original["checklist"]["criteria"][0]["weight"] = 20
    assert score_evaluation(original)["total"] == 87.33
    assert score_evaluation(before)["total"] == 85.38


def test_na_denominator_and_all_na():
    score = score_evaluation(evaluation(), [appeal(newValue=None)])
    assert score["earned"] == 40.5 and score["possible"] == 45 and score["total"] == 90
    item = evaluation()
    item["checklist"]["criteria"] = [criterion for criterion in item["checklist"]["criteria"] if criterion["id"] in ("discovery", "gross-errors")]
    item["results"] = [result for result in item["results"] if result["criterionId"] in ("discovery", "gross-errors")]
    item["results"][-1]["value"] = 1
    score = score_evaluation(item)
    assert score["total"] is None and score["possible"] == 0 and score["critical"] is True


def test_half_up_decimal_rounding():
    item = evaluation()
    criterion = item["checklist"]["criteria"][3]
    criterion.update(max=10000, allowedValues=[0, 268, 10000], weight=1)
    item["checklist"]["criteria"] = [criterion]
    item["results"] = [{**item["results"][3], "value": 268}]
    assert score_evaluation(item)["total"] == 2.68
    criterion.update(max=20000, allowedValues=[0, 537, 20000])
    item["results"][0]["value"] = 537
    assert score_evaluation(item)["total"] == 2.69  # exact 2.685 rounds half-up


@pytest.mark.parametrize("criterion_index,value", [(0, None), (0, True), (3, 12), (5, 8), (7, 2), (7, None), (3, float("nan")), (3, float("inf")), (3, "15"), (3, 10**1000)])
def test_invalid_result_values_rejected(criterion_index, value):
    item = evaluation()
    item["results"][criterion_index]["value"] = value
    with pytest.raises(ValueError):
        score_evaluation(item)


def test_invalid_accepted_appeal_value_rejected():
    with pytest.raises(ValueError, match="Недопустимое"):
        score_evaluation(evaluation(), [appeal(newValue=18)])


@pytest.mark.parametrize("patch", [
    {"weight": -1}, {"weight": float("nan")}, {"weight": float("inf")}, {"weight": True}, {"weight": 1_000_001}, {"weight": 10**1000},
    {"max": 0}, {"max": -1}, {"max": 2}, {"max": "1"}, {"allowNA": "true"},
    {"title": " "}, {"description": ""}, {"id": " id "}, {"type": "unknown"},
    {"allowedValues": [0, 0, 1]}, {"allowedValues": [0, True]}, {"allowedValues": [0]}, {"allowedValues": [0, 0.5, 1]},
    {"allowNA": True, "naCondition": ""},
])
def test_malformed_checklist_criteria(patch):
    checklist = initial_checklist()
    checklist["criteria"][0].update(patch)
    with pytest.raises(ValueError):
        validate_checklist(checklist)


@pytest.mark.parametrize("patch", [{"weight": 1}, {"max": 2}, {"allowNA": True, "naCondition": "reason"}])
def test_flag_invariants(patch):
    checklist = initial_checklist()
    checklist["criteria"][-1].update(patch)
    with pytest.raises(ValueError):
        validate_checklist(checklist)


def test_checklist_ids_shape_and_bounds():
    for invalid in [None, {}, {"id": "x", "name": "x", "version": True, "criteria": []}]:
        with pytest.raises(ValueError):
            validate_checklist(invalid)
    checklist = initial_checklist()
    checklist["criteria"][1]["id"] = checklist["criteria"][0]["id"]
    with pytest.raises(ValueError, match="уникальными"):
        validate_checklist(checklist)
    checklist = initial_checklist()
    checklist["criteria"] *= 13
    with pytest.raises(ValueError):
        validate_checklist(checklist)


@pytest.mark.parametrize("values", [None, [], [5, 20], [0, 15], [0, -1, 20], [0, 21, 20], [0, float("nan"), 20]])
def test_scale_values_required_bounded_and_complete(values):
    checklist = initial_checklist()
    checklist["criteria"][3]["allowedValues"] = values
    with pytest.raises(ValueError):
        validate_checklist(checklist)


def test_checklist_custom_scale_and_unknown_metadata_retained_without_mutation():
    checklist = initial_checklist()
    checklist["criteria"][3].update(max=2.5, allowedValues=[0, 1.25, 2.5], metadata="preserve")
    validated = validate_checklist(checklist)
    assert validated == checklist and validated is not checklist
    assert validated["criteria"][3] is not checklist["criteria"][3]


def test_import_envelope_exact_text_timestamps_ids_and_no_invented_date():
    payload = {"error": "", "isSuccess": True, "data": {"dataset_group_id": "DEV", "records": [record()]}}
    result = normalize_import(json.dumps(payload, ensure_ascii=False).encode())[0]
    assert result["date"] is None
    assert result["duration"] == "00:15"
    assert result["transcript"][1] == {"id": "test-001-m2", "timestamp": "00:17", "speaker": "client", "text": " Как найти отчёт?\n"}
    assert result["title"] == " Как найти отчёт?\n"
    assert result["operator"] == "Оператор не указан"
    assert result["evaluation"] is None


def test_import_deduplicates_only_after_complete_validation():
    second = record(conversation_id="test-002")
    assert [item["id"] for item in normalize_import([record(), second, second], ["test-001"])] == ["test-002"]
    assert normalize_import([record()], ["test-001"]) == []
    with pytest.raises(ValueError, match="Запись 2"):
        normalize_import([record(), record(language="INVALID")], ["test-001"])


def test_voice_dates_and_elapsed_time_across_timezones_preserved():
    item = voice_record()
    result = normalize_import([item])[0]
    assert result["date"] == "2026-11-07T10:00:05+05:00"
    assert result["duration"] == "02:17"
    assert [message["timestamp"] for message in result["transcript"]] == [message["timestamp"] for message in item["transcript"]]
    item["transcript"].reverse()
    assert normalize_import([item])[0]["date"] == "2026-11-07T10:00:05+05:00"


@pytest.mark.parametrize("value", ["invalid", "", "2026-02-30", "2026-11-07T24:00:00Z", "2026-11-07T12:00:00", 1])
def test_bad_explicit_dates_rejected(value):
    with pytest.raises(ValueError, match="date/timestamp"):
        normalize_import([record(date=value)])


def test_nullable_dates_and_valid_leap_day():
    assert normalize_import([record(date=None)])[0]["date"] is None
    assert normalize_import([record(date="2024-02-29")])[0]["date"] == "2024-02-29"
    assert normalize_import([voice_record(date=None)])[0]["date"] == "2026-11-07T10:00:05+05:00"


@pytest.mark.parametrize("timestamp", ["00:60", "00:1", "00:01:01", "-01:01", "Tinvalid", "2026-11-07T10:00:00Z", "9" * 100 + ":00"])
def test_bad_chat_timestamp(timestamp):
    with pytest.raises(ValueError, match="timestamp"):
        normalize_import([record(transcript=[{"speaker": "client", "text": "x", "timestamp": timestamp}])])


@pytest.mark.parametrize("timestamp", ["00:00", "Tinvalid", "2026-02-30T10:00:00Z", "2026-11-07T10:00:60Z", "2026-11-07T10:00:00", "2026-11-07T10:00:00+25:00"])
def test_bad_voice_timestamp(timestamp):
    with pytest.raises(ValueError, match="timestamp"):
        normalize_import([voice_record(transcript=[{"speaker": "client", "text": "x", "timestamp": timestamp}])])


@pytest.mark.parametrize("audio", ["javascript:alert(1)", "file:///tmp/a", "https://user:pass@example.com/a", "https://", "https://exa mple.com/a", "https://example.com:99999/a", "https://example.com/\n", 42])
def test_unsafe_audio_rejected(audio):
    with pytest.raises(ValueError, match="audio_url"):
        normalize_import([record(audio_url=audio)])


@pytest.mark.parametrize("audio", [None, "", "  "])
def test_empty_audio_is_null(audio):
    assert normalize_import([record(audio_url=audio)])[0]["audioUrl"] is None


def test_safe_audio_long_chat_operator_and_aliases():
    item = record(audio_url="https://example.com/audio.mp3", operator="  Test Operator  ")
    item["transcript"][-1]["timestamp"] = "121:17"
    item["id"] = item.pop("conversation_id")
    result = normalize_import([item])[0]
    assert result["duration"] == "121:15"
    assert result["audioUrl"] == item["audio_url"]
    assert result["operator"] == "Test Operator" and result["initials"] == "TO"


@pytest.mark.parametrize("patch", [
    {"channel": "email"}, {"language": "DE"}, {"conversation_id": " "}, {"transcript": []},
    {"transcript": [{"timestamp": "00:01", "speaker": "unknown", "text": "x"}]},
    {"transcript": [{"timestamp": "00:01", "speaker": "client", "text": " "}]},
    {"transcript": [None]},
])
def test_invalid_import_record(patch):
    with pytest.raises(ValueError):
        normalize_import([record(**patch)])


def test_import_envelope_errors_and_limits():
    for payload in ["{", b"\xff", [], {}, {"isSuccess": False}, {"isSuccess": 1, "data": {"records": [record()]}}]:
        with pytest.raises(ValueError):
            normalize_import(payload)
    with pytest.raises(ValueError, match="1000"):
        normalize_import([record()] * 1001)
    with pytest.raises(ValueError, match="5 МБ"):
        normalize_import(" " * (5 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="5 МБ"):
        normalize_import([record(extra="я" * (3 * 1024 * 1024))])


def test_evaluation_real_evidence_and_immutable_snapshot():
    conversation = normalize_import([record()])[0]
    source = evaluation()
    valid = validate_evaluation(source, conversation)
    assert valid == source and valid is not source
    assert valid["checklist"] is not source["checklist"]


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown", "evidence", "empty_evidence", "source", "date", "missing_value", "explanation"])
def test_evaluation_malformed_coverage_and_evidence(change):
    item = evaluation()
    if change == "missing":
        item["results"].pop()
    elif change == "duplicate":
        item["results"][1]["criterionId"] = item["results"][0]["criterionId"]
    elif change == "unknown":
        item["results"][0]["criterionId"] = "invented"
    elif change == "evidence":
        item["results"][0]["evidenceIds"] = ["nonexistent-message"]
    elif change == "empty_evidence":
        item["results"][0]["evidenceIds"] = []
    elif change == "source":
        item["source"] = "demo"
    elif change == "date":
        item["createdAt"] = "not-a-date"
    elif change == "missing_value":
        del item["results"][0]["value"]
    else:
        item["results"][0]["explanation"] = " "
    with pytest.raises(ValueError):
        validate_evaluation(item, normalize_import([record()])[0])
