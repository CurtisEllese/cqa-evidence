"""Offline cryptographic/security acceptance. No production app/DB/API imported."""
import asyncio
import json
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security import AccessDenied, EntraVerifier, Principal, SecuritySettings
from backend.security_access import (EnterpriseAccessMiddleware, RULES, Rule, ALL,
    authoritative_appeal_payload, authoritative_actor_payload, authorize,
    install_enterprise_access, rule_for)

TENANT = "11111111-1111-4111-8111-111111111111"
API = "22222222-2222-4222-8222-222222222222"
SPA = "33333333-3333-4333-8333-333333333333"
USER = "44444444-4444-4444-8444-444444444444"
OTHER = "55555555-5555-4555-8555-555555555555"


@pytest.fixture
def settings():
    return SecuritySettings(mode="entra", deployment="external", tenant_id=TENANT,
                            audience=API, client_ids=(SPA,), origins=("https://quality.example",))


@pytest.fixture
def signing(settings):
    jwt = pytest.importorskip("jwt", reason="optional enterprise requirements not installed")
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="key1", use="sig", alg="RS256", issuer=settings.issuer)
    now = int(time.time())
    claims = dict(iss=settings.issuer, aud=API, tid=TENANT, oid=USER, sub="opaque-subject",
                  azp=SPA, ver="2.0", scp="access_as_user", roles=["qa_reviewer"],
                  iat=now, nbf=now-1, exp=now+300)
    def token(changes=None, *, remove=(), headers=None, signing_key=None):
        data = {**claims, **(changes or {})}
        for name in remove:
            data.pop(name, None)
        return jwt.encode(data, signing_key or key, algorithm="RS256",
                          headers={"kid": "key1", **(headers or {})})
    return token, jwk


def run_check(settings, jwk, token):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"keys": [jwk]}))) as client:
            return await EntraVerifier(settings, client=client).verify(token)
    return asyncio.run(go())


def test_valid_signature_and_verified_identity(settings, signing):
    token, jwk = signing
    principal = run_check(settings, jwk, token())
    assert principal.actor_id == f"entra:{TENANT}:{USER}"
    assert principal.roles == frozenset({"qa_reviewer"})


@pytest.mark.parametrize("changes", [
    {"iss": "https://evil.example"}, {"aud": SPA}, {"aud": [API, SPA]},
    {"tid": OTHER}, {"azp": OTHER}, {"exp": 1}, {"nbf": 9999999999},
    {"iat": 9999999999}, {"ver": "1.0"}, {"scp": "openid profile"},
    {"scp": ["access_as_user"]}, {"oid": "Display Name"}, {"sub": ""},
    {"roles": "admin"}, {"roles": ["Global Administrator"]},
    {"roles": []}, {"exp": "9999999999"}, {"iat": True},
])
def test_rejects_invalid_claims(settings, signing, changes):
    token, jwk = signing
    with pytest.raises(AccessDenied):
        run_check(settings, jwk, token(changes))


@pytest.mark.parametrize("claim", ["exp", "iss", "aud", "tid", "oid", "scp", "azp", "nbf", "iat", "sub", "ver"])
def test_requires_claims(settings, signing, claim):
    token, jwk = signing
    with pytest.raises(AccessDenied):
        run_check(settings, jwk, token(remove=[claim]))


def test_wrong_signature_and_key_issuer(settings, signing):
    from cryptography.hazmat.primitives.asymmetric import rsa
    token, jwk = signing
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AccessDenied):
        run_check(settings, jwk, token(signing_key=other_key))
    with pytest.raises(AccessDenied):
        run_check(settings, {**jwk, "issuer": "https://evil.example"}, token())


