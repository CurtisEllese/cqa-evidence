# Enterprise access acceptance — Freedom Quality

Final status, 2026-11-08 (Asia/Qyzylorda): **API wiring, real MSAL browser flow and atomic audit implemented; ready for independent QA, code frozen. No live tenant SSO or Dynamics end-to-end claim.**

## Immediate coordination / QA handoff

- `backend/app.py` now installs the access middleware and public `/api/auth/config` / protected `/api/auth/me`. Local mode stays registration-free. Entra mode validates configuration/dependencies before opening SQLite.
- `/api/batches/{id}/cancel` is included alongside start/current/get/stop/resume. Unknown API routes default deny.
- `src/services/api.ts` uses a configurable `authorizedFetch` for JSON, exports and submission downloads. `AuthGate` installs the real MSAL session transport before mounting `StoreProvider`.
- Voice owner contract: `src/services/authMedia.ts::loadAuthorizedAudio(src, signal)` returns `{url, dispose}`. Local mode returns original src. Enterprise accepts only same-origin `/api/conversations/{id}/audio`, uses bearer fetch, refuses redirects, requires audio MIME, bounds streamed bytes to 32 MiB, creates/revokes a blob URL. Voice owner has integrated the helper into AudioPlayer (observed await/abort/dispose/late-result cleanup in its source); media browser verification belongs to that owner's report and independent QA. No bearer query parameter or unprotected media exemption.
- **DTO handoff completed by quality owner:** optional `createdBy` / `decidedBy` are in `AppealDTO`, codecs and generated contracts. Existing records return null; request DTOs reject actor fields. `AuditedRepository` stamps these keys inside the transaction and app handlers pass `request.state.principal`; no displayname mapping. An integration test checks SQLite, DTO and rejection of forged actor fields together.
- Earlier expanded verification: 161 passed, 2 failed across access, Dynamics, API, batch and evaluation tests. `test_initial_state_and_restart_share_database` and `test_changed_seed_on_restart_does_not_rewrite_saved_checklist_or_scores` started a second application while the first held the queue worker lock. The queue owner reconciled test lifecycles; both pass in the final full run. The worker lock was not weakened.
- Frontend build and existing 20 JavaScript contract/batch tests passed. Working server was **not restarted**. No `.env.local`, working SQLite or DEV results were edited; no LLM API spend, deployment, publishing, commit or push.

## Access design

Single-tenant Microsoft Entra public cloud v2 delegated access tokens; fixed server-side RS256 algorithm and tenant JWKS endpoint. PyJWT verifies signature/issuer/audience/expiry/nbf/iat; additionally require tenant UUID, object UUID, v2 token, allowed SPA `azp`, delegated `access_as_user` scope and recognized app roles. Separate API and SPA app registrations prevent treating an ID token as API authorization. A Graph/Dataverse token is not accepted as a Freedom Quality token.

Roles are exact Entra app-role values `qa_reviewer`, `operator`, `manager`, `admin`. Roles from headers, browser storage, display names, query parameters or request JSON are not used. Stable audit identity is `entra:{tid}:{oid}`. Missing/invalid bearer => 401; authenticated insufficient permission => 403; unavailable signing keys => 503, never anonymous fallback. JWKS requests use TLS, no redirects, a pinned Microsoft tenant URL, a 256 KiB bound, 5-second timeout, 5-minute cache and 30-second refresh cooldown. Token-supplied jku/x5u is ignored. Key issuer is checked when present.

`CQA_AUTH_MODE=local` allows a loopback-only deployment and no registration. Middleware checks actual peer plus host and rejects forwarded headers. Production has no testserver/testclient exception; `backend/tests/conftest.py` sets a realistic loopback peer/base URL for test transports only. Local identity is explicitly marked `local`, not represented as an authenticated corporate user. `CQA_DEPLOYMENT=external` with local mode, an unsafe local bind, unknown mode, missing tenant/app IDs or unsafe origins fails closed. Do not expose local mode through a reverse proxy that strips all forwarding evidence; use Entra mode for any external hosting.

## Endpoint matrix

All permissions below are enforced on the server. Enterprise `/api/auth/config` is the sole anonymous API endpoint. Static login shell / redirect bridge contains no workspace data.

