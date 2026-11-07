"""Read-only Dynamics 365 / Dataverse v9.2 server adapter (public cloud).

No iframe, postMessage trust, browser credentials, delegated-user impersonation,
SQL writes, publishing or background synchronization. Not wired into app.py yet.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from backend.security import guid


class DynamicsError(Exception):
    def __init__(self, code="DYNAMICS_UNAVAILABLE", *, status=502, retry_after=0):
        self.code, self.status = code, status
        self.retry_after = max(0, min(300, retry_after))
        super().__init__(code)  # Never include Microsoft responses/tokens/secrets.


@dataclass(frozen=True)
class DataverseSettings:
    environment_url: str
    tenant_id: str
    client_id: str
    # Explicit table/column allowlist from tenant's solution metadata.
    tables: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def __post_init__(self):
        parsed = urlsplit(self.environment_url)
        if (parsed.scheme != "https" or parsed.path or parsed.query or parsed.fragment
                or parsed.username or parsed.password or parsed.port
                or not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.crm\d*\.dynamics\.com", parsed.netloc)):
            raise ValueError("An exact public-cloud Dataverse HTTPS environment origin is required")
        if guid(self.tenant_id) != self.tenant_id or guid(self.client_id) != self.client_id:
            raise ValueError("Canonical tenant/application UUIDs required")
        seen = set()
        for table, columns in self.tables:
            if table in seen or not columns:
                raise ValueError("Unique table names and explicit columns required")
            seen.add(table)
            if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in (table, *columns)):
                raise ValueError("Invalid table/column identifier")

    @property
    def scope(self):
        return self.environment_url + "/.default"


class TokenProvider(Protocol):
    def token(self) -> str: ...


class MsalApplicationTokenProvider:
    """Microsoft MSAL confidential client, app-only Dataverse token.

    credential is an MSAL certificate dictionary (preferred) or a server secret.
    Supply via secret manager; never from a request body/browser. MSAL caches tokens.
    This object is server-owned and must not be logged or serialized.
    """
    def __init__(self, settings: DataverseSettings, credential):
        import msal
        if not credential:
            raise ValueError("A server-owned certificate/credential is required")
        self.settings = settings
        self._application = msal.ConfidentialClientApplication(
            settings.client_id, authority=f"https://login.microsoftonline.com/{settings.tenant_id}",
            client_credential=credential, timeout=5, enable_pii_log=False)

    def token(self):
        try:
            result = self._application.acquire_token_for_client(scopes=[self.settings.scope])
            token = result.get("access_token")
            if not isinstance(token, str) or not token:
                raise DynamicsError("DYNAMICS_AUTH_UNAVAILABLE", status=503)
            return token
        except DynamicsError:
            raise
        except Exception:
            raise DynamicsError("DYNAMICS_AUTH_UNAVAILABLE", status=503) from None


class DataverseClient:
    def __init__(self, settings: DataverseSettings, provider: TokenProvider, *, client: httpx.AsyncClient | None = None):
        self.settings, self.provider, self.client = settings, provider, client

    async def _request(self, relative: str, params=None):
        try:
            token = await asyncio.to_thread(self.provider.token)
            if not isinstance(token, str) or not token or len(token) > 32768 or any(c.isspace() for c in token):
                raise DynamicsError("DYNAMICS_AUTH_UNAVAILABLE", status=503)
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
                       "OData-MaxVersion": "4.0", "OData-Version": "4.0"}
            url = self.settings.environment_url + "/api/data/v9.2/" + relative
            if self.client is not None:
                return await self._send(self.client, url, headers, params)
            async with httpx.AsyncClient(trust_env=False) as client:
                return await self._send(client, url, headers, params)
        except DynamicsError:
            raise
        except (httpx.HTTPError, ValueError, TypeError):
            raise DynamicsError() from None

    async def _send(self, client, url, headers, params):
        async with client.stream("GET", url, headers=headers, params=params,
                                 timeout=10, follow_redirects=False) as response:
            status = response.status_code
            if status == 429:
                retry = response.headers.get("Retry-After", "0")
                raise DynamicsError("DYNAMICS_RATE_LIMIT", status=503,
                                    retry_after=int(retry) if retry.isdecimal() and len(retry) <= 6 else 0)
            if status in (401, 403):
                raise DynamicsError("DYNAMICS_PERMISSION_DENIED", status=502)
            if status == 404:
                raise DynamicsError("DYNAMICS_NOT_FOUND", status=404)
            if status != 200:  # Includes redirects: never forward a bearer token.
                raise DynamicsError()
            if "application/json" not in response.headers.get("Content-Type", ""):
                raise DynamicsError("DYNAMICS_INVALID_RESPONSE")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 2 * 1024 * 1024:
                    raise DynamicsError("DYNAMICS_RESPONSE_TOO_LARGE")
            try:
                payload = json.loads(content)
            except ValueError:
                raise DynamicsError("DYNAMICS_INVALID_RESPONSE") from None
            if not isinstance(payload, dict):
                raise DynamicsError("DYNAMICS_INVALID_RESPONSE")
            return payload

    async def who_am_i(self):
        """Admin-only readiness diagnostic; app user identity, not human identity."""
        data = await self._request("WhoAmI")
        try:
            return {field: guid(data[field]) for field in ("UserId", "BusinessUnitId", "OrganizationId")}
        except (ValueError, KeyError, TypeError):
            raise DynamicsError("DYNAMICS_INVALID_RESPONSE") from None

    async def read_record(self, entity_set: str, record_id: str):
        columns = dict(self.settings.tables).get(entity_set)
        if not columns:
            raise ValueError("Table is not allowlisted")
        canonical_id = guid(record_id)
        payload = await self._request(f"{entity_set}({canonical_id})", {"$select": ",".join(columns)})
        # Do not expose arbitrary extra fields/nextLinks or fetch another origin.
        return {key: payload[key] for key in columns if key in payload}
