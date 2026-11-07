import asyncio
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from backend.contracts.v2 import EvaluationRequest
from backend.evaluator import AdapterFailure, EvaluationService
from backend.repository import Repository
from evaluator.budget import BudgetConfig, BudgetError, BudgetLedger
from evaluator.openai_v2 import OpenAIV2Evaluator, write_usage_event
from evaluator.settings import OpenAISettings


def payload():
    return {"model": "gpt-6-luna", "service_tier": "default", "max_output_tokens": 1000, "input": "Привет"}


def request():
    return EvaluationRequest.model_validate(json.loads((Path(__file__).parents[1] /
        "evaluator/fixtures/synthetic_v2.json").read_text(encoding="utf-8"))[0]["request"])


def envelope(text="invalid"):
    return {"model": "gpt-6-luna", "status": "completed",
            "usage": {"input_tokens": 40, "output_tokens": 20},
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": text}]}]}


def test_restart_and_idempotent_settlement(tmp_path):
    path = tmp_path / "budget.sqlite"
    first = BudgetLedger(path)
    identifier = first.reserve(payload(), "safe-hash")
    reserved = Decimal(first.summary()["accounted_usd"])
    assert reserved > 0
    second = BudgetLedger(path)
    assert second.summary()["unresolved_attempts"] == 1
    assert second.settle(identifier, 40, 20, "parsed") == "0.000015"
    assert second.settle(identifier, 99999, 99999, "duplicate") == "0.000015"
    assert first.summary() == {"accounted_usd": "0.000015", "attempts": 1, "unresolved_attempts": 0}


def test_concurrent_process_connections_never_overreserve(tmp_path):
    path = tmp_path / "budget.sqlite"
    config = BudgetConfig(limit_usd="0.004")

    def reserve(_):
        try:
            return BudgetLedger(path, config=config).reserve(payload(), "parallel")
        except BudgetError as exc:
            assert exc.code == "BUDGET_EXCEEDED"
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(20)))
    assert len([x for x in results if x]) == 2
    summary = BudgetLedger(path, config=config).summary()
    assert Decimal(summary["accounted_usd"]) <= config.limit_usd
    assert summary["attempts"] == 2


def test_unknown_attempt_is_not_refunded_on_restart(tmp_path):
    path = tmp_path / "budget.sqlite"
    ledger = BudgetLedger(path, config=BudgetConfig(limit_usd="0.002"))
    identifier = ledger.reserve(payload(), "timeout")
    before = ledger.summary()["accounted_usd"]
    assert ledger.settle(identifier, None, None, "PROVIDER_UNAVAILABLE") is None
    restarted = BudgetLedger(path, config=ledger.config)
    assert restarted.summary()["accounted_usd"] == before
    with pytest.raises(BudgetError, match="BUDGET_EXCEEDED"):
        restarted.reserve(payload(), "retry")


def test_separate_processes_share_one_atomic_cap(tmp_path):
    code = '''
import sys
from evaluator.budget import BudgetLedger, BudgetConfig, BudgetError
ledger = BudgetLedger(sys.argv[1], config=BudgetConfig(limit_usd="0.004"))
try:
    ledger.reserve({"model":"gpt-6-luna","service_tier":"default","max_output_tokens":1000}, "process")
    print("reserved")
except BudgetError as exc:
    print(exc.code)
'''
    path = tmp_path / "processes.sqlite"
    children = [subprocess.Popen([sys.executable, "-X", "utf8", "-c", code, str(path)],
                cwd=Path(__file__).parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                encoding="utf-8") for _ in range(5)]
    outputs = [child.communicate(timeout=30) for child in children]
    assert all(child.returncode == 0 for child in children), outputs
    assert sum(out.strip() == "reserved" for out, _ in outputs) == 2
    assert sum(out.strip() == "BUDGET_EXCEEDED" for out, _ in outputs) == 3
    assert Decimal(BudgetLedger(path).summary()["accounted_usd"]) <= Decimal("0.004")


