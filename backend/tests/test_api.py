import copy
import json
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.domain import initial_checklist
from backend.repository import Repository, Conflict

VERSION = '2.0'


def wire(**fields):
    return {'schema_version': VERSION, **fields}


def assert_api_error(response, status):
    assert response.status_code == status
    body = response.json()
    assert body['schema_version'] == VERSION
    assert isinstance(body['code'], str) and body['code']
    assert isinstance(body['message'], str) and body['message']
    assert isinstance(body['details'], list)
    assert isinstance(body['request_id'], str) and body['request_id']
    assert isinstance(body['retryable'], bool)
    assert 'error' not in body and 'detail' not in body
    return body


def record(identifier='test-only'):
    return {'conversation_id':identifier,'channel':'agent chat','language':'RU','audio_url':None,'transcript':[{'speaker':'client','text':'Синтетический запрос для теста','timestamp':'00:00'},{'speaker':'operator','text':'Здравствуйте, я тестовый оператор. До свидания.','timestamp':'00:05'}]}


def dev_payload():
    return {'isSuccess':True,'data':{'dataset_group_id':'DEV','total_records':65,'records':[record(f'test-only-{i}') for i in range(65)]}}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('EVALUATOR_ADAPTER', '')
    cache=tmp_path/'snapshot.json';cache.write_text(json.dumps({'payload':dev_payload(),'fetchedAt':'2026-11-07T10:00:00Z'}),encoding='utf-8')
    app=create_app(tmp_path/'test.sqlite',cache,organizer_url='')
    with TestClient(app) as client: yield client,app.state.repo,tmp_path,cache


def test_initial_state_and_restart_share_database(setup):
    client,repo,path,cache=setup
    state=client.get('/api/state').json()
    assert state['schema_version']==VERSION
    assert len(state['data']['conversations'])==65
    assert all(c['evaluation'] is None for c in state['data']['conversations'])
    rubric=state['data']['checklist'];rubric['name']='Серверная версия'
    assert rubric['schema_version']==VERSION
    assert rubric['criteria'][0]['type']=='yes_no'
    assert all(isinstance(c['weight'],str) and isinstance(c['max'],str) and all(isinstance(value,str) for value in c['allowedValues']) for c in rubric['criteria'])
    response=client.post('/api/checklists/cqa-official/versions',json=wire(checklist=rubric,expectedVersion=1))
    assert response.status_code==200 and response.json()['data']['checklist']['version']==2
    app2=create_app(path/'test.sqlite',cache,organizer_url='')
    with TestClient(app2) as other:
        assert other.get('/api/state').json()['data']['checklist']['name']=='Серверная версия'
        assert len(other.get('/api/checklists').json())==2


def test_versions_conflict_and_invalid_draft_are_atomic(setup):
    client,repo,*_=setup
    rubric=client.get('/api/state').json()['data']['checklist']
    assert client.post('/api/checklists/cqa-official/versions',json=wire(checklist=rubric,expectedVersion=1)).status_code==200
    assert_api_error(client.post('/api/checklists/cqa-official/versions',json=wire(checklist=rubric,expectedVersion=1)),409)
    rubric['criteria'][0]['weight']='-10'
    assert_api_error(client.post('/api/checklists/cqa-official/versions',json=wire(checklist=rubric,expectedVersion=2)),422)
    assert len(client.get('/api/checklists').json())==2


def test_import_validation_duplicates_and_shared_visibility(setup):
    client,repo,*_=setup
    rows=[record('manual-new'),record('bad')];rows[1]['language']='unknown'
    assert_api_error(client.post('/api/import',json=wire(text=json.dumps(rows))),422)
    assert len(repo.state()['data']['conversations'])==65
    response=client.post('/api/import',json=wire(text=json.dumps([record('manual-new')])) )
    assert response.status_code==200 and response.json()['imported']==1
    assert_api_error(client.post('/api/import',json=wire(text=json.dumps([record('manual-new')]))),422)
    result=client.get('/api/conversations?q=manual-new').json()
    assert result['total']==1 and result['items'][0]['date'] is None
    assert client.get('/api/export').headers['content-disposition'].startswith('attachment;')


