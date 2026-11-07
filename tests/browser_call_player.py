"""Read-only call player acceptance against running UI and real local media.

python tests/browser_call_player.py --url http://127.0.0.1:5197
No model calls: every non-read API request is blocked. Failure/loading cases
intercept only media requests; buffering/rejected-play cases inject media events.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

sys.stdout.reconfigure(encoding="utf-8")
parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:5197")
parser.add_argument("--stub-missing-batch", action="store_true", help="Compatibility for shared UI ahead of running backend; only a 404 current-batch GET is stubbed")
parser.add_argument("--stub-missing-auth", action="store_true", help="Compatibility for shared UI ahead of running backend; only a 404 auth-config GET is stubbed to local mode")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
output = root / "tmp" / "call-player"
output.mkdir(parents=True, exist_ok=True)
report = {"checks": [], "pageErrors": [], "mutationsBlocked": [], "screenshots": [], "compatibilityStubs": []}


def passed(label):
    report["checks"].append(label)
    print("PASS", label)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")

    def guard(route):
        if route.request.method not in ("GET", "HEAD", "OPTIONS"):
            report["mutationsBlocked"].append(route.request.url)
            route.abort()
        elif args.stub_missing_batch and route.request.url.endswith("/api/batches/current"):
            response = route.fetch()
            if response.status == 404:
                report["compatibilityStubs"].append("GET /api/batches/current: 404 -> empty batch (shared backend predates parallel batch work)")
                route.fulfill(status=200, json={"schema_version": "2.0", "batch": None})
            else:
                route.fulfill(response=response)
        elif args.stub_missing_auth and route.request.url.endswith("/api/auth/config"):
            response = route.fetch()
            if response.status == 404:
                report["compatibilityStubs"].append("GET /api/auth/config: 404 -> local mode (shared backend predates auth work)")
                route.fulfill(status=200, json={"mode": "local", "authenticationRequired": False})
            else:
                route.fulfill(response=response)
        else:
            route.continue_()

    context.route("**/api/**", guard)
    page = context.new_page()
    page.on("pageerror", lambda e: report["pageErrors"].append(str(e)))
    state = context.request.get(args.url + "/api/state").json()
    calls = [c for c in state["data"]["conversations"] if c["channel"] == "voice call" and c["audioUrl"]]
    call = next(c for c in calls if c["title"] == "2")
    second = next(c for c in calls if c["id"] != call["id"])
    audio_requests = []
    page.on("request", lambda r: audio_requests.append(r.url) if re.search(r"/audio(?:\?|$)", r.url) else None)

    def navigate(identifier):
        page.evaluate("id => { location.hash = encodeURIComponent('dialog/' + id); }", identifier)
        expect(page.locator(".detail-heading .eyebrow")).to_contain_text(identifier)

    def ready():
        page.wait_for_function("document.querySelector('audio')?.readyState >= 3", timeout=60000)
        expect(page.get_by_role("slider", name="Позиция записи")).to_be_enabled()

    def media():
        return page.locator("audio").evaluate("a => ({duration:a.duration, position:a.currentTime, paused:a.paused, volume:a.volume, muted:a.muted})")

    def shot(name, full=False):
        path = output / (name + ".png")
        page.screenshot(path=str(path), full_page=full)
        report["screenshots"].append(str(path.relative_to(root)))

    page.goto(args.url + "/#conversations")
    page.wait_for_load_state("networkidle")
    assert audio_requests == [], audio_requests
    page.get_by_role("textbox", name="Поиск диалогов", exact=True).fill(call["id"])
    link = page.locator(".dialog-link").filter(has_text="Звонок от")
    expect(link).to_have_count(1)
    title = link.inner_text().strip()
    link.click()
    expect(page.locator(".detail-heading h1")).to_have_text(title)
    ready()
    assert media()["paused"] and media()["duration"] > 0
    assert all(call["id"] in url for url in audio_requests), audio_requests
    report["realAudio"] = {"id": call["id"], "durationSeconds": media()["duration"], "title": title}
    passed("List/detail title match; no audio requested by list; selected real audio preloads without autoplay")
    expect(page.get_by_role("combobox", name="Версия для новой оценки")).to_be_visible()
    expect(page.get_by_role("button", name="Оценить заново", exact=True)).to_be_visible()
    shot("desktop-1440")
    page.locator(".call-audio-panel").screenshot(path=str(output / "player-1440.png"))

    play = page.get_by_role("button", name="Воспроизвести запись", exact=True)
    play.focus()
    page.keyboard.press("Space")
    page.wait_for_function("document.querySelector('audio').currentTime > 0")
    pause = page.get_by_role("button", name="Приостановить запись", exact=True)
    pause.focus()
    page.keyboard.press("Enter")
    assert media()["paused"]
    timeline = page.get_by_role("slider", name="Позиция записи")
    timeline.fill("10")
    page.wait_for_function("Math.abs(document.querySelector('audio').currentTime - 10) < .2")
    timeline.focus()
    page.keyboard.press("ArrowRight")
    assert 10 < media()["position"] < 11
    assert timeline.get_attribute("aria-valuetext").startswith("0:10 из")
    page.keyboard.press("End")
    assert abs(media()["position"] - media()["duration"]) < .2
    page.keyboard.press("Home")
    assert media()["position"] < .2
    page.get_by_role("slider", name="Громкость записи").fill("0.35")
    assert abs(media()["volume"] - .35) < .01
    page.get_by_role("button", name="Выключить звук", exact=True).click()
    assert media()["muted"]
    page.get_by_role("button", name="Включить звук", exact=True).click()
    assert not media()["muted"]
    passed("Actual playback, pause, seek, keyboard arrows/Home/End, volume and mute")

    offset = page.get_by_label("Смещение записи, сек.")
    offset.fill("3.5")
    page.get_by_role("button", name=re.compile("К записи реплики")).first.click()
    page.wait_for_function("Math.abs(document.querySelector('audio').currentTime - 3.5) < .2")
    assert media()["paused"]
    offset.fill("99999")
    page.get_by_role("button", name=re.compile("К записи реплики")).first.click()
    expect(page.get_by_text("Метка выходит за длительность записи. Скорректируйте смещение.")).to_be_visible()
    assert media()["position"] < 10
    offset.fill("0")
    passed("Transcript seek respects manual offset, stays paused, rejects out-of-range timestamps")

    page.set_viewport_size({"width": 375, "height": 900})
    offset.evaluate("e => e.blur()")
    page.evaluate("scrollTo(0,0)")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    shot("mobile-375")
    page.locator(".call-audio-panel").screenshot(path=str(output / "player-375.png"))
    for control in page.locator(".call-player button, .call-player input").all():
        bounds = control.bounding_box()
        assert bounds["width"] >= 44 and bounds["height"] >= 44, bounds
    page.get_by_role("button", name="Воспроизвести запись", exact=True).focus()
    page.keyboard.press("Tab")
    page.keyboard.press("Shift+Tab")
    assert page.locator(".call-player-play").evaluate("e => getComputedStyle(e).outlineStyle") != "none"
    expect(page.get_by_role("combobox", name="Версия для новой оценки")).to_be_visible()
    expect(page.get_by_role("button", name="Оценить заново", exact=True)).to_be_visible()
    page.set_viewport_size({"width": 900, "height": 375})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    passed("375px mobile and landscape fit, 44px controls, visible keyboard focus; evaluation/version remain expanded")

    page.set_viewport_size({"width": 1440, "height": 1000})
    page.evaluate("window.previousAudio = document.querySelector('audio')")
    offset.fill("5")
    page.get_by_role("button", name="Воспроизвести запись", exact=True).click()
    navigate(second["id"])
    ready()
    assert page.evaluate("previousAudio.paused && !previousAudio.hasAttribute('src')")
    assert media()["paused"] and media()["position"] == 0
    expect(offset).to_have_value("0")
    passed("Call switch pauses/releases previous media and resets offset/position without autoplay")

    # Hold a real media request until the UI has displayed loading and queued a seek.
    held = []
    media_pattern = "**/api/conversations/" + call["id"] + "/audio"
    page.route(media_pattern, lambda route: held.append(route))
    navigate(call["id"])
    expect(page.locator(".call-player-status")).to_have_text("Загружаем запись…")
    expect(timeline).to_be_disabled()
    offset.fill("7")
    page.get_by_role("button", name=re.compile("К записи реплики")).first.click()
    shot("loading-1440")
    page.wait_for_function("document.querySelector('audio').networkState === 2")
    assert held
    for route in held:
        try:
            route.continue_()
        except Exception as error:
            # StrictMode can abort the first media request before effect replay.
            if "already handled" not in str(error):
                raise
    page.unroute(media_pattern)
    ready()
    page.wait_for_function("Math.abs(document.querySelector('audio').currentTime - 7) < .2")
    assert media()["paused"]
    passed("Delayed media shows loading; transcript seek before metadata is applied once ready")

    page.locator("audio").dispatch_event("waiting")
    expect(page.locator(".call-player-status")).to_have_text("Буферизация…")
    page.locator("audio").dispatch_event("canplay")
    expect(page.locator(".call-player-status")).to_have_text("Запись готова")
    passed("Injected waiting/canplay events update buffering feedback")

    page.clock.install()
    page.clock.fast_forward(25000)
    expect(page.locator(".call-player-status")).to_have_text("Запись готова")
    page.locator("audio").dispatch_event("waiting")
    expect(page.locator(".call-player-status")).to_have_text("Буферизация…")
    page.clock.fast_forward(20001)
    expect(page.get_by_role("button", name="Повторить загрузку", exact=True)).to_be_visible()
    expect(page.locator(".call-player-status")).to_contain_text("Загрузка задерживается")
    page.get_by_role("button", name="Повторить загрузку", exact=True).click()
    ready()
    page.wait_for_function("Math.abs(document.querySelector('audio').currentTime - 7) < .2")
    assert media()["paused"]
    page.locator("audio").dispatch_event("waiting")
    page.locator("audio").dispatch_event("canplay")
    page.clock.fast_forward(20001)
    expect(page.locator(".call-player-status")).to_have_text("Запись готова")
    page.locator("audio").dispatch_event("waiting")
    page.locator("audio").dispatch_event("pause")
    page.clock.fast_forward(20001)
    expect(page.locator(".call-player-status")).to_have_text("Запись готова")
    passed("Late buffering gets its own 20s retry timeout; retry preserves position; canplay/pause cancel timeout")

    # A real browser media load error followed by retry of the real available audio.
    page.route(media_pattern, lambda route: route.fulfill(status=503, content_type="text/plain", body="Unavailable for test"))
    navigate(second["id"])
    navigate(call["id"])
    expect(page.get_by_role("button", name="Повторить загрузку", exact=True)).to_be_visible(timeout=15000)
    expect(page.locator(".call-player-status")).to_contain_text("Не удалось загрузить запись")
    expect(page.get_by_role("button", name="Воспроизвести запись", exact=True)).to_be_disabled()
    shot("error-1440")
    page.unroute(media_pattern)
    page.get_by_role("button", name="Повторить загрузку", exact=True).click()
    ready()
    assert media()["paused"]
    passed("HTTP 503 displays accessible error/retry; retry reloads real audio without autoplay")

    page.locator("audio").evaluate("a => { a.play = () => Promise.reject(new DOMException('Blocked for test', 'NotAllowedError')); }")
    page.get_by_role("button", name="Воспроизвести запись", exact=True).click()
    expect(page.locator(".call-player-status")).to_contain_text("Не удалось начать воспроизведение")
    page.locator("audio").evaluate("a => { delete a.play; }")
    page.get_by_role("button", name="Повторить загрузку", exact=True).click()
    ready()
    passed("Rejected play promise is handled with an accessible recovery action")

    page.evaluate("window.previousAudio = document.querySelector('audio'); location.hash = 'conversations'")
    expect(page.locator(".call-player")).to_have_count(0)
    assert page.evaluate("previousAudio.paused && !previousAudio.hasAttribute('src')")
    # Browser-only source fixtures verify both visible date locations together.
    for index, (date, provenance, title, metadata) in enumerate([
        ("2026-11-03T20:00:00Z", "conversation", "Звонок от 4 ноября", "4 ноября 2026 г."),
        ("2026-11-03T20:00:00Z", "import", "Звонок · дата не указана", "Дата не указана"),
        (None, "conversation", "Звонок · дата не указана", "Дата не указана"),
        ("2026-11-08", "conversation", "Звонок от 8 ноября", "8 ноября 2026 г."),
    ]):
        fixture = json.loads(json.dumps(state))
        record = next(c for c in fixture["data"]["conversations"] if c["id"] == call["id"])
        record["date"], record["dateSource"] = date, provenance
        page.route("**/api/state", lambda route: route.fulfill(json=fixture))
        page.goto(args.url + "/?call-date-fixture=" + str(index) + "#dialog%2F" + call["id"])
        expect(page.locator(".detail-heading h1")).to_have_text(title)
        expect(page.locator(".transcript-panel .panel-heading p")).to_contain_text(metadata)
        page.get_by_role("button", name="Все диалоги", exact=True).click()
        page.get_by_role("textbox", name="Поиск диалогов", exact=True).fill(call["id"])
        row = page.locator("tbody tr").filter(has_text=call["id"])
        expect(row.locator(".dialog-link")).to_have_text(title)
        if provenance == "import" or date is None:
            expect(row.locator(".table-date")).to_have_text("Дата не указана")
        if date is None or provenance == "import" or "T" not in date:
            expect(row.locator(".table-date + .table-caption")).to_have_count(0)
        page.unroute("**/api/state")
    passed("Browser date fixtures: timezone midnight, import/missing/date-only agree in list/heading/operator; no invented time")
    fixture = json.loads(json.dumps(state))
    record = next(c for c in fixture["data"]["conversations"] if c["id"] == call["id"])
    record["evaluation"]["status"] = "needs_review"
    record["scores"] = None
    missing = record["evaluation"]["results"][0]
    missing.update(assessment_status="unassessable", value=None, applicability_reason=None,
                   review_reason="Для проверки дикции нет пригодных аудиоданных.")
    not_applicable = record["evaluation"]["results"][1]
    not_applicable.update(assessment_status="assessed", value=None, review_reason=None,
                          applicability_reason="Критерий неприменим в тестовом сценарии.")
    page.route("**/api/state", lambda route: route.fulfill(json=fixture))
    page.goto(args.url + "/?unassessable-fixture=1#dialog%2F" + call["id"])
    missing_card = page.locator(".result-card").first
    expect(missing_card.locator(".result-card-top > strong")).to_have_text("Недостаточно данных")
    expect(missing_card).to_contain_text(missing["review_reason"])
    expect(missing_card).to_contain_text("Оценка не выставлена")
    assert "N/A" not in missing_card.inner_text()
    assert "исключён из расчёта" not in missing_card.inner_text()
    expect(missing_card.get_by_role("button", name="Оспорить", exact=True)).to_have_count(0)
    expect(page.locator(".result-card").nth(1).locator(".result-card-top > strong")).to_have_text("N/A")
    expect(page.locator(".score-summary")).not_to_contain_text("69,47")
    expect(page.get_by_role("combobox", name="Версия для новой оценки")).to_be_visible()
    expect(page.get_by_role("button", name="Оценить заново", exact=True)).to_be_visible()
    shot("insufficient-data-1440")
    page.set_viewport_size({"width": 375, "height": 900})
    missing_card.scroll_into_view_if_needed()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    shot("insufficient-data-375")
    page.unroute("**/api/state")
    passed("Unassessable criterion shows Недостаточно данных + reviewReason, no score/N/A/exclusion; real N/A and reevaluation controls preserved")
    assert not report["pageErrors"], report["pageErrors"]
    assert not report["mutationsBlocked"], report["mutationsBlocked"]
    passed("Unmount releases media; no page errors and no mutating API requests")
    browser.close()

(output / "acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
