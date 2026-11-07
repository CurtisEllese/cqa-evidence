"""Synthetic browser QA: real frontend, intercepted APIs, no database or model calls.

Run with a dedicated Vite server, e.g. --url http://127.0.0.1:5199/.
Synthetic evaluation payloads exercise UI state, not model accuracy or real interviews.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import traceback
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(ROOT))
from backend.domain import initial_checklist, normalize_import, score_evaluation
from backend.contracts.v2.codecs import to_wire_checklist, to_wire_evaluation, to_wire_score, to_wire_state
from backend.contracts.v2.models import WorkspaceState, EvaluationResult, Checklist

ARTIFACTS = ROOT / "tmp" / "jury-ui"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
FIXTURE = json.loads((ROOT / "fixtures" / "synthetic-personas.json").read_text(encoding="utf-8"))
NOW = "2026-11-08T10:00:00Z"
GROUP = "SYN-JURY"
REPORT = {"synthetic": True, "realInterviews": False, "apiMode": "intercepted",
          "checks": [], "consoleErrors": [], "pageErrors": [], "unexpectedRequests": [], "screenshots": []}


class FakeAPI:
    def __init__(self):
        self.rubric = initial_checklist()
        self.rubric["version"] = 2
        self.versions = [deepcopy(self.rubric), {**deepcopy(self.rubric), "version": 1}]
        self.records = normalize_import(FIXTURE)
        for i in range(10):
            clone = deepcopy(FIXTURE["data"]["records"][0])
            clone["conversation_id"] = f"SYN-PAGE-{i:02}"
            self.records.extend(normalize_import([clone]))
        extra = deepcopy(FIXTURE["data"]["records"][1])
        extra["conversation_id"] = "SYN-OUTSIDE-GROUP"
        self.records.extend(normalize_import([extra]))
        self.by_id = {r["id"]: r for r in self.records}
        self.group_ids = [r["id"] for r in self.records if r["id"] != "SYN-OUTSIDE-GROUP"]
        self.appeals, self.starts, self.exports, self.imports, self.pending = [], [], [], [], []
        self.revision, self.serial, self.hold_next = 1, 0, False
        self.assign("SYN-CHAT-LANGUAGE", "needs_review")
        self.assign("SYN-CHAT-IDENTITY", "failed")
        self.assign("SYN-CHAT-GROSS-ERROR", "completed")
        for i in range(10):
            self.assign(f"SYN-PAGE-{i:02}", "completed")

    def evaluation(self, record, status="completed", version=1):
        self.serial += 1
        rubric = next(deepcopy(v) for v in self.versions if v["version"] == version)
        expected = next((c["checks"] for c in FIXTURE["expectedObservations"] if c["conversationId"] == record["id"]), [])
        results = []
        operator = next(m for m in record["transcript"] if m["speaker"] == "operator")
        for c in rubric["criteria"]:
            check = next((x for x in expected if x["criterionId"] == c["id"]), {})
            value = check.get("expectedValue", 0 if c["type"] == "flag" else c["max"])
            numbers = check.get("messageNumbers", [])
            selected = [record["transcript"][n - 1] for n in numbers] or [operator]
            message = next((m for m in selected if m["speaker"] == "operator"), selected[0])
            evidence = dict(kind="message", message_id=message["id"], quote=message["text"],
                            start_message_id=None, end_message_id=None, source_text=message["text"],
                            verification_status="verified", reason=None)
            if value is None or value == 0:
                evidence.update(kind="not_applicable" if value is None else "absence",
                                message_id=None, quote=None, start_message_id=selected[0]["id"],
                                end_message_id=selected[-1]["id"],
                                source_text="\n".join(m["text"] for m in record["transcript"][
                                    record["transcript"].index(selected[0]):record["transcript"].index(selected[-1]) + 1]))
            results.append(dict(criterionId=c["id"], value=value,
                                explanation="Синтетический ответ API для проверки интерфейса. " + check.get("invariant", "Не является эталонной оценкой качества модели."),
                                evidence=[evidence], evidenceIds=[m["id"] for m in selected], critical=c["type"] == "flag" and value == 1,
                                applicability_reason=check.get("invariant", c.get("naCondition")) if value is None else None))
        error = None
        if status == "failed":
            error = dict(schema_version="2.0", code="SYNTHETIC_FAILURE", message="Синтетическая ошибка одного диалога",
                         details=[], request_id="SYN-ERROR", retryable=True)
        return dict(id=f"SYN-EVAL-{self.serial}", checklist=rubric, results=results,
                    createdAt=NOW, source="api", status=status, error=error,
                    provenance=dict(provider="synthetic", model="mock-no-model-call", promptVersion="ui-test"))

    def assign(self, identifier, status, version=1):
        record = self.by_id[identifier]
        record["evaluation"] = self.evaluation(record, status, version)
        if status == "completed":
            original = score_evaluation(record["evaluation"], wire_decimals=True)
            final = score_evaluation(record["evaluation"], self.appeals, wire_decimals=True)
            record["scores"] = dict(original=original, final=final, sections={})
        else:
            record.pop("scores", None)
        self.revision += 1
        return to_wire_evaluation(record["evaluation"], identifier, identifier, record["transcript"])

    def state(self, **extra):
        dataset = dict(group=GROUP, fetchedAt=NOW, source="cache", total=len(self.group_ids), conversationIds=self.group_ids)
        other = dict(group="SYN-OTHER", fetchedAt=NOW, source="cache", total=1, conversationIds=["SYN-OUTSIDE-GROUP"])
        result = to_wire_state(dict(data=dict(conversations=self.records, checklist=self.rubric, appeals=self.appeals),
                                    dataset=dataset, datasets=[dataset, other], evaluationAvailable=True,
                                    revision=self.revision, **extra))
        WorkspaceState.model_validate(result)
        return result

    def finish(self, route, body):
        status = "needs_review" if body["conversationId"] == "SYN-CHAT-INCOMPLETE" else "completed"
        result = self.assign(body["conversationId"], status, body["checklistVersion"])
        EvaluationResult.model_validate(result)
        route.fulfill(json=result)

    def handle(self, route):
        url = urlparse(route.request.url)
        path = unquote(url.path)
        if path == "/api/state":
            route.fulfill(json=self.state())
        elif path == "/api/checklists":
            route.fulfill(json=[to_wire_checklist(v) for v in self.versions])
        elif path == "/api/evaluations" and route.request.method == "POST":
            body = route.request.post_data_json
            self.starts.append(body)
            if self.hold_next:
                self.hold_next = False
                self.pending.append((route, body))
            else:
                self.finish(route, body)
        elif path.endswith("/evaluations") and path.startswith("/api/conversations/"):
            record = self.by_id[path.split("/")[3]]
            ev = record["evaluation"]
            route.fulfill(json=[] if not ev else [to_wire_evaluation(ev, record["id"], record["id"], record["transcript"])])
        elif path == "/api/submission":
            self.exports.append(dict(path=path, query=parse_qs(url.query)))
            route.fulfill(json={"synthetic": True, "group": parse_qs(url.query)["groupId"][0], "results": []})
        elif path == "/api/appeals" and route.request.method == "POST":
            body = route.request.post_data_json
            record = self.by_id[body["conversationId"]]
            result = next(r for r in record["evaluation"]["results"] if r["criterionId"] == body["criterionId"])
            self.appeals.append({k: v for k, v in body.items() if k != "schema_version"} | dict(
                id="SYN-APPEAL-1", status="pending", createdAt=NOW, originalValue=result["value"]))
            self.revision += 1
            route.fulfill(json=self.state())
        elif path.startswith("/api/appeals/") and (path.endswith("/preview") or path.endswith("/decision")):
            body = route.request.post_data_json
            appeal = self.appeals[0]
            modified = deepcopy(appeal)
            modified.update(status=body["status"], decision=body["decision"],
                            newValue=None if body["newValue"] is None else float(body["newValue"]))
            record = self.by_id[appeal["conversationId"]]
            score = score_evaluation(record["evaluation"], [modified], wire_decimals=True)
            if path.endswith("/preview"):
                route.fulfill(json=to_wire_score(score, record["transcript"]))
            else:
                appeal.update(modified)
                record["scores"]["final"] = score
                self.revision += 1
                route.fulfill(json=self.state())
        elif path == "/api/import":
            body = route.request.post_data_json
            new = normalize_import(body["text"], existing_ids=self.by_id)
            self.imports.append(len(new))
            self.records.extend(new)
            self.by_id.update({r["id"]: r for r in new})
            self.revision += 1
            route.fulfill(json=self.state(imported=len(new)))
        else:
            REPORT["unexpectedRequests"].append(route.request.method + " " + path)
            route.abort()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5199/")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    api = FakeAPI()
    api.state()
    for version in api.versions:
        Checklist.model_validate(to_wire_checklist(version))
    if args.validate_only:
        print("PASS: synthetic state and two checklist versions validated against v2; no browser, DB or API calls")
        return
    from playwright.sync_api import sync_playwright, expect
    origin = urlparse(args.url)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, timeout=20000)
        context = browser.new_context(viewport={"width": 1440, "height": 1050}, reduced_motion="reduce", accept_downloads=True)

        def route_all(route):
            target = urlparse(route.request.url)
            if target.hostname != origin.hostname or target.port != origin.port:
                REPORT["unexpectedRequests"].append("BLOCKED EXTERNAL " + route.request.url)
                route.abort()
            elif target.path.startswith("/api/"):
                api.handle(route)
            else:
                route.continue_()
        context.route("**/*", route_all)
        page = context.new_page()
        page.set_default_timeout(7000)
        page.on("pageerror", lambda error: REPORT["pageErrors"].append(str(error)))
        page.on("console", lambda msg: REPORT["consoleErrors"].append(msg.text) if msg.type == "error" else None)
        page.goto(args.url + "#conversations")
        page.wait_for_load_state("networkidle")
        (ARTIFACTS / "initial-dom.txt").write_text(page.locator("body").inner_text(), encoding="utf-8")

        def check(name, action):
            try:
                action()
                REPORT["checks"].append(dict(name=name, status="passed"))
                print("PASS:", name, flush=True)
            except Exception as error:
                REPORT["checks"].append(dict(name=name, status="failed", error=str(error)))
                page.screenshot(path=str(ARTIFACTS / (re.sub(r"[^a-z0-9]+", "-", name.lower()) + "-failure.png")), full_page=True)
                print("FAIL:", name, str(error)[:600], flush=True)

        def go(route):
            page.evaluate("route => location.hash = encodeURIComponent(route)", route)

        def nav(name):
            page.get_by_role("navigation", name="Основная навигация").get_by_role("button", name=name, exact=True).click()

        def batch_stop_resume():
            expect(page.get_by_role("combobox", name="Набор диалогов", exact=True)).to_have_value("group:" + GROUP)
            page.get_by_role("combobox", name="Сохранённый чек-лист", exact=True).select_option("1")
            expect(page.get_by_role("button", name="Оценить набор · 2", exact=True)).to_be_enabled()
            api.hold_next = True
            page.get_by_role("button", name="Оценить набор · 2", exact=True).click()
            expect(page.get_by_role("button", name="Остановить очередь", exact=True)).to_be_enabled()
            page.get_by_role("button", name="Остановить очередь", exact=True).click()
            expect(page.get_by_text("Останавливаем очередь…", exact=True)).to_be_visible()
            assert len(api.starts) == 1 and len(api.pending) == 1
            assert api.starts[0] == dict(schema_version="2.0", conversationId="SYN-CHAT-DISSATISFACTION", checklistVersion=1)
            api.finish(*api.pending.pop())
            expect(page.get_by_text("Очередь остановлена", exact=True)).to_be_visible()
            assert len(api.starts) == 1
            page.get_by_role("button", name="Продолжить оставшиеся · 1", exact=True).click()
            expect(page.get_by_text("Запуск завершён", exact=True)).to_be_visible()
            assert [x["conversationId"] for x in api.starts] == ["SYN-CHAT-DISSATISFACTION", "SYN-CHAT-INCOMPLETE"]
            assert all(x["checklistVersion"] == 1 for x in api.starts)
        check("batch selected group version stop after current resume", batch_stop_resume)

        def retry_errors():
            previous = len(api.starts)
            page.get_by_role("button", name="Повторить ошибки · 1", exact=True).click()
            expect(page.get_by_role("button", name="Повторить ошибки · 0", exact=True)).to_be_disabled()
            assert [x["conversationId"] for x in api.starts[previous:]] == ["SYN-CHAT-IDENTITY"]
            assert not any(x["conversationId"] in {"SYN-CHAT-LANGUAGE", "SYN-OUTSIDE-GROUP"} for x in api.starts)
        check("retry only failed excludes needs review and other group", retry_errors)

        def filters_preserved():
            page.get_by_role("button", name="Фильтры", exact=True).click()
            page.get_by_label("Статус", exact=True).select_option("done")
            page.get_by_label("Язык диалога", exact=True).select_option("RU")
            page.get_by_label("Поиск диалогов", exact=True).fill("SYN-")
            page.get_by_role("button", name="Следующая страница", exact=True).click()
            footer = page.locator(".table-footer").text_content()
            page.locator(".conversation-panel .dialog-link").first.click()
            expect(page.locator(".detail-heading")).to_be_visible()
            page.get_by_role("button", name="Все диалоги", exact=True).click()
            expect(page.get_by_label("Статус", exact=True)).to_have_value("done")
            expect(page.get_by_label("Язык диалога", exact=True)).to_have_value("RU")
            expect(page.get_by_label("Поиск диалогов", exact=True)).to_have_value("SYN-")
            expect(page.locator(".table-footer")).to_have_text(footer)
            page.get_by_role("button", name="Сбросить", exact=True).click()
        check("filters search and page preserved through detail", filters_preserved)

        def submission():
            go("conversations")
            with page.expect_download() as download:
                page.get_by_role("button", name="Файл для сдачи", exact=True).click()
            assert download.value.suggested_filename == "results.json"
            assert api.exports == [dict(path="/api/submission", query={"groupId": [GROUP]})]
        check("administrator official export uses selected group route", submission)

        def evidence():
            go("dialog/SYN-CHAT-DISSATISFACTION")
            card = page.locator(".result-card").filter(has=page.get_by_role("heading", name=re.compile("Эмпатия")))
            card.get_by_role("button", name="Показать доказательство", exact=True).click()
            expect(page.locator("#message-SYN-CHAT-DISSATISFACTION-m3")).to_be_focused()
            expect(page.locator("#message-SYN-CHAT-DISSATISFACTION-m3")).to_have_class(re.compile("message-highlight"))
            expect(card.locator("blockquote")).to_contain_text("Понимаю, как раздражает повторно обращаться")
        check("skeptical QA evidence highlights and focuses original operator quote", evidence)

        def appeal():
            go("dialog/SYN-CHAT-IDENTITY")
            card = page.locator(".result-card").filter(has=page.get_by_role("heading", name=re.compile("Идентификация")))
            original = page.locator(".score-summary").inner_text()
            card.get_by_role("button", name="Оспорить", exact=True).click()
            dialog = page.get_by_role("dialog")
            expect(dialog.get_by_role("button", name="Отправить апелляцию", exact=True)).to_be_disabled()
            dialog.get_by_label("Почему оценку нужно пересмотреть?", exact=True).fill("Синтетическая апелляция: проверьте отсутствие подтверждённой идентификации.")
            dialog.get_by_role("button", name="Отправить апелляцию", exact=True).click()
            expect(dialog).not_to_be_visible()
            expect(card.get_by_role("button", name="На рассмотрении", exact=True)).to_be_visible()
            assert page.locator(".score-summary").inner_text() == original
            card.get_by_role("button", name="На рассмотрении", exact=True).click()
            page.get_by_role("button", name="Рассмотреть", exact=True).click()
            dialog = page.get_by_role("dialog")
            system_quote = dialog.locator(".review-evidence").filter(has_text="Согласие зарегистрировано.")
            REPORT["appealSystemEvidenceLabel"] = system_quote.locator("small").inner_text()
            dialog.screenshot(path=str(ARTIFACTS / "appeal-review.png"))
            dialog.get_by_label("Решение", exact=True).select_option("rejected")
            dialog.get_by_label("Обоснование решения", exact=True).fill("Подтверждение личности отсутствует до защищённого действия; исходное значение сохранено.")
            dialog.get_by_role("button", name="Сохранить решение", exact=True).click()
            expect(dialog).not_to_be_visible()
            page.get_by_role("tab", name=re.compile("Отклонённые")).click()
            expect(page.locator(".appeal-card")).to_contain_text("Подтверждение личности отсутствует")
            assert api.appeals[0]["status"] == "rejected"
        check("anxious operator appeal pending and separate reasoned decision", appeal)
        check("appeal review preserves system evidence speaker", lambda: expect_system_speaker())

        def manager():
            go("conversations")
            page.get_by_role("button", name="Грубые ошибки", exact=True).click()
            expect(page.locator(".conversation-panel .dialog-link")).to_have_count(1)
            page.locator(".conversation-panel .dialog-link").click()
            expect(page.get_by_text("Отмечена грубая ошибка", exact=True)).to_be_visible()
            page.get_by_role("button", name="Показать основание исходного флага", exact=True).click()
            expect(page.locator("#message-SYN-CHAT-GROSS-ERROR-m4")).to_be_focused()
            expect(page.locator(".result-card").last).to_contain_text("не влияет на балл")
        check("rushed manager finds gross error and its original evidence", manager)

        def dirty_checklist():
            go("checklist")
            name = page.get_by_label("Название чек-листа", exact=True)
            old = name.input_value()
            name.fill(old + " SYN-черновик")
            nav("Диалоги")
            dialog = page.get_by_role("dialog", name="Сохранить изменения перед выходом?")
            expect(dialog).to_be_visible()
            dialog.get_by_role("button", name="Вернуться к редактированию", exact=True).click()
            expect(name).to_have_value(old + " SYN-черновик")
            nav("Диалоги")
            dialog.get_by_role("button", name="Уйти без сохранения", exact=True).click()
            expect(page.get_by_role("combobox", name="Набор диалогов", exact=True)).to_be_visible()
            nav("Чек-лист")
            expect(page.get_by_label("Название чек-листа", exact=True)).to_have_value(old)
        check("methodologist dirty checklist keep draft or discard explicitly", dirty_checklist)

        def import_repeat():
            go("conversations")
            record = deepcopy(FIXTURE["data"]["records"][0])
            record["conversation_id"] = "SYN-CHAT-IMPORT-ONLY"
            for expected in (1, 0):
                page.get_by_role("button", name="Загрузить диалоги", exact=True).first.click()
                dialog = page.get_by_role("dialog")
                dialog.get_by_label("Или вставьте JSON", exact=True).fill(json.dumps([record], ensure_ascii=False))
                dialog.get_by_role("button", name="Загрузить JSON", exact=True).click()
                if expected:
                    expect(dialog).not_to_be_visible()
                else:
                    expect(dialog.get_by_role("alert")).to_contain_text("Все диалоги уже загружены")
                    dialog.get_by_role("button", name="Закрыть", exact=True).click()
                assert api.imports[-1] == expected
            assert sum(r["id"] == record["conversation_id"] for r in api.records) == 1
        check("administrator imports JSON and duplicate remains one record", import_repeat)

        def screenshots_and_overflow():
            expect(page.get_by_role("dialog")).to_have_count(0)
            for route_name in ("conversations", "dialog/SYN-CHAT-GROSS-ERROR", "checklist"):
                go(route_name)
                page.wait_for_timeout(150)
                for width in (375, 768, 1440):
                    page.set_viewport_size({"width": width, "height": 1050 if width == 1440 else 900})
                    page.wait_for_timeout(100)
                    file = f'{route_name.split("/")[0]}-{width}.png'
                    page.screenshot(path=str(ARTIFACTS / file), full_page=True)
                    measure = page.evaluate("({width:innerWidth,documentWidth:document.documentElement.scrollWidth,bodyWidth:document.body.scrollWidth})")
                    REPORT["screenshots"].append(dict(file=file, **measure))
            assert all(s["documentWidth"] <= s["width"] + 1 and s["bodyWidth"] <= s["width"] + 1 for s in REPORT["screenshots"]), REPORT["screenshots"]
        check("responsive screenshots 375 768 1440 no document overflow", screenshots_and_overflow)
        REPORT["evaluationRequests"] = api.starts
        REPORT["submissionRequests"] = api.exports
        check("no console errors page exceptions or unhandled network requests", lambda: assert_clean())
        browser.close()

    (ARTIFACTS / "report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
    if any(c["status"] == "failed" for c in REPORT["checks"]):
        raise SystemExit(1)


def assert_clean():
    assert not REPORT["pageErrors"], REPORT["pageErrors"]
    assert not REPORT["consoleErrors"], REPORT["consoleErrors"]
    assert not REPORT["unexpectedRequests"], REPORT["unexpectedRequests"]


def expect_system_speaker():
    assert "Система" in REPORT.get("appealSystemEvidenceLabel", ""), (
        "System transcript message is mislabeled in appeal review: " + REPORT.get("appealSystemEvidenceLabel", "missing"))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        REPORT["fatalError"] = str(error)
        (ARTIFACTS / "report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
        traceback.print_exc()
        raise SystemExit(1)
