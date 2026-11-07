"""Opt-in real-model acceptance for five synthetic personas, in an isolated DB.

This is a technical/semantic simulation, not interviews or accuracy measurement.
Successful saved evaluations are reused on restart; provider failure stops the run.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from backend.app import create_app
from evaluator.budget import BudgetLedger

DATABASE = ROOT / "local-data/synthetic-acceptance.sqlite"
REPORT = ROOT / "docs/audit/synthetic-personas-2026-11-08.json"
FIXTURE = ROOT / "fixtures/synthetic-personas.json"


def save(report):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require(response, status=200):
    if response.status_code != status:
        raise RuntimeError(f"Local API returned HTTP {response.status_code}")
    return response.json()


def checks_for(result, expected):
    rows = {row["criterionId"]: row for row in result["results"]}
    checks = []
    for expectation in expected:
        row = rows.get(expectation["criterionId"])
        check = dict(expectation)
        check["actualValue"] = row["value"] if row else None
        if "expectedValue" in expectation:
            want = expectation["expectedValue"]
            actual = check["actualValue"]
            equal = (actual is None) if want is None else actual is not None and Decimal(actual) == Decimal(str(want))
            check["valueStatus"] = "PASS" if row is not None and equal else "FAIL"
        else:
            check["valueStatus"] = "NOT_SPECIFIED"
        check["semanticStatus"] = "REVIEW_REQUIRED"
        check["evidenceVerified"] = bool(row and row["evidence"] and all(
            item["verification_status"] == "verified" for item in row["evidence"]))
        checks.append(check)
    return checks


def run(verify_saved=False):
    # The parent process and this script share the same durable budget ledger.
    if Decimal(os.getenv("OPENAI_BUDGET_USD", "0")) != Decimal("0.50"):
        raise RuntimeError("Acceptance requires the approved shared $0.50 budget")
    if os.getenv("LLM_PROVIDER") != "openai" or os.getenv("LLM_MODEL") != "gpt-6-luna":
        raise RuntimeError("Acceptance requires the configured OpenAI GPT-6 Luna")
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    records = fixture["data"]["records"]
    assert len(records) == 5 and all(row["synthetic"] for row in records)
    expected = {case["conversationId"]: case["checks"] for case in fixture["expectedObservations"]}
    ledger = BudgetLedger.from_env()
    report = {"checkedAt": datetime.now(timezone.utc).isoformat(), "syntheticOnly": True,
        "notice": "Пять вымышленных сценариев. Не интервью, не независимый Ground Truth и не измерение accuracy.",
        "modelInput": "Only normalized data.records and the saved rubric; personas and expectedObservations are excluded.",
        "database": "local-data/synthetic-acceptance.sqlite", "budgetLimitUsd": "0.50",
        "costBasis": "Shared accounted upper estimate; not the provider invoice",
        "budgetBefore": ledger.summary(), "cases": [], "checks": []}
    previous = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
    if any(case["status"] == "failed" for case in previous.get("cases", [])):
        raise RuntimeError("Previous provider failure exists; refusing automatic paid retry")
    cache = ROOT / "local-data/synthetic-acceptance-no-organizer-cache.json"
    app = create_app(DATABASE, cache, organizer_url="", evaluation_timeout=90)
    try:
        with TestClient(app) as client:
            current = require(client.get("/api/state"))
            before_count = len(current["data"]["conversations"])
            imported_count = 0
            if before_count == 0:
                imported = require(client.post("/api/import", json={"schema_version": "2.0", "text": json.dumps(records, ensure_ascii=False)}))
                imported_count = imported["imported"]
            repeated = client.post("/api/import", json={"schema_version": "2.0", "text": json.dumps(records, ensure_ascii=False)})
            after_count = len(require(client.get("/api/state"))["data"]["conversations"])
            duplicate_message = repeated.json().get("message", "")
            report["checks"].append({"name": "isolated import and duplicate rejection without new records", "status": "PASS" if repeated.status_code == 422 and "уже загружены" in duplicate_message and after_count == 5 else "FAIL", "alreadyPresent": before_count, "firstImportedThisRun": imported_count, "afterRepeatCount": after_count, "repeatHttpStatus": repeated.status_code, "repeatMessage": duplicate_message})
            rubric = require(client.get("/api/checklists"))[0]
            report["rubric"] = rubric
            for record in records:
                identifier = record["conversation_id"]
                history = require(client.get(f"/api/conversations/{identifier}/evaluations"))
                if history:
                    result = history[0]
                    reused = True
                else:
                    if verify_saved:
                        raise RuntimeError("Missing saved evaluation; verification never calls the model")
                    job = require(client.post("/api/evaluations", json={"schema_version": "2.0", "conversationId": identifier, "checklistVersion": rubric["version"]}), 202)
                    result = require(client.get("/api/evaluations/" + job["id"]))
                    reused = False
                report["cases"].append({"conversationId": identifier, "status": result["status"],
                    "evaluationId": result["id"], "reused": reused, "provenance": result.get("provenance"),
                    "score": result["score"], "criteria": result["results"], "error": result.get("error"),
                    "invariants": checks_for(result, expected[identifier])})
                report["budgetAfter"] = ledger.summary()
                save(report)
                print(identifier, result["status"], "reused" if reused else "evaluated", flush=True)
                if result["status"] not in ("completed", "needs_review"):
                    report["stopped"] = "Provider/evaluation failure; remaining cases were not called"
                    save(report)
                    return 1
            with app.state.repo.connect() as connection:
                attempts = connection.execute("SELECT job_id, COUNT(*) AS total FROM evaluation_attempts GROUP BY job_id").fetchall()
                report["modelAttempts"] = {row["job_id"]: row["total"] for row in attempts}
                requests = [json.loads(row[0]) for row in connection.execute("SELECT request FROM evaluation_jobs")]
                clean = all("personas" not in item and "expectedObservations" not in item for item in requests)
                report["checks"].append({"name": "saved model inputs exclude persona and expectation metadata", "status": "PASS" if clean and len(requests) == 5 else "FAIL", "requestKeys": sorted(requests[0]) if requests else []})
            sources = {record["conversation_id"]: record["transcript"] for record in records}
            exact = True
            normalized = True
            for case in report["cases"]:
                messages = sources[case["conversationId"]]
                for criterion in case["criteria"]:
                    for evidence in criterion["evidence"]:
                        if evidence.get("message_id"):
                            expected_text = messages[int(evidence["message_id"].rsplit("-m", 1)[1]) - 1]["text"]
                        else:
                            start = int(evidence["start_message_id"].rsplit("-m", 1)[1]) - 1
                            end = int(evidence["end_message_id"].rsplit("-m", 1)[1])
                            expected_text = "\n".join(item["text"] for item in messages[start:end])
                        exact = exact and evidence["source_text"] == expected_text
                if case["score"]:
                    values = {item["criterionId"]: item["value"] for item in case["criteria"]}
                    possible = sum(Decimal(item["weight"]) for item in rubric["criteria"] if item["type"] != "flag" and values[item["id"]] is not None)
                    normalized = normalized and possible == Decimal(case["score"]["possible"])
            report["checks"].extend([
                {"name": "all evidence source text exactly matches fixture, including KZ", "status": "PASS" if exact else "FAIL"},
                {"name": "N/A excluded from applicable weight denominator", "status": "PASS" if normalized else "FAIL"}])
            # This appeal deliberately changes a valid value to test mechanics.
            # It is NOT an expert correction or a claim about the right answer.
            target = next(case for case in report["cases"] if case["conversationId"] == "SYN-CHAT-IDENTITY")
            original = require(client.get("/api/evaluations/" + target["evaluationId"]))
            if original["status"] != "completed":
                report["appeal"] = {"status": "NOT_RUN", "reason": "Identity evaluation needs review"}
            else:
                state = require(client.get("/api/state"))
                existing = [appeal for appeal in state["data"]["appeals"] if appeal["evaluationId"] == original["id"]]
                if existing:
                    appeal = existing[0]
                else:
                    response = require(client.post("/api/appeals", json={"schema_version": "2.0", "conversationId": target["conversationId"], "evaluationId": original["id"], "criterionId": "identification", "comment": "Синтетическая проверка механики принятой апелляции; не экспертная корректировка."}))
                    appeal = response["data"]["appeals"][0]
                pending_unchanged = require(client.get("/api/evaluations/" + original["id"])) == original
                old = next(row["value"] for row in original["results"] if row["criterionId"] == "identification")
                new = "1" if old != "1" else "0"
                if appeal["status"] == "pending":
                    state = require(client.post("/api/appeals/" + appeal["id"] + "/decision", json={"schema_version": "2.0", "status": "accepted", "decision": "Только технический эксперимент на вымышленных данных: проверяем сохранность оригинала и отдельный пересчёт.", "newValue": new}))
                else:
                    state = require(client.get("/api/state"))
                item = next(row for row in state["data"]["conversations"] if row["id"] == target["conversationId"])
                unchanged = require(client.get("/api/evaluations/" + original["id"])) == original
                changed = item["scores"]["original"]["total"] != item["scores"]["final"]["total"]
                report["appeal"] = {"syntheticMechanicalChangeOnly": True, "appealId": appeal["id"], "originalValue": old, "newValue": new,
                    "pendingOriginalUnchanged": pending_unchanged, "acceptedOriginalUnchanged": unchanged,
                    "originalScore": item["scores"]["original"]["total"], "finalScore": item["scores"]["final"]["total"],
                    "finalScoreChanged": changed, "status": "PASS" if unchanged and changed and pending_unchanged else "FAIL"}
            before_restart = require(client.get("/api/export"))
        with TestClient(create_app(DATABASE, cache, organizer_url="", evaluation_timeout=90)) as client:
            after_restart = require(client.get("/api/export"))
            report["checks"].append({"name": "restart preserves evaluations and appeal", "status": "PASS" if after_restart == before_restart else "FAIL"})
        return 0 if all(check["status"] == "PASS" for check in report["checks"]) and report.get("appeal", {}).get("status") == "PASS" else 1
    except Exception as error:
        report["stopped"] = type(error).__name__
        raise
    finally:
        # Keep a prior manual semantic review only for the exact immutable
        # evaluations it inspected, never transfer it to a fresh model result.
        old_cases = {case["evaluationId"]: case for case in previous.get("cases", [])}
        if len(report["cases"]) == 5 and {case["evaluationId"] for case in report["cases"]} == set(old_cases):
            for case in report["cases"]:
                old_checks = {item["criterionId"]: item for item in old_cases[case["evaluationId"]]["invariants"]}
                for check in case["invariants"]:
                    old = old_checks.get(check["criterionId"], {})
                    for key in ("semanticStatus", "reviewMethod", "reviewReason"):
                        if key in old:
                            check[key] = old[key]
            for key in ("semanticReview", "limitations", "initialImportObservation"):
                if key in previous:
                    report[key] = previous[key]
        report["budgetAfter"] = ledger.summary()
        save(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Explicitly permit the five real-model evaluations")
    parser.add_argument("--verify-saved", action="store_true", help="Check existing results only; never call the model")
    args = parser.parse_args()
    if not args.run and not args.verify_saved:
        parser.error("--run or --verify-saved is required")
    raise SystemExit(run(verify_saved=args.verify_saved))
