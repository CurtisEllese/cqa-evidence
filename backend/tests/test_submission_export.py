"""Official submission has original IDs/turns, arithmetic, typed values, and errors."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.submission import build_submission, validate_submission
from backend.tests.test_control_datasets import dataset
from backend.tests.test_evaluation_api import model_output


@pytest.fixture
def exported(tmp_path, monkeypatch):
    monkeypatch.setenv('EVALUATOR_ADAPTER', '')
    monkeypatch.delenv('KNOWLEDGE_FACTS_PATH', raising=False)
    async def adapter(request):
        output = model_output(request)
        # Include the original client turn and operator turn in absence evidence.
        output['results'][-1]['evidence'] = [dict(kind='absence', start_message_id=request.messages[0].id, end_message_id=request.messages[1].id)]
        return output
    adapter.describe = lambda: dict(provider='openai', model='gpt-6-luna', promptVersion='synthetic-test')
    app = create_app(tmp_path / 'export.sqlite', tmp_path / 'no-cache', organizer_url='', evaluator=adapter)
    app.state.repo.save_dataset(dataset('DEV', 2), 'DEV')
    app.state.repo.save_dataset(dataset('CONTROL', 1), 'CONTROL')
    with TestClient(app) as client:
        job = client.post('/api/evaluations', json=dict(schema_version='2.0', conversationId='DEV-0')).json()
        assert client.get('/api/evaluations/' + job['id']).json()['status'] == 'completed'
        yield client, job['id']


def test_official_export_is_scoped_typed_complete_and_preserves_failures(exported):
    client, _ = exported
    response = client.get('/api/submission', params={'groupId': 'DEV'})
    assert response.status_code == 200
    assert 'results.json' in response.headers['content-disposition']
    payload = response.json()
    assert payload['schema_version'] == '1.0' and payload['team_name'] == 'Spooky spooky'
    assert payload['model'] == dict(mode='external_api', name='gpt-6-luna')
    assert validate_submission(payload, ['DEV-0', 'DEV-1']) == dict(total=2, ok=1, errors=1)
    good, error = payload['results']
    assert good['score'] == dict(final_score=85.38, critical_flag=False)
    rows = {row['criterion_id']: row for row in good['criteria']}
    assert rows['contact']['value'] is True and rows['contact']['evidence_turns'] == [2]
    assert rows['discovery']['value'] is None
    assert rows['consultation']['value'] == 15 and type(rows['consultation']['value']) is int
    assert rows['critical']['value'] is False and rows['critical']['evidence_turns'] == [1, 2]
    assert error['conversation_id'] == 'DEV-1' and error['score'] is None and error['criteria'] == []
    assert client.get('/api/submission?groupId=UNKNOWN').status_code == 404
    assert len(client.get('/api/submission?groupId=CONTROL').json()['results']) == 1


def test_appeal_does_not_silently_replace_machine_submission(exported):
    client, evaluation_id = exported
    before = client.get('/api/submission').json()
    response = client.post('/api/appeals', json=dict(schema_version='2.0', conversationId='DEV-0', evaluationId=evaluation_id, criterionId='consultation', comment='Синтетическая проверка отдельного результата апелляции.'))
    appeal_id = response.json()['data']['appeals'][0]['id']
    assert client.post('/api/appeals/' + appeal_id + '/decision', json=dict(schema_version='2.0', status='accepted', newValue='20', decision='Синтетическая проверка сохранения исходной оценки.')).status_code == 200
    assert client.get('/api/submission').json() == before


def test_changed_rubric_exports_error_not_misleading_official_score(exported):
    client, _ = exported
    state = client.get('/api/state').json()
    state['data']['conversations'][0]['evaluation']['checklist']['criteria'][0]['weight'] = '100'
    payload = build_submission(state, 'DEV')
    assert payload['results'][0]['status'] == 'error'
    assert validate_submission(payload)['errors'] == 2


def test_validator_checks_evidence_against_source_transcript(exported):
    client, _ = exported
    payload = client.get('/api/submission').json()
    payload['results'][0]['criteria'][0]['evidence_turns'] = [999999]
    with pytest.raises(ValueError, match='границы'):
        validate_submission(payload, ['DEV-0', 'DEV-1'], {'DEV-0': 2, 'DEV-1': 2})


@pytest.mark.parametrize('mutation', ['binary', 'score', 'duplicate', 'critical', 'turns'])
def test_validator_rejects_corrupt_outputs(exported, mutation):
    client, _ = exported
    payload = deepcopy(client.get('/api/submission').json())
    row = payload['results'][0]
    if mutation == 'binary': row['criteria'][0]['value'] = 1
    if mutation == 'score': row['score']['final_score'] = 0
    if mutation == 'duplicate': payload['results'].append(deepcopy(row))
    if mutation == 'critical': row['score']['critical_flag'] = True
    if mutation == 'turns': row['criteria'][0]['evidence_turns'] = [0]
    with pytest.raises(ValueError): validate_submission(payload)
