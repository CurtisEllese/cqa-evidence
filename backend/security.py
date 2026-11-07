"""Opt-in Entra access-token verification. No login simulation or tenant provisioning.

Importing this module does not load secrets, contact Microsoft, or change the app.
Install integrations/requirements-enterprise.txt only for enterprise mode.
"""
from __future__ import annotations

import asyncio
import ipaddress
import math
import os
import time
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit
from uuid import UUID

import httpx


class AccessDenied(Exception):
    def __init__(self, code: str = "INVALID_TOKEN", status: int = 401):
        self.code, self.status = code, status
        super().__init__(code)


def guid(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected a UUID")
    return str(UUID(value))


def origin(value: str, *, local: bool = False) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in (("http", "https") if local else ("https",))
            or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment
            or parsed.netloc != parsed.netloc.lower()):
        raise ValueError("Expected an exact origin without a trailing slash")
    # Force validation of malformed ports.
    _ = parsed.port
    if local and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Local origins must be loopback")
    return value


@dataclass(frozen=True)
class SecuritySettings:
    mode: str = "local"
    deployment: str = "local"
    bind_host: str = "127.0.0.1"
    tenant_id: str = ""
    audience: str = ""
    client_ids: tuple[str, ...] = ()
    origins: tuple[str, ...] = ("http://localhost:8000", "http://127.0.0.1:8000",
                              "http://localhost:5173", "http://127.0.0.1:5173",
                              "http://localhost:4173", "http://127.0.0.1:4173")
    scope: str = "access_as_user"

    def __post_init__(self):
        if self.mode not in {"local", "entra"} or self.deployment not in {"local", "external"}:
            raise ValueError("Unknown security mode/deployment")
        if self.mode == "local":
            if self.deployment != "local" or self.bind_host not in {"127.0.0.1", "::1"}:
                raise ValueError("Unauthenticated mode requires a loopback-only deployment")
        else:
            if guid(self.tenant_id) != self.tenant_id or guid(self.audience) != self.audience:
                raise ValueError("Use canonical tenant/API application UUIDs")
            if not self.client_ids or any(guid(x) != x for x in self.client_ids):
                raise ValueError("Explicit allowed client application UUIDs are required")
            if self.audience in self.client_ids:
                raise ValueError("Use separate API and SPA registrations")
            if not self.scope or any(c.isspace() for c in self.scope):
                raise ValueError("One delegated API scope is required")
        if not self.origins:
            raise ValueError("At least one explicit browser origin is required")
        for value in self.origins:
            origin(value, local=self.mode == "local")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None):
        env = os.environ if env is None else env
        defaults = cls()
        return cls(mode=env.get("CQA_AUTH_MODE", "local"),
                   deployment=env.get("CQA_DEPLOYMENT", "local"),
                   bind_host=env.get("CQA_BIND_HOST", "127.0.0.1"),
                   tenant_id=env.get("CQA_ENTRA_TENANT_ID", ""),
                   audience=env.get("CQA_ENTRA_API_CLIENT_ID", ""),
                   client_ids=tuple(x.strip() for x in env.get("CQA_ENTRA_CLIENT_IDS", "").split(",") if x.strip()),
                   origins=tuple(x.strip() for x in env.get("CQA_AUTH_ORIGINS",
                       env.get("APP_ORIGINS", ",".join(defaults.origins)) if env.get("CQA_AUTH_MODE", "local") == "local"
                       else ",".join(defaults.origins)).split(",") if x.strip()),
                   scope=env.get("CQA_ENTRA_SCOPE", "access_as_user"))

    @property
    def issuer(self):
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def jwks_url(self):
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"


ROLES = frozenset({"qa_reviewer", "operator", "manager", "admin"})


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    object_id: str
    roles: frozenset[str]
    local: bool = False

    @property
    def actor_id(self):
        # Stable identifier, never a mutable display name/email/browser field.
        return "local:development" if self.local else f"entra:{self.tenant_id}:{self.object_id}"


LOCAL_PRINCIPAL = Principal("", "", frozenset({"admin"}), local=True)


