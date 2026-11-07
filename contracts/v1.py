"""Canonical wire types; export JSON Schema with python -m contracts.export.

All fields are required (nullable where appropriate). UI internal types are
adapted explicitly. Decimal values stay strings across the wire.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(min_length=1)]
DecimalText = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]{1,6})?$")]
ComputedDecimalText = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")]
ScoreText = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]{2})$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Message(WireModel):
    id: Text
    timestamp: Text
    speaker: Literal["operator", "client", "bot", "system"]
    text: Text


class Conversation(WireModel):
    id: Text
    content_hash: Digest
    language: Literal["RU", "KZ", "EN"]
    channel: Literal["agent chat", "voice call"]
    transcript: Annotated[list[Message], Field(min_length=1)]
    audio_url: str | None

    @model_validator(mode="after")
    def ids_are_canonical(self):
        if any(m.id != f"{self.id}-m{i}" for i, m in enumerate(self.transcript, 1)):
            raise ValueError("message IDs must equal conversation-id-m1, m2, ... in original order")
        return self


class Criterion(WireModel):
    id: Text
    title: Text
    description: Text
    type: Literal["binary", "scale", "flag"]
    weight: DecimalText
    max: Annotated[int, Field(gt=0)]
    allowed_values: Annotated[list[int], Field(min_length=2)]
    allow_na: bool
    na_condition: str | None
    section: Text

    @model_validator(mode="after")
    def check_scale(self):
        values = self.allowed_values
        if values != sorted(set(values)) or values[0] != 0 or values[-1] != self.max:
            raise ValueError("scale must be sorted, unique, start at 0 and end at max")
        if self.type in ("binary", "flag") and (values != [0, 1] or self.max != 1):
            raise ValueError("binary and flag use 0/1, max=1")
        if self.type == "flag" and Decimal(self.weight) != 0:
            raise ValueError("flag has zero weight")
        if self.allow_na and (not self.na_condition or not self.na_condition.strip()):
            raise ValueError("N/A requires an explicit condition")
        if not self.allow_na and self.na_condition is not None:
            raise ValueError("na_condition must be null when N/A is forbidden")
        return self


class Checklist(WireModel):
    schema_version: Literal["1.0"]
    id: Text
    version: Annotated[int, Field(ge=1)]
    name: Text
    channel: Literal["agent chat", "voice call", "any"]
    criteria: Annotated[list[Criterion], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_ids(self):
        if len({c.id for c in self.criteria}) != len(self.criteria):
            raise ValueError("duplicate criterion IDs")
        return self


class Evidence(WireModel):
    kind: Literal["message", "absence", "not_applicable"]
    message_ids: list[Text]
    start_message_id: str | None
    end_message_id: str | None
    quote: str | None


class ModelDecision(WireModel):
    criterion_id: Text
    value: int | None
    explanation: Text
    na_reason: str | None
    evidence: Evidence


class ModelOutput(WireModel):
    schema_version: Literal["1.0"]
    results: list[ModelDecision]


class ValidationIssue(WireModel):
    code: Text
    criterion_id: str | None
    path: str
    message: Text


class ValidationReport(WireModel):
    status: Literal["valid", "invalid", "needs_review"]
    errors: list[ValidationIssue]
    evidence_issues: list[ValidationIssue]


class CriterionResult(ModelDecision):
    evidence_status: Literal["verified", "unverified"]
    # Backend-computed values; UI never re-aggregates these.
    weight: DecimalText
    effective_weight: ComputedDecimalText | None
    contribution: ComputedDecimalText | None


class SectionScore(WireModel):
    section: Text
    score: ScoreText | None


class ScoreSnapshot(WireModel):
    status: Literal["completed", "needs_review"]
    results: list[CriterionResult]
    total_score: ScoreText | None
    provisional_total_score: ScoreText | None
    score_unavailable_reason: Literal["unverified_evidence", "no_applicable_weight"] | None
    critical_error: bool | None
    sections: list[SectionScore]
    validation_issues: list[ValidationIssue]

    @model_validator(mode="after")
    def no_unverified_final_score(self):
        if self.status == "needs_review":
            if self.total_score is not None or self.score_unavailable_reason != "unverified_evidence":
                raise ValueError("needs_review cannot expose a confirmed total")
        else:
            if self.provisional_total_score is not None:
                raise ValueError("completed score is not provisional")
            if any(r.evidence_status != "verified" for r in self.results):
                raise ValueError("completed result requires verified evidence")
            if self.total_score is None and self.score_unavailable_reason != "no_applicable_weight":
                raise ValueError("missing total requires no_applicable_weight")
            if self.total_score is not None and self.score_unavailable_reason is not None:
                raise ValueError("confirmed total has no unavailable reason")
        for value in [self.total_score, self.provisional_total_score, *[s.score for s in self.sections]]:
            if value is not None and Decimal(value) > 100:
                raise ValueError("normalized scores must be within 0..100")
        return self


class ReviewDecision(WireModel):
    id: Text
    appeal_id: Text
    criterion_id: Text
    action: Literal["confirm", "change"]
    previous_value: int | None
    final_value: int | None
    reason: Text
    created_at: Text
    revision: Annotated[int, Field(ge=1)]


class Provenance(WireModel):
    provider: Text
    model: Text
    prompt_version: Text
    knowledge_version: Text
    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    cost_usd: DecimalText | None


class Evaluation(WireModel):
    schema_version: Literal["1.0"]
    id: Text
    conversation_id: Text
    conversation_hash: Digest
    checklist: Checklist
    created_at: Text
    revision: Annotated[int, Field(ge=0)]
    original: ScoreSnapshot
    final: ScoreSnapshot
    decisions: list[ReviewDecision]
    provenance: Provenance


class ErrorBody(WireModel):
    code: Literal["INVALID_REQUEST", "INVALID_CHECKLIST", "MODEL_OUTPUT_INVALID", "PROVIDER_RATE_LIMIT", "PROVIDER_UNAVAILABLE", "MODEL_REFUSAL", "NOT_FOUND", "REVISION_CONFLICT", "INTERNAL_ERROR"]
    message: Text
    retryable: bool
    retry_after_seconds: int | None
    details: list[ValidationIssue]


class ErrorResponse(WireModel):
    schema_version: Literal["1.0"]
    request_id: Text
    error: ErrorBody


class EvaluationJob(WireModel):
    schema_version: Literal["1.0"]
    id: Text
    status: Literal["queued", "running", "completed", "needs_review", "failed"]
    evaluation: Evaluation | None
    error: ErrorResponse | None

    @model_validator(mode="after")
    def valid_state(self):
        if self.status in ("queued", "running"):
            if self.evaluation is not None or self.error is not None:
                raise ValueError("pending job has neither evaluation nor error")
        elif self.status == "failed":
            if self.evaluation is not None or self.error is None:
                raise ValueError("failed job has an error and no evaluation")
        elif self.evaluation is None or self.error is not None or self.evaluation.final.status != self.status:
            raise ValueError("finished job must match final snapshot status")
        return self


class EvaluatorInput(WireModel):
    conversation: Conversation
    checklist: Checklist
    knowledge_context: str
    prompt_version: Text
    knowledge_version: Text

    @model_validator(mode="after")
    def matching_channel(self):
        if self.checklist.channel not in ("any", self.conversation.channel):
            raise ValueError("checklist channel does not match the conversation")
        return self


class AdapterResult(WireModel):
    output: ModelOutput
    provenance: Provenance


class Evaluator(Protocol):
    async def evaluate(self, request: EvaluatorInput) -> AdapterResult:
        """Called by backend inside the same process; provider HTTP stays here."""
        ...


def validate_output(output: ModelOutput, checklist: Checklist, conversation: Conversation) -> ValidationReport:
    """Cross-object validation; arithmetic belongs to the backend scoring module."""
    errors: list[ValidationIssue] = []
    evidence_issues: list[ValidationIssue] = []
    def add(target, code, cid, path, message):
        target.append(ValidationIssue(code=code, criterion_id=cid, path=path, message=message))
    criteria = {c.id: c for c in checklist.criteria}
    received = [r.criterion_id for r in output.results]
    for cid in criteria:
        if received.count(cid) != 1:
            add(errors, "CRITERION_COVERAGE", cid, "/results", "Expected exactly one result for each configured criterion")
    messages = {m.id: m for m in conversation.transcript}
    positions = {m.id: i for i, m in enumerate(conversation.transcript)}
    for i, result in enumerate(output.results):
        cid, path = result.criterion_id, f"/results/{i}"
        criterion = criteria.get(cid)
        if criterion is None:
            add(errors, "UNKNOWN_CRITERION", cid, path, "Criterion does not belong to this checklist version")
            continue
        if result.value is None:
            if not criterion.allow_na:
                add(errors, "NA_FORBIDDEN", cid, path + "/value", "N/A is forbidden")
            if not result.na_reason or not result.na_reason.strip():
                add(errors, "NA_REASON_MISSING", cid, path + "/na_reason", "N/A needs a nonempty reason")
            if result.evidence.kind != "not_applicable":
                add(errors, "NA_EVIDENCE_KIND", cid, path + "/evidence/kind", "N/A requires not_applicable evidence")
        else:
            if result.value not in criterion.allowed_values:
                add(errors, "VALUE_NOT_ALLOWED", cid, path + "/value", "Value is not a configured scale step")
            if result.na_reason is not None or result.evidence.kind == "not_applicable":
                add(errors, "NA_INCONSISTENT", cid, path, "Applicable result cannot carry N/A fields")
        ev = result.evidence
        refs = ev.message_ids + [x for x in [ev.start_message_id, ev.end_message_id] if x is not None]
        for mid in refs:
            if mid not in messages:
                add(evidence_issues, "MESSAGE_NOT_FOUND", cid, path + "/evidence", "Evidence refers to an unknown message")
        has_range = ev.start_message_id is not None and ev.end_message_id is not None
        if (ev.start_message_id is None) != (ev.end_message_id is None):
            add(evidence_issues, "RANGE_INCOMPLETE", cid, path + "/evidence", "Both range endpoints are required")
        if has_range and ev.start_message_id in positions and ev.end_message_id in positions:
            if positions[ev.start_message_id] > positions[ev.end_message_id]:
                add(evidence_issues, "RANGE_REVERSED", cid, path + "/evidence", "Evidence range is reversed")
        if ev.kind == "message" and not ev.message_ids:
            add(evidence_issues, "EVIDENCE_MISSING", cid, path + "/evidence", "Message evidence requires a message ID")
        if ev.kind == "absence" and not has_range:
            add(evidence_issues, "EVIDENCE_MISSING", cid, path + "/evidence", "Absence requires a checked range")
        if ev.kind == "not_applicable" and not ev.message_ids and not has_range:
            add(evidence_issues, "EVIDENCE_MISSING", cid, path + "/evidence", "N/A needs an inspected message or range")
        if ev.quote is not None:
            if not ev.quote.strip() or len(ev.message_ids) != 1 or ev.message_ids[0] not in messages or ev.quote not in messages[ev.message_ids[0]].text:
                add(evidence_issues, "QUOTE_MISMATCH", cid, path + "/evidence/quote", "Quote must exactly match a substring of one referenced message")
    return ValidationReport(status="invalid" if errors else "needs_review" if evidence_issues else "valid", errors=errors, evidence_issues=evidence_issues)
