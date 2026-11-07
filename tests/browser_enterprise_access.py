"""Login UI acceptance with only intercepted API responses, no tenant calls."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

OUT = Path(__file__).resolve().parents[1] / "tmp" / "enterprise-browser-qa"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:5219"
CONFIG = {"mode": "entra", "authenticationRequired": True,
          "authority": "https://login.microsoftonline.com/11111111-1111-4111-8111-111111111111",
          "clientId": "33333333-3333-4333-8333-333333333333",
          "scopes": ["api://22222222-2222-4222-8222-222222222222/access_as_user"]}
report = {"checks": [], "pageErrors": [], "externalRequests": []}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for name, config in (("login", CONFIG), ("invalid", {"mode": "entra"}),
                         ("local", {"mode": "local", "authenticationRequired": False})):
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        calls = []
        page.on("pageerror", lambda error: report["pageErrors"].append(str(error)))
        def api(route):
            path = route.request.url.split("/api/", 1)[1]
            calls.append(path)
            if path == "auth/config":
                route.fulfill(json=config)
            elif path == "batches/current":
                route.fulfill(json={"schema_version": "2.0", "batch": None})
            else:
                route.fulfill(status=503, json={"schema_version": "2.0", "code": "FIXTURE_UNAVAILABLE",
                    "message": "Test data unavailable", "details": [], "request_id": "offline", "retryable": True})
        page.route("**/api/**", api)
        def external(route):
            report["externalRequests"].append(route.request.url.split("?")[0])
            route.abort()
        page.route("https://**", external)
        page.goto(BASE)
        page.wait_for_load_state("networkidle")
        if name == "login":
            button = page.get_by_role("button", name="Войти через Microsoft", exact=True)
            expect(button).to_be_visible()
            button.focus()
            expect(button).to_be_focused()
            assert calls == ["auth/config"]
            page.screenshot(path=str(OUT / "login-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 375, "height": 812})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            expect(button).to_be_visible()
            page.screenshot(path=str(OUT / "login-mobile.png"), full_page=True)
            report["checks"].append("Actual MSAL initialization + accessible login shell; no workspace request before sign-in; desktop/mobile fit")
        elif name == "invalid":
            expect(page.get_by_role("alert")).to_contain_text("не настроен")
            assert calls == ["auth/config"]
            report["checks"].append("Invalid enterprise configuration shows an error and never mounts workspace")
        else:
            expect(page.locator(".app-shell")).to_be_visible()
            assert "state" in calls
            expect(page.get_by_role("button", name="Войти через Microsoft", exact=True)).to_have_count(0)
            report["checks"].append("Local mode opens existing workspace without sign-in; intercepted fixture API only")
        context.close()
    browser.close()

assert not report["pageErrors"], report["pageErrors"]
assert not report["externalRequests"], report["externalRequests"]
(OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("PASS: 3 browser scenarios; no external requests or page errors")