@pytest.mark.parametrize("change", [{"model": "other-model"}, {"service_tier": "auto"},
                                   {"service_tier": "priority"}])
def test_unknown_model_or_tariff_never_reaches_budget(tmp_path, change):
    body = payload() | change
    with pytest.raises(BudgetError, match="MODEL_NOT_ALLOWED"):
        BudgetLedger(tmp_path / "budget.sqlite").reserve(body, "hash")


@pytest.mark.parametrize("env,value", [("OPENAI_INPUT_USD_PER_MILLION", ""),
    ("OPENAI_OUTPUT_USD_PER_MILLION", "NaN"), ("OPENAI_INPUT_USD_PER_MILLION", "0"),
    ("OPENAI_INPUT_USD_PER_MILLION", "0.01"), ("OPENAI_BUDGET_USD", "6")])
def test_invalid_or_underpriced_configuration_fails_closed(monkeypatch, env, value):
    monkeypatch.setenv(env, value)
    with pytest.raises(BudgetError, match="BUDGET_CONFIGURATION"):
        BudgetConfig.from_env()


def test_large_context_cannot_use_small_context_price(tmp_path):
    with pytest.raises(BudgetError, match="BUDGET_CONFIGURATION"):
        BudgetLedger(tmp_path / "budget.sqlite").reserve(payload() | {"input": "a" * 272001}, "hash")


def test_cost_null_history_repriced_once_without_changing_journal(tmp_path):
    journal = tmp_path / "usage.jsonl"
    line = json.dumps({"model": "gpt-6-luna", "input_tokens": 1000000,
                       "output_tokens": 1000000, "cost_usd": None}) + "\n"
    journal.write_text(line, encoding="utf-8")
    ledger = BudgetLedger(tmp_path / "budget.sqlite", history_path=journal)
    assert ledger.summary() == {"accounted_usd": "1", "attempts": 1, "unresolved_attempts": 0}
    assert BudgetLedger(ledger.path, history_path=journal).summary() == ledger.summary()
    assert journal.read_text(encoding="utf-8") == line


@pytest.mark.parametrize("line", ['{"model":"unknown","cost_usd":null}', 'not-json',
                                   '{"model":"gpt-6-luna","input_tokens":true,"output_tokens":1}'])
def test_unknown_historical_spend_blocks_requests_and_is_persisted(tmp_path, line):
    journal = tmp_path / "usage.jsonl"
    journal.write_text(line + "\n", encoding="utf-8")
    ledger = BudgetLedger(tmp_path / "budget.sqlite", history_path=journal)
    with pytest.raises(BudgetError, match="BUDGET_HISTORY_UNKNOWN"):
        ledger.reserve(payload(), "hash")
    assert ledger.summary()["unresolved_attempts"] == 1
    assert ledger.summary()["attempts"] == 1


def test_spent_history_is_included_in_cap(tmp_path):
    journal = tmp_path / "usage.jsonl"
    journal.write_text('{"model":"old-model","cost_usd":5}\n', encoding="utf-8")
    with pytest.raises(BudgetError, match="BUDGET_EXCEEDED"):
        BudgetLedger(tmp_path / "budget.sqlite", history_path=journal).reserve(payload(), "hash")