| Endpoint / method | Operator | QA reviewer | Manager | Admin |
|---|---|---|---|---|
| GET auth/me, health, checklists | yes | yes | yes | yes |
| GET state, conversations (global list) | deny | yes | yes | yes |
| GET individual conversation/evaluation/history/audio/export | ownership required; currently unmapped => deny | yes | yes | yes |
| POST evaluations; POST batches; GET batches/current, batches/id | deny | yes | yes | yes |
| POST batches/id/stop,resume,cancel | deny | yes | yes | yes |
| POST appeals | deny until scoped response + ownership exists | yes | yes | yes |
| POST appeals/id/preview,decision | deny | yes | yes | yes |
| POST checklist versions | deny | deny | yes | yes |
| GET export, submission | deny | deny | yes | yes |
| POST import,migrate,organizer/refresh | deny | deny | deny | yes |
| GET API docs/openapi | deny | deny | deny | yes |
| GET audio-page (raw upstream embed) / unknown endpoints | deny | deny | deny | deny |

No operator ownership mapping exists in customer records. No mapping is inferred from `operator` display text. Global `/api/state` and mutations returning a whole workspace cannot safely be exposed to operators. Middleware has a trusted async `ownership_resolver(Request) -> actor_id | None` extension, but production installs none, so per-record operator reads also fail closed. Enabling operator workflows requires a reviewed persisted mapping from tenant/object IDs to conversations, joins from evaluations/appeals to conversations, filtered queries/projections, and negative cross-owner tests. A verified secondary reviewer/manager/admin role explicitly grants organization-wide access.

## Atomic appeal audit

`backend/security_repository.py` extends the shared repository without modifying its implementation. The API supplies verified `Principal` explicitly. The connection wrapper stamps `createdBy` / `decidedBy` in appeal JSON and inserts an `enterprise_actor_audit` row **before the same SQLite transaction commits**. An audit failure rolls back the business mutation. Preview is not audited as a decision. Request-supplied actor keys are not part of request models. This is an application audit trail, not a tamper-proof external ledger; storage administrators remain trusted.

## Browser flow and configuration

`src/services/auth.ts`: fetch safe public configuration, initialize official `@azure/msal-browser` only in Entra mode, show an explicit Microsoft sign-in button, use real `loginPopup` (authorization code + PKCE), then `acquireTokenSilent` for API access. Tokens remain in the MSAL memory cache. No token paste UI, fake login, client secret, localStorage roles or ID-token-as-bearer shortcut. Failed/expired sessions unmount workspace and require a fresh page before reauthentication, avoiding cross-account reuse of workspace caches. Logout reloads the app. Requests cannot forward bearer outside the same origin or across redirects and mutations are not silently replayed.

MSAL Browser **5.29.0** requires its own bridge page. `auth-redirect.html` is a separate Vite build entry and runs only Microsoft's `broadcastResponseToMainFrame`. Register the exact `https://YOUR_APP_ORIGIN/auth-redirect.html` URI as an SPA redirect. Serve it without COOP. Middleware permits only self framing for that bridge (needed by silent flows); all other pages use `frame-ancestors 'none'`. No Dynamics iframe/postMessage integration is claimed.

Required deployment configuration (no actual customer values available):

```text
CQA_AUTH_MODE=entra
CQA_DEPLOYMENT=external
CQA_BIND_HOST=127.0.0.1
CQA_AUTH_ORIGINS=https://YOUR_APP_ORIGIN
CQA_ENTRA_TENANT_ID=<canonical tenant UUID>
CQA_ENTRA_API_CLIENT_ID=<separate API registration UUID>
CQA_ENTRA_CLIENT_IDS=<allowed SPA registration UUID, first is public client>
CQA_ENTRA_SCOPE=access_as_user
CQA_TRUSTED_PROXY_IPS=127.0.0.1
```

Use `api://<API UUID>/access_as_user` as the exposed delegated scope; request v2 access tokens; define the four app roles on the API registration and assign users/groups through Entra. The SPA needs no secret. Backend JWT verification needs no tenant secret. Production HTTPS termination must preserve Host and be the only proxy trusted for forwarded scheme/address. `npm start` uses the checked Python entrypoint on port 8000; local mode disables proxy-header trust. Never use a wildcard trusted proxy list.

