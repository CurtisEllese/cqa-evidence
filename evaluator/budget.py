"""Durable, cross-process reservations for the project's $5 OpenAI allowance.

The ledger is authoritative. An interrupted/unknown attempt keeps its full reserve;
it is never automatically refunded, including after a process restart.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path

from backend.evaluator import AdapterFailure

ROOT = Path(__file__).resolve().parents[1]
SCALE = Decimal(1_000_000_000)
MODEL = "gpt-6-luna"
# Standard prices verified at https://developers.openai.com/api/docs/models/gpt-6-luna
# Cache discounts are deliberately ignored; all input is charged at the higher
# cache-write rate. This is an accounted upper estimate, not an OpenAI invoice.
# No priority/regional routing is used.
INPUT_PRICE = Decimal("0.125")
OUTPUT_PRICE = Decimal("0.50")


class BudgetError(AdapterFailure):
    def __init__(self, code):
        super().__init__(code, retryable=False)


def positive(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            raise ValueError
        return number
    except (InvalidOperation, ValueError, TypeError):
        raise BudgetError("BUDGET_CONFIGURATION") from None


def units(value):
    return int((value * SCALE).to_integral_value(rounding=ROUND_CEILING))


def token_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


@dataclass(frozen=True)
class BudgetConfig:
    limit_usd: Decimal = Decimal("5")
    input_price: Decimal = INPUT_PRICE
    output_price: Decimal = OUTPUT_PRICE

    def __post_init__(self):
        for name in ("limit_usd", "input_price", "output_price"):
            object.__setattr__(self, name, positive(getattr(self, name)))
        if self.limit_usd > 5:
            raise BudgetError("BUDGET_CONFIGURATION")
        # Overriding verified prices downwards would weaken the actual $5 cap.
        if self.input_price < INPUT_PRICE or self.output_price < OUTPUT_PRICE:
            raise BudgetError("BUDGET_CONFIGURATION")

    @classmethod
    def from_env(cls):
        return cls(os.getenv("OPENAI_BUDGET_USD", "5"),
                   os.getenv("OPENAI_INPUT_USD_PER_MILLION", str(INPUT_PRICE)),
                   os.getenv("OPENAI_OUTPUT_USD_PER_MILLION", str(OUTPUT_PRICE)))

    def cost(self, incoming, outgoing):
        # Requests larger than 272k use a different tariff. New requests are
        # bounded below that threshold; old records still need correct repricing.
        large = incoming > 272_000
        return units((incoming * self.input_price * (2 if large else 1)
                      + outgoing * self.output_price * (Decimal("1.5") if large else 1))
                     / 1_000_000)


class BudgetLedger:
    def __init__(self, path, *, config=None, history_path=None):
        self.path = Path(path)
        self.config = config or BudgetConfig.from_env()
        self.history_path = Path(history_path) if history_path else None

    @classmethod
    def from_env(cls):
        return cls(os.getenv("OPENAI_BUDGET_LEDGER", str(ROOT / "local-data/openai-budget.sqlite3")),
                   history_path=os.getenv("OPENAI_USAGE_LOG", str(ROOT / "local-data/openai-usage.jsonl")))

    @contextmanager
    def transaction(self):
        connection = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""CREATE TABLE IF NOT EXISTS attempts (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                model TEXT NOT NULL, fingerprint TEXT, state TEXT NOT NULL,
                reserved_units INTEGER NOT NULL, charged_units INTEGER NOT NULL,
                input_tokens INTEGER, output_tokens INTEGER,
                input_price TEXT NOT NULL, output_price TEXT NOT NULL,
                outcome TEXT)""")
            connection.execute("CREATE TABLE IF NOT EXISTS history_imports (id TEXT PRIMARY KEY)")
            yield connection
            connection.commit()
        except (OSError, sqlite3.Error):
            raise BudgetError("BUDGET_STORAGE_ERROR") from None
        finally:
            if connection is not None:
                connection.close()

    def _import_history(self, connection):
        if self.history_path is None or not self.history_path.exists():
            return
        try:
            lines = self.history_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            raise BudgetError("BUDGET_HISTORY_UNKNOWN") from None
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            identifier = "legacy:" + hashlib.sha256(
                f"{self.history_path.resolve()}:{index}:{line}".encode("utf-8")).hexdigest()
            if connection.execute("SELECT 1 FROM history_imports WHERE id=?", (identifier,)).fetchone():
                continue
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError
            except ValueError:
                event = {}
            # New journal entries refer to the existing durable reservation.
            attempt_id = event.get("attempt_id")
            if isinstance(attempt_id, str) and connection.execute(
                    "SELECT 1 FROM attempts WHERE id=?", (attempt_id,)).fetchone():
                connection.execute("INSERT INTO history_imports VALUES (?)", (identifier,))
                continue
            model = str(event.get("model", "unknown"))
            incoming, outgoing = event.get("input_tokens"), event.get("output_tokens")
            charge = None
            try:
                price = Decimal(str(event.get("cost_usd")))
                if price.is_finite() and price >= 0:
                    charge = units(price)
            except (InvalidOperation, ValueError):
                pass
            if token_count(incoming) and token_count(outgoing) and (model == MODEL or model.startswith(MODEL + "-")):
                charge = max(charge or 0, self.config.cost(incoming, outgoing))
            unknown = charge is None
            connection.execute("""INSERT INTO attempts
                (id,model,state,reserved_units,charged_units,input_price,output_price,outcome)
                VALUES (?,?,?,?,?,?,?,?)""",
                (identifier, model, "history_unknown" if unknown else "history",
                 0, charge or 0, str(self.config.input_price), str(self.config.output_price), "imported"))
            connection.execute("INSERT INTO history_imports VALUES (?)", (identifier,))

    def reserve(self, payload, fingerprint):
        if payload.get("model") != MODEL or payload.get("service_tier") != "default":
            raise BudgetError("MODEL_NOT_ALLOWED")
        # One token per UTF-8 byte over the entire JSON body is a deliberately
        # loose upper bound; extra headroom covers server-side framing/schema.
        incoming = len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 8192
        outgoing = payload.get("max_output_tokens")
        if incoming > 272_000 or not token_count(outgoing) or outgoing < 1:
            raise BudgetError("BUDGET_CONFIGURATION")
        amount = self.config.cost(incoming, outgoing)
        identifier = str(uuid.uuid4())
        blocked = None
        with self.transaction() as connection:
            self._import_history(connection)
            if connection.execute("SELECT 1 FROM attempts WHERE state='history_unknown' LIMIT 1").fetchone():
                blocked = "BUDGET_HISTORY_UNKNOWN"
            elif connection.execute("SELECT COALESCE(SUM(charged_units),0) FROM attempts").fetchone()[0] + amount > units(self.config.limit_usd):
                blocked = "BUDGET_EXCEEDED"
            else:
                connection.execute("""INSERT INTO attempts
                    (id,model,fingerprint,state,reserved_units,charged_units,input_price,output_price)
                    VALUES (?,?,?,'reserved',?,?,?,?)""",
                    (identifier, MODEL, fingerprint, amount, amount,
                     str(self.config.input_price), str(self.config.output_price)))
        # Commit imports even when they cause the request to be rejected.
        if blocked:
            raise BudgetError(blocked)
        return identifier

    def settle(self, identifier, incoming, outgoing, outcome):
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM attempts WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise BudgetError("BUDGET_STORAGE_ERROR")
            if row["state"] == "reserved":
                known = token_count(incoming) and token_count(outgoing)
                charge = BudgetConfig(input_price=row["input_price"], output_price=row["output_price"]).cost(incoming, outgoing) if known else row["reserved_units"]
                connection.execute("""UPDATE attempts SET state=?,charged_units=?,input_tokens=?,output_tokens=?,outcome=? WHERE id=?""",
                                   ("completed" if known else "unknown", charge,
                                    incoming if known else None, outgoing if known else None, outcome, identifier))
            else:
                charge, known = row["charged_units"], row["state"] == "completed"
        return str(Decimal(charge) / SCALE) if known else None

    def summary(self):
        with self.transaction() as connection:
            self._import_history(connection)
            total = connection.execute("SELECT COALESCE(SUM(charged_units),0), COUNT(*) FROM attempts").fetchone()
            unknown = connection.execute("SELECT COUNT(*) FROM attempts WHERE state IN ('unknown','reserved','history_unknown')").fetchone()[0]
        return {"accounted_usd": str(Decimal(total[0]) / SCALE), "attempts": total[1], "unresolved_attempts": unknown}
