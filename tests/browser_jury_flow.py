"""UI regressions with synthetic v2 responses; no provider calls or production DB writes.

Run against Vite: .venv/bin/python tests/browser_jury_flow.py --url http://127.0.0.1:5173/
Requires the optional Playwright test dependency and its Chromium browser.
"""
import argparse
from copy import deepcopy
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def rubric(version):
    return dict(schema_version="2.0", id="test-rubric", name="Синтетический чек-лист", version=version,
                criteria=[dict(id="custom-flag", title="Тестовый флаг", description="Найти нарушение",
                               section="Флаги", type="flag", weight="0", max="1", allowedValues=["0", "1"],
                               allowNA=False, naCondition=None)])


def result(flag):
    return dict(criterionId="custom-flag", value="1" if flag else "0", explanation="Тестовое исходное основание",
                applicability_reason=None, critical=flag, evidence=[dict(kind="message", message_id="m1",
                quote=None, source_text="Синтетическая реплика", verification_status="verified", reason=None)])


def score(flag):
    return dict(schema_version="2.0", total=None, earned="0", possible="0", applicable=0,
                critical=flag, results=[result(flag)])


def conversation(identifier, original=False, final=False, status="completed", language="RU"):
    evaluation = dict(schema_version="2.0", id=f"job-{identifier}", conversationId=identifier,
                      conversationHash=identifier, checklist=rubric(1), results=[result(original)],
                      createdAt="2026-11-07T10:00:00Z", source="api", status=status, error=None,
                      score=score(original)) if status else None
    return dict(schema_version="2.0", id=identifier, contentHash=identifier, title=identifier,
                operator="Оператор", initials="ОП", language=language, channel="agent chat", date=None,
                duration="00:01", audioUrl=None,
                transcript=[dict(id="m1", message_index=0, timestamp="00:01", speaker="operator",
                                 text="Синтетическая реплика")], evaluation=evaluation,
                scores=dict(original=score(original), final=score(final), sections={"Флаги": score(final)}))


def workspace():
    rows = [conversation(f"negative-{i}") for i in range(9)]
    rows += [conversation("appealed", True, False), conversation("positive-en", True, True, language="EN"),
             conversation("review", True, True, "needs_review"), conversation("fresh", status=None)]
    appeal = dict(id="appeal-1", conversationId="appealed", evaluationId="job-appealed", criterionId="custom-flag",
                  comment="Тест", status="accepted", createdAt="2026-11-07T10:00:00Z", decision="Флаг снят",
                  newValue="0", originalValue="1")
    return dict(schema_version="2.0", revision=1, dataset=None, evaluationAvailable=True,
                data=dict(checklist=rubric(2), conversations=rows, appeals=[appeal]))


