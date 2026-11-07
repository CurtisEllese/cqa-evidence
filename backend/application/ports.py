"""Persistence and runtime ports; provider-specific code belongs to the AI adapter."""
from typing import Callable, Protocol
from contracts.v1 import Checklist, Evaluation, EvaluationJob, EvaluatorInput, ModelOutput

Clock = Callable[[], str]
IdentifierFactory = Callable[[], str]


class EvaluationStore(Protocol):
    def request(self, conversation_id: str, checklist_id: str, version: int,
                knowledge_context: str, prompt_version: str, knowledge_version: str) -> EvaluatorInput: ...
    def create_job(self, job: EvaluationJob, request: EvaluatorInput, created_at: str) -> None: ...
    def get_job(self, job_id: str) -> EvaluationJob: ...
    def update_job(self, job: EvaluationJob) -> None: ...
    def finish_job(self, job_id: str, evaluation: Evaluation, output: ModelOutput,
                   engine_version: str) -> EvaluationJob: ...
    def recover_interrupted(self, make_error: Callable[[str], EvaluationJob]) -> None: ...
    def save_checklist(self, checklist: Checklist, created_at: str) -> Checklist: ...


class ProviderFailure(Exception):
    """Safe adapter signal, deliberately without the provider's response or credentials."""
    def __init__(self, code: str = "PROVIDER_UNAVAILABLE", retry_after_seconds: int | None = None) -> None:
        if code not in ("PROVIDER_UNAVAILABLE", "PROVIDER_RATE_LIMIT", "MODEL_REFUSAL", "MODEL_OUTPUT_INVALID"):
            raise ValueError("Unsupported provider failure code")
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(code)
