"""Explicit provider selection. No silent fallback to a different provider."""
import os
from backend.contracts.v2 import EvaluationRequest, ModelResponse
from backend.evaluator import AdapterFailure


async def evaluate(request: EvaluationRequest) -> ModelResponse:
    provider = os.getenv("LLM_PROVIDER", "openai")
    if provider == "openai":
        from .openai_v2 import evaluate as selected
    elif provider == "gemini":
        from .v2 import evaluate as selected
    else:
        raise AdapterFailure("PROVIDER_UNAVAILABLE", retryable=False)
    return await selected(request)