def run(url):
    out = Path(__file__).resolve().parents[1] / "tmp" / "jury-ui"
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        errors, calls, pending = [], [], []
        state = workspace()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/state", lambda route: route.fulfill(json=state))
        page.route("**/api/checklists", lambda route: route.fulfill(json=[rubric(2), rubric(1)]))
        page.goto(url + "#conversations")
        expect(page.locator(".table-footer")).to_contain_text("из 13 диалогов")
        page.get_by_role("button", name="Следующая страница").click()
        page.get_by_label("Грубые ошибки", exact=True).select_option("present")
        expect(page.locator("tbody tr")).to_have_count(2)
        expect(page.locator(".table-footer")).to_contain_text("1–2 из 2")
        expect(page.locator("tbody")).to_contain_text("снята после апелляции")
        page.get_by_label("Язык диалога").select_option("EN")
        expect(page.locator("tbody tr")).to_have_count(1)
        expect(page.locator("tbody")).to_contain_text("positive-en")
        page.get_by_label("Язык диалога").select_option("all")
        page.get_by_role("button", name="Фильтры", exact=True).click()
        page.get_by_label("Канал", exact=True).select_option("voice call")
        expect(page.locator("tbody tr")).to_have_count(0)
        page.get_by_label("Канал", exact=True).select_option("all")
        page.get_by_label("Грубые ошибки", exact=True).select_option("absent")
        expect(page.locator(".table-footer")).to_contain_text("из 9 диалогов")
        page.get_by_label("Грубые ошибки", exact=True).select_option("unknown")
        expect(page.locator("tbody tr")).to_have_count(2)
        expect(page.locator("tbody")).to_contain_text("review")
        expect(page.locator("tbody")).to_contain_text("fresh")
        page.get_by_label("Грубые ошибки", exact=True).select_option("present")
        page.get_by_role("button", name="Открыть диалог appealed", exact=True).click()
        findings = page.get_by_role("region", name="Грубые ошибки исходной оценки")
        expect(findings).to_contain_text("После апелляции флаг снят")
        expect(findings).to_contain_text("Тестовое исходное основание")
        findings.get_by_role("button", name="Показать основание исходного флага").click()
        expect(page.locator("#message-m1")).to_have_class("message message-operator message-highlight")
        expect(page.get_by_role("button", name="Оценка зафиксирована")).to_be_disabled()
        expect(page.get_by_label("Версия для новой оценки")).to_be_disabled()
        expect(page.get_by_text("Переоценка заблокирована:", exact=False)).to_be_visible()
        page.screenshot(path=str(out / "original-finding.png"), full_page=True)
        print("PASS: original flag, appeals, unknown results, combined filters, pagination, evidence", flush=True)

        page.goto(url + "#dialog/fresh")
        selector = page.get_by_label("Версия для новой оценки")
        expect(selector).to_have_value("2")
        selector.select_option("1")

        def start(route):
            body = route.request.post_data_json
            calls.append(body)
            job = conversation("fresh", status="queued")["evaluation"]
            state["data"]["conversations"][-1]["evaluation"] = job
            state["revision"] = 2
            route.fulfill(status=202, json=job)

        page.route("**/api/evaluations", start)
        page.route("**/api/evaluations/job-fresh", lambda route: pending.append(route))
        page.get_by_role("button", name="Оценить диалог", exact=True).dblclick()
        expect(page.get_by_role("button", name="Оцениваем…")).to_be_disabled()
        expect(selector).to_be_disabled()
        expect(page.locator(".score-summary-meta")).to_contain_text("Чек-лист v1")
        page.wait_for_function("document.querySelector('.evaluation-actions button').disabled")
        page.wait_for_timeout(1200)
        assert calls == [dict(schema_version="2.0", conversationId="fresh", checklistVersion=1)], calls
        assert pending, "Polling must resume the same job"
        finished = conversation("fresh")
        state["data"]["conversations"][-1] = finished
        state["revision"] = 3
        pending.pop().fulfill(json=finished["evaluation"])
        expect(page.get_by_role("button", name="Обновить оценку", exact=True)).to_be_enabled()
        expect(page.locator(".evaluation-controls")).to_have_count(1)
        expect(page.locator(".score-summary-meta")).to_contain_text("Чек-лист v1")
        expect(selector).to_have_value("1")
        page.reload()
        expect(page.locator(".score-summary-meta")).to_contain_text("Чек-лист v1")
        expect(selector).to_have_value("2")
        print("PASS: selected version reaches API, one job, running snapshot, saved version on reload", flush=True)

        archived = deepcopy(finished['evaluation'])
        archived['id'] = 'older-job'
        archived['provenance'] = dict(provider='test-only', model='synthetic-model', promptVersion='test-prompt')
        page.route('**/api/conversations/fresh/evaluations', lambda route: route.fulfill(json=[finished['evaluation'], archived]))
        page.get_by_role('button', name='История оценок', exact=True).click()
        expect(page.locator('.history-entry')).to_have_count(2)
        page.locator('.history-entry summary').last.click()
        expect(page.locator('.evaluation-history')).to_contain_text('synthetic-model')
        page.locator('.history-entry').last.get_by_role('button', name='Показать реплики').click()
        expect(page.locator('#message-m1')).to_have_class('message message-operator message-highlight')
        expect(page.locator('.evaluation-controls')).to_have_count(1)
        page.get_by_role('button', name='Скрыть историю оценок', exact=True).click()
        print('PASS: archived evaluation metadata, evidence and one control panel after updates', flush=True)

        page.set_viewport_size({"width": 375, "height": 812})
        expect(selector).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(out / "version-mobile.png"), full_page=True)
        page.goto(url + "#conversations")
        expect(page.get_by_label("Грубые ошибки", exact=True)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(out / "filter-mobile.png"), full_page=True)

        # A failed catalog must not silently evaluate using the current rubric.
        page.unroute("**/api/checklists")
        page.route("**/api/checklists", lambda route: route.fulfill(status=503, json=dict(
            schema_version="2.0", code="UNAVAILABLE", message="Тестовая ошибка версий", details=[],
            request_id="test", retryable=True)))
        page.goto(url + "#dialog/fresh")
        expect(page.get_by_text("Тестовая ошибка версий", exact=False)).to_be_visible()
        expect(page.get_by_role("button", name="Обновить оценку", exact=True)).to_be_disabled()
        page.unroute("**/api/checklists")
        page.route("**/api/checklists", lambda route: route.fulfill(json=[rubric(2), rubric(1)]))
        page.get_by_role("button", name="Повторить загрузку версий").click()
        expect(selector).to_have_value("2")
        expect(page.get_by_role("button", name="Обновить оценку", exact=True)).to_be_enabled()

        empty = deepcopy(state)
        empty["data"]["conversations"] = [conversation("fresh", status=None)]
        empty["data"]["appeals"] = []
        page.unroute("**/api/state")
        page.route("**/api/state", lambda route: route.fulfill(json=empty))
        page.goto(url)
        page.reload()
        expect(page.get_by_role("button", name="Перейти к оценке диалога")).to_be_visible()
        assert not errors, errors
        print("PASS: 375px layout, catalog failure/retry, truthful empty dashboard, no JS errors", flush=True)
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5173/")
    run(parser.parse_args().url)