def test_no_algorithm_confusion_or_token_jku_fetch(settings, signing):
    import jwt
    token, jwk = signing
    for invalid in (jwt.encode({"roles": ["admin"]}, "s"*32, algorithm="HS256", headers={"kid": "key1"}),
                    jwt.encode({"roles": ["admin"]}, key=None, algorithm="none"),
                    "invalid", "x"*16385):
        with pytest.raises(AccessDenied):
            run_check(settings, jwk, invalid)
    assert run_check(settings, jwk, token(headers={"jku": "https://evil.example/keys"})).object_id == USER


def test_jwks_cache_unknown_key_cooldown_and_outage(settings, signing):
    token, jwk = signing
    calls = []
    async def go():
        def transport(request):
            calls.append(str(request.url))
            return httpx.Response(200, json={"keys": [jwk]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            verifier = EntraVerifier(settings, client=client)
            await verifier.verify(token())
            await verifier.verify(token())
            with pytest.raises(AccessDenied):
                await verifier.verify(token(headers={"kid": "unknown"}))
            assert calls == [settings.jwks_url]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
            with pytest.raises(AccessDenied) as exc:
                await EntraVerifier(settings, client=client).verify(token())
            assert exc.value.status == 503
    asyncio.run(go())


@pytest.mark.parametrize("kwargs", [
    {"deployment": "external"}, {"bind_host": "0.0.0.0"}, {"mode": "disabled"},
    {"origins": ("https://public.example",)}, {"origins": ()},
])
def test_unsafe_local_settings_fail_at_construction(kwargs):
    with pytest.raises(ValueError):
        SecuritySettings(**kwargs)


def make_app(settings, verifier=None, ownership=None):
    app = FastAPI()
    install_enterprise_access(app, settings, verifier=verifier, ownership_resolver=ownership)
    @app.api_route("/{path:path}", methods=["GET", "POST", "DELETE"])
    def dummy(path: str):
        return {"accepted": path}
    return app


class StaticVerifier:
    def __init__(self, role="qa_reviewer"):
        self.principal = Principal(TENANT, USER, frozenset({role}))
    async def verify(self, token):
        if token != "verified-in-test":
            raise AccessDenied()
        return self.principal


def test_local_unregistered_loopback_only_and_no_forwarding():
    app = make_app(SecuritySettings())
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        assert client.get("/api/state").status_code == 200
        assert client.get("/api/auth/config").json() == {"mode": "local", "authenticationRequired": False}
        assert client.get("/api/state", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403
        assert client.get("/api/state", headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.get("/api/state", headers={"Host": "evil.example"}).status_code == 403
    with TestClient(app, base_url="http://localhost", client=("198.51.100.1", 1234)) as client:
        assert client.get("/api/state").status_code == 403


def test_enterprise_no_header_role_trust_public_config_and_me(settings):
    app = make_app(settings, StaticVerifier())
    with TestClient(app, base_url="https://quality.example") as client:
        config = client.get("/api/auth/config")
        assert config.status_code == 200 and config.json()["clientId"] == SPA
        response = client.get("/api/state", headers={"X-Role": "admin"})
        assert response.status_code == 401 and response.headers["WWW-Authenticate"] == "Bearer"
        assert response.headers["Cache-Control"] == "no-store"
        client.headers["Authorization"] = "Bearer verified-in-test"
        assert client.get("/api/auth/me").json()["actorId"] == f"entra:{TENANT}:{USER}"
        assert client.get("/api/state").status_code == 200
        assert client.post("/api/import", headers={"X-Role": "admin"}).status_code == 403
        assert client.post("/api/batches").status_code == 200
        assert client.get("/api/unknown").status_code == 403
        assert client.get("/api/state", headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.get("http://quality.example/api/state").status_code == 403


@pytest.mark.parametrize("path", ["/api/state", "/api/conversations", "/api/export", "/api/submission", "/api/batches/current"])
def test_operator_cannot_read_global_views(settings, path):
    with TestClient(make_app(settings, StaticVerifier("operator")), base_url="https://quality.example") as client:
        assert client.get(path, headers={"Authorization": "Bearer verified-in-test"}).status_code == 403


@pytest.mark.parametrize("owner,expected", [(None, 403), (f"entra:{TENANT}:{OTHER}", 403), (f"entra:{TENANT}:{USER}", 200)])
def test_operator_record_ownership(settings, owner, expected):
    async def resolve(request):
        return owner
    with TestClient(make_app(settings, StaticVerifier("operator"), resolve), base_url="https://quality.example") as client:
        assert client.get("/api/conversations/c1", headers={"Authorization": "Bearer verified-in-test"}).status_code == expected


@pytest.mark.parametrize("role,method,path,allowed", [
    ("qa_reviewer", "POST", "/api/evaluations", True),
    ("operator", "POST", "/api/evaluations", False),
    ("qa_reviewer", "POST", "/api/checklists/c1/versions", False),
    ("manager", "POST", "/api/checklists/c1/versions", True),
    ("manager", "POST", "/api/import", False),
    ("admin", "POST", "/api/import", True),
    ("qa_reviewer", "GET", "/api/export", False),
    ("manager", "GET", "/api/export", True),
    ("operator", "POST", "/api/appeals/a1/decision", False),
    ("qa_reviewer", "POST", "/api/appeals/a1/decision", True),
    ("operator", "GET", "/api/docs", False),
    ("admin", "GET", "/api/openapi.json", True),
])
def test_business_permission_matrix(role, method, path, allowed):
    principal = Principal(TENANT, USER, frozenset({role}))
    if allowed:
        authorize(principal, rule_for(method, path))
    else:
        with pytest.raises(AccessDenied):
            authorize(principal, rule_for(method, path))


def test_actor_fields_are_server_derived_and_missing_ownership_denied():
    principal = Principal(TENANT, USER, frozenset({"operator"}))
    assert authoritative_appeal_payload({"createdBy": "browser-admin"}, principal)["createdBy"] == principal.actor_id
    assert authoritative_actor_payload({"decidedBy": "browser-admin"}, principal, "decidedBy")["decidedBy"] == principal.actor_id
    rule = Rule("POST", r"/api/appeals", ALL, True)
    with pytest.raises(AccessDenied):
        authorize(principal, rule)
    authorize(principal, rule, principal.actor_id)
    for path in ("/api/batches", "/api/batches/b1/stop", "/api/batches/b1/resume", "/api/appeals"):
        with pytest.raises(AccessDenied):
            authorize(principal, rule_for("POST", path))


def test_real_api_factory_wrapped_without_touching_live_database(tmp_path):
    from backend.app import create_app
    async def no_provider_call(_):
        pytest.fail("Security tests must never call an evaluator")
    settings = SecuritySettings(mode="entra", deployment="external", tenant_id=TENANT,
        audience=API, client_ids=(SPA,), origins=("https://quality.example",))
    app = create_app(database_path=tmp_path / "security-test.sqlite",
                     cache_path=tmp_path / "absent-cache.json", organizer_url="", evaluator=no_provider_call,
                     security_settings=settings, token_verifier=StaticVerifier("operator"))
    with TestClient(app, base_url="https://quality.example") as client:
        assert client.get("/api/state").status_code == 401
        client.headers["Authorization"] = "Bearer verified-in-test"
        assert client.get("/api/auth/me").json()["roles"] == ["operator"]
        for path in ("/api/state", "/api/export", "/api/batches/current", "/api/conversations/c1"):
            assert client.get(path).status_code == 403
        for path in ("/api/batches", "/api/batches/b1/stop", "/api/batches/b1/resume", "/api/batches/b1/cancel", "/api/appeals"):
            assert client.post(path, json={}).status_code == 403


def test_real_api_local_mode_still_runs_without_registration(tmp_path):
    from backend.app import create_app
    async def no_provider_call(_):
        pytest.fail("Security tests must never call an evaluator")
    app = create_app(database_path=tmp_path / "local-security-test.sqlite",
                     cache_path=tmp_path / "absent-cache.json", organizer_url="", evaluator=no_provider_call)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        assert client.get("/api/state").status_code == 200
        assert client.get("/api/auth/config").json()["authenticationRequired"] is False


def test_missing_enterprise_configuration_fails_before_database(tmp_path, monkeypatch):
    from backend.app import create_app
    monkeypatch.setenv("CQA_AUTH_MODE", "entra")
    monkeypatch.delenv("CQA_ENTRA_TENANT_ID", raising=False)
    path = tmp_path / "must-not-exist.sqlite"
    with pytest.raises(ValueError):
        create_app(path, tmp_path / "none", organizer_url="")
    assert not path.exists()


def test_actor_saved_atomically_and_body_actor_rejected(tmp_path, settings):
    from backend.app import create_app
    from backend.tests.test_api import record, evaluated, wire
    async def no_provider_call(_):
        pytest.fail("No LLM calls")
    app = create_app(tmp_path / "audit.sqlite", tmp_path / "none", organizer_url="",
        evaluator=no_provider_call, security_settings=settings, token_verifier=StaticVerifier())
    repo = app.state.repo
    repo.import_data(json.dumps([record("audit-test")]))
    conversation, evaluation = evaluated(repo)
    body = wire(conversationId=conversation["id"], evaluationId=evaluation["id"],
                criterionId="dialogue", comment="Synthetic audit acceptance comment")
    with TestClient(app, base_url="https://quality.example", headers={"Authorization": "Bearer verified-in-test"}) as client:
        assert client.post("/api/appeals", json={**body, "createdBy": "attacker"}).status_code == 422
        response = client.post("/api/appeals", json=body)
        assert response.status_code == 200
        appeal = response.json()["data"]["appeals"][0]
        assert appeal["createdBy"] == f"entra:{TENANT}:{USER}"
        assert appeal.get("decidedBy") is None
        with repo.connect() as db:
            stored = json.loads(db.execute("SELECT body FROM appeals WHERE id=?", (appeal["id"],)).fetchone()[0])
            assert stored["createdBy"] == f"entra:{TENANT}:{USER}"
            assert db.execute("SELECT COUNT(*) FROM enterprise_actor_audit").fetchone()[0] == 1
        decision = wire(status="rejected", decision="Synthetic audit decision justification")
        assert client.post(f"/api/appeals/{appeal['id']}/decision", json={**decision, "decidedBy": "attacker"}).status_code == 422
        decision_response = client.post(f"/api/appeals/{appeal['id']}/decision", json=decision)
        assert decision_response.status_code == 200
        assert decision_response.json()["data"]["appeals"][0]["decidedBy"] == f"entra:{TENANT}:{USER}"
        with repo.connect() as db:
            stored = json.loads(db.execute("SELECT body FROM appeals WHERE id=?", (appeal["id"],)).fetchone()[0])
            assert stored["decidedBy"] == f"entra:{TENANT}:{USER}"
            assert db.execute("SELECT COUNT(*) FROM enterprise_actor_audit").fetchone()[0] == 2


def test_audit_failure_rolls_back_appeal(tmp_path):
    from backend.security_repository import AuditedRepository
    from backend.tests.test_api import record, evaluated
    repo = AuditedRepository(tmp_path / "rollback.sqlite", enterprise=True)
    repo.import_data(json.dumps([record("rollback-test")]))
    conversation, evaluation = evaluated(repo)
    with repo.connect(write=True) as db:
        db.execute("CREATE TRIGGER reject_test_audit BEFORE INSERT ON enterprise_actor_audit BEGIN SELECT RAISE(ABORT,'test audit unavailable'); END")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_appeal(dict(conversationId=conversation["id"], evaluationId=evaluation["id"],
            criterionId="dialogue", comment="Synthetic rollback acceptance comment"),
            actor=Principal(TENANT, USER, frozenset({"qa_reviewer"})))
    with repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM appeals").fetchone()[0] == 0