def evaluated(repo):
    conversation=repo.state()['data']['conversations'][0]
    checklist=initial_checklist()
    values={'greeting':1,'discovery':None,'identification':None,'consultation':15,'empathy':1,'dialogue':7,'closing':1,'gross-errors':0}
    evaluation={'checklist':checklist,'results':[{'criterionId':c['id'],'value':values[c['id']],'explanation':'Синтетическое обоснование для теста.','evidenceIds':[conversation['transcript'][0]['id']],'critical':False} for c in checklist['criteria']]}
    stored=repo.record_evaluation(conversation['id'],evaluation,conversation['contentHash'])
    return conversation,stored


def test_evaluation_and_appeal_preview_decision_persistence(setup):
    client,repo,*_=setup
    conversation,evaluation=evaluated(repo)
    item=client.get('/api/conversations/'+conversation['id']).json()
    assert item['schema_version']==VERSION
    assert item['scores']['final']['total']=='85.38'
    assert item['scores']['final']['schema_version']==VERSION
    assert all(isinstance(item['scores']['final'][key],str) for key in ('earned','possible'))
    assert all(result['applicability_reason'] for result in item['evaluation']['results'] if result['value'] is None)
    assert all('evidenceIds' not in result and result['evidence'][0]['verification_status']=='verified' for result in item['evaluation']['results'])
    assert item['scores']['sections']
    body=wire(conversationId=conversation['id'],evaluationId=evaluation['id'],criterionId='gross-errors',comment='Проверяем грубую ошибку в тесте.')
    response=client.post('/api/appeals',json=body);assert response.status_code==200
    appeal=response.json()['data']['appeals'][0]
    assert appeal['originalValue']=='0' and appeal['status']=='pending'
    assert client.post('/api/appeals',json=body).status_code==409
    change=wire(status='accepted',newValue='1',decision='Обоснованное решение для теста.')
    preview=client.post('/api/appeals/'+appeal['id']+'/preview',json=change)
    assert preview.status_code==200 and preview.json()['critical'] is True and preview.json()['total']=='85.38'
    assert repo.state()['data']['appeals'][0]['status']=='pending'
    saved=client.post('/api/appeals/'+appeal['id']+'/decision',json=change)
    assert saved.status_code==200
    item=next(c for c in saved.json()['data']['conversations'] if c['id']==conversation['id'])
    assert item['scores']['original']['critical'] is False and item['scores']['final']['critical'] is True
    assert client.post('/api/appeals/'+appeal['id']+'/decision',json=change).status_code==409
    assert client.get('/api/evaluations/'+evaluation['id']).json()['results'][-1]['value']=='0'


def test_invalid_appeal_value_and_wrong_conversation_rejected(setup):
    client,repo,*_=setup;conversation,evaluation=evaluated(repo)
    body=wire(conversationId='wrong',evaluationId=evaluation['id'],criterionId='dialogue',comment='Проверяем значение критерия.')
    assert client.post('/api/appeals',json=body).status_code==422
    body['conversationId']=conversation['id']
    appeal=client.post('/api/appeals',json=body).json()['data']['appeals'][0]
    response=client.post('/api/appeals/'+appeal['id']+'/decision',json=wire(status='accepted',newValue='6',decision='Значение вне официальной шкалы.'))
    assert response.status_code==422
    assert repo.state()['data']['appeals'][0]['status']=='pending'


def test_refresh_failure_preserves_all_data_and_no_fake_evaluation(setup):
    client,repo,*_=setup
    before=repo.state()
    assert_api_error(client.post('/api/organizer/refresh',json=wire()),503)
    assert repo.state()==before
    assert_api_error(client.post('/api/evaluations',json=wire(conversationId='test-only-0')),503)
    assert all(c['evaluation'] is None for c in repo.state()['data']['conversations'])


def test_changed_transcript_is_rejected_and_preserves_current_score(setup):
    client,repo,*_=setup;conversation,evaluation=evaluated(repo)
    payload=dev_payload();payload['data']['records'][0]['transcript'][0]['text']='Другой синтетический запрос'
    before=repo.state()
    with pytest.raises(Conflict): repo.replace_dev(payload)
    assert repo.state()==before
    assert repo.state()['data']['conversations'][0]['evaluation']['id']==evaluation['id']
    assert client.get('/api/evaluations/'+evaluation['id']).status_code==200


