"""Explicit boundary between legacy numeric repository dictionaries and the active v2 wire."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json

from .models import AppealDTO, Checklist, ConversationDTO, EvaluationResult, Score, WorkspaceState

VERSION = "2.0"


def decimal_text(value) -> str:
    """Format without exponent notation or rounding; never accept booleans/nonfinite values."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("Expected a finite nonnegative decimal value")
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("Invalid decimal value") from error
    if not number.is_finite() or number < 0:
        raise ValueError("Expected a finite nonnegative decimal value")
    result = format(number, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return "0" if number == 0 else result


def internal_number(value: str):
    """Convert a validated wire decimal only if its decimal representation round-trips exactly."""
    number = Decimal(value)
    if number == number.to_integral_value():
        return int(number)
    converted = float(number)
    if Decimal(str(converted)) != number:
        raise ValueError("Decimal cannot round-trip through the legacy numeric representation")
    return converted


def _dump(model):
    return model.model_dump(mode="json", exclude_unset=True)


def to_wire_checklist(checklist: dict) -> dict:
    if checklist.get("schema_version") == VERSION:
        return _dump(Checklist.model_validate(checklist))
    criteria = []
    for criterion in checklist["criteria"]:
        item = {key: criterion[key] for key in ("id", "title", "description", "section", "allowNA")}
        item.update(
            type="yes_no" if criterion["type"] == "binary" else criterion["type"],
            weight=decimal_text(criterion["weight"]), max=decimal_text(criterion["max"]),
            allowedValues=[decimal_text(value) for value in criterion.get("allowedValues", [0, 1])],
            naCondition=criterion.get("naCondition"),
        )
        criteria.append(item)
    return _dump(Checklist.model_validate({"schema_version": VERSION, "id": checklist["id"], "name": checklist["name"], "version": checklist["version"], "criteria": criteria}))


def from_wire_checklist(checklist: dict | Checklist) -> dict:
    validated = checklist if isinstance(checklist, Checklist) else Checklist.model_validate(checklist)
    result = validated.model_dump(exclude={"schema_version"})
    for criterion in result["criteria"]:
        criterion["type"] = "binary" if criterion["type"] == "yes_no" else criterion["type"]
        criterion["weight"] = internal_number(criterion["weight"])
        criterion["max"] = internal_number(criterion["max"])
        criterion["allowedValues"] = [internal_number(value) for value in criterion["allowedValues"]]
        if criterion.get("naCondition") is None:
            criterion.pop("naCondition", None)
    # Keep the existing storage invariants as a separate, explicit legacy boundary.
    from ...domain import validate_checklist
    return validate_checklist(result)


def _message_map(messages):
    return {message["id"]: message for message in (messages or [])}


def _wire_result(result, messages=None):
    source = _message_map(messages)
    evidence = []
    if "evidence" in result:
        for item in result["evidence"]:
            evidence.append(dict(item))
    else:
        # Legacy IDs were server-validated. Extract the original source, never manufacture quotes.
        for identifier in result.get("evidenceIds", []):
            message = source.get(identifier)
            text = message.get("text") if message else None
            evidence.append({
                "kind": "not_applicable" if result.get("value") is None else "message",
                "message_id": identifier, "quote": text, "start_message_id": None, "end_message_id": None,
                "source_text": text, "verification_status": "verified" if text else "unverified",
                "reason": None if text else "Original message is unavailable for legacy evidence verification",
            })
    value = result.get("value")
    return {
        "criterionId": result["criterionId"], "value": None if value is None else decimal_text(value),
        "explanation": result["explanation"],
        "applicability_reason": result.get("applicability_reason", result["explanation"] if value is None else None),
        "assessment_status": result.get("assessment_status", "assessed"),
        "review_reason": result.get("review_reason"),
        "evidence": evidence, "critical": result.get("critical", False),
    }


def to_wire_score(score: dict | None, messages=None) -> dict | None:
    if score is None:
        return None
    if score.get("schema_version") == VERSION:
        return _dump(Score.model_validate(score))
    return _dump(Score.model_validate({
        "schema_version": VERSION,
        "total": None if score["total"] is None else decimal_text(score["total"]),
        "earned": decimal_text(score["earned"]), "possible": decimal_text(score["possible"]),
        "applicable": score["applicable"], "critical": score["critical"],
        "results": [_wire_result(result, messages) for result in score["results"]],
        "breakdown": score.get("breakdown", {}),
    }))


def to_wire_evaluation(evaluation: dict | None, conversation_id=None, conversation_hash=None, messages=None) -> dict | None:
    if evaluation is None:
        return None
    if evaluation.get("schema_version") == VERSION:
        return _dump(EvaluationResult.model_validate(evaluation))
    if evaluation.get("source") != "api":
        raise ValueError("Only api evaluations may cross the wire boundary")
    identifier = evaluation.get("conversationId", conversation_id)
    digest = evaluation.get("conversationHash", conversation_hash)
    if not identifier or not digest:
        raise ValueError("Evaluation wire conversion requires conversation ID and content hash")
    results = [_wire_result(result, messages) for result in evaluation.get("results", [])]
    status = evaluation.get("status", "completed")
    if status == "completed" and any(result["assessment_status"] != "assessed" or not result["evidence"] or any(item.get("verification_status") != "verified" for item in result["evidence"]) for result in results):
        status = "needs_review"
    score = None
    if status == "completed":
        raw_score = evaluation.get("score")
        if raw_score is None:
            from ...domain import score_evaluation
            raw_score = score_evaluation(evaluation, wire_decimals=True)
        score = to_wire_score(raw_score, messages)
    return _dump(EvaluationResult.model_validate({
        "schema_version": VERSION, "id": evaluation["id"], "conversationId": identifier, "conversationHash": digest,
        "checklist": to_wire_checklist(evaluation["checklist"]), "results": results, "createdAt": evaluation["createdAt"],
        "source": "api", "status": status, "error": evaluation.get("error"), "score": score,
        "provenance": evaluation.get("provenance"),
    }))


def to_wire_appeal(appeal: dict) -> dict:
    if appeal.get("schema_version") == VERSION:
        return _dump(AppealDTO.model_validate(appeal))
    item = {key: appeal[key] for key in ("id", "conversationId", "criterionId", "evaluationId", "comment", "status", "createdAt")}
    item.update(schema_version=VERSION, originalValue=None if appeal["originalValue"] is None else decimal_text(appeal["originalValue"]),
                createdBy=appeal.get("createdBy"), decidedBy=appeal.get("decidedBy"))
    for key in ("decision", "decidedAt", "createdBy", "decidedBy"):
        if key in appeal:
            item[key] = appeal[key]
    if "newValue" in appeal:
        item["newValue"] = None if appeal["newValue"] is None else decimal_text(appeal["newValue"])
    return _dump(AppealDTO.model_validate(item))


def to_wire_conversation(conversation: dict) -> dict:
    if conversation.get("schema_version") == VERSION:
        return _dump(ConversationDTO.model_validate(conversation))
    item = {key: conversation[key] for key in ("id", "title", "operator", "initials", "language", "channel", "date", "duration", "audioUrl")}
    messages = conversation["transcript"]
    digest = conversation.get("contentHash")
    if not digest:
        content = {key: conversation.get(key) for key in ("transcript", "language", "channel")}
        digest = hashlib.sha256(json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    item.update(schema_version=VERSION, contentHash=digest, transcript=[
        {key: message[key] for key in ("id", "timestamp", "speaker", "text")} | {"message_index": index}
        for index, message in enumerate(messages)
    ])
    for key in ("dateSource", "audioPageUrl"):
        if key in conversation:
            item[key] = conversation[key]
    item["evaluation"] = to_wire_evaluation(conversation.get("evaluation"), conversation["id"], digest, messages)
    if conversation.get("scores") and item["evaluation"] and item["evaluation"]["status"] == "completed":
        scores = conversation["scores"]
        item["scores"] = {
            "original": to_wire_score(scores["original"], messages), "final": to_wire_score(scores["final"], messages),
            "sections": {name: to_wire_score(score, messages) for name, score in scores["sections"].items()},
        }
    return _dump(ConversationDTO.model_validate(item))


def to_wire_state(state: dict) -> dict:
    if state.get("schema_version") == VERSION:
        return _dump(WorkspaceState.model_validate(state))
    data = state["data"]
    item = {
        "schema_version": VERSION,
        "data": {"conversations": [to_wire_conversation(conversation) for conversation in data["conversations"]], "checklist": to_wire_checklist(data["checklist"]), "appeals": [to_wire_appeal(appeal) for appeal in data["appeals"]]},
        "dataset": state.get("dataset"), "datasets": state.get("datasets", [state["dataset"]] if state.get("dataset") else []), "evaluationAvailable": state["evaluationAvailable"], "revision": state["revision"],
    }
    if "imported" in state:
        item["imported"] = state["imported"]
    return _dump(WorkspaceState.model_validate(item))
