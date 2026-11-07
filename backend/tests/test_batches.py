"""Durable batch acceptance: temporary SQLite + synthetic evaluator, never API spend."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.batches import BatchService, WorkerLock
from backend.contracts.v2 import StartBatchRequest, EvaluationBatch
from backend.evaluator import AdapterFailure, EvaluationService
from backend.repository import Repository, Conflict


def records():
    return [{'conversation_id': f'batch-{i}', 'channel': 'agent chat', 'language': 'RU', 'audio_url': None,
             'transcript': [{'speaker': 'client', 'timestamp': '00:00', 'text': 'Синтетический вопрос.'},
                            {'speaker': 'operator', 'timestamp': '00:01', 'text': 'Здравствуйте! Синтетический ответ.'}]}
            for i in range(3)]


def output(request):
    return {'schema_version': '2.0', 'results': [
        {'criterionId': c.id, 'value': '0' if c.type == 'flag' else c.max,
         'explanation': 'Синтетический ответ для теста очереди.', 'applicability_reason': None,
         'evidence': [{'kind': 'message', 'message_id': request.messages[1].id, 'quote': 'Здравствуйте!'}]}
        for c in request.checklist.criteria]}


@pytest.fixture
def setup(tmp_path):
    repo = Repository(tmp_path / 'batch.sqlite')
    repo.import_data(records())
    calls = []

    async def adapter(request):
        calls.append(request.model_copy(deep=True))
        await asyncio.sleep(0)
        return output(request)

    service = EvaluationService(repo, adapter)
    batches = BatchService(repo, service)
    return repo, service, batches, calls


def body(repo, **overrides):
    return StartBatchRequest.model_validate({'schema_version': '2.0', 'requestId': str(uuid4()), 'group': 'workspace:',
        'conversationIds': ['batch-0', 'batch-1', 'batch-2'], 'checklistVersion': 1,
        'expectedRevision': repo.state()['revision'], **overrides})


def test_duplicate_start_two_tabs_repeated_http_identity_and_worker_claims(setup):
    repo, service, batches, calls = setup
    request = body(repo)
    # Distinct keys, same reviewed snapshot: even racing browser tabs coalesce.
    requests = [request.model_copy(update={'requestId': str(uuid4())}) for _ in range(12)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        started = list(pool.map(batches.create, requests))
    identifier = started[0]['id']
    assert {r['id'] for r in started} == {identifier}

    async def race():
        await asyncio.gather(*(batches.run(identifier) for _ in range(12)))
        for item in batches.get(identifier)['items']:
            await service.run(item['evaluationId'], allow_retry=False)
    asyncio.run(race())
    assert [r.conversationId for r in calls] == ['batch-0', 'batch-1', 'batch-2']
    assert batches.get(identifier)['status'] == 'completed'
    assert batches.create(request)['id'] == identifier  # retry after response loss/completion
    assert batches.create(requests[0])['id'] == identifier
    assert len(calls) == 3
    with pytest.raises(Conflict):
        batches.create(requests[0].model_copy(update={'conversationIds': ['batch-0']}))
    with pytest.raises(Conflict):
        batches.create(request.model_copy(update={'requestId': str(uuid4()), 'conversationIds': ['batch-0']}))
    with repo.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM evaluation_jobs').fetchone()[0] == 3
        assert db.execute('SELECT COUNT(*) FROM evaluation_attempts').fetchone()[0] == 3


def test_stop_finishes_current_resume_keeps_snapshot_and_only_remaining(setup):
    repo, service, batches, calls = setup

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def hold(request):
            calls.append(request.model_copy(deep=True))
            if len(calls) == 1:
                entered.set(); await release.wait()
            return output(request)
        service.adapter = hold
        batch = batches.create(body(repo))
        identifier = batch['id']
        task = asyncio.create_task(batches.run(identifier))
        await entered.wait()
        assert batches.stop(identifier, 0)['status'] == 'stopping'
        assert len(calls) == 1
        changed = deepcopy(repo.state()['data']['checklist']); changed['criteria'][0]['weight'] = 20
        repo.save_checklist(changed, 1)
        release.set(); await task
        paused = batches.get(identifier)
        assert paused['status'] == 'paused'
        assert [i['status'] for i in paused['items']] == ['completed', 'queued', 'queued']
        assert paused['checklist']['version'] == 1
        assert batches.current()['batch'] == paused
        assert batches.resume(identifier, paused['revision'])['status'] == 'running'
        batches.resume(identifier, paused['revision'])  # duplicate resume
        await asyncio.gather(batches.run(identifier), batches.run(identifier))
        # A delayed replay of the old resume after a later stop cannot restart work.
        assert batches.get(identifier)['status'] == 'completed'
        assert [r.conversationId for r in calls] == ['batch-0', 'batch-1', 'batch-2']
        assert {r.checklist.version for r in calls} == {1}
        assert {r.checklist.criteria[0].weight for r in calls} == {'10'}
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['BUDGET_EXCEEDED', 'BUDGET_HISTORY_UNKNOWN', 'BUDGET_STORAGE_ERROR',
                                    'PROVIDER_UNAVAILABLE', 'PROVIDER_RATE_LIMIT', 'MODEL_NOT_ALLOWED', 'timeout', 'unknown'])
def test_budget_provider_unknown_stop_without_retry_and_resume_skips_failed(setup, failure):
    repo, service, batches, calls = setup
    async def adapter(request):
        calls.append(request.conversationId)
        if len(calls) == 1:
            if failure == 'timeout': await asyncio.sleep(1)
            if failure == 'unknown': raise RuntimeError('private provider error with secret')
            raise AdapterFailure(failure, retryable=True)
        return output(request)
    service.adapter = adapter; service.timeout = .02
    batch = batches.create(body(repo))
    asyncio.run(batches.run(batch['id']))
    paused = batches.get(batch['id'])
    assert calls == ['batch-0'] and paused['status'] == 'paused'
    assert [i['status'] for i in paused['items']][1:] == ['queued', 'queued']
    if failure in ('timeout', 'unknown', 'PROVIDER_UNAVAILABLE'): assert paused['items'][0]['status'] == 'uncertain'
    assert 'secret' not in json.dumps(paused)
    batches.resume(batch['id'], paused['revision'])
    asyncio.run(batches.run(batch['id']))
    assert calls == ['batch-0', 'batch-1', 'batch-2']


def test_restart_unknown_is_persisted_no_replay_and_pending_jobs_survive(setup):
    repo, service, batches, calls = setup
    async def crash():
        entered = asyncio.Event()
        async def pending(request):
            calls.append(request.conversationId); entered.set()
            await asyncio.Event().wait()
        service.adapter = pending
        batch = batches.create(body(repo))
        task = asyncio.create_task(batches.run(batch['id']))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        return batch
    batch = asyncio.run(crash())
    async def adapter(request): calls.append(request.conversationId); return output(request)
    restarted = EvaluationService(Repository(repo.path), adapter)
    recovered = BatchService(restarted.repo, restarted)
    recovered.recover(); restarted.recover(); recovered.recover()
    paused = recovered.get(batch['id'])
    assert paused['status'] == 'paused'
    assert [i['status'] for i in paused['items']] == ['uncertain', 'queued', 'queued']
    failed = restarted.get(paused['items'][0]['evaluationId'])
    assert failed['error']['code'] == 'BATCH_OUTCOME_UNKNOWN'
    assert failed['error']['retryable'] is False
    assert calls == ['batch-0']
    recovered.resume(batch['id'], paused['revision'])
    asyncio.run(recovered.run(batch['id']))
    assert calls == ['batch-0', 'batch-1', 'batch-2']


def test_restart_after_result_commit_reconciles_success_without_second_call(setup):
    repo, service, batches, calls = setup
    batch = batches.create(body(repo))
    job = batches._claim(batch['id'])
    asyncio.run(service.run(job, allow_retry=False))  # crash before batch progress commit
    restarted = BatchService(repo, service)
    restarted.recover(); service.recover()
    paused = restarted.get(batch['id'])
    assert paused['items'][0]['status'] == 'completed' and paused['status'] == 'paused'
    restarted.resume(batch['id'], paused['revision'])
    asyncio.run(restarted.run(batch['id']))
    assert len(calls) == 3


@pytest.mark.parametrize('change', ['inactive', 'content', 'appeal'])
def test_context_change_marks_job_terminal_and_cancel_unblocks_new_batch(setup, change):
    repo, service, batches, calls = setup
    if change == 'appeal':
        prior = service.create('batch-0'); asyncio.run(service.run(prior['id']))
    batch = batches.create(body(repo))
    if change == 'appeal':
        repo.create_appeal({'conversationId': 'batch-0', 'evaluationId': prior['id'], 'criterionId': 'greeting', 'comment': 'Синтетическая апелляция для проверки.'})
    else:
        with repo.connect(write=True) as db:
            db.execute("UPDATE conversations SET " + ('active=0' if change == 'inactive' else "content_hash='changed'") + " WHERE id='batch-0'")
    before = len(calls)
    asyncio.run(batches.run(batch['id']))
    paused = batches.get(batch['id'])
    assert paused['status'] == 'paused' and paused['items'][0]['status'] == 'failed'
    assert service.get(paused['items'][0]['evaluationId'])['status'] == 'failed_validation'
    assert len(calls) == before
    cancelled = batches.cancel(batch['id'], paused['revision'])
    assert cancelled['status'] == 'completed'
    assert [i['status'] for i in cancelled['items']] == ['failed', 'cancelled', 'cancelled']
    assert all(service.get(i['evaluationId'])['status'] not in ('queued', 'running') for i in cancelled['items'])
    new = batches.create(body(repo, conversationIds=['batch-1', 'batch-2']))
    asyncio.run(batches.run(new['id']))
    assert len(calls) == before + 2
    assert batches.get(batch['id']) == cancelled  # history retained


def test_cancel_during_call_finishes_current_and_unblocks_single_evaluation(setup):
    repo, service, batches, calls = setup
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def hold(request):
            calls.append(request.conversationId); entered.set(); await release.wait(); return output(request)
        service.adapter = hold
        batch = batches.create(body(repo))
        task = asyncio.create_task(batches.run(batch['id'])); await entered.wait()
        cancelled = batches.cancel(batch['id'], 0)
        assert cancelled['status'] == 'stopping'
        assert [i['status'] for i in cancelled['items']] == ['running', 'cancelled', 'cancelled']
        with pytest.raises(Conflict): service.create('batch-2')
        release.set(); await task
        assert calls == ['batch-0']
        assert batches.get(batch['id'])['status'] == 'completed'
        job = service.create('batch-2'); await service.run(job['id'])
        assert calls == ['batch-0', 'batch-2']
    asyncio.run(scenario())


def test_stale_resume_cannot_restart_a_new_pause(setup):
    repo, service, batches, calls = setup
    batch = batches.create(body(repo))
    paused = batches.stop(batch['id'], 0)
    batches.resume(batch['id'], paused['revision'])
    latest = batches.stop(batch['id'], 0)
    with pytest.raises(Conflict): batches.resume(batch['id'], paused['revision'])
    assert batches.get(batch['id']) == latest and calls == []


def test_batch_creation_rolls_back_all_snapshots_on_invalid_member(setup):
    repo, service, batches, calls = setup
    with pytest.raises(KeyError): batches.create(body(repo, conversationIds=['batch-0', 'missing']))
    assert batches.current()['batch'] is None
    with repo.connect() as db: assert db.execute('SELECT COUNT(*) FROM evaluation_jobs').fetchone()[0] == 0


def test_group_membership_is_server_validated(setup):
    repo, service, batches, calls = setup
    with repo.connect(write=True) as db:
        repo.set_metadata(db, 'dataset:SYNTHETIC', {'group': 'SYNTHETIC', 'conversationIds': ['batch-1']})
    with pytest.raises(Conflict): batches.create(body(repo, group='group:SYNTHETIC'))
    batch = batches.create(body(repo, group='group:SYNTHETIC', conversationIds=['batch-1']))
    assert batch['group'] == 'group:SYNTHETIC' and len(batch['items']) == 1


def test_worker_lock_prevents_recovering_a_live_process(tmp_path):
    first, second = WorkerLock(tmp_path / 'test.sqlite'), WorkerLock(tmp_path / 'test.sqlite')
    first.acquire()
    try:
        with pytest.raises(RuntimeError): second.acquire()
    finally: first.release()
    second.acquire(); second.release()


@pytest.mark.parametrize('code', ['BATCH_OUTCOME_UNKNOWN', 'PROVIDER_TIMEOUT', 'PROVIDER_UNAVAILABLE', 'PROVIDER_CONNECTION_ERROR', 'EVALUATOR_ERROR', 'WORKER_INTERRUPTED'])
def test_unknown_outcome_is_blocked_from_new_batch_even_direct_post(setup, code):
    repo, service, batches, calls = setup
    job = service.create('batch-0')
    from backend.evaluator import error_payload
    service.update(job['id'], 'failed', error=error_payload(code, 'Неопределённый результат.'))
    with pytest.raises(Conflict): batches.create(body(repo))
    assert batches.current()['batch'] is None and calls == []
    # Explicit single evaluation remains available after human reconciliation.
    manual = service.create('batch-0')
    asyncio.run(service.run(manual['id']))
    assert batches.create(body(repo))['status'] == 'running'


def test_http_close_reload_two_clients_and_repeat_post_are_observation_only(tmp_path):
    calls, entered, release = [], threading.Event(), threading.Event()
    async def adapter(request):
        calls.append(request.conversationId); entered.set()
        while not release.is_set(): await asyncio.sleep(.005)
        return output(request)
    app = create_app(tmp_path / 'http.sqlite', tmp_path / 'missing.json', organizer_url='', evaluator=adapter)
    app.state.repo.import_data(records())
    with TestClient(app) as client:
        request = body(app.state.repo).model_dump()
        response = client.post('/api/batches', json=request)
        assert response.status_code == 202
        batch = EvaluationBatch.model_validate(response.json())
        assert entered.wait(2)
        # Two independent clients/tab observers, then no client traffic while worker finishes.
        other = TestClient(app)  # A second HTTP client shares the existing app lifespan.
        try:
            assert other.get('/api/batches/current').json()['batch']['id'] == batch.id
            assert other.post('/api/batches', json={**request, 'requestId': str(uuid4())}).json()['id'] == batch.id
        finally: other.close()
        assert client.post('/api/batches', json=request).json()['id'] == batch.id
        assert calls == ['batch-0']
        release.set()
        deadline = time.monotonic() + 3
        while app.state.batches.get(batch.id)['status'] != 'completed' and time.monotonic() < deadline:
            time.sleep(.01)
        current = client.get('/api/batches/current').json()['batch']
        assert current['status'] == 'completed'
        assert calls == ['batch-0', 'batch-1', 'batch-2']
        assert client.post('/api/batches', json=request).json()['id'] == batch.id
        assert len(calls) == 3


def test_http_transport_unknown_cannot_be_mass_retried(tmp_path):
    calls = []
    async def adapter(request):
        calls.append(request.conversationId)
        raise AdapterFailure('PROVIDER_UNAVAILABLE', retryable=True)
    app = create_app(tmp_path / 'transport.sqlite', tmp_path / 'missing.json', organizer_url='', evaluator=adapter)
    repo = app.state.repo
    repo.import_data(records())
    with TestClient(app) as client:
        started = client.post('/api/batches', json=body(repo, conversationIds=['batch-0']).model_dump())
        assert started.status_code == 202
        deadline = time.monotonic() + 3
        while app.state.batches.get(started.json()['id'])['status'] != 'completed' and time.monotonic() < deadline:
            time.sleep(.01)
        current = client.get('/api/batches/current').json()['batch']
        assert current['items'][0]['status'] == 'uncertain'
        assert calls == ['batch-0']
        repeat = client.post('/api/batches', json=body(repo, conversationIds=['batch-0']).model_dump())
        assert repeat.status_code == 409
        assert calls == ['batch-0']


def test_appeal_added_during_current_call_keeps_prior_result(setup):
    repo, service, batches, calls = setup
    prior = service.create('batch-0'); asyncio.run(service.run(prior['id']))
    async def adapter(request):
        calls.append(request)
        repo.create_appeal({'conversationId': 'batch-0', 'evaluationId': prior['id'], 'criterionId': 'greeting', 'comment': 'Проверка апелляции во время запроса.'})
        return output(request)
    service.adapter = adapter
    started = batches.create(body(repo, conversationIds=['batch-0']))
    asyncio.run(batches.run(started['id']))
    job_id = batches.get(started['id'])['items'][0]['evaluationId']
    assert service.get(job_id)['error']['code'] == 'INPUT_CHANGED'
    with repo.connect() as db:
        assert [row[0] for row in db.execute('SELECT id FROM evaluations')] == [prior['id']]


def test_restart_before_dispatch_requires_resume_without_unknown_charge(setup):
    repo, service, batches, calls = setup
    started = batches.create(body(repo))
    batches.recover(); service.recover()
    current = batches.get(started['id'])
    assert current['status'] == 'paused'
    assert all(i['status'] == 'queued' for i in current['items'])
    assert calls == []
    batches.resume(started['id'], current['revision'])
    asyncio.run(batches.run(started['id']))
    assert len(calls) == 3


def test_second_backend_startup_fails_closed_without_recovering_live_jobs(tmp_path):
    calls = []
    async def fake(request): calls.append(request); return output(request)
    database, cache = tmp_path / 'owner.sqlite', tmp_path / 'missing.json'
    app = create_app(database, cache, organizer_url='', evaluator=fake)
    app.state.repo.import_data(records())
    with TestClient(app):
        queued = app.state.batches.create(body(app.state.repo))
        second = create_app(database, cache, organizer_url='', evaluator=fake)
        with pytest.raises(RuntimeError, match='другим процессом'):
            with TestClient(second): pass
        assert app.state.batches.get(queued['id']) == queued
        assert all(app.state.evaluator.get(item['evaluationId'])['status'] == 'queued' for item in queued['items'])
        assert calls == []
