"""Atomic identity audit extension without altering the shared Repository module."""
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from .repository import Repository, now
from .security import LOCAL_PRINCIPAL, Principal

_event = ContextVar("cqa_identity_event", default=None)


class AuditedRepository(Repository):
    def __init__(self, path, *, enterprise=False):
        self.enterprise = enterprise
        super().__init__(path)
        with super().connect(write=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS enterprise_actor_audit (
                id TEXT PRIMARY KEY, action TEXT NOT NULL, resource_id TEXT NOT NULL,
                actor_id TEXT NOT NULL, created_at TEXT NOT NULL)""")

    @contextmanager
    def connect(self, write=False):
        # Stamp the actor before the SAME transaction commits. An audit failure
        # rolls back the appeal/decision too; no success-with-missing-author gap.
        with super().connect(write=write) as db:
            yield db
            event = _event.get()
            if write and event is not None:
                action, actor, resource = event
                if action == "appeal.created":
                    row = db.execute("SELECT id FROM appeals WHERE evaluation_id=? AND criterion_id=?", resource).fetchone()
                    resource_id, field = row[0], "createdBy"
                else:
                    resource_id, field = resource, "decidedBy"
                db.execute("UPDATE appeals SET body=json_set(body, ?, ?) WHERE id=?",
                           ("$." + field, actor.actor_id, resource_id))
                db.execute("INSERT INTO enterprise_actor_audit VALUES(?,?,?,?,?)",
                           (str(uuid4()), action, resource_id, actor.actor_id, now()))

    def _actor(self, actor):
        if actor is None and not self.enterprise:
            return LOCAL_PRINCIPAL
        if not isinstance(actor, Principal) or (self.enterprise and actor.local):
            raise ValueError("A verified server identity is required")
        return actor

    def create_appeal(self, body, *, actor=None):
        event = _event.set(("appeal.created", self._actor(actor), (body.get("evaluationId"), body.get("criterionId"))))
        try:
            return super().create_appeal(body)
        finally:
            _event.reset(event)

    def decide_appeal(self, appeal_id, body, preview=False, *, wire_decimals=False, actor=None):
        event = _event.set(None if preview else ("appeal.decided", self._actor(actor), appeal_id))
        try:
            return super().decide_appeal(appeal_id, body, preview=preview, wire_decimals=wire_decimals)
        finally:
            _event.reset(event)