Optional backend dependency file: `backend/integrations/requirements-enterprise.txt` (PyJWT[crypto] 2.15.1, Microsoft MSAL 1.39.0). The local project had cryptography/httpx but no PyJWT/Authlib/MSAL. Standard libraries were chosen instead of handwritten cryptography. For testing, packages were installed only under ignored `tmp/enterprise-security-deps`, leaving the shared Python environment unchanged. The official browser package is a pinned application dependency.

## Dynamics 365 / Dataverse adapter (not tenant-integrated)

`backend/integrations/dynamics.py` implements real server-side MSAL application-token acquisition for `<environment>/.default`, GET `WhoAmI`, and allowlisted entity record reads via `/api/data/v9.2/<entityset>(<UUID>)?$select=...`. Table/column metadata must be explicitly configured from the customer's solution. Only exact HTTPS public-cloud `*.crm[region].dynamics.com` environments are accepted; no arbitrary URL paths, query injection, userinfo or custom ports. No redirects/nextLinks are followed. Response size is bounded to 2 MiB, timeouts are set, upstream failures are sanitized, and Retry-After is bounded without automatic replay. No CRM write or import into working SQLite is performed.

App-only identity is **not** the signed-in user's identity. Any future API exposing adapter data must first enforce Freedom Quality RBAC/ownership and then Dataverse least-privilege application-user security roles. Needed externally: real environment URL, tenant ID, application registration, certificate (preferred) or server secret, corresponding Dataverse application user, appropriate Dataverse security role, table/column mapping and approved data flow. Backend-owned credentials only; none in public auth config or browser. Government/sovereign clouds and generic Dynamics iframe hosting are not implemented.

## Threats and limits

| Threat | Control / remaining constraint |
|---|---|
| Unsigned token / algorithm confusion / other tenant or resource token | Fixed RS256, trusted JWKS, issuer/audience/tid/azp/scope/version validation |
| Browser/header role forgery | Only verified signed app roles are used |
| Operator IDOR / displayname collision | Deny absent exact stable ownership; global projections remain closed |
| Forged appeal author / decision author | Verified actor inside transaction and surfaced through agreed DTO fields |
| Public deployment accidentally unauthenticated | Startup config checks + loopback peer/host/forwarding restrictions; external must use Entra |
| SSRF / bearer exfiltration through CRM redirects | Pinned authority/environment, input allowlists, no redirects |
| Audio bearer leakage | Same-origin fetch/blob helper, no query tokens, 32 MiB bound, cleanup contract |
| XSS / stolen bearer / token revocation delay | Memory cache reduces persistence; XSS prevention/short token lifetimes/Entra conditional access still necessary; no custom continuous access evaluation |
| Stolen app secret / broad CRM privileges | Server secret manager/certificate and Dataverse least privilege required externally |
| Key endpoint outage / unknown-kid flooding | Fail closed, bounded cache/cooldown; edge rate limiting remains deployment work |
| Workspace data in iframe | CSP forbids embedding; adapter-only integration is the stated scope |

## Sources and skill vetting

Read requested local `find-skills` and `verification-before-completion`. Targeted skills.sh lookup included Microsoft/Entra/OIDC/RBAC/Dynamics. CLI searches were attempted but npm DNS failed in the restricted environment, so primary web/source inspection was used. No external skill was installed. Microsoft `azure-skills` is official (~1.6k repository stars at inspection); its app-registration source was reviewed. Useful registration/MSAL concepts were cross-checked against current Microsoft docs; its generic implicit-flow suggestion was not adopted. Azure RBAC infrastructure roles are not application RBAC. ClawLink Dynamics skill showed only 2 installs and introduces a third-party credential broker, so it was rejected for this task. Popularity is not treated as proof of security.

