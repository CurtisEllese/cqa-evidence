"""Append-only evaluation snapshots using the team's existing SQLite connection."""
from __future__ import annotations

import hashlib
import json
from typing import Callable
from contracts.v1 import Checklist, Conversation, Evaluation, EvaluationJob, EvaluatorInput, ModelOutput
from backend.repository import Repository, Conflict


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def checklist_from_ui(value: dict) -> Checklist:
    """Explicit bridge for existing editor records; never round stored weights."""
    if "schema_version" in value:
        return Checklist.model_validate(value)
    return Checklist(schema_version="1.0", id=value["id"], version=value["version"],
        name=value["name"], channel=value.get("channel", "any"), criteria=[{
            "id": c["id"], "title": c["title"], "description": c["description"], "type": c["type"],
            "weight": str(c["weight"]), "max": c["max"], "allowed_values": sorted(c["allowedValues"]),
            "allow_na": c["allowNA"], "na_condition": c.get("naCondition") if c["allowNA"] else None,
            "section": c["section"],
        } for c in value["criteria"]])


def conversation_from_ui(value: dict) -> Conversation:
    body = {"id": value["id"], "language": value["language"], "channel": value["channel"],
            "transcript": value["transcript"], "audio_url": value.get("audioUrl")}
    return Conversation(**body, content_hash=digest(body))


def checklist_to_ui(checklist: Checklist) -> dict:
    return {"id": checklist.id, "name": checklist.name, "version": checklist.version, "channel": checklist.channel,
            "criteria": [{"id": c.id, "title": c.title, "description": c.description, "type": c.type,
                          "weight": c.weight, "max": c.max, "allowedValues": c.allowed_values,
                          "allowNA": c.allow_na, "section": c.section,
                          **({"naCondition": c.na_condition} if c.allow_na else {})} for c in checklist.criteria]}


