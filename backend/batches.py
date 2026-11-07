"""Durable, serial SQLite batches. A submitted provider call is never replayed.

One backend process owns a database (OS lock for the entire application lifespan).
SQLite transactions arbitrate start/stop/resume and job claims, not browser tabs.
Restart requires an explicit resume; only never-submitted items can be resumed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from .contracts.v2 import to_wire_checklist
from .evaluator import error_payload
from .repository import Conflict, dump, now


def uncertain_error(code):
    # The adapter currently folds transport/5xx failures into PROVIDER_UNAVAILABLE.
    # Only a confirmed rate-limit rejection is safe to classify as a known failure.
    return code in ('BATCH_OUTCOME_UNKNOWN', 'EVALUATOR_ERROR', 'WORKER_INTERRUPTED') or (
        code.startswith('PROVIDER_') and code != 'PROVIDER_RATE_LIMIT')


class WorkerLock:
    """Fail closed if a second application tries to recover a live worker's DB."""
    def __init__(self, path):
        self.path = str(Path(path).resolve()) + '.worker.lock'
        self.handle = None

    def acquire(self):
        handle = open(self.path, 'a+b')
        try:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError('Эта база уже обслуживается другим процессом. Используйте один backend worker.') from None
        self.handle = handle

    def release(self):
        if self.handle:
            self.handle.close(); self.handle = None