- [Microsoft Entra claims validation](https://learn.microsoft.com/en-us/entra/identity-platform/claims-validation)
- [Microsoft access token claims](https://learn.microsoft.com/en-us/entra/identity-platform/access-token-claims-reference)
- [Microsoft app roles](https://learn.microsoft.com/en-us/entra/identity-platform/howto-add-app-roles-in-apps)
- [MSAL initialization](https://learn.microsoft.com/en-us/entra/msal/javascript/browser/initialization)
- [MSAL token acquisition](https://learn.microsoft.com/en-us/entra/msal/javascript/browser/acquire-token)
- [MSAL memory caching](https://learn.microsoft.com/en-us/entra/msal/javascript/browser/caching)
- [MSAL v5 redirect bridge](https://learn.microsoft.com/en-us/entra/msal/javascript/browser/redirect-bridge)
- [Dataverse OAuth / application user](https://learn.microsoft.com/en-us/power-apps/developer/data-platform/authenticate-oauth)
- [Dataverse HTTP and errors](https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/compose-http-requests-handle-errors)
- [Dataverse service protection limits](https://learn.microsoft.com/en-us/power-apps/developer/data-platform/api-limits)
- [MSAL Python acquire tokens](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens)
- [PyJWT usage](https://pyjwt.readthedocs.io/en/stable/usage.html)
- [Microsoft skill source inspected](https://github.com/microsoft/azure-skills/blob/main/skills/entra-app-registration/SKILL.md)
- [Rejected third-party Dynamics skill](https://www.skills.sh/clawlink-hq/skills/dynamics-365)

## Exact verification environment / reproducibility

The project launcher selects **`C:\Users\plddo\OneDrive\Документы\ChatGPT\hack\.venv311\Scripts\python.exe`**, Python 3.11.18. The isolated optional dependencies live at **`C:\Users\plddo\OneDrive\Документы\ChatGPT\hack\tmp\enterprise-security-deps`**. Observed versions: PyJWT 2.15.1, cryptography 50.0.2, MSAL 1.39.0. Without this PYTHONPATH (or equivalent installation into the selected test environment), cryptographic/MSAL tests intentionally skip; **60 passed / 35 skipped is not enterprise acceptance**.

Reproduce the targeted 95-test run from the project folder in PowerShell:

```powershell
$env:PYTHONPATH = (Resolve-Path 'tmp/enterprise-security-deps').Path
node scripts/python.mjs -m pytest -q backend/tests/test_enterprise_access.py backend/tests/test_dynamics_adapter.py -o cache_dir=tmp/enterprise-verified-cache --basetemp=tmp/enterprise-verified
```

Fresh targeted result before the final DTO assertions: **95 passed, 0 skipped, 8.65s**. Includes actual API factory wiring, all roles, batch cancel denial, ownership deny, local mode, missing config before DB creation, SQLite actor persistence, actor spoof rejection and audit-failure rollback. A subsequent full-suite run includes the added DTO assertions. On this Windows runner, the restricted process environment hangs in Python asyncio's local socketpair initialization; these tests ran with permission for local sockets. All identity/Dataverse traffic is mocked. No real tenant or model call is needed.

If the ignored dependency folder is absent, install `backend/integrations/requirements-enterprise.txt` in the intended project test environment, or recreate an isolated target. The original command (system Python provides pip for the project's pip-less interpreter) was:

```powershell
python -m pip --python .venv311/Scripts/python.exe install --target tmp/enterprise-security-deps -r backend/integrations/requirements-enterprise.txt
```

Other completed checks:

- `node --test src/services/auth.test.mjs src/services/contracts.test.mjs src/features/batchPlan.test.mjs src/features/conversationTitle.test.mjs`: **33 passed**, no failures. MSAL calls are mocked only in tests; token forwarding constraints, no anonymous fallback, memory cache, audio origin/MIME/stream cap and blob disposal are exercised.
- `tests/browser_enterprise_access.py` via the local `with_server.py` helper on port 5219: **3 scenarios passed**, no page errors/external requests. Actual MSAL SDK initialization, no workspace fetch before login, invalid-config deny, local no-registration mode, keyboard focus and 1440/375px layout. No sign-in or token acquisition against a tenant was attempted. Screenshots visually reviewed in `tmp/enterprise-browser-qa/login-desktop.png` and `login-mobile.png`.
- The browser bridge is present in Vite build output. Build/types and generated-contract checks are recorded in final result below.

Full suite used the same interpreter/PYTHONPATH, distinct `--basetemp=tmp/enterprise-full`, `-o cache_dir=tmp/enterprise-full-cache`, `-o faulthandler_timeout=45`. It has completed; results below. It was not rerun wholesale.

## Files owned/changed by this task

- New backend: `backend/security.py`, `backend/security_access.py`, `backend/security_repository.py`.
- New integration: `backend/integrations/__init__.py`, `backend/integrations/dynamics.py`, `backend/integrations/requirements-enterprise.txt`.
- After PM ownership handoff: narrow auth wiring in `backend/app.py`, checked launch in `backend/main.py` (batch routes/lifespan/worker lock preserved).
- New frontend: `src/services/auth.ts`, `src/services/authMedia.ts`, `src/components/AuthGate.tsx`, `src/components/AuthGate.css`, `auth-redirect.html`.
- After PM ownership handoff: `src/services/api.ts` transport wiring, `src/App.tsx` gate/logout only; `package.json`/`package-lock.json` official MSAL dependency and test/launch commands; `vite.config.ts` bridge build entry.
- Tests: `backend/tests/test_enterprise_access.py`, `backend/tests/test_dynamics_adapter.py`, `backend/tests/conftest.py` loopback test transport only, `src/services/auth.test.mjs`, `tests/browser_enterprise_access.py`.
- Report: `docs/audit/enterprise-access-acceptance.md`.
- Ignored test-only outputs/dependencies under `tmp/enterprise-*`; built output under `dist/`. The staged `backend/integrations/entra-browser.ts` was removed when promoted to the real frontend module.

Other authors own the batch implementation/test lifecycle fixes, DTO/codecs/generated contracts, voice player integration and previous UI cleanup. They are not attributed to this task or reverted. No working-server restart was performed.

## External blockers / readiness scope

1. **Live SSO/Dataverse E2E blocked:** no tenant, application registrations, real redirect registration, assignments/consent, credentials, Dataverse environment or tenant administrator. Unit/API/browser mock acceptance cannot establish live tenant integration.
2. **Operator data workflow intentionally unavailable:** no stable operator-to-conversation mapping. Operators see an honest access-not-open screen; all data/appeal/batch/export requests are denied. No displayname inference.
3. **Dynamics is adapter-only:** no customer table mapping, migration/synchronization, CRM write endpoint, application user role or iframe experience. Current CSP explicitly prevents app embedding; a future embedding design needs a separate reviewed parent/identity/origin contract.
4. **Deployment is not executed:** TLS/proxy configuration, secret/certificate management, operational rate limits, audit retention, and tenant policy belong to deployment acceptance. No live customer security certification is claimed.

## Final verification result / code freeze

- Full Python suite: **657 passed, 1 failed, 0 skipped, 130.92s**. The sole failure was `backend/tests/test_scoring_properties.py::test_bounded_score_and_na_denominator_are_invariant_to_order`, a Hypothesis `FailedHealthCheck` for slow input generation (3 valid inputs after 1.80s), not a failing score assertion. All enterprise/Dynamics/API/actor DTO tests passed in that run.
- Repeated **only that test**, unmodified, with the exact reported `--hypothesis-seed=247958206421654271502060309455306256797`: **1 passed in 0.85s**. No health check/deadline was disabled and no production code changed. Do not describe the original full run as entirely green; independent QA can assess timing stability under its load.
- JavaScript combined contract/batch/title/auth/media tests: **33 passed, 0 failed**.
- `npm run build`: passed after final auth/media/DTO integration; TypeScript check passed, dedicated MSAL redirect bridge emitted.
- `node scripts/python.mjs scripts/generate_contracts.py --check`: **41 contract artifacts checked**.
- Browser auth acceptance: **3 passed**, zero external requests/page errors; desktop/mobile screenshots visually inspected.
- `git diff --check` on owned shared integration files: passed (only the repository's informational LF/CRLF warning).

**Code freeze:** this task's auth/backend/browser/media-helper code is ready for QA. The worker on localhost:8000 was not restarted. `dist/` was rebuilt; the running old backend must be restarted in the coordinated rollout before using localhost:8000 to assess the new `/api/auth/config` flow. No live tenant login, CRM data access, paid model API, publish/deploy, commit/push or Telegram message occurred.