class SQLiteEvaluations:
    def __init__(self, source: Repository) -> None:
        self.source = source
        with source.connect(write=True) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS checklist_snapshots (
                    checklist_id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
                    content_hash TEXT NOT NULL, fingerprints TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(checklist_id,version));
                CREATE TABLE IF NOT EXISTS transcript_snapshots (
                    conversation_id TEXT NOT NULL, content_hash TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(conversation_id,content_hash));
                CREATE TABLE IF NOT EXISTS evaluation_jobs (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, body TEXT NOT NULL,
                    request TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evaluation_snapshots (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
                    conversation_id TEXT NOT NULL, conversation_hash TEXT NOT NULL,
                    checklist_id TEXT NOT NULL, checklist_version INTEGER NOT NULL,
                    model_output TEXT NOT NULL, body TEXT NOT NULL, engine_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(checklist_id,checklist_version) REFERENCES checklist_snapshots(checklist_id,version),
                    FOREIGN KEY(conversation_id,conversation_hash) REFERENCES transcript_snapshots(conversation_id,content_hash));
                CREATE INDEX IF NOT EXISTS evaluations_v1_conversation
                    ON evaluation_snapshots(conversation_id,conversation_hash,created_at);
            """)
        self.sync_legacy_checklists()

    def sync_legacy_checklists(self) -> None:
        with self.source.connect() as db:
            rows = db.execute("SELECT body,created_at FROM checklists ORDER BY version").fetchall()
        for row in rows:
            self.save_checklist(checklist_from_ui(json.loads(row["body"])), row["created_at"])

    def save_checklist(self, checklist: Checklist, created_at: str) -> Checklist:
        body = canonical_json(checklist.model_dump())
        with self.source.connect(write=True) as db:
            existing = db.execute("SELECT body FROM checklist_snapshots WHERE checklist_id=? AND version=?",
                                  (checklist.id, checklist.version)).fetchone()
            if existing:
                if existing["body"] != body:
                    raise Conflict("Сохранённая версия чек-листа неизменяема.")
                return checklist
            latest = db.execute("SELECT MAX(version) FROM checklist_snapshots WHERE checklist_id=?", (checklist.id,)).fetchone()[0]
            if checklist.version != (latest or 0) + 1:
                raise Conflict("Новая версия должна следовать за последней сохранённой.")
            fingerprints = {c.id: digest(c.model_dump()) for c in checklist.criteria}
            db.execute("INSERT INTO checklist_snapshots VALUES(?,?,?,?,?,?)", (
                checklist.id, checklist.version, body, digest(checklist.model_dump()), canonical_json(fingerprints), created_at))
            current = self.source.checklist(db)
            legacy = db.execute("SELECT body FROM checklists WHERE version=?", (checklist.version,)).fetchone()
            if current["id"] == checklist.id and legacy is None:
                db.execute("INSERT INTO checklists VALUES(?,?,?)", (checklist.version, canonical_json(checklist_to_ui(checklist)), created_at))
                self.source.bump(db)
        return checklist

    def list_checklists(self) -> list[Checklist]:
        self.sync_legacy_checklists()
        with self.source.connect() as db:
            return [Checklist.model_validate_json(row[0]) for row in db.execute("SELECT body FROM checklist_snapshots ORDER BY checklist_id,version")]

    def request(self, conversation_id: str, checklist_id: str, version: int,
                knowledge_context: str, prompt_version: str, knowledge_version: str) -> EvaluatorInput:
        self.sync_legacy_checklists()
        with self.source.connect() as db:
            conversation_row = db.execute("SELECT body FROM conversations WHERE id=? AND active=1", (conversation_id,)).fetchone()
            checklist_row = db.execute("SELECT body FROM checklist_snapshots WHERE checklist_id=? AND version=?", (checklist_id, version)).fetchone()
        if not conversation_row:
            raise KeyError("Диалог не найден.")
        if not checklist_row:
            raise KeyError("Версия чек-листа не найдена.")
        return EvaluatorInput(conversation=conversation_from_ui(json.loads(conversation_row[0])),
                              checklist=Checklist.model_validate_json(checklist_row[0]), knowledge_context=knowledge_context,
                              prompt_version=prompt_version, knowledge_version=knowledge_version)

    def create_job(self, job: EvaluationJob, request: EvaluatorInput, created_at: str) -> None:
        conversation = request.conversation
        with self.source.connect(write=True) as db:
            db.execute("INSERT OR IGNORE INTO transcript_snapshots VALUES(?,?,?)",
                       (conversation.id, conversation.content_hash, canonical_json(conversation.model_dump())))
            db.execute("INSERT INTO evaluation_jobs VALUES(?,?,?,?,?)",
                       (job.id, job.status, job.model_dump_json(), request.model_dump_json(), created_at))

    def get_job(self, job_id: str) -> EvaluationJob:
        with self.source.connect() as db:
            row = db.execute("SELECT body FROM evaluation_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError("Задача оценки не найдена.")
        return EvaluationJob.model_validate_json(row[0])

    def update_job(self, job: EvaluationJob) -> None:
        with self.source.connect(write=True) as db:
            updated = db.execute("UPDATE evaluation_jobs SET status=?,body=? WHERE id=? AND status IN ('queued','running')",
                                 (job.status, job.model_dump_json(), job.id))
            if updated.rowcount != 1:
                raise Conflict("Завершённая задача не может быть перезаписана.")

    def finish_job(self, job_id: str, evaluation: Evaluation, output: ModelOutput, engine_version: str) -> EvaluationJob:
        fingerprint = digest({"conversation_hash": evaluation.conversation_hash,
                              "checklist": evaluation.checklist.model_dump(), "output": output.model_dump(),
                              "provenance": evaluation.provenance.model_dump(), "engine_version": engine_version})
        with self.source.connect(write=True) as db:
            current = db.execute("SELECT status FROM evaluation_jobs WHERE id=?", (job_id,)).fetchone()
            if not current or current[0] not in ("queued", "running"):
                raise Conflict("Задача уже завершена.")
            stored = db.execute("SELECT body FROM evaluation_snapshots WHERE fingerprint=?", (fingerprint,)).fetchone()
            if stored:
                evaluation = Evaluation.model_validate_json(stored[0])
            else:
                db.execute("INSERT INTO evaluation_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)", (
                    evaluation.id, fingerprint, evaluation.conversation_id, evaluation.conversation_hash,
                    evaluation.checklist.id, evaluation.checklist.version, output.model_dump_json(),
                    evaluation.model_dump_json(), engine_version, evaluation.created_at))
            job = EvaluationJob(schema_version="1.0", id=job_id, status=evaluation.final.status, evaluation=evaluation, error=None)
            db.execute("UPDATE evaluation_jobs SET status=?,body=? WHERE id=?", (job.status, job.model_dump_json(), job.id))
            self.source.bump(db)
        return job

    def recover_interrupted(self, make_error: Callable[[str], EvaluationJob]) -> None:
        with self.source.connect(write=True) as db:
            rows = db.execute("SELECT id FROM evaluation_jobs WHERE status IN ('queued','running')").fetchall()
            for row in rows:
                job = make_error(row[0])
                db.execute("UPDATE evaluation_jobs SET status=?,body=? WHERE id=?", (job.status, job.model_dump_json(), job.id))

    def latest_for(self, conversation: dict) -> Evaluation | None:
        with self.source.connect() as db:
            raw = db.execute("SELECT body FROM conversations WHERE id=? AND active=1", (conversation["id"],)).fetchone()
            if raw is None:
                return None
            current = conversation_from_ui(json.loads(raw[0]))
            row = db.execute("SELECT body FROM evaluation_snapshots WHERE conversation_id=? AND conversation_hash=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                             (current.id, current.content_hash)).fetchone()
        return Evaluation.model_validate_json(row[0]) if row else None