class BatchService:
    def __init__(self, repo, evaluator):
        self.repo = repo
        self.evaluator = evaluator
        self.tasks = set()
        with repo.connect(write=True) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS evaluation_batches(
                    id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                    fingerprint TEXT NOT NULL UNIQUE, group_name TEXT NOT NULL,
                    checklist TEXT NOT NULL, status TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0, reason TEXT,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS batch_items(
                    batch_id TEXT NOT NULL REFERENCES evaluation_batches(id),
                    ordinal INTEGER NOT NULL, conversation_id TEXT NOT NULL,
                    evaluation_id TEXT NOT NULL UNIQUE REFERENCES evaluation_jobs(id),
                    status TEXT NOT NULL, error TEXT,
                    PRIMARY KEY(batch_id, ordinal), UNIQUE(batch_id, conversation_id));
                CREATE TABLE IF NOT EXISTS batch_start_requests(
                    request_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES evaluation_batches(id));
            ''')

    def _get(self, db, identifier):
        row = db.execute('SELECT * FROM evaluation_batches WHERE id=?', (identifier,)).fetchone()
        if not row: raise KeyError('Очередь не найдена.')
        checklist = json.loads(row['checklist'])
        return {'schema_version': '2.0', 'id': row['id'], 'group': row['group_name'],
                'version': checklist['version'], 'checklist': to_wire_checklist(checklist),
                'revision': row['revision'], 'status': row['status'],
                'running': row['status'] in ('running', 'stopping'), 'stopping': row['status'] == 'stopping',
                'blockedReason': row['reason'], 'createdAt': row['created_at'],
                'items': [{'id': item['conversation_id'], 'evaluationId': item['evaluation_id'],
                           'status': item['status'], 'error': item['error']}
                          for item in db.execute('SELECT * FROM batch_items WHERE batch_id=? ORDER BY ordinal', (identifier,))]}

    def get(self, identifier):
        with self.repo.connect() as db: return self._get(db, identifier)

    def current(self):
        with self.repo.connect() as db:
            row = db.execute('SELECT id FROM evaluation_batches ORDER BY rowid DESC LIMIT 1').fetchone()
            return {'schema_version': '2.0', 'batch': self._get(db, row[0]) if row else None}

    def create(self, body):
        # Order is canonical so two tabs selecting the same set coalesce.
        ids = sorted(set(body.conversationIds))
        payload = {'group': body.group, 'ids': ids, 'version': body.checklistVersion, 'revision': body.expectedRevision}
        fingerprint = hashlib.sha256(dump(payload).encode()).hexdigest()
        with self.repo.connect(write=True) as db:
            old = db.execute('''SELECT id,fingerprint FROM evaluation_batches WHERE request_id=?
                UNION SELECT b.id,b.fingerprint FROM evaluation_batches b JOIN batch_start_requests r ON r.batch_id=b.id WHERE r.request_id=?''', (body.requestId, body.requestId)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint: raise Conflict('Ключ запуска уже использован с другим составом.')
                return self._get(db, old['id'])
            old = db.execute('SELECT id FROM evaluation_batches WHERE fingerprint=?', (fingerprint,)).fetchone()
            if old:
                db.execute('INSERT OR IGNORE INTO batch_start_requests VALUES(?,?)', (body.requestId, old['id']))
                return self._get(db, old['id'])
            if db.execute("SELECT 1 FROM evaluation_batches WHERE status<>'completed'").fetchone():
                raise Conflict('Сначала завершите текущую очередь. Её состояние сохранено на сервере.')
            if body.expectedRevision != self.repo.metadata(db, 'revision'):
                raise Conflict('Данные изменились. Обновите состав перед новым запуском.')
            if db.execute("SELECT 1 FROM evaluation_jobs WHERE status IN ('queued','running')").fetchone():
                raise Conflict('Дождитесь завершения текущей одиночной оценки.')
            checklist = self.repo.checklist(db, body.checklistVersion)
            if body.group != 'workspace:':
                if not body.group.startswith('group:'): raise ValueError('Выберите сохранённый набор.')
                dataset = self.repo.metadata(db, 'dataset:' + body.group[6:])
                if not dataset:
                    latest = self.repo.metadata(db, 'dataset')
                    dataset = latest if latest and latest['group'] == body.group[6:] else None
                if not dataset or not set(ids).issubset(dataset['conversationIds']):
                    raise Conflict('Состав не соответствует сохранённому набору.')
            identifier = str(uuid4())
            db.execute('INSERT INTO evaluation_batches VALUES(?,?,?,?,?,?,0,NULL,?)',
                       (identifier, body.requestId, fingerprint, body.group, dump(checklist), 'running', now()))
            db.execute('INSERT INTO batch_start_requests VALUES(?,?)', (body.requestId, identifier))
            for ordinal, conversation_id in enumerate(ids):
                latest = db.execute('''SELECT j.result FROM evaluation_jobs j JOIN conversations c
                    ON c.id=j.conversation_id AND c.content_hash=j.content_hash
                    WHERE j.conversation_id=? ORDER BY j.created_at DESC,j.rowid DESC LIMIT 1''', (conversation_id,)).fetchone()
                if latest and uncertain_error((json.loads(latest[0]).get('error') or {}).get('code', '')):
                    raise Conflict('У диалога есть запрос с неопределённым результатом. Проверьте результат и расходы перед отдельной переоценкой; массовый повтор заблокирован.')
                job = self.evaluator.create(conversation_id, body.checklistVersion, transaction=db)
                db.execute('INSERT INTO batch_items VALUES(?,?,?,?,?,NULL)',
                           (identifier, ordinal, conversation_id, job['id'], 'queued'))
            self.repo.bump(db)
            return self._get(db, identifier)

    @staticmethod
    def _change(db, identifier, status, reason=None):
        db.execute('UPDATE evaluation_batches SET status=?,reason=?,revision=revision+1 WHERE id=?', (status, reason, identifier))

    def stop(self, identifier, expected_revision):
        with self.repo.connect(write=True) as db:
            current = self._get(db, identifier)
            # Stop is idempotent and safe even when a progress update won the race.
            if current['status'] == 'running':
                running = any(i['status'] == 'running' for i in current['items'])
                self._change(db, identifier, 'stopping' if running else 'paused')
            return self._get(db, identifier)

    def resume(self, identifier, expected_revision):
        with self.repo.connect(write=True) as db:
            current = self._get(db, identifier)
            if current['status'] in ('running', 'stopping', 'completed'): return current
            if any(i['status'] == 'running' for i in current['items']):
                raise Conflict('Нужно перезапустить сервер для сверки прерванного запроса.')
            if current['revision'] != expected_revision:
                raise Conflict('Состояние очереди изменилось. Обновите данные перед продолжением.')
            self._change(db, identifier, 'running' if any(i['status'] == 'queued' for i in current['items']) else 'completed')
            return self._get(db, identifier)

    def cancel(self, identifier, expected_revision):
        """Cancel only never-submitted work; keep current call and all history."""
        with self.repo.connect(write=True) as db:
            batch = self._get(db, identifier)
            if batch['status'] == 'completed': return batch
            for item in batch['items']:
                if item['status'] != 'queued': continue
                row = db.execute('SELECT result FROM evaluation_jobs WHERE id=?', (item['evaluationId'],)).fetchone()
                result = json.loads(row[0])
                result.update(status='failed', results=[], error=error_payload('BATCH_CANCELLED', 'Отменено пользователем до отправки запроса.'))
                db.execute("UPDATE evaluation_jobs SET status='failed',result=?,updated_at=? WHERE id=?", (dump(result), now(), item['evaluationId']))
                db.execute("UPDATE batch_items SET status='cancelled',error=? WHERE evaluation_id=?", (result['error']['message'], item['evaluationId']))
            running = any(i['status'] == 'running' for i in batch['items'])
            self._change(db, identifier, 'stopping' if running else 'completed', 'Оставшиеся диалоги отменены пользователем.')
            self.repo.bump(db)
            return self._get(db, identifier)

    def _claim(self, identifier):
        with self.repo.connect(write=True) as db:
            batch = self._get(db, identifier)
            if batch['status'] != 'running' or any(i['status'] == 'running' for i in batch['items']): return None
            item = next((i for i in batch['items'] if i['status'] == 'queued'), None)
            if not item:
                self._change(db, identifier, 'completed', batch['blockedReason']); return None
            # Re-check appeals/content immediately before entering the provider path.
            job = db.execute('SELECT * FROM evaluation_jobs WHERE id=?', (item['evaluationId'],)).fetchone()
            conversation = db.execute('SELECT content_hash,active FROM conversations WHERE id=?', (item['id'],)).fetchone()
            appealed = db.execute('SELECT 1 FROM appeals a JOIN evaluations e ON a.evaluation_id=e.id WHERE e.conversation_id=?', (item['id'],)).fetchone()
            if appealed or not conversation or not conversation['active'] or conversation['content_hash'] != job['content_hash']:
                result = json.loads(job['result'])
                result.update(status='failed_validation', error=error_payload('INPUT_CHANGED', 'Диалог изменился или появилась апелляция. Запрос не отправлен.'))
                db.execute("UPDATE evaluation_jobs SET status='failed_validation',result=?,updated_at=? WHERE id=?", (dump(result), now(), item['evaluationId']))
                db.execute("UPDATE batch_items SET status='failed',error=? WHERE evaluation_id=?", (result['error']['message'], item['evaluationId']))
                self._change(db, identifier, 'paused', result['error']['message'])
                self.repo.bump(db)
                return None
            db.execute("UPDATE batch_items SET status='running' WHERE evaluation_id=? AND status='queued'", (item['evaluationId'],))
            db.execute('UPDATE evaluation_batches SET revision=revision+1 WHERE id=?', (identifier,))
            return item['evaluationId']

    def _settle(self, db, identifier, evaluation_id, *, recovering=False):
        job = db.execute('SELECT * FROM evaluation_jobs WHERE id=?', (evaluation_id,)).fetchone()
        result = json.loads(job['result'])
        saved = db.execute('SELECT body FROM evaluations WHERE id=?', (evaluation_id,)).fetchone()
        if saved:
            result = json.loads(saved[0]); result.update(status='completed', error=None)
        elif result['status'] in ('queued', 'running'):
            # Even a crash between claim and I/O is treated conservatively as unknown.
            result.update(status='failed', results=[], error=error_payload('BATCH_OUTCOME_UNKNOWN', 'Результат запроса неизвестен. Автоматический повтор запрещён; нужна сверка расходов и результата.'))
        error = result.get('error') or {}
        code = error.get('code', '')
        uncertain = uncertain_error(code)
        if uncertain:
            error['retryable'] = False
        status = 'uncertain' if uncertain else result['status'] if result['status'] in ('completed', 'needs_review') else 'failed'
        db.execute('UPDATE evaluation_jobs SET status=?,result=?,updated_at=? WHERE id=?', (result['status'], dump(result), now(), evaluation_id))
        db.execute('UPDATE batch_items SET status=?,error=? WHERE evaluation_id=?', (status, error.get('message'), evaluation_id))
        batch = self._get(db, identifier)
        pending = any(i['status'] == 'queued' for i in batch['items'])
        blocked = uncertain or code.startswith(('BUDGET_', 'PROVIDER_')) or code == 'MODEL_NOT_ALLOWED'
        reason = error.get('message') if blocked else batch['blockedReason']
        next_status = 'completed' if not pending else 'paused' if recovering or blocked or batch['status'] == 'stopping' else 'running'
        self._change(db, identifier, next_status, reason)
        self.repo.bump(db)

    async def run(self, identifier):
        try:
            while evaluation_id := self._claim(identifier):
                await self.evaluator.run(evaluation_id, allow_retry=False)
                with self.repo.connect(write=True) as db: self._settle(db, identifier, evaluation_id)
        except asyncio.CancelledError:
            raise  # On startup recover() records uncertainty; no submitted call is replayed.
        except Exception:
            # Fail closed if persistence/worker code fails after submitting a request.
            with self.repo.connect(write=True) as db:
                for item in db.execute("SELECT evaluation_id FROM batch_items WHERE batch_id=? AND status='running'", (identifier,)).fetchall():
                    self._settle(db, identifier, item[0], recovering=True)
                batch = self._get(db, identifier)
                pending = any(i['status'] == 'queued' for i in batch['items'])
                self._change(db, identifier, 'paused' if pending else 'completed', batch['blockedReason'] or 'Работа очереди прервана. Проверьте результаты перед продолжением оставшихся диалогов.')

    def kick(self, identifier):
        task = asyncio.create_task(self.run(identifier))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def close(self):
        for task in list(self.tasks): task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def recover(self):
        with self.repo.connect(write=True) as db:
            rows = db.execute("SELECT id FROM evaluation_batches WHERE status<>'completed'").fetchall()
            for row in rows:
                identifier = row['id']
                for item in db.execute("SELECT evaluation_id FROM batch_items WHERE batch_id=? AND status='running'", (identifier,)).fetchall():
                    self._settle(db, identifier, item[0], recovering=True)
                batch = self._get(db, identifier)
                pending = any(i['status'] == 'queued' for i in batch['items'])
                self._change(db, identifier, 'paused' if pending else 'completed', batch['blockedReason'] or 'Сервер перезапущен. Продолжите только оставшиеся диалоги.')
