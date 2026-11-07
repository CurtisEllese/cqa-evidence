"""Organizer groups, transactional preservation and audio provenance; no external calls."""
from copy import deepcopy
from dataclasses import dataclass, field
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.contracts.v2 import ApiError, WorkspaceState
from backend.tests.test_evaluation_api import model_output


def wire(**fields):
    return {"schema_version": "2.0", **fields}


def record(identifier, *, audio=None):
    return {
        "conversation_id": identifier, "channel": "agent chat", "language": "RU", "audio_url": audio,
        "transcript": [
            {"speaker": "client", "timestamp": "00:00", "text": "Синтетический вопрос для проверки загрузки."},
            {"speaker": "operator", "timestamp": "00:05", "text": "Здравствуйте! Синтетический ответ."},
        ],
    }


def dataset(group, count):
    return {"isSuccess": True, "data": {
        "dataset_group_id": group, "total_records": count,
        "records": [record(f"{group}-{i}") for i in range(count)],
    }}


@dataclass
class Organizer:
    payload: object = field(default_factory=lambda: dataset("CONTROL", 10))
    requests: list = field(default_factory=list)
    status: int = 200
    raw: bytes | None = None
    error: type[httpx.HTTPError] | None = None

    def __call__(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error("Synthetic upstream failure: signed-secret", request=request)
        if self.raw is not None:
            return httpx.Response(self.status, content=self.raw)
        return httpx.Response(self.status, json=self.payload)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("EVALUATOR_ADAPTER", "")
    organizer, calls = Organizer(), []
    async_client = httpx.AsyncClient
    monkeypatch.setattr("backend.app.httpx.AsyncClient", lambda **kwargs: async_client(
        transport=httpx.MockTransport(organizer), **kwargs,
    ))

    async def adapter(request):
        calls.append(request)
        return model_output(request)

    paths = (tmp_path / "groups.sqlite", tmp_path / "missing-cache.json")
    app = create_app(*paths, organizer_url="https://organizer.example/data?sig=signed-secret&group_id=DEV", evaluator=adapter)
    with TestClient(app) as client:
        yield client, app.state.repo, organizer, calls, paths


def refresh(client, group="CONTROL"):
    return client.post("/api/organizer/refresh", json=wire(groupId=group))


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    error = ApiError.model_validate(response.json())
    assert error.code == code
    assert "signed-secret" not in response.text and "https://" not in response.text
    return error


@pytest.mark.parametrize("group,count", [("DEV", 65), ("CONTROL", 10), ("OTHER", 3), ("EMPTY", 0)])
def test_group_size_metadata_and_no_automatic_evaluation(setup, group, count):
    client, repo, organizer, calls, _ = setup
    organizer.payload = dataset(group, count)
    response = refresh(client, group)
    assert response.status_code == 200, response.text
    state = WorkspaceState.model_validate(response.json())
    assert state.dataset.group == group and state.dataset.total == count
    assert state.dataset.conversationIds == [r["conversation_id"] for r in organizer.payload["data"]["records"]]
    assert len(state.data.conversations) == count and not calls
    assert len(organizer.requests) == 1
    assert organizer.requests[0].url.params["group_id"] == group
    assert organizer.requests[0].url.params["sig"] == "signed-secret"
    assert "signed-secret" not in response.text
    # Empty is a real loaded group; state must not silently fetch DEV afterwards.
    assert client.get("/api/state").status_code == 200 and len(organizer.requests) == 1


def test_old_request_defaults_to_dev_and_group_is_encoded_as_data(setup):
    client, _, organizer, _, _ = setup
    organizer.payload = dataset("DEV", 2)
    assert client.post("/api/organizer/refresh", json=wire()).status_code == 200
    group = "бақылау / final + 10?&sig=other"
    organizer.payload = dataset(group, 1)
    # Keep the synthetic conversation ID independent from a group's arbitrary syntax.
    organizer.payload["data"]["records"][0]["conversation_id"] = "encoded-group-test"
    assert refresh(client, "  " + group + "  ").status_code == 200
    request = organizer.requests[-1]
    assert request.url.host == "organizer.example"
    assert request.url.params.get_list("group_id") == [group]
    assert request.url.params.get_list("sig") == ["signed-secret"]


@pytest.mark.parametrize("group", [None, True, 10, "", "   ", "x" * 513])
def test_invalid_group_rejected_before_network(setup, group):
    client, repo, organizer, _, _ = setup
    before = repo.state()
    error = assert_error(refresh(client, group), 422, "INVALID_REQUEST")
    assert error.details[0].path == "/body/groupId"
    assert not organizer.requests and repo.state() == before


@pytest.mark.parametrize("bad", [
    {"isSuccess": False}, {"isSuccess": 1}, {"data": None},
    {"dataset_group_id": "WRONG"}, {"total_records": 9}, {"total_records": 10.0},
    {"total_records": True}, {"total_records": "10"}, {"total_records": -1}, {"records": {}},
    {"records": [record("duplicate"), record(" duplicate ")], "total_records": 2},
    {"records": [record("valid-before-error"), {**record("bad"), "language": "UNKNOWN"}], "total_records": 2},
])
def test_malformed_envelope_preserves_entire_database(setup, bad):
    client, repo, organizer, _, _ = setup
    repo.import_data([record("previous-manual")])
    before = repo.state()
    if "isSuccess" in bad or "data" in bad:
        organizer.payload.update(bad)
    else:
        organizer.payload["data"].update(bad)
    assert_error(refresh(client), 502, "UPSTREAM_ERROR")
    assert repo.state() == before


@pytest.mark.parametrize("failure", ["timeout", "network", "rate_limit", "server_error", "redirect", "invalid_json", "too_large"])
def test_upstream_errors_are_sanitized_and_atomic(setup, failure):
    client, repo, organizer, _, _ = setup
    repo.import_data([record("previous-manual")])
    before = repo.state()
    if failure in ("timeout", "network"):
        organizer.error = httpx.ReadTimeout if failure == "timeout" else httpx.ConnectError
    elif failure in ("rate_limit", "server_error", "redirect"):
        organizer.status = {"rate_limit": 429, "server_error": 500, "redirect": 302}[failure]
    else:
        organizer.raw = b"not JSON: signed-secret" if failure == "invalid_json" else b"x" * (5 * 1024 * 1024 + 1)
    assert_error(refresh(client), 502, "UPSTREAM_ERROR")
    assert repo.state() == before and len(organizer.requests) == 1


@pytest.mark.parametrize("change", ["text", "language", "audio"])
def test_id_collision_returns_409_before_any_inserts(setup, change):
    client, repo, organizer, _, _ = setup
    existing = record("existing")
    repo.import_data([existing])
    changed = deepcopy(existing)
    if change == "text":
        changed["transcript"][0]["text"] = "Изменённый текст"
    elif change == "language":
        changed["language"] = "KZ"
    else:
        changed["audio_url"] = "https://organizer.example/different-audio"
    organizer.payload["data"].update(records=[record("must-not-be-inserted"), changed], total_records=2)
    before = repo.state()
    assert_error(refresh(client), 409, "CONFLICT")
    assert repo.state() == before
    manual = client.post("/api/import", json=wire(text=json.dumps([record("also-not-inserted"), changed])))
    assert_error(manual, 409, "CONFLICT")
    assert repo.state() == before


def test_evaluation_appeal_manual_import_and_groups_survive_refresh_and_restart(setup):
    client, repo, organizer, calls, paths = setup
    repo.import_data([record("manual-kept")])
    organizer.payload = dataset("DEV", 65)
    assert refresh(client, "DEV").status_code == 200
    job = client.post("/api/evaluations", json=wire(conversationId="DEV-0")).json()
    original = client.get("/api/evaluations/" + job["id"]).json()
    assert original["status"] == "completed" and original["score"]["total"] == "85.38"
    created = client.post("/api/appeals", json=wire(
        conversationId="DEV-0", evaluationId=job["id"], criterionId="gross-errors",
        comment="Синтетическая проверка сохранности апелляции.",
    ))
    assert created.status_code == 200
    appeal = created.json()["data"]["appeals"][0]
    decision = client.post("/api/appeals/" + appeal["id"] + "/decision", json=wire(
        status="accepted", newValue="1", decision="Синтетическое подтверждённое решение проверяющего.",
    ))
    assert decision.status_code == 200
    saved = client.get("/api/conversations/DEV-0").json()

    organizer.payload = dataset("CONTROL", 10)
    assert refresh(client).status_code == 200
    assert refresh(client).status_code == 200
    assert len(calls) == 1  # Reimport never invokes Gemini or changes its original answer.
    state = client.get("/api/export").json()
    assert len(state["data"]["conversations"]) == 76
    assert state["dataset"]["group"] == "CONTROL" and state["dataset"]["total"] == 10
    assert client.get("/api/conversations/manual-kept").status_code == 200
    assert client.get("/api/conversations/DEV-0").json() == saved
    assert client.get("/api/evaluations/" + job["id"]).json() == original
    with repo.connect() as db:
        assert repo.metadata(db, "dataset:DEV")["total"] == 65
        assert repo.metadata(db, "dataset:CONTROL")["total"] == 10
        assert db.execute("SELECT COUNT(*) FROM evaluation_jobs").fetchone()[0] == 1

    with TestClient(create_app(*paths, organizer_url="")) as restarted:
        expected = deepcopy(state)
        expected["evaluationAvailable"] = False
        assert restarted.get("/api/export").json() == expected
        assert restarted.get("/api/conversations/DEV-0").json() == saved
        assert restarted.get("/api/evaluations/" + job["id"]).json() == original


def test_same_group_smaller_refresh_and_overlapping_group_keep_prior_records(setup):
    client, repo, organizer, _, _ = setup
    organizer.payload = dataset("DEV", 3)
    assert refresh(client, "DEV").status_code == 200
    organizer.payload = dataset("DEV", 1)
    assert refresh(client, "DEV").status_code == 200
    organizer.payload = dataset("CONTROL", 1)
    organizer.payload["data"]["records"] = [record("DEV-0")]
    assert refresh(client).status_code == 200
    state = repo.state()
    assert len(state["data"]["conversations"]) == 3
    assert state["dataset"]["conversationIds"] == ["DEV-0"]
    with repo.connect() as db:
        assert db.execute("SELECT source FROM conversations WHERE id='DEV-0'").fetchone()[0] == "organizer:DEV"


def test_ten_new_dialogues_are_evaluated_and_exported_with_test_adapter(setup):
    client, _, organizer, calls, _ = setup
    for i, row in enumerate(organizer.payload["data"]["records"]):
        row["language"] = ["RU", "KZ", "EN"][i % 3]
    response = refresh(client)
    assert response.status_code == 200
    identifiers = response.json()["dataset"]["conversationIds"]
    for identifier in identifiers:
        posted = client.post("/api/evaluations", json=wire(conversationId=identifier))
        assert posted.status_code == 202
        result = client.get("/api/evaluations/" + posted.json()["id"]).json()
        assert result["status"] == "completed" and result["score"]["total"] == "85.38"
    exported = WorkspaceState.model_validate(client.get("/api/export").json())
    assert len(calls) == 10 and len(exported.data.conversations) == 10
    assert all(c.evaluation.status == "completed" for c in exported.data.conversations)
    assert {request.language for request in calls} == {"RU", "KZ", "EN"}


def test_control_audio_uses_existing_trust_boundary_and_manual_never_gains_trust(setup, monkeypatch):
    client, repo, organizer, _, _ = setup
    manual = record("manual-audio", audio="https://organizer.example/player?sig=manual")
    repo.import_data([manual])
    organizer.payload["data"].update(total_records=3, records=[
        record("control-audio", audio="https://organizer.example/player?sig=signed-secret"),
        record("foreign-audio", audio="https://other.example/player"), manual,
    ])
    assert refresh(client).status_code == 200
    control = client.get("/api/conversations/control-audio").json()
    assert control["audioUrl"] == "/api/conversations/control-audio/audio"
    assert control["audioPageUrl"] == "/api/conversations/control-audio/audio-page"
    assert repo.audio_source("control-audio")[1] is True
    assert repo.audio_source("manual-audio")[1] is False
    # Invalid origin and manually imported audio are rejected before any HTTP/DNS.
    requests_before = len(organizer.requests)
    assert client.get("/api/conversations/manual-audio/audio").status_code == 403
    assert client.get("/api/conversations/foreign-audio/audio").status_code == 403
    assert len(organizer.requests) == requests_before

    forwarded = []
    async def proxy(url, host, range_header, trusted):
        from starlette.responses import Response
        forwarded.append((url, host, range_header, trusted))
        return Response(b"synthetic-audio", status_code=206, media_type="audio/mpeg")
    monkeypatch.setattr("backend.app.audio_response", proxy)
    assert client.get(control["audioUrl"], headers={"Range": "bytes=0-99"}).status_code == 206
    assert forwarded[0][1:] == ("organizer.example", "bytes=0-99", True)
    assert "signed-secret" not in client.get(control["audioPageUrl"]).text
    # Existing SQLite rows with the pre-upgrade DEV source keep their proxy behavior.
    with repo.connect(write=True) as db:
        db.execute("UPDATE conversations SET source='DEV' WHERE id='control-audio'")
    assert repo.audio_source("control-audio")[1] is True


def test_legacy_dev_cache_does_not_replace_existing_control_group(setup):
    client, repo, _, _, paths = setup
    cache = paths[1]
    cache.write_text(json.dumps({"payload": dataset("DEV", 65), "fetchedAt": "2026-11-07T10:00:00Z"}))
    assert refresh(client).status_code == 200
    before = repo.state()
    create_app(*paths, organizer_url="")
    assert repo.state() == before
