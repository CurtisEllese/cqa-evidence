"""Combined HTTP/adapter/storage checks; only the SDK transport is synthetic."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from google.genai import errors, types

from backend.app import create_app
from backend.contracts.v2 import EvaluationResult, WorkspaceState
from evaluator.settings import EvaluatorSettings
from evaluator.v2 import GeminiV2Evaluator


@pytest.mark.parametrize("first_failure", ["invalid_json", "rate_limit"])
def test_gemini_v2_http_retry_and_process_state_survive_restart(tmp_path, monkeypatch, first_failure):
    monkeypatch.delenv("EVALUATOR_ADAPTER", raising=False)
    fixture = json.loads((Path(__file__).resolve().parents[2] / "evaluator/fixtures/synthetic_v2.json").read_text())[0]
    source = fixture["request"]

    class SyntheticTransport:
        def __init__(self):
            self.models = self
            self.calls = []

        async def generate_content(self, **kwargs):
            request = json.loads(kwargs["contents"])
            self.calls.append(request)
            if len(self.calls) == 1 and first_failure == "rate_limit":
                raise errors.APIError(429, {"error": {"message": "Synthetic rate limit"}})
            decisions = [{
                "criterionId": criterion["id"], "value": fixture["expected"][criterion["id"]],
                "explanation": "Synthetic integration-test decision.",
                "applicability_reason": "Synthetic N/A reason." if fixture["expected"][criterion["id"]] is None else None,
                "evidence": [{"kind": "message", "message_id": request["messages"][1]["id"], "quote": None}],
            } for criterion in request["checklist"]["criteria"]]
            text = "malformed" if len(self.calls) == 1 else json.dumps({"schema_version": "2.0", "results": decisions})
            return types.GenerateContentResponse(candidates=[types.Candidate(
                finish_reason="STOP", content=types.Content(parts=[types.Part(text=text)]),
            )])

    transport = SyntheticTransport()
    adapter = GeminiV2Evaluator(EvaluatorSettings("unused-test-key", "synthetic-model", min_interval_seconds=0), client=transport)
    database, cache = tmp_path / "integration.sqlite", tmp_path / "missing.json"
    app = create_app(database, cache, organizer_url="", evaluator=adapter.evaluate)
    record = {
        "conversation_id": source["conversationId"], "language": source["language"], "channel": source["channel"],
        "audio_url": None, "transcript": [{key: message[key] for key in ("timestamp", "speaker", "text")} for message in source["messages"]],
    }
    with TestClient(app) as client:
        assert client.post("/api/import", json={"schema_version": "2.0", "text": json.dumps([record])}).status_code == 200
        posted = client.post("/api/evaluations", json={"schema_version": "2.0", "conversationId": record["conversation_id"]})
        assert posted.status_code == 202
        identifier = posted.json()["id"]
        result = EvaluationResult.model_validate(client.get(f"/api/evaluations/{identifier}").json())
        assert result.status == "completed" and result.score.total == "100"
        assert all(evidence.verification_status == "verified" for decision in result.results for evidence in decision.evidence)
        assert len(transport.calls) == 2
        assert bool(transport.calls[1]["repair_feedback"]) == (first_failure == "invalid_json")
        with app.state.repo.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM evaluation_attempts WHERE job_id=?", (identifier,)).fetchone()[0] == 2

    with TestClient(create_app(database, cache, organizer_url="")) as restarted:
        assert EvaluationResult.model_validate(restarted.get(f"/api/evaluations/{identifier}").json()) == result
        state = WorkspaceState.model_validate(restarted.get("/api/state").json())
        assert state.data.conversations[0].evaluation == result
        assert not state.evaluationAvailable and len(transport.calls) == 2
