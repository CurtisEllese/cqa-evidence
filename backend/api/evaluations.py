"""Canonical evaluation API; requests never include model-generated scores."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from contracts.v1 import Checklist, EvaluationJob
from backend.application.evaluations import EvaluationService, failure
from backend.application.ports import ProviderFailure
from backend.infrastructure.evaluations import SQLiteEvaluations


class EvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    conversation_id: str = Field(min_length=1)
    checklist_id: str = Field(min_length=1)
    checklist_version: int = Field(ge=1)


def evaluation_router(service: EvaluationService, store: SQLiteEvaluations) -> APIRouter:
    router = APIRouter()

    @router.post("/api/evaluations", response_model=EvaluationJob, status_code=202)
    async def evaluate(body: EvaluateRequest, request: Request):
        try:
            return service.start(body.conversation_id, body.checklist_id, body.checklist_version)
        except ProviderFailure:
            error = failure(request.state.request_id, "PROVIDER_UNAVAILABLE", "Адаптер модели ещё не подключён.", retryable=True)
            return JSONResponse(error.model_dump(), status_code=503)

    @router.get("/api/evaluations/{job_id}", response_model=EvaluationJob)
    def get_evaluation(job_id: str) -> EvaluationJob:
        return store.get_job(job_id)

    @router.get("/api/evaluations/{job_id}/export", response_model=EvaluationJob)
    def export_evaluation(job_id: str) -> JSONResponse:
        return JSONResponse(store.get_job(job_id).model_dump(), headers={"Content-Disposition": 'attachment; filename="cqa-evaluation.json"'})

    @router.post("/api/checklists", response_model=Checklist, status_code=201)
    def create_checklist(checklist: Checklist) -> Checklist:
        return store.save_checklist(checklist, service.clock())

    return router
