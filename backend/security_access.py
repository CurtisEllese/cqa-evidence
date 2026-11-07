"""Default-deny API policy and opt-in FastAPI middleware. See acceptance contract.

Global/list endpoints deliberately exclude operators until repository projections
exist. Per-record access requires trusted ownership resolution, never display names.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .security import AccessDenied, EntraVerifier, LOCAL_PRINCIPAL, Principal, SecuritySettings, loopback_peer

REVIEW = frozenset({"qa_reviewer", "manager", "admin"})
MANAGE = frozenset({"manager", "admin"})
ADMIN = frozenset({"admin"})
ALL = REVIEW | {"operator"}


@dataclass(frozen=True)
class Rule:
    method: str
    pattern: str
    roles: frozenset[str]
    operator_owned: bool = False


RULES = (
    Rule("GET", r"/api/health", ALL),
    Rule("GET", r"/api/auth/me", ALL),
    Rule("GET", r"/api/(?:state|conversations)", REVIEW),
    Rule("GET", r"/api/checklists", ALL),
    Rule("GET", r"/api/(?:export|submission)", MANAGE),
    Rule("GET", r"/api/(?:docs|openapi\.json)", ADMIN),
    Rule("POST", r"/api/(?:import|migrate|organizer/refresh)", ADMIN),
    Rule("POST", r"/api/checklists/[^/]+/versions", MANAGE),
    Rule("POST", r"/api/evaluations", REVIEW),
    Rule("GET", r"/api/batches/(?:current|[^/]+)", REVIEW),
    Rule("POST", r"/api/batches", REVIEW),
    Rule("POST", r"/api/batches/[^/]+/(?:stop|resume|cancel)", REVIEW),
    # Operator appeal creation remains denied until handler author/response hooks
    # are installed; current handler returns the entire workspace.
    Rule("POST", r"/api/appeals", REVIEW),
    Rule("POST", r"/api/appeals/[^/]+/(?:decision|preview)", REVIEW),
    Rule("GET", r"/api/conversations/[^/]+(?:/(?:evaluations|audio|export))?", ALL, True),
    Rule("GET", r"/api/evaluations/[^/]+(?:/export)?", ALL, True),
    # audio-page embeds an arbitrary upstream URL; not enterprise approved.
    # Other API paths require explicit extra_rules; no wildcard fallback.
)


def rule_for(method: str, path: str, rules=RULES) -> Rule:
    for rule in rules:
        if rule.method == method and re.fullmatch(rule.pattern, path):
            return rule
    raise AccessDenied("ENDPOINT_FORBIDDEN", 403)


def authorize(principal: Principal, rule: Rule, owner_actor_id: str | None = None):
    if principal.local:
        return
    if not principal.roles & rule.roles:
        raise AccessDenied("ROLE_FORBIDDEN", 403)
    # A verified reviewer/manager/admin assignment grants organization-wide scope.
    if principal.roles & REVIEW:
        return
    if rule.operator_owned and owner_actor_id != principal.actor_id:
        raise AccessDenied("OWNERSHIP_REQUIRED", 403)


def authoritative_appeal_payload(payload: dict, principal: Principal) -> dict:
    """Proposed create-handler hook; repository must persist createdBy explicitly.

    Current repository ignores extra fields, so this helper alone is insufficient.
    Use authoritative_actor_payload(..., 'decidedBy') for actual decisions.
    """
    return authoritative_actor_payload(payload, principal, "createdBy")


def authoritative_actor_payload(payload: dict, principal: Principal, field: str) -> dict:
    if field not in {"createdBy", "decidedBy"}:
        raise ValueError("Unsupported audit actor field")
    return {**payload, field: principal.actor_id}


OwnershipResolver = Callable[[Request], Awaitable[str | None]]


class EnterpriseAccessMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, settings: SecuritySettings, verifier=None,
                 ownership_resolver: OwnershipResolver | None = None, extra_rules: tuple[Rule, ...] = ()):
        super().__init__(app)
        self.settings = settings
        self.verifier = verifier or (EntraVerifier(settings) if settings.mode == "entra" else None)
        self.ownership_resolver = ownership_resolver
        self.rules = RULES + extra_rules

    async def dispatch(self, request: Request, call_next):
        try:
            settings = self.settings
            host = request.url.hostname
            supplied_origin = request.headers.get("origin")
            if supplied_origin and supplied_origin not in settings.origins:
                raise AccessDenied("ORIGIN_FORBIDDEN", 403)
            if settings.mode == "local":
                peer = request.client.host if request.client else ""
                if (host not in {"localhost", "127.0.0.1", "::1"} or not loopback_peer(peer)
                        or any(k in request.headers for k in ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto"))):
                    raise AccessDenied("LOCAL_ONLY", 403)
                principal = LOCAL_PRINCIPAL
            else:
                if host not in {urlsplit(x).hostname for x in settings.origins}:
                    raise AccessDenied("HOST_FORBIDDEN", 403)
                if request.url.scheme != "https":
                    raise AccessDenied("HTTPS_REQUIRED", 403)
                # Static shell may load so a future real MSAL flow can start.
                if request.method == "GET" and request.url.path == "/api/auth/config":
                    principal = None
                elif request.url.path == "/api" or request.url.path.startswith("/api/"):
                    values = request.headers.getlist("authorization")
                    if len(values) != 1:
                        raise AccessDenied("AUTHENTICATION_REQUIRED")
                    parts = values[0].split()
                    if len(parts) != 2 or parts[0].lower() != "bearer":
                        raise AccessDenied("AUTHENTICATION_REQUIRED")
                    principal = await self.verifier.verify(parts[1])
                    rule = rule_for(request.method, request.url.path, self.rules)
                    owner = None
                    if rule.operator_owned and not principal.roles & REVIEW:
                        if self.ownership_resolver:
                            owner = await self.ownership_resolver(request)
                    authorize(principal, rule, owner)
                else:
                    principal = None
            request.state.principal = principal
            response = await call_next(request)
        except AccessDenied as exc:
            response = JSONResponse({"schema_version": "2.0", "code": exc.code,
                "message": "Доступ не разрешён." if exc.status != 503 else "Сервис проверки доступа временно недоступен.",
                "details": [], "request_id": str(uuid4()), "retryable": exc.status == 503}, status_code=exc.status)
            if exc.status == 401:
                response.headers["WWW-Authenticate"] = "Bearer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'self'" if request.url.path == "/auth-redirect.html" else "frame-ancestors 'none'"
        if request.url.path == "/api" or request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response


def install_enterprise_access(app, settings: SecuritySettings | None = None, **kwargs):
    """Call after existing middleware registrations, before app starts serving.

    Local launch must bind using settings.bind_host with proxy headers disabled.
    Enterprise still needs the host/origin boundary and actor hooks in app.py.
    """
    settings = settings or SecuritySettings.from_env()
    if settings.mode == "entra" and kwargs.get("verifier") is None:
        # Missing optional crypto dependency is a configuration/startup error,
        # never a fallback to local access.
        kwargs["verifier"] = EntraVerifier(settings)
    existing_count = len(app.router.routes)
    app.include_router(auth_router(settings))
    # create_app ends with a /{path:path} SPA fallback. New API routes must
    # precede it even when the hook is called at the very end of the factory.
    added = app.router.routes[existing_count:]
    app.router.routes = added + app.router.routes[:existing_count]
    app.add_middleware(EnterpriseAccessMiddleware, settings=settings, **kwargs)
    return settings


def auth_router(settings: SecuritySettings) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["access"])

    @router.get("/config")
    def config():
        if settings.mode == "local":
            return {"mode": "local", "authenticationRequired": False}
        return {"mode": "entra", "authenticationRequired": True,
                "authority": f"https://login.microsoftonline.com/{settings.tenant_id}",
                "clientId": settings.client_ids[0],
                "scopes": [f"api://{settings.audience}/{settings.scope}"]}

    @router.get("/me")
    def me(request: Request):
        principal = getattr(request.state, "principal", None)
        if not isinstance(principal, Principal):
            # Fail closed if someone installs only this router.
            return JSONResponse({"code": "AUTHENTICATION_REQUIRED"}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
        return {"actorId": principal.actor_id, "roles": sorted(principal.roles),
                "local": principal.local,
                "access": {"workspace": bool(principal.roles & REVIEW),
                           "evaluate": bool(principal.roles & REVIEW),
                           "manageChecklists": bool(principal.roles & MANAGE),
                           "import": "admin" in principal.roles,
                           "ownershipRequired": principal.roles == frozenset({"operator"})}}

    return router