def test_local_migration_is_idempotent_and_refuses_overwriting_server_edits(setup):
    client,repo,*_=setup
    state=repo.state()['data'];state['checklist']['version']=3;state['checklist']['name']='Из браузера'
    response=client.post('/api/migrate',json=wire(data=state))
    assert response.status_code==200 and response.json()['data']['checklist']['name']=='Из браузера'
    revision=response.json()['revision']
    assert client.post('/api/migrate',json=wire(data=state)).json()['revision']==revision
    changed=copy.deepcopy(state);changed['checklist']['name']='Другая локальная версия'
    assert client.post('/api/migrate',json=wire(data=changed)).status_code==409
    assert repo.state()['data']['checklist']['name']=='Из браузера'


def test_limits_origin_errors_and_unknown_routes(setup):
    client,repo,*_=setup
    assert_api_error(client.post('/api/import',json=wire(text='[]'),headers={'Origin':'https://unrelated.example'}),403)
    assert client.post('/api/import',json=wire(text='x'*(5*1024*1024+1))).status_code==413
    assert_api_error(client.post('/api/appeals',json=wire(secret='must-not-echo')),422)
    assert 'must-not-echo' not in client.post('/api/appeals',json=wire(secret='must-not-echo')).text
    assert_api_error(client.get('/api/unknown'),404)
    assert client.get('/api/conversations?limit=-1').status_code==422


def test_migration_preserves_conflicting_local_transcripts(setup):
    client,repo,*_=setup
    data=repo.state()['data'];data['conversations'][0]['transcript'][0]['text']='Локальный изменённый текст'
    revision=repo.state()['revision']
    assert client.post('/api/migrate',json=wire(data=data)).status_code==409
    assert repo.state()['revision']==revision
    assert repo.state()['data']['conversations'][0]['transcript'][0]['text']!='Локальный изменённый текст'


def test_malformed_sources_and_missing_worker_hash_fail_safely(setup):
    client,repo,*_=setup
    with pytest.raises(ValueError): repo.replace_dev({'isSuccess':True,'data':None})
    assert client.post('/api/migrate',json=wire(data={'conversations':[None]})).status_code==422
    with pytest.raises(ValueError): repo.record_evaluation('test-only-0',{},None)


@pytest.mark.parametrize('field,value', [
    ('weight', 10), ('weight', 0.125), ('weight', True),
    ('max', 1), ('allowedValues', [0, 1]), ('type', 'binary'),
    ('weight', '0.0000001'), ('weight', '1000000.000001'),
])
def test_checklist_rejects_private_numeric_and_old_binary_wire(setup, field, value):
    client,repo,*_=setup
    state=client.get('/api/state').json()
    rubric=copy.deepcopy(state['data']['checklist'])
    rubric['criteria'][0][field]=value
    assert_api_error(client.post('/api/checklists/cqa-official/versions',json=wire(checklist=rubric,expectedVersion=1)),422)
    assert repo.state()['revision']==state['revision']


@pytest.mark.parametrize('version', [None, '1.0', 2])
def test_all_mutations_require_exact_active_root_version(setup, version):
    client,repo,*_=setup
    state=client.get('/api/state').json()
    requests=[
        ('/api/import', {'text':json.dumps([record('version-rejection')])}),
        ('/api/organizer/refresh', {}),
        ('/api/evaluations', {'conversationId':'test-only-0'}),
        ('/api/migrate', {'data':repo.state()['data']}),
        ('/api/checklists/cqa-official/versions', {'checklist':state['data']['checklist'],'expectedVersion':1}),
        ('/api/appeals', {'conversationId':'test-only-0','evaluationId':'unused','criterionId':'greeting','comment':'Проверяем обязательную версию.'}),
        ('/api/appeals/unused/decision', {'status':'accepted','newValue':'1','decision':'Проверяем обязательную версию.'}),
        ('/api/appeals/unused/preview', {'status':'accepted','newValue':'1','decision':'Проверяем обязательную версию.'}),
    ]
    for path,body in requests:
        if version is not None: body['schema_version']=version
        assert_api_error(client.post(path,json=body),422)
    assert repo.state()['revision']==state['revision']


def test_checklist_requires_its_own_version_and_rejects_extra_fields(setup):
    client,repo,*_=setup
    state=client.get('/api/state').json()
    rubric=copy.deepcopy(state['data']['checklist']);rubric.pop('schema_version')
    assert_api_error(client.post('/api/checklists/cqa-official/versions',json=wire(checklist=rubric,expectedVersion=1)),422)
    assert_api_error(client.post('/api/import',json=wire(text=json.dumps([record('extra-field')]),unknown=True)),422)
    assert repo.state()['revision']==state['revision']


