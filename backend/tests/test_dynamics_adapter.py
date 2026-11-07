"""Mock-only Dataverse acceptance; never contacts or mutates a tenant."""
import asyncio

import httpx
import pytest

from backend.integrations.dynamics import (
    DataverseClient, DataverseSettings, DynamicsError, MsalApplicationTokenProvider)

TENANT = "11111111-1111-4111-8111-111111111111"
APP = "22222222-2222-4222-8222-222222222222"
RECORD = "33333333-3333-4333-8333-333333333333"


def settings(url="https://sample.crm4.dynamics.com"):
    return DataverseSettings(url, TENANT, APP, (("incidents", ("incidentid", "title")),))


class Provider:
    def token(self):
        return "opaque-test-token"


def run(handler, action="record"):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = DataverseClient(settings(), Provider(), client=http)
            if action == "who":
                return await client.who_am_i()
            return await client.read_record("incidents", RECORD)
    return asyncio.run(go())


def test_real_request_shape_and_minimal_response_projection():
    def handler(request):
        assert request.method == "GET"
        assert str(request.url).startswith(f"https://sample.crm4.dynamics.com/api/data/v9.2/incidents({RECORD})?")
        assert request.url.params["$select"] == "incidentid,title"
        assert request.headers["Authorization"] == "Bearer opaque-test-token"
        assert request.headers["OData-Version"] == "4.0"
        return httpx.Response(200, json={"incidentid": RECORD, "title": "Case", "secret": "not requested",
                                         "@odata.nextLink": "https://evil.example/steal"})
    assert run(handler) == {"incidentid": RECORD, "title": "Case"}


def test_who_am_i_returns_application_user_identity():
    def handler(request):
        assert request.url.path.endswith("/WhoAmI")
        return httpx.Response(200, json={"UserId": APP, "BusinessUnitId": RECORD, "OrganizationId": TENANT})
    assert run(handler, "who")["UserId"] == APP


@pytest.mark.parametrize("url", ["http://sample.crm.dynamics.com", "https://evil.example",
    "https://sample.crm.dynamics.com.evil.example", "https://127.0.0.1", "https://user:secret@sample.crm.dynamics.com",
    "https://sample.crm.dynamics.com/", "https://sample.crm.dynamics.com?url=evil",
    "https://sample.crm.dynamics.com:443", "https://sample.crm.dynamics.com#evil"])
def test_untrusted_environment_rejected(url):
    with pytest.raises(ValueError):
        settings(url)


@pytest.mark.parametrize("table,record", [("contacts", RECORD), ("incidents/../../", RECORD),
                                        ("incidents", "x)?$expand=secret"), ("incidents", "Name")])
def test_path_and_query_injection_rejected_before_network(table, record):
    async def go():
        def unexpected(_):
            pytest.fail("Unsafe input reached network")
        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as http:
            with pytest.raises(ValueError):
                await DataverseClient(settings(), Provider(), client=http).read_record(table, record)
    asyncio.run(go())


@pytest.mark.parametrize("status,code", [(301, "DYNAMICS_UNAVAILABLE"), (302, "DYNAMICS_UNAVAILABLE"),
    (401, "DYNAMICS_PERMISSION_DENIED"), (403, "DYNAMICS_PERMISSION_DENIED"),
    (404, "DYNAMICS_NOT_FOUND"), (500, "DYNAMICS_UNAVAILABLE"), (429, "DYNAMICS_RATE_LIMIT")])
def test_remote_errors_sanitized_redirects_not_followed(status, code):
    calls = []
    def handler(request):
        calls.append(request.url)
        return httpx.Response(status, text="sensitive upstream details", headers={"Location": "https://evil.example", "Retry-After": "9999"})
    with pytest.raises(DynamicsError) as exc:
        run(handler)
    assert str(exc.value) == code and len(calls) == 1
    if status == 429:
        assert exc.value.retry_after == 300


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="<html>error</html>"),
    httpx.Response(200, content=b"not json", headers={"Content-Type": "application/json"}),
    httpx.Response(200, json=[]),
    httpx.Response(200, content=b"x"*(2*1024*1024+1), headers={"Content-Type": "application/json"}),
])
def test_invalid_or_oversized_response_rejected(response):
    with pytest.raises(DynamicsError):
        run(lambda _: response)


def test_timeout_sanitized():
    def handler(request):
        raise httpx.ReadTimeout("secret upstream", request=request)
    with pytest.raises(DynamicsError, match="^DYNAMICS_UNAVAILABLE$"):
        run(handler)


def test_msal_uses_tenant_authority_and_environment_scope(monkeypatch):
    msal = pytest.importorskip("msal", reason="optional enterprise requirements not installed")
    captured = {}
    class Application:
        def __init__(self, client_id, **kwargs):
            captured.update(client_id=client_id, **kwargs)
        def acquire_token_for_client(self, scopes):
            captured["scopes"] = scopes
            return {"access_token": "opaque-test-token"}
    monkeypatch.setattr(msal, "ConfidentialClientApplication", Application)
    provider = MsalApplicationTokenProvider(settings(), "test-only-credential")
    assert provider.token() == "opaque-test-token"
    assert captured["authority"] == f"https://login.microsoftonline.com/{TENANT}"
    assert captured["scopes"] == ["https://sample.crm4.dynamics.com/.default"]
    assert captured["enable_pii_log"] is False


def test_msal_failure_does_not_leak_provider_error(monkeypatch):
    msal = pytest.importorskip("msal")
    class Application:
        def __init__(self, *args, **kwargs):
            pass
        def acquire_token_for_client(self, **kwargs):
            return {"error_description": "sensitive provider message"}
    monkeypatch.setattr(msal, "ConfidentialClientApplication", Application)
    with pytest.raises(DynamicsError, match="^DYNAMICS_AUTH_UNAVAILABLE$"):
        MsalApplicationTokenProvider(settings(), "test-only-credential").token()
