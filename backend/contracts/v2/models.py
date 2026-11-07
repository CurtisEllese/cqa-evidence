"""Authoritative v2 wire contracts. Generate JSON Schema and TypeScript from these models."""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

SCHEMA_VERSION = "2.0"
NonEmpty = Annotated[str, StringConstraints(strict=True, min_length=1, pattern=r"\S")]
Identifier = Annotated[NonEmpty, Field(max_length=512)]
DecimalString = Annotated[str, StringConstraints(strict=True, pattern=r"^(0|[1-9][0-9]*)(\.[0-9]{1,6})?$", max_length=32)]
ScoreDecimalString = Annotated[str, StringConstraints(strict=True, pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$", max_length=128)]
NonnegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, ge=1, le=1_000_000)]
Speaker = Literal["operator", "client", "bot", "system"]
Status = Literal["queued", "running", "completed", "needs_review", "failed_validation", "failed", "retryable"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_default=True)


class RootContract(Contract):
    schema_version: Literal["2.0"]


class ValidationIssue(Contract):
    code: NonEmpty
    path: str
    message: NonEmpty


class ApiError(RootContract):
    code: NonEmpty
    message: NonEmpty
    details: list[ValidationIssue] = Field(default_factory=list)
    request_id: Identifier
    retryable: bool


class Criterion(Contract):
    id: Identifier
    title: Annotated[NonEmpty, Field(max_length=500)]
    description: Annotated[NonEmpty, Field(max_length=20_000)]
    section: Annotated[NonEmpty, Field(max_length=500)]
    type: Literal["yes_no", "scale", "flag"]
    weight: DecimalString
    max: DecimalString
    allowedValues: Annotated[list[DecimalString], Field(min_length=2, max_length=1000)]
    allowNA: bool
    naCondition: str | None = None

    @model_validator(mode="after")
    def validate_scale(self):
        weight, maximum = Decimal(self.weight), Decimal(self.max)
        values = [Decimal(value) for value in self.allowedValues]
        if weight > 1_000_000 or not 0 < maximum <= 1_000_000:
            raise ValueError("weight/max must be bounded by 1000000 and max must be positive")
        if len(set(values)) != len(values) or any(value < 0 or value > maximum for value in values):
            raise ValueError("allowedValues must be numerically unique and within 0..max")
        if Decimal(0) not in values or maximum not in values:
            raise ValueError("allowedValues must include 0 and max")
        if self.type in ("yes_no", "flag") and (maximum != 1 or set(values) != {Decimal(0), Decimal(1)}):
            raise ValueError("yes_no/flag require max 1 and values 0/1")
        if self.type == "flag" and (weight != 0 or self.allowNA):
            raise ValueError("flag requires weight 0 and forbids N/A")
        if self.allowNA and (self.naCondition is None or not self.naCondition.strip()):
            raise ValueError("allowNA requires naCondition")
        return self


class Checklist(RootContract):
    id: Identifier
    name: Annotated[NonEmpty, Field(max_length=500)]
    version: PositiveInt
    criteria: Annotated[list[Criterion], Field(min_length=1, max_length=100)]

    @field_validator("criteria")
    @classmethod
    def unique_ids(cls, criteria):
        if len({criterion.id for criterion in criteria}) != len(criteria):
            raise ValueError("criterion IDs must be unique")
        return criteria


class MessageDTO(Contract):
    id: Identifier
    message_index: NonnegativeInt
    timestamp: NonEmpty
    speaker: Speaker
    text: NonEmpty


class KnowledgeFact(Contract):
    text: NonEmpty
    source: NonEmpty
    effective_date: Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]
    product: NonEmpty
    jurisdiction: NonEmpty
    verified_by: NonEmpty


class EvaluationRequest(RootContract):
    conversationId: Identifier
    conversationHash: NonEmpty
    language: Literal["RU", "KZ", "EN"]
    channel: Literal["agent chat", "voice call"]
    checklist: Checklist
    messages: Annotated[list[MessageDTO], Field(min_length=1)]
    knowledgeFacts: list[KnowledgeFact] = Field(default_factory=list)
    repair_feedback: list[ValidationIssue] | None = None

    @field_validator("messages")
    @classmethod
    def unique_messages(cls, messages):
        if len({message.id for message in messages}) != len(messages) or len({message.message_index for message in messages}) != len(messages):
            raise ValueError("message IDs and indices must be unique")
        return messages


class Evidence(Contract):
    kind: Literal["message", "absence", "not_applicable"] = "message"
    message_id: Identifier | None = None
    quote: NonEmpty | None = None
    start_message_id: Identifier | None = None
    end_message_id: Identifier | None = None


class ModelCriterionResult(Contract):
    criterionId: Identifier
    value: DecimalString | None
    explanation: Annotated[NonEmpty, Field(max_length=20_000)]
    applicability_reason: str | None
    evidence: list[Evidence]

    @model_validator(mode="after")
    def applicable_reason(self):
        if self.value is None and (self.applicability_reason is None or not self.applicability_reason.strip()):
            raise ValueError("N/A requires applicability_reason")
        if self.value is not None and Decimal(self.value) > 1_000_000:
            raise ValueError("value exceeds 1000000")
        return self


class ModelResponse(RootContract):
    results: Annotated[list[ModelCriterionResult], Field(min_length=1, max_length=100)]

    @field_validator("results")
    @classmethod
    def unique_results(cls, results):
        if len({result.criterionId for result in results}) != len(results):
            raise ValueError("criterion results must be unique")
        return results