@pytest.mark.parametrize('value', [1, 1.0, True, '1e0'])
def test_appeal_decisions_require_plain_decimal_strings(setup, value):
    client,repo,*_=setup;conversation,evaluation=evaluated(repo)
    request=wire(conversationId=conversation['id'],evaluationId=evaluation['id'],criterionId='gross-errors',comment='Проверяем строгий формат значения.')
    appeal=client.post('/api/appeals',json=request).json()['data']['appeals'][0]
    revision=repo.state()['revision']
    for action in ('preview','decision'):
        body=wire(status='accepted',newValue=value,decision='Обоснованное решение для проверки.')
        assert_api_error(client.post(f"/api/appeals/{appeal['id']}/{action}",json=body),422)
    assert repo.state()['revision']==revision
    assert repo.state()['data']['appeals'][0]['status']=='pending'


@pytest.mark.parametrize('original_value,new_value', [(1,None),(None,1)])
def test_na_appeal_transitions_keep_worker_evidence_and_original_immutable(setup, original_value, new_value):
    client,repo,*_=setup
    conversation=repo.state()['data']['conversations'][0]
    checklist=initial_checklist()
    message=conversation['transcript'][0]
    values={'greeting':1,'discovery':original_value,'identification':None,'consultation':15,'empathy':1,'dialogue':7,'closing':1,'gross-errors':0}
    results=[]
    for criterion in checklist['criteria']:
        value=values[criterion['id']]
        results.append({
            'criterionId':criterion['id'],'value':value,
            'explanation':'Синтетическое обоснование проверенного ответа.',
            'applicability_reason':'Условия неприменимости подтверждены.' if value is None else None,
            'evidenceIds':[message['id']], 'critical':False,
            'evidence':[{
                'kind':'not_applicable' if value is None else 'message',
                'message_id':message['id'],'quote':message['text'],
                'start_message_id':None,'end_message_id':None,
                'source_text':message['text'],'verification_status':'verified','reason':None,
            }],
        })
    evaluation=repo.record_evaluation(conversation['id'],{'checklist':checklist,'results':results},conversation['contentHash'])
    original_response=client.get('/api/evaluations/'+evaluation['id'])
    assert original_response.status_code==200
    original=original_response.json()
    created=client.post('/api/appeals',json=wire(conversationId=conversation['id'],evaluationId=evaluation['id'],criterionId='discovery',comment='Пересматриваем применимость критерия в тесте.'))
    assert created.status_code==200
    appeal=created.json()['data']['appeals'][0]
    decision='Проверяющий уточнил применимость по исходному диалогу.'
    expected_value=None if new_value is None else str(new_value)
    body=wire(status='accepted',newValue=expected_value,decision=decision)
    revision=repo.state()['revision']
    preview=client.post('/api/appeals/'+appeal['id']+'/preview',json=body)
    assert preview.status_code==200,preview.text
    assert repo.state()['revision']==revision
    expected=next(result for result in preview.json()['results'] if result['criterionId']=='discovery')
    assert expected['value']==expected_value
    assert expected['applicability_reason']==(decision if new_value is None else None)
    assert expected['evidence'][0]['kind']==('not_applicable' if new_value is None else 'message')
    assert expected['evidence'][0]['verification_status']=='verified'
    saved=client.post('/api/appeals/'+appeal['id']+'/decision',json=body)
    assert saved.status_code==200,saved.text
    reloaded=client.get('/api/conversations/'+conversation['id'])
    assert reloaded.status_code==200,reloaded.text
    scores=reloaded.json()['scores']
    assert scores['final']['total']==preview.json()['total']
    final_result=next(result for result in scores['final']['results'] if result['criterionId']=='discovery')
    assert final_result==expected
    original_result=next(result for result in scores['original']['results'] if result['criterionId']=='discovery')
    assert original_result['value']==(None if original_value is None else str(original_value))
    assert original_result['applicability_reason']==('Условия неприменимости подтверждены.' if original_value is None else None)
    assert original_result['evidence'][0]['kind']==('not_applicable' if original_value is None else 'message')
    assert client.get('/api/evaluations/'+evaluation['id']).json()==original
