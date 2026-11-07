"""One opt-in live evaluation with an API-edited rubric; isolated synthetic DB.

--verify-saved performs only read requests and never evaluates or edits a rubric.
"""
import argparse
from copy import deepcopy
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

DATABASE = ROOT / "local-data/custom-rubric-acceptance.sqlite"
REPORT = ROOT / "docs/audit/custom-rubric-2026-11-08.json"
IDENTIFIER = "SYN-CHAT-DISSATISFACTION"
CACHE = ROOT / "local-data/custom-rubric-no-organizer-cache.json"


def require(response, status=200):
    if response.status_code != status:
        raise RuntimeError(f"Local API returned HTTP {response.status_code}")
    return response.json()


def save(report):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(verify_saved=False):
    if Decimal(os.getenv("OPENAI_BUDGET_USD", "0")) != Decimal("0.50"):
        raise RuntimeError("The shared approved budget must remain $0.50")
    if os.getenv("LLM_PROVIDER") != "openai" or os.getenv("LLM_MODEL") != "gpt-6-luna":
        raise RuntimeError("Only the configured OpenAI GPT-6 Luna is permitted")
    previous = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
    if previous.get("evaluation", {}).get("status") == "failed" and not verify_saved:
        raise RuntimeError("Previous model failure; no automatic paid rerun")
    if verify_saved and not DATABASE.exists():
        raise RuntimeError("No saved database to verify")
    ledger = BudgetLedger.from_env()
    report = {"checkedAt": datetime.now(timezone.utc).isoformat(), "syntheticOnly": True,
        "notice": "Техническая проверка пользовательской шкалы. Не accuracy и не экспертная разметка.",
        "budgetLimitUsd": "0.50", "budgetBefore": ledger.summary(),
        "database": "local-data/custom-rubric-acceptance.sqlite", "checks": []}
    app = create_app(DATABASE, CACHE, organizer_url="", evaluation_timeout=90)
    try:
        with TestClient(app) as client:
            versions = require(client.get("/api/checklists"))
            original = next(item for item in versions if item["version"] == 1)
            report["originalRubric"] = original
            original_before = previous.get("originalRubric", deepcopy(original))
            if not verify_saved:
                state = require(client.get("/api/state"))
                if not state["data"]["conversations"]:
                    fixture = json.loads((ROOT / "fixtures/synthetic-personas.json").read_text(encoding="utf-8"))
                    record = next(item for item in fixture["data"]["records"] if item["conversation_id"] == IDENTIFIER)
                    require(client.post("/api/import", json={"schema_version": "2.0", "text": json.dumps([record], ensure_ascii=False)}))
                if len(versions) == 1:
                    changed = deepcopy(original)
                    greeting = next(item for item in changed["criteria"] if item["id"] == "greeting")
                    greeting.update(type="scale", weight="10", max="5", allowedValues=["0", "2", "5"],
                        allowNA=False, naCondition="",
                        description="5: оператор в первом ответе поздоровался и представился. 2: поздоровался, но не представился. 0: не поздоровался.")
                    require(client.post("/api/checklists/" + changed["id"] + "/versions", json={"schema_version": "2.0", "checklist": changed, "expectedVersion": 1}))
                    versions = require(client.get("/api/checklists"))
            updated = next(item for item in versions if item["version"] == 2)
            report["updatedRubric"] = updated
            save(report)
            history = require(client.get(f"/api/conversations/{IDENTIFIER}/evaluations"))
            if history:
                result = history[0]
                report["reused"] = True
            else:
                if verify_saved:
                    raise RuntimeError("No saved evaluation; verification cannot call the model")
                job = require(client.post("/api/evaluations", json={"schema_version": "2.0", "conversationId": IDENTIFIER, "checklistVersion": 2}), 202)
                result = require(client.get("/api/evaluations/" + job["id"]))
                report["reused"] = False
            report["evaluation"] = result
            save(report)
            if result["status"] != "completed":
                report["stopped"] = "Evaluation did not complete; no further model calls"
                return 1
            greeting_result = next(item for item in result["results"] if item["criterionId"] == "greeting")
            greeting = next(item for item in updated["criteria"] if item["id"] == "greeting")
            versions_after = require(client.get("/api/checklists"))
            original_after = next(item for item in versions_after if item["version"] == 1)
            report["checks"] = [
                {"name": "new user-defined greeting scale gives 5", "status": "PASS" if greeting_result["value"] == "5" else "FAIL", "actualValue": greeting_result["value"]},
                {"name": "version 2 snapshot exactly equals saved API rubric", "status": "PASS" if result["checklist"] == updated and result["checklist"]["version"] == 2 else "FAIL"},
                {"name": "custom scale, weight and applicability persisted", "status": "PASS" if greeting["type"] == "scale" and greeting["allowedValues"] == ["0", "2", "5"] and greeting["weight"] == "10" and greeting["allowNA"] is False else "FAIL"},
                {"name": "original version 1 unchanged", "status": "PASS" if original_after == original_before else "FAIL"},
                {"name": "provider provenance retained", "status": "PASS" if result.get("provenance", {}).get("model") == "gpt-6-luna" else "FAIL"}]
            with app.state.repo.connect() as connection:
                report["modelAttempts"] = connection.execute("SELECT COUNT(*) FROM evaluation_attempts").fetchone()[0]
                report["evaluationCount"] = connection.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
                stored_request = json.loads(connection.execute("SELECT request FROM evaluation_jobs WHERE id=?", (result["id"],)).fetchone()[0])
                report["checks"].append({"name": "model request carries saved version 2 rubric", "status": "PASS" if stored_request["checklist"] == updated else "FAIL"})
            before = require(client.get("/api/export"))
        with TestClient(create_app(DATABASE, CACHE, organizer_url="", evaluation_timeout=90)) as client:
            equal = require(client.get("/api/export")) == before
            saved_result = require(client.get("/api/evaluations/" + result["id"]))
            equal = equal and saved_result == result and require(client.get("/api/checklists")) == versions_after
            report["checks"].append({"name": "restart retains evaluation and both rubric versions", "status": "PASS" if equal else "FAIL"})
        print(json.dumps({"status": result["status"], "greeting": greeting_result["value"], "version": result["checklist"]["version"], "modelAttempts": report["modelAttempts"], "reused": report["reused"], "checks": report["checks"]}, ensure_ascii=False), flush=True)
        return 0 if all(check["status"] == "PASS" for check in report["checks"]) else 1
    except Exception as error:
        report["stopped"] = type(error).__name__
        raise
    finally:
        report["budgetAfter"] = ledger.summary()
        save(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--verify-saved", action="store_true")
    args = parser.parse_args()
    if not args.run and not args.verify_saved:
        parser.error("--run or --verify-saved is required")
    raise SystemExit(run(verify_saved=args.verify_saved))