class VerifiedEvidence(Evidence):
    source_text: str | None
    verification_status: Literal["verified", "unverified"]
    reason: str | None

    @model_validator(mode="after")
    def verification_fields(self):
        if self.verification_status == "verified" and (self.source_text is None or self.reason is not None):
            raise ValueError("verified evidence requires source_text and no failure reason")
        if self.verification_status == "unverified" and (self.reason is None or not self.reason.strip()):
            raise ValueError("unverified evidence requires a reason")
        return self


class CriterionResult(ModelCriterionResult):
    evidence: list[VerifiedEvidence]
    critical: bool


class Score(RootContract):
    total: ScoreDecimalString | None
    earned: ScoreDecimalString
    possible: ScoreDecimalString
    applicable: NonnegativeInt
    critical: bool
    results: list[CriterionResult]

    @model_validator(mode="after")
    def confirmed_score(self):
        if self.total is not None and Decimal(self.total) > 100:
            raise ValueError("normalized total must be within 0..100")
        if Decimal(self.possible) == 0 and self.total is not None:
            raise ValueError("zero applicable weight requires a null total")
        if any(not result.evidence for result in self.results) or any(evidence.verification_status != "verified" for result in self.results for evidence in result.evidence):
            raise ValueError("confirmed scores require verified evidence")
        return self


class EvaluationResult(RootContract):
    id: Identifier
    conversationId: Identifier
    conversationHash: NonEmpty
    checklist: Checklist
    results: list[CriterionResult]
    createdAt: NonEmpty
    source: Literal["api"]
    status: Status
    error: ApiError | None
    score: Score | None

    @model_validator(mode="after")
    def status_score(self):
        if self.status != "completed" and self.score is not None:
            raise ValueError("noncompleted evaluations must not expose a score")
        if self.status == "completed":
            if self.score is None:
                raise ValueError("completed evaluations require a score")
            if {result.criterionId for result in self.results} != {criterion.id for criterion in self.checklist.criteria} or len(self.results) != len(self.checklist.criteria):
                raise ValueError("completed results must cover the checklist exactly")
            if any(not result.evidence for result in self.results) or any(evidence.verification_status != "verified" for result in self.results for evidence in result.evidence):
                raise ValueError("completed evaluations require verified evidence")
        return self


class ConversationScores(Contract):
    original: Score
    final: Score
    sections: dict[str, Score]


class ConversationDTO(RootContract):
    id: Identifier
    title: NonEmpty
    operator: NonEmpty
    initials: NonEmpty
    language: Literal["RU", "KZ", "EN"]
    channel: Literal["agent chat", "voice call"]
    date: str | None
    dateSource: Literal["conversation", "import"] | None = None
    duration: NonEmpty
    transcript: Annotated[list[MessageDTO], Field(min_length=1)]
    audioUrl: str | None
    audioPageUrl: str | None = None
    contentHash: NonEmpty
    evaluation: EvaluationResult | None
    scores: ConversationScores | None = None


class AppealDTO(RootContract):
    id: Identifier
    conversationId: Identifier
    criterionId: Identifier
    evaluationId: Identifier
    comment: NonEmpty
    status: Literal["pending", "accepted", "rejected"]
    createdAt: NonEmpty
    originalValue: DecimalString | None
    decision: str | None = None
    newValue: DecimalString | None = None
    decidedAt: str | None = None


class DatasetInfo(Contract):
    group: NonEmpty
    fetchedAt: NonEmpty
    source: Literal["live", "cache"]
    total: NonnegativeInt
    conversationIds: list[Identifier]


class WorkspaceData(Contract):
    conversations: list[ConversationDTO]
    checklist: Checklist
    appeals: list[AppealDTO]


class WorkspaceState(RootContract):
    data: WorkspaceData
    dataset: DatasetInfo | None
    evaluationAvailable: bool
    revision: NonnegativeInt
    imported: NonnegativeInt | None = None


class VersionRequest(RootContract):
    checklist: Checklist
    expectedVersion: PositiveInt


class ImportRequest(RootContract):
    text: NonEmpty


class MigrationRequest(RootContract):
    # Transitional, untrusted legacy browser shape; repository validates every field.
    data: dict[str, object]


class CreateAppealRequest(RootContract):
    conversationId: Identifier
    evaluationId: Identifier
    criterionId: Identifier
    comment: Annotated[NonEmpty, Field(min_length=10, max_length=2000)]


class DecideAppealRequest(RootContract):
    status: Literal["accepted", "rejected"]
    decision: Annotated[str, Field(max_length=2000)] = ""
    newValue: DecimalString | None = None

    @model_validator(mode="after")
    def accepted_value_present(self):
        if self.status == "accepted" and "newValue" not in self.model_fields_set:
            raise ValueError("accepted decisions require newValue (null is an explicit N/A)")
        return self


class StartEvaluationRequest(RootContract):
    conversationId: Identifier


class RefreshRequest(RootContract):
    groupId: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=512)] = "DEV"


class ConversationPage(RootContract):
    total: NonnegativeInt
    items: list[ConversationDTO]


class Health(RootContract):
    status: Literal["ok"]
    database: Literal["sqlite"]
    evaluationAvailable: bool


class ConversationExport(RootContract):
    conversation: ConversationDTO
    appeals: list[AppealDTO]


MODELS = {
    name: value for name, value in list(globals().items())
    if isinstance(value, type) and issubclass(value, Contract) and value not in (Contract, RootContract)
}

