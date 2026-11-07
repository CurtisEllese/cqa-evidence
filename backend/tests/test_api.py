import copy
import json
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.domain import initial_checklist
from backend.repository import Repository


def record(identifier='test-only'):
    return {'conversation_id':identifier,'channel':'agent chat','language':'RU','audio_url':None,'transcript':[{'speaker':'client','text':'Синтетический запрос для теста','timestamp':'00:00'},{'speaker':'operator','text':'Здравствуйте, я тестовый оператор. До свидания.','timestamp':'00:05'}]}


def dev_payload():
    return {'isSuccess':True,'data':{'dataset_group_id':'DEV','total_records':65,'records':[record(f'test-only-{i}') for i in range(65)]}}


@pytest.fixture
def setup(tmp_path):
    cache=tmp_path/'snapshot.json';cache.write_text(json.dumps({'payload':dev_payload(),'fetchedAt':'2026-11-07T10:00:00Z'}),encoding='utf-8')
    app=create_app(tmp_path/'test.sqlite',cache,organizer_url='')
    with TestClient(app) as client: yield client,app.state.repo,tmp_path,cache


def test_initial_state_and_restart_share_database(setup):
    client,repo,path,cache=setup
    state=client.get('/api/state').json()
    assert len(state['data']['conversations'])==65
    assert all(c['evaluation'] is None for c in state['data']['conversations'])
    rubric=state['data']['checklist'];rubric['name']='Серверная версия'
    response=client.post('/api/checklists/cqa-official/versions',json={'checklist':rubric,'expectedVersion':1})
    assert response.status_code==200 and response.json()['data']['checklist']['version']==2
    app2=create_app(path/'test.sqlite',cache,organizer_url='')
    with TestClient(app2) as other:
        assert other.get('/api/state').json()['data']['checklist']['name']=='Серверная версия'
        assert len(other.get('/api/checklists').json())==2


def test_versions_conflict_and_invalid_draft_are_atomic(setup):
    client,repo,*_=setup
    rubric=initial_checklist()
    assert client.post('/api/checklists/cqa-official/versions',json={'checklist':rubric,'expectedVersion':1}).status_code==200
    assert client.post('/api/checklists/cqa-official/versions',json={'checklist':rubric,'expectedVersion':1}).status_code==409
    rubric['criteria'][0]['weight']=-10
    assert client.post('/api/checklists/cqa-official/versions',json={'checklist':rubric,'expectedVersion':2}).status_code==422
    assert len(client.get('/api/checklists').json())==2


def test_import_validation_duplicates_and_shared_visibility(setup):
    client,repo,*_=setup
    rows=[record('manual-new'),record('bad')];rows[1]['language']='unknown'
    assert client.post('/api/import',json={'text':json.dumps(rows)}).status_code==422
    assert len(repo.state()['data']['conversations'])==65
    response=client.post('/api/import',json={'text':json.dumps([record('manual-new')])})
    assert response.status_code==200 and response.json()['imported']==1
    assert client.post('/api/import',json={'text':json.dumps([record('manual-new')])}).status_code==422
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
    assert item['scores']['final']['total']==85.38
    assert item['scores']['sections']
    body={'conversationId':conversation['id'],'evaluationId':evaluation['id'],'criterionId':'gross-errors','comment':'Проверяем грубую ошибку в тесте.'}
    response=client.post('/api/appeals',json=body);assert response.status_code==200
    appeal=response.json()['data']['appeals'][0]
    assert appeal['originalValue']==0 and appeal['status']=='pending'
    assert client.post('/api/appeals',json=body).status_code==409
    change={'status':'accepted','newValue':1,'decision':'Обоснованное решение для теста.'}
    preview=client.post('/api/appeals/'+appeal['id']+'/preview',json=change)
    assert preview.status_code==200 and preview.json()['critical'] is True and preview.json()['total']==85.38
    assert repo.state()['data']['appeals'][0]['status']=='pending'
    saved=client.post('/api/appeals/'+appeal['id']+'/decision',json=change)
    assert saved.status_code==200
    item=next(c for c in saved.json()['data']['conversations'] if c['id']==conversation['id'])
    assert item['scores']['original']['critical'] is False and item['scores']['final']['critical'] is True
    assert client.post('/api/appeals/'+appeal['id']+'/decision',json=change).status_code==409
    assert client.get('/api/evaluations/'+evaluation['id']).json()['results'][-1]['value']==0


