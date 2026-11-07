"""Validate adapter results and turn them into immutable, server-scored jobs."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from pydantic import ValidationError
from contracts.v1 import (
    AdapterResult, Checklist, CriterionResult, ErrorBody, ErrorResponse, Evaluation,
    EvaluationJob, Evaluator, EvaluatorInput, ModelOutput, ScoreSnapshot,
    SectionScore, ValidationIssue, validate_output,
)
from backend.domain.scoring import CriterionDefinition, ENGINE_VERSION, ScoringEngine, decimal_text
from .ports import Clock, EvaluationStore, IdentifierFactory, ProviderFailure


def failure(request_id: str, code: str, message: str, *, retryable: bool = False,
            details: list[ValidationIssue] | None = None, retry_after: int | None = None) -> ErrorResponse:
    return ErrorResponse(schema_version="1.0", request_id=request_id, error=ErrorBody(
        code=code, message=message, retryable=retryable, retry_after_seconds=retry_after, details=details or [],
    ))


def score(request: EvaluatorInput, output: ModelOutput, engine: ScoringEngine) -> ScoreSnapshot:
    report = validate_output(output, request.checklist, request.conversation)
    if report.errors:
        raise InvalidOutput(report.errors)
    definitions = tuple(CriterionDefinition(
        id=c.id, kind=c.type, weight=Decimal(c.weight), maximum=c.max,
        allowed_values=tuple(c.allowed_values), allow_na=c.allow_na, section=c.section,
    ) for c in request.checklist.criteria)
    calculated = engine.calculate(definitions, {r.criterion_id: r.value for r in output.results})
    contributions = {c.criterion_id: c for c in calculated.contributions}
    unverified = {issue.criterion_id for issue in report.evidence_issues}
    decisions = {r.criterion_id: r for r in output.results}
    messages = {m.id: m.text for m in request.conversation.transcript}
    results: list[CriterionResult] = []
    for criterion in request.checklist.criteria:
        decision = decisions[criterion.id]
        evidence = decision.evidence.model_copy(deep=True)
        # Resolve full original text on the server; never manufacture missing quotes.
        if evidence.quote is None and len(evidence.message_ids) == 1 and evidence.message_ids[0] in messages:
            evidence.quote = messages[evidence.message_ids[0]]
        contribution = contributions[criterion.id]
        results.append(CriterionResult(
            **{**decision.model_dump(), "evidence": evidence.model_dump()},
            evidence_status="unverified" if criterion.id in unverified else "verified",
            weight=criterion.weight, effective_weight=decimal_text(contribution.effective_weight),
            contribution=decimal_text(contribution.contribution),
        ))
    flags = [c for c in request.checklist.criteria if c.type == "flag"]
    critical = None if any(c.id in unverified for c in flags) else any(decisions[c.id].value == 1 for c in flags)
    bad_sections = {c.section for c in request.checklist.criteria if c.id in unverified}
    needs_review = bool(report.evidence_issues)
    return ScoreSnapshot(
        status="needs_review" if needs_review else "completed", results=results,
        total_score=None if needs_review else decimal_text(calculated.total),
        provisional_total_score=decimal_text(calculated.total) if needs_review else None,
        score_unavailable_reason="unverified_evidence" if needs_review else "no_applicable_weight" if calculated.total is None else None,
        critical_error=critical,
        sections=[SectionScore(section=name, score=None if name in bad_sections else decimal_text(value)) for name, value in calculated.sections],
        validation_issues=report.evidence_issues,
    )


class InvalidOutput(Exception):
    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__("Model output violates the checklist")


class EvaluationService:
    def __init__(self, store: EvaluationStore, evaluator: Evaluator | None, clock: Clock,
                 identifiers: IdentifierFactory, *, knowledge_context: str = "",
                 prompt_version: str = "v1", knowledge_version: str = "none",
                 concurrency: int = 2, timeout_seconds: float = 120) -> None:
        if concurrency < 1 or timeout_seconds <= 0:
            raise ValueError("Concurrency and timeout must be positive")
        self.store, self.evaluator, self.clock, self.identifiers = store, evaluator, clock, identifiers
        self.knowledge_context, self.prompt_version, self.knowledge_version = knowledge_context, prompt_version, knowledge_version
        self.engine = ScoringEngine()
        self._limit = asyncio.Semaphore(concurrency)
        self._timeout = timeout_seconds
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def available(self) -> bool:
        return self.evaluator is not None

    def start(self, conversation_id: str, checklist_id: str, version: int) -> EvaluationJob:
        if self.evaluator is None:
            raise ProviderFailure()
        request = self.store.request(conversation_id, checklist_id, version, self.knowledge_context,
                                     self.prompt_version, self.knowledge_version)
        job = EvaluationJob(schema_version="1.0", id=self.identifiers(), status="queued", evaluation=None, error=None)
        self.store.create_job(job, request, self.clock())
        task = asyncio.create_task(self._run(job.id, request))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    def failed(self, job_id: str, error: ErrorResponse) -> EvaluationJob:
        return EvaluationJob(schema_version="1.0", id=job_id, status="failed", evaluation=None, error=error)

    def recover(self) -> None:
        self.store.recover_interrupted(lambda job_id: self.failed(job_id, failure(
            job_id, "PROVIDER_UNAVAILABLE", "Обработка прервана перезапуском. Запустите оценку повторно.", retryable=True,
        )))

    async def close(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, job_id: str, request: EvaluatorInput) -> None:
        try:
            async with self._limit:
                self.store.update_job(EvaluationJob(schema_version="1.0", id=job_id, status="running", evaluation=None, error=None))
                async with asyncio.timeout(self._timeout):
                    # One invocation here: the adapter owns the shared two-provider-call budget.
                    response = await self.evaluator.evaluate(request.model_copy(deep=True))
                result = AdapterResult.model_validate(response.model_dump() if isinstance(response, AdapterResult) else response)
                snapshot = score(request, result.output, self.engine)
                evaluation = Evaluation(
                    schema_version="1.0", id=job_id, conversation_id=request.conversation.id,
                    conversation_hash=request.conversation.content_hash, checklist=request.checklist,
                    created_at=self.clock(), revision=0, original=snapshot, final=snapshot.model_copy(deep=True),
                    decisions=[], provenance=result.provenance,
                )
                self.store.finish_job(job_id, evaluation, result.output, ENGINE_VERSION)
                return
        except InvalidOutput as error:
            safe_error = failure(job_id, "MODEL_OUTPUT_INVALID", "Ответ модели не соответствует чек-листу.", details=error.issues)
        except ValidationError as error:
            issues = [ValidationIssue(code="INVALID_TYPE", criterion_id=None,
                      path="/" + "/".join(map(str, item["loc"])), message="Ответ не соответствует схеме.")
                      for item in error.errors(include_input=False, include_url=False)]
            safe_error = failure(job_id, "MODEL_OUTPUT_INVALID", "Ответ модели не соответствует схеме.", details=issues)
        except ProviderFailure as error:
            safe_error = failure(job_id, error.code, "Провайдер не вернул пригодный для оценки ответ.",
                                 retryable=error.code in ("PROVIDER_UNAVAILABLE", "PROVIDER_RATE_LIMIT"), retry_after=error.retry_after_seconds)
        except (TimeoutError, asyncio.CancelledError):
            safe_error = failure(job_id, "PROVIDER_UNAVAILABLE", "Обработка прервана или превышено время ожидания.", retryable=True)
        except Exception:
            # Provider exception text can contain credentials, URLs and transcripts.
            safe_error = failure(job_id, "INTERNAL_ERROR", "Не удалось завершить оценку. Повторите запрос.", retryable=True)
        self.store.update_job(self.failed(job_id, safe_error))
