"""Isolated AudioPlayer + actual authMedia helper with synthetic session/audio.

No real identity, database, audio, or model calls. The session dependency alone
is replaced by a browser module; all /api requests are fixture-only and guarded.
"""
import io
import json
from pathlib import Path
import sys
import wave
from playwright.sync_api import sync_playwright, expect

sys.stdout.reconfigure(encoding="utf-8")
output = Path(__file__).resolve().parents[1] / "tmp" / "call-player"
output.mkdir(parents=True, exist_ok=True)
report = {"checks": [], "pageErrors": [], "unauthorizedRequests": []}
wav = io.BytesIO()
with wave.open(wav, "wb") as writer:
    writer.setnchannels(1)
    writer.setsampwidth(2)
    writer.setframerate(8000)
    writer.writeframes(b"\x00\x00" * 8000 * 4)
wav_bytes = wav.getvalue()
html = """<!doctype html><html><head><link rel="stylesheet" href="/src/styles.css"></head><body><div id="root"></div>
<script type="module">
import RefreshRuntime from '/@react-refresh';
RefreshRuntime.injectIntoGlobalHook(window);
window.$RefreshReg$ = () => {};
window.$RefreshSig$ = () => type => type;
window.__vite_plugin_react_preamble_installed__ = true;
await import('/tests/fixtures/call-player-auth.tsx');
</script></body></html>"""
session_module = """export async function getEnterpriseSession() {
 return { mode: window.fixtureMode || 'entra', fetch: (url, opts={}) => {
   window.authorizedFetches.push(url);
   opts.signal?.addEventListener('abort', () => window.abortedFetches.push(url));
   return fetch(url, {...opts, headers:{Authorization:'Bearer fixture-token-not-valid'}});
 }};
}"""

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 800, "height": 600})
    page.on("pageerror", lambda e: report["pageErrors"].append(str(e)))
    page.add_init_script("""window.createdBlobs=[];window.revokedBlobs=[];window.authorizedFetches=[];window.abortedFetches=[];
 const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
 URL.createObjectURL = b => { const u=create(b);window.createdBlobs.push(u);return u; };
 URL.revokeObjectURL = u => {window.revokedBlobs.push(u);revoke(u);};""")
    page.route("**/__call_player_fixture", lambda r: r.fulfill(content_type="text/html", body=html))
    page.route("**/src/services/auth.ts*", lambda r: r.fulfill(content_type="text/javascript", body=session_module))
    held = []
    failures = {"value": False}

    def media_route(route):
        path = route.request.url.split("/api/")[-1]
        if page.evaluate("window.fixtureMode") != "local" and route.request.headers.get("authorization") != "Bearer fixture-token-not-valid":
            report["unauthorizedRequests"].append(path)
            route.abort()
        elif path == "conversations/slow/audio":
            held.append(route)
        elif failures["value"]:
            route.fulfill(status=403, body="Denied by isolated fixture")
        else:
            route.fulfill(status=200, content_type="audio/wav", body=wav_bytes)

    page.route("**/api/**", media_route)
    page.goto("http://127.0.0.1:5197/__call_player_fixture")

    def ready():
        try:
            page.wait_for_function("document.querySelector('audio')?.readyState >= 3")
        except Exception:
            print(json.dumps({"errors": report["pageErrors"], "body": page.locator("body").inner_text()}, ensure_ascii=False))
            raise

    ready()
    assert page.locator("audio").evaluate("a => a.src.startsWith('blob:') && a.paused")
    assert page.get_by_role("link", name="Открыть запись").get_attribute("href").startswith("blob:")
    assert not report["unauthorizedRequests"]
    report["checks"].append("Enterprise media uses authorized fetch then blob; no anonymous media src, no autoplay")
    initial = page.locator("audio").evaluate("a => a.src")
    page.evaluate("setAudioSource('/api/conversations/b/audio')")
    ready()
    page.wait_for_function("url => revokedBlobs.includes(url)", arg=initial)
    assert page.locator("audio").evaluate("a => a.paused")
    report["checks"].append("Source switch revokes previous object URL")

    page.evaluate("setAudioSource('/api/conversations/slow/audio'); window.pendingSeek=1.5")
    expect(page.locator(".call-player-status")).to_have_text("Загружаем запись…")
    expect(page.get_by_role("button", name="Воспроизвести запись", exact=True)).to_be_disabled()
    page.wait_for_function("authorizedFetches.includes('/api/conversations/slow/audio')")
    while not held:
        page.wait_for_timeout(20)
    for route in held:
        route.fulfill(status=200, content_type="audio/wav", body=wav_bytes)
    held.clear()
    ready()
    page.wait_for_function("Math.abs(document.querySelector('audio').currentTime-1.5)<.1")
    assert page.locator("audio").evaluate("a => a.paused")
    report["checks"].append("Pending transcript seek survives delayed authorized fetch/metadata without autoplay")

    page.evaluate("setAudioSource('/api/conversations/b/audio')")
    ready()
    page.evaluate("setAudioSource('/api/conversations/slow/audio')")
    page.wait_for_function("authorizedFetches.filter(u => u.includes('/slow/')).length >= 2")
    page.evaluate("setAudioSource('/api/conversations/c/audio')")
    ready()
    assert page.evaluate("abortedFetches.includes('/api/conversations/slow/audio')")
    for route in held:
        route.abort()
    held.clear()
    report["checks"].append("Source switch aborts in-flight authorized fetch")

    failures["value"] = True
    page.evaluate("setAudioSource('/api/conversations/denied/audio')")
    expect(page.locator(".call-player-status")).to_have_text("Не удалось получить доступ к записи.")
    expect(page.get_by_role("button", name="Повторить загрузку", exact=True)).to_be_visible()
    assert page.locator("audio").evaluate("a => !a.hasAttribute('src')")
    expect(page.get_by_role("link", name="Открыть запись")).to_have_count(0)
    failures["value"] = False
    page.get_by_role("button", name="Повторить загрузку", exact=True).click()
    ready()
    assert page.locator("audio").evaluate("a => a.src.startsWith('blob:') && a.paused")
    report["checks"].append("Rejected authorized fetch displays retry; no public fallback; successful retry remains paused")

    page.evaluate("unmountPlayer()")
    assert page.evaluate("createdBlobs.every(url => revokedBlobs.includes(url))")
    report["checks"].append("Unmount disposes all created blob URLs including StrictMode lifecycle")
    page.goto("http://127.0.0.1:5197/__call_player_fixture")
    ready()
    page.evaluate("window.fixtureMode='local'; setAudioSource('/api/conversations/local/audio')")
    ready()
    page.wait_for_function("document.querySelector('audio').src.endsWith('/api/conversations/local/audio')")
    assert page.locator("audio").evaluate("a => a.preload === 'auto' && a.paused")
    assert page.evaluate("!authorizedFetches.includes('/api/conversations/local/audio')")
    report["checks"].append("Local mode keeps original URL/preload without authorized blob fetch")
    assert not report["pageErrors"], report["pageErrors"]
    assert not report["unauthorizedRequests"], report["unauthorizedRequests"]
    browser.close()

(output / "auth-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