def test_invalid_appeal_value_and_wrong_conversation_rejected(setup):
    client,repo,*_=setup;conversation,evaluation=evaluated(repo)
    body={'conversationId':'wrong','evaluationId':evaluation['id'],'criterionId':'dialogue','comment':'Проверяем значение критерия.'}
    assert client.post('/api/appeals',json=body).status_code==422
    body['conversationId']=conversation['id']
    appeal=client.post('/api/appeals',json=body).json()['data']['appeals'][0]
    response=client.post('/api/appeals/'+appeal['id']+'/decision',json={'status':'accepted','newValue':6,'decision':'Значение вне официальной шкалы.'})
    assert response.status_code==422
    assert repo.state()['data']['appeals'][0]['status']=='pending'


def test_refresh_failure_preserves_all_data_and_no_fake_evaluation(setup):
    client,repo,*_=setup
    before=repo.state()
    assert client.post('/api/organizer/refresh').status_code==503
    assert repo.state()==before
    assert client.post('/api/evaluations',json={'conversationId':'test-only-0'}).status_code==503
    assert all(c['evaluation'] is None for c in repo.state()['data']['conversations'])


def test_changed_transcript_invalidates_current_score_but_preserves_history(setup):
    client,repo,*_=setup;conversation,evaluation=evaluated(repo)
    payload=dev_payload();payload['data']['records'][0]['transcript'][0]['text']='Другой синтетический запрос'
    repo.replace_dev(payload)
    assert repo.state()['data']['conversations'][0]['evaluation'] is None
    assert client.get('/api/evaluations/'+evaluation['id']).status_code==200


def test_local_migration_is_idempotent_and_refuses_overwriting_server_edits(setup):
    client,repo,*_=setup
    state=repo.state()['data'];state['checklist']['version']=3;state['checklist']['name']='Из браузера'
    response=client.post('/api/migrate',json={'data':state})
    assert response.status_code==200 and response.json()['data']['checklist']['name']=='Из браузера'
    revision=response.json()['revision']
    assert client.post('/api/migrate',json={'data':state}).json()['revision']==revision
    changed=copy.deepcopy(state);changed['checklist']['name']='Другая локальная версия'
    assert client.post('/api/migrate',json={'data':changed}).status_code==409
    assert repo.state()['data']['checklist']['name']=='Из браузера'


def test_limits_origin_errors_and_unknown_routes(setup):
    client,repo,*_=setup
    assert client.post('/api/import',json={'text':'[]'},headers={'Origin':'https://unrelated.example'}).status_code==403
    assert client.post('/api/import',content=b'x'*(5*1024*1024+1),headers={'content-type':'application/json'}).status_code==413
    assert client.post('/api/appeals',json={'secret':'must-not-echo'}).status_code==422
    assert 'must-not-echo' not in client.post('/api/appeals',json={'secret':'must-not-echo'}).text
    assert client.get('/api/unknown').status_code==404
    assert client.get('/api/conversations?limit=-1').status_code==422


def test_migration_preserves_conflicting_local_transcripts(setup):
    client,repo,*_=setup
    data=repo.state()['data'];data['conversations'][0]['transcript'][0]['text']='Локальный изменённый текст'
    revision=repo.state()['revision']
    assert client.post('/api/migrate',json={'data':data}).status_code==409
    assert repo.state()['revision']==revision
    assert repo.state()['data']['conversations'][0]['transcript'][0]['text']!='Локальный изменённый текст'


def test_malformed_sources_and_missing_worker_hash_fail_safely(setup):
    client,repo,*_=setup
    with pytest.raises(ValueError): repo.replace_dev({'isSuccess':True,'data':None})
    assert client.post('/api/migrate',json={'data':{'conversations':[None]}}).status_code==422
    with pytest.raises(ValueError): repo.record_evaluation('test-only-0',{},None)