class EntraVerifier:
    """Single-tenant public-cloud Entra v2 delegated access tokens only.

    JWKS URL/algorithm are configured on the server; token jku/x5u are ignored.
    TLS, bounded response, no redirects, TTL cache and refresh cooldown apply.
    An injected HTTP client is intended for tests and remains caller-owned.
    """
    def __init__(self, settings: SecuritySettings, *, client: httpx.AsyncClient | None = None):
        if settings.mode != "entra":
            raise ValueError("Entra settings required")
        try:
            import jwt
        except ImportError:
            raise RuntimeError("Install backend/integrations/requirements-enterprise.txt") from None
        self.jwt = jwt
        self.settings, self.client = settings, client
        self._keys: dict[str, dict] = {}
        self._expires = 0.0
        self._attempted = float("-inf")
        self._lock = asyncio.Lock()

    async def _download(self, client):
        async with client.stream("GET", self.settings.jwks_url,
                                 timeout=5, follow_redirects=False) as response:
            if response.status_code != 200:
                raise AccessDenied("IDENTITY_UNAVAILABLE", 503)
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 256 * 1024:
                    raise AccessDenied("IDENTITY_UNAVAILABLE", 503)
        import json
        return json.loads(content)

    async def _key(self, kid):
        async with self._lock:
            now = time.monotonic()
            if kid in self._keys and now < self._expires:
                return self._keys[kid]
            if now - self._attempted < 30:
                raise AccessDenied("IDENTITY_UNAVAILABLE", 503) if now >= self._expires else AccessDenied()
            self._attempted = now
            try:
                if self.client is None:
                    async with httpx.AsyncClient(trust_env=False) as client:
                        document = await self._download(client)
                else:
                    document = await self._download(self.client)
                keys = document["keys"]
                if not isinstance(keys, list) or not 1 <= len(keys) <= 100:
                    raise ValueError()
                accepted = {}
                for key in keys:
                    if not isinstance(key, dict):
                        raise ValueError()
                    key_issuer = key.get("issuer", self.settings.issuer)
                    if not isinstance(key_issuer, str):
                        continue
                    key_issuer = key_issuer.replace("{tenantid}", self.settings.tenant_id)
                    if (key.get("kty") == "RSA" and key.get("use") == "sig"
                            and key.get("alg", "RS256") == "RS256"
                            and key_issuer == self.settings.issuer
                            and isinstance(key.get("kid"), str)):
                        if key["kid"] in accepted:
                            raise ValueError()
                        accepted[key["kid"]] = key
                if not accepted:
                    raise ValueError()
                self._keys, self._expires = accepted, now + 300
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                raise AccessDenied("IDENTITY_UNAVAILABLE", 503) from None
            if kid not in self._keys:
                raise AccessDenied()
            return self._keys[kid]

    async def verify(self, token: str) -> Principal:
        jwt = self.jwt
        if len(token) > 16384:
            raise AccessDenied()
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if header.get("alg") != "RS256" or not isinstance(kid, str) or not 1 <= len(kid) <= 128:
                raise AccessDenied()
            if header.get("crit"):
                raise AccessDenied()
            key = jwt.PyJWK.from_dict(await self._key(kid), algorithm="RS256")
            claims = jwt.decode(token, key=key.key, algorithms=["RS256"],
                                audience=self.settings.audience, issuer=self.settings.issuer,
                                leeway=30, options={"strict_aud": True,
                                    "require": ["exp", "iat", "nbf", "iss", "aud", "sub", "tid", "oid", "azp", "scp", "ver"]})
            if (claims["tid"] != self.settings.tenant_id or claims["ver"] != "2.0"
                    or claims["azp"] not in self.settings.client_ids
                    or not isinstance(claims["scp"], str)
                    or self.settings.scope not in claims["scp"].split()
                    or not isinstance(claims["sub"], str) or not claims["sub"]
                    or any(type(claims[k]) not in (int, float) or not math.isfinite(claims[k]) for k in ("exp", "iat", "nbf"))):
                raise AccessDenied()
            object_id = guid(claims["oid"])
            roles = claims.get("roles", [])
            if not isinstance(roles, list) or not all(isinstance(x, str) for x in roles):
                raise AccessDenied()
            allowed = frozenset(roles) & ROLES
            if not allowed:
                raise AccessDenied("ROLE_FORBIDDEN", 403)
            return Principal(self.settings.tenant_id, object_id, allowed)
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, OverflowError):
            raise AccessDenied() from None


def loopback_peer(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