def test_unwritable_ledger_fails_closed(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(BudgetError, match="BUDGET_STORAGE_ERROR"):
        BudgetLedger(blocker / "budget.sqlite").reserve(payload(), "hash")


def test_every_http_attempt_including_repair_has_one_ledger_entry(tmp_path, monkeypatch):
    req = request()
    journal = tmp_path / "usage.jsonl"
    monkeypatch.setenv("OPENAI_USAGE_LOG", str(journal))
    ledger = BudgetLedger(tmp_path / "budget.sqlite", history_path=journal)
    calls = []
    events = []

    async def handle(sent):
        calls.append(sent)
        assert json.loads(sent.content)["service_tier"] == "default"
        return httpx.Response(200, json=envelope())

    def observe(event):
        events.append(event)
        write_usage_event(event)

    judge = OpenAIV2Evaluator(OpenAISettings("test-secret", "gpt-6-luna", min_interval_seconds=0),
                             transport=httpx.MockTransport(handle), budget=ledger, on_attempt=observe)
    repo = Repository(tmp_path / "app.sqlite")
    repo.import_data([{"conversation_id": req.conversationId, "language": req.language,
        "channel": req.channel, "audio_url": None, "transcript": [
            {"speaker": m.speaker, "timestamp": m.timestamp, "text": m.text} for m in req.messages]}])
    service = EvaluationService(repo, judge.evaluate, timeout=5)
    job = service.create(req.conversationId)
    asyncio.run(service.run(job["id"]))
    assert len(calls) == 2
    assert len({event.attempt_id for event in events}) == 2
    assert ledger.summary() == {"accounted_usd": "0.00003", "attempts": 2, "unresolved_attempts": 0}
    with sqlite3.connect(ledger.path) as connection:
        rows = repr(connection.execute("SELECT * FROM attempts").fetchall())
    assert "test-secret" not in rows and req.messages[0].text not in rows


def test_exhausted_budget_prevents_http_and_does_not_retry(tmp_path):
    calls = []
    ledger = BudgetLedger(tmp_path / "budget.sqlite", config=BudgetConfig(limit_usd="0.000001"))
    judge = OpenAIV2Evaluator(OpenAISettings("test", "gpt-6-luna", min_interval_seconds=0),
                             transport=httpx.MockTransport(lambda sent: calls.append(sent)), budget=ledger)
    with pytest.raises(BudgetError) as error:
        asyncio.run(judge.evaluate(request()))
    assert error.value.code == "BUDGET_EXCEEDED" and not error.value.retryable
    assert not calls


def test_http_timeout_keeps_full_reservation(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite")

    async def timeout(_):
        raise httpx.ReadTimeout("private text")

    judge = OpenAIV2Evaluator(OpenAISettings("test", "gpt-6-luna", min_interval_seconds=0),
                             transport=httpx.MockTransport(timeout), budget=ledger)
    with pytest.raises(AdapterFailure, match="PROVIDER_UNAVAILABLE"):
        asyncio.run(judge.evaluate(request()))
    assert ledger.summary()["unresolved_attempts"] == 1
    assert Decimal(ledger.summary()["accounted_usd"]) > 0


def test_cancelled_http_attempt_retains_reserve(tmp_path):
    ledger = BudgetLedger(tmp_path / "cancel.sqlite")

    async def scenario():
        entered = asyncio.Event()

        async def pending(_):
            entered.set()
            await asyncio.Event().wait()

        judge = OpenAIV2Evaluator(OpenAISettings("test", "gpt-6-luna", min_interval_seconds=0),
                                 transport=httpx.MockTransport(pending), budget=ledger)
        task = asyncio.create_task(judge.evaluate(request()))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert ledger.summary()["unresolved_attempts"] == 1
    assert Decimal(ledger.summary()["accounted_usd"]) > 0


def test_live_constructor_always_uses_shared_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_BUDGET_LEDGER", str(tmp_path / "shared.sqlite"))
    monkeypatch.setenv("OPENAI_USAGE_LOG", str(tmp_path / "history.jsonl"))
    settings = OpenAISettings("not-used", "gpt-6-luna")
    one, two = OpenAIV2Evaluator(settings), OpenAIV2Evaluator(settings, on_attempt=lambda _: None)
    assert one.budget.path == two.budget.path == tmp_path / "shared.sqlite"
    custom = OpenAIV2Evaluator(settings, transport=httpx.AsyncHTTPTransport())
    assert custom.budget.path == one.budget.path
    one.budget.reserve(payload(), "hash")
    assert two.budget.summary()["attempts"] == 1


def test_provider_cannot_fallback_to_gemini(monkeypatch):
    from evaluator.provider import evaluate
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    with pytest.raises(AdapterFailure, match="MODEL_NOT_ALLOWED"):
        asyncio.run(evaluate(request()))
