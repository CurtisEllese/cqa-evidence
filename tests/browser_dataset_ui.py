"""Browser checks with synthetic API responses; never modifies the workspace DB."""
import argparse
from copy import deepcopy
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url', default='http://127.0.0.1:5173/')
args = parser.parse_args()
artifacts = Path(__file__).resolve().parents[1] / 'tmp' / 'dataset-ui'
artifacts.mkdir(parents=True, exist_ok=True)

def conversation(identifier):
    return dict(schema_version="2.0", id=identifier, contentHash=identifier,
                title=identifier, operator="Оператор", initials="ОП", language="RU",
                channel="agent chat", date=None, duration="00:01", transcript=[],
                audioUrl=None, evaluation=None, scores=None)


initial = dict(schema_version="2.0", revision=1, dataset=None,
               evaluationAvailable=False, data=dict(
                   checklist=dict(schema_version="2.0", id="checklist", name="Тест",
                                  version=1, criteria=[]),
                   conversations=[conversation("manual-old")], appeals=[]))
loaded = deepcopy(initial)
loaded["revision"] = 2
loaded["data"]["conversations"] += [conversation(f"CONTROL-{i}") for i in range(10)]
loaded["dataset"] = dict(group="CONTROL", total=10, source="live",
                         fetchedAt="2026-11-07T10:00:00Z",
                         conversationIds=[f"CONTROL-{i}" for i in range(10)])

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1050})
    errors, requests, pending = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/state", lambda route: route.fulfill(json=initial))

    def hold_refresh(route):
        requests.append(route.request.post_data_json)
        pending.append(route)

    page.route("**/api/organizer/refresh", hold_refresh)
    page.goto(args.url)
    page.wait_for_load_state("networkidle")
    expect(page.locator(".dataset-summary")).to_contain_text("Группа набора не указана")
    page.get_by_role("button", name="Загрузить диалоги", exact=True).first.click()
    dialog = page.get_by_role("dialog")
    group = dialog.get_by_label("Группа набора", exact=True)
    button = dialog.get_by_role("button", name="Загрузить набор", exact=True)
    expect(group).to_have_value("DEV")
    group.fill("   ")
    expect(button).to_be_disabled()
    group.fill("  CONTROL  ")
    button.click()
    expect(group).to_be_disabled()
    expect(dialog.get_by_role("button", name="Загружаем набор…")).to_be_disabled()
    expect(dialog.get_by_role("button", name="Загрузить JSON")).to_be_disabled()
    expect(dialog.get_by_role("button", name="Выбрать файл")).to_be_disabled()
    expect(dialog.get_by_text('Загружаем группу «CONTROL»…', exact=True)).to_be_visible()
    expect(dialog.locator('.dataset-summary')).to_contain_text('Группа набора не указана')
    expect(dialog.locator('.dataset-summary')).to_contain_text('пространстве: 1')
    dialog.locator("form").evaluate("form => { form.requestSubmit(); form.requestSubmit(); }")
    page.wait_for_timeout(150)
    assert requests == [dict(schema_version="2.0", groupId="CONTROL")], requests
    pending.pop().fulfill(json=loaded)
    expect(dialog.get_by_text("Набор загружен.", exact=True)).to_be_visible()
    expect(dialog.locator(".dataset-summary")).to_contain_text("Последний загруженный набор: CONTROL")
    expect(dialog.locator(".dataset-summary")).to_contain_text("Записей в наборе: 10")
    expect(dialog.locator(".dataset-summary")).to_contain_text("Всего диалогов в рабочем пространстве: 11")
    group.fill("BROKEN")
    expect(dialog.locator(".dataset-summary")).to_contain_text("CONTROL")
    button.click()
    expect(group).to_be_disabled()
    page.wait_for_timeout(150)
    pending.pop().fulfill(status=502, json=dict(schema_version="2.0", code="UPSTREAM_ERROR",
        message="Тестовая ошибка загрузки", details=[], request_id="ui-test", retryable=True))
    expect(dialog.get_by_role("alert")).to_contain_text("Тестовая ошибка загрузки")
    expect(dialog.get_by_role("alert")).to_contain_text("Текущие диалоги и оценки сохранены.")
    expect(dialog.locator(".dataset-summary")).to_contain_text("CONTROL")
    expect(dialog.locator(".dataset-summary")).to_contain_text("пространстве: 11")
    expect(button).to_be_enabled()
    # The global retry must repeat the failed group, not merely GET /api/state.
    dialog.get_by_role('button', name='Закрыть', exact=True).click()
    page.get_by_role('button', name='Повторить загрузку', exact=True).click()
    # Starting the retry clears the old error banner and its button.
    expect(page.get_by_role('button', name='Повторить загрузку', exact=True)).to_have_count(0)
    expect(page.get_by_text('Загружаем диалоги заказчика…', exact=True)).to_be_visible()
    page.wait_for_timeout(150)
    assert requests[-1] == dict(schema_version='2.0', groupId='BROKEN')
    assert len(requests) == 3
    pending.pop().fulfill(json={**loaded, 'revision': 3})
    expect(page.get_by_role('button', name='Повторить загрузку', exact=True)).to_have_count(0)
    page.get_by_role('button', name='Загрузить диалоги', exact=True).first.click()
    # An empty successful dataset must not be confused with missing metadata.
    group.fill("EMPTY")
    button.click()
    expect(group).to_be_disabled()
    page.wait_for_timeout(150)
    empty = deepcopy(loaded)
    empty.update(revision=4, dataset={**loaded["dataset"], "group": "EMPTY", "total": 0, "conversationIds": []})
    pending.pop().fulfill(json=empty)
    expect(dialog.locator(".dataset-summary")).to_contain_text("Последний загруженный набор: EMPTY")
    expect(dialog.locator(".dataset-summary")).to_contain_text("Записей в наборе: 0")
    # Narrow viewport and a long identifier stay within the modal.
    page.set_viewport_size({"width": 390, "height": 844})
    group.fill("CONTROL-" * 60)
    expect(button).to_be_enabled()
    assert dialog.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
    page.screenshot(path=str(artifacts / 'mobile.png'), full_page=True)
    # Manual JSON fallback still submits and preserves dataset metadata.
    manual_calls = []
    imported = deepcopy(empty)
    imported.update(revision=5, imported=1)
    imported["data"]["conversations"].append(conversation("manual-new"))
    def import_json(route):
        manual_calls.append(route.request.post_data_json)
        route.fulfill(json=imported)
    page.route("**/api/import", import_json)
    dialog.get_by_label("Или вставьте JSON").fill("[]")
    dialog.get_by_role("button", name="Загрузить JSON").click()
    expect(dialog).not_to_be_visible()
    expect(page.locator(".dataset-summary")).to_contain_text("пространстве: 12")
    assert manual_calls == [dict(schema_version="2.0", text="[]")]
    assert len(requests) == 4
    assert not errors, errors
    empty_page = browser.new_page()
    no_data = deepcopy(initial)
    no_data['data']['conversations'] = []
    empty_page.route('**/api/state', lambda route: route.fulfill(json=no_data))
    empty_page.goto(args.url)
    expect(empty_page.locator('.dataset-summary')).to_contain_text('Набор не загружен')
    expect(empty_page.locator('.dataset-summary')).to_contain_text('пространстве: 0')
    browser.close()
    print("PASS: selected group, trim, duplicate prevention, loading, error preservation, empty dataset, mobile layout, JSON fallback")
