"""Load the expert-approved server file without exposing its contents in errors."""
from pathlib import Path
from datetime import date
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from pydantic import TypeAdapter, ValidationError, model_validator

from .contracts.v2 import KnowledgeFact
from .contracts.v2.models import Contract, NonEmpty, KnowledgeScope, KnowledgeAudit

MAX_KNOWLEDGE_BYTES = 1024 * 1024


class KnowledgeConfigurationError(ValueError):
    """An explicitly configured, unusable file must not become an empty reference."""


def load_knowledge_facts(path: str | Path | None) -> tuple[KnowledgeFact, ...]:
    if path is None:
        return ()
    try:
        with Path(path).open("rb") as source:
            raw = source.read(MAX_KNOWLEDGE_BYTES + 1)
    except OSError:
        raise KnowledgeConfigurationError("KNOWLEDGE_FACTS_PATH: не удалось прочитать файл фактов.") from None
    if len(raw) > MAX_KNOWLEDGE_BYTES:
        raise KnowledgeConfigurationError("KNOWLEDGE_FACTS_PATH: файл превышает 1 МБ.")
    try:
        facts = TypeAdapter(list[KnowledgeFact]).validate_json(raw.decode("utf-8-sig"))
        for fact in facts:
            date.fromisoformat(fact.effective_date)
    except (ValidationError, ValueError):
        # Pydantic's original error includes the rejected input, possibly private facts.
        raise KnowledgeConfigurationError(
            "KNOWLEDGE_FACTS_PATH: ожидается JSON-массив KnowledgeFact с корректными датами действия."
        ) from None
    return tuple(facts)


# Public integration alias; the shared contract stores the same verified metadata.
KnowledgeContext = KnowledgeScope


class ScopedKnowledgeFact(Contract):
    """Separate review schema. An observed web page is not an approved runtime fact."""
    id: NonEmpty
    text: NonEmpty
    source: NonEmpty
    source_locator: NonEmpty
    source_published_at: str | None
    observed_at: str
    entity: NonEmpty
    jurisdiction: NonEmpty
    product: NonEmpty
    valid_from: str | None
    valid_to: str | None
    validity_evidence: str | None
    review_status: Literal["candidate", "source_verified", "expert_approved"]
    verified_by: str | None
    approval_reference: str | None
    limits: NonEmpty

    @model_validator(mode="after")
    def provenance(self):
        url = urlparse(self.source)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("source must be an HTTPS reference without credentials")
        for field in (self.observed_at, self.source_published_at, self.valid_from, self.valid_to):
            if field is not None:
                if date.fromisoformat(field).isoformat() != field:
                    raise ValueError("dates must be YYYY-MM-DD")
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("reversed validity interval")
        if self.review_status == "expert_approved" and any(not v or not v.strip() for v in
                (self.verified_by, self.approval_reference, self.valid_from, self.valid_to, self.validity_evidence)):
            raise ValueError("approval needs expert, receipt, proven start and end of applicability")
        return self


@dataclass(frozen=True)
class KnowledgeSelection:
    facts: tuple[KnowledgeFact, ...]
    included_ids: tuple[str, ...]
    excluded: tuple[dict[str, str], ...]


def select_applicable_facts(records: tuple[ScopedKnowledgeFact, ...],
                            context: KnowledgeContext | None) -> KnowledgeSelection:
    """Fail closed before model input. Dates are inclusive; no implicit open-ended period.

    The caller owns verification of the metadata and approval receipts. A free-text
    name in verified_by is not cryptographic proof of an expert or of applicability.
    """
    accepted, ids, excluded = [], [], []
    if len({record.id for record in records}) != len(records):
        raise KnowledgeConfigurationError("Duplicate scoped knowledge fact ID")
    for record in records:
        reason = None
        if context is None:
            reason = "conversation_context_unverified"
        elif record.review_status != "expert_approved":
            reason = "expert_approval_missing"
        elif (record.entity, record.jurisdiction, record.product) != (context.entity, context.jurisdiction, context.product):
            reason = "entity_jurisdiction_or_product_mismatch"
        elif not record.valid_from or not record.valid_to or not record.validity_evidence:
            reason = "effective_period_unverified"
        elif not record.valid_from <= context.conversation_date <= record.valid_to:
            reason = "outside_proven_effective_period"
        if reason:
            excluded.append({"id": record.id, "reason": reason})
        else:
            ids.append(record.id)
            accepted.append(KnowledgeFact(text=record.text + "\nОграничения: " + record.limits,
                source=record.source + " (" + record.source_locator + ")",
                effective_date=record.valid_from, product=record.product,
                jurisdiction=record.jurisdiction, verified_by=record.verified_by))
    return KnowledgeSelection(tuple(accepted), tuple(ids), tuple(excluded))


def load_scoped_knowledge(path: str | Path) -> tuple[ScopedKnowledgeFact, ...]:
    """Load review catalog without accidentally accepting the legacy runtime schema."""
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_KNOWLEDGE_BYTES + 1)
        if len(raw) > MAX_KNOWLEDGE_BYTES:
            raise ValueError("too large")
        records = TypeAdapter(list[ScopedKnowledgeFact]).validate_json(raw.decode("utf-8-sig"))
        if len({r.id for r in records}) != len(records):
            raise ValueError("duplicate ID")
        return tuple(records)
    except (OSError, ValueError):
        raise KnowledgeConfigurationError("Invalid scoped knowledge review catalog") from None


def evaluation_knowledge(records: tuple[ScopedKnowledgeFact, ...], context: KnowledgeContext | None,
                         *, legacy_count: int = 0) -> tuple[list[KnowledgeFact], KnowledgeAudit]:
    """One server-side selection before the job snapshot is persisted."""
    selection = select_applicable_facts(records, context)
    excluded_count = legacy_count + len(selection.excluded)
    status = "scoped_verified" if selection.facts else "excluded" if records or legacy_count else "not_configured"
    reason = ("Applicable expert-approved facts selected for confirmed conversation scope."
              if selection.facts else "legacy_scope_unverified: legacy facts lack independently confirmed applicability."
              if legacy_count else "No applicable expert-approved facts for confirmed conversation scope."
              if records else "No product knowledge configured; assess observable behavior and disclose factual uncertainty.")
    return list(selection.facts), KnowledgeAudit(status=status, reason=reason, scope=context,
        included_fact_ids=list(selection.included_ids), excluded_fact_count=excluded_count)


def knowledge_request_is_consistent(request) -> bool:
    """Check the server selection receipt, without silently changing provider input."""
    audit = request.knowledge_context
    if not request.knowledgeFacts:
        return audit is None or audit.status != "scoped_verified"
    if audit is None or audit.status != "scoped_verified" or audit.scope is None:
        return False
    if len(request.knowledgeFacts) != len(audit.included_fact_ids):
        return False
    return all(f.product == audit.scope.product and f.jurisdiction == audit.scope.jurisdiction
               and f.effective_date <= audit.scope.conversation_date for f in request.knowledgeFacts)
