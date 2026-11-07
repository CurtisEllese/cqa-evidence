import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from google.genai import errors, types
from backend.contracts.v2 import EvaluationRequest, ModelResponse
from backend.evaluator import AdapterFailure, EvaluationService, inspect_output, load_adapter
from backend.repository import Repository
from evaluator.settings import EvaluatorSettings
from evaluator.v2 import GeminiV2Evaluator, provider_schema


def request():
    return EvaluationRequest.model_validate(json.loads((Path(__file__).parents[1]/'evaluator/fixtures/synthetic_v2.json').read_text())[0]['request'])


def answer(req=None):
    req=req or request()
    return {'schema_version':'2.0','results':[{'criterionId':c.id,'value':'0','explanation':'Synthetic test answer','applicability_reason':None,'evidence':[{'kind':'absence','start_message_id':req.messages[0].id,'end_message_id':req.messages[-1].id}]} for c in req.checklist.criteria]}


def response(body=None, *, text=None, finish='STOP'):
    return types.GenerateContentResponse(candidates=[types.Candidate(finish_reason=finish,content=types.Content(parts=[types.Part(text=text if text is not None else json.dumps(body or answer()))]))],usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=40,candidates_token_count=20,thoughts_token_count=5))


class Fake:
    def __init__(self,*items): self.items=list(items);self.calls=[];self.models=self
    async def generate_content(self,**kwargs):
        self.calls.append(kwargs)
        item=self.items.pop(0)
        if isinstance(item,Exception): raise item
        return item


def judge(fake, **kwargs):
    return GeminiV2Evaluator(EvaluatorSettings('test-secret','configured-model',min_interval_seconds=0),client=fake,**kwargs)


def test_v2_uses_exact_active_contract_and_no_v1_fields():
    req=request();fake=Fake(response());events=[]
    result=asyncio.run(judge(fake,on_attempt=events.append).evaluate(req))
    assert isinstance(result,ModelResponse)
    payload=json.loads(fake.calls[0]['contents'])
    assert payload==req.model_dump(mode='json')
    assert fake.calls[0]['config'].response_json_schema==provider_schema(ModelResponse.model_json_schema())
    assert events[0].output_tokens==25
    assert 'test-secret' not in str(events)


@pytest.mark.parametrize('resp',[response(text='bad'),response(finish='MAX_TOKENS')])
def test_v2_never_repairs_inside_adapter(resp):
    fake=Fake(resp)
    with pytest.raises(json.JSONDecodeError): asyncio.run(judge(fake).evaluate(request()))
    assert len(fake.calls)==1


@pytest.mark.parametrize('code,retryable',[(401,False),(429,True),(503,True)])
def test_v2_maps_errors_for_backend_retry_without_own_retry(code,retryable):
    fake=Fake(errors.APIError(code,{'error':{'message':'secret-key customer data'}}))
    with pytest.raises(AdapterFailure) as caught: asyncio.run(judge(fake).evaluate(request()))
    assert caught.value.retryable==retryable
    assert 'secret-key' not in str(caught.value)
    assert len(fake.calls)==1


def test_v2_does_not_let_backend_shorten_long_retry_after():
    fake=Fake(errors.APIError(429,{'error':{'details':[{'@type':'type.googleapis.com/google.rpc.RetryInfo','retryDelay':'120s'}]}}))
    with pytest.raises(AdapterFailure) as caught: asyncio.run(judge(fake).evaluate(request()))
    assert caught.value.retryable is False


def test_v2_refusal_is_not_a_score():
    with pytest.raises(AdapterFailure) as caught: asyncio.run(judge(Fake(response(finish='SAFETY'))).evaluate(request()))
    assert caught.value.code=='MODEL_REFUSAL'


def test_backend_feedback_is_passed_without_modification():
    data=request().model_dump();data['repair_feedback']=[{'code':'VALUE_NOT_ALLOWED','path':'/results/0/value','message':'Use configured steps'}]
    req=EvaluationRequest.model_validate(data)
    fake=Fake(response())
    asyncio.run(judge(fake).evaluate(req))
    assert json.loads(fake.calls[0]['contents'])['repair_feedback'][0]['code']=='VALUE_NOT_ALLOWED'


def test_v2_evidence_validation_is_left_to_backend():
    body=answer();body['results'][0]['evidence'][0]['start_message_id']='unknown'
    result=asyncio.run(judge(Fake(response(body))).evaluate(request()))
    _, errors_,reviews=inspect_output(result,request())
    assert not errors_ and reviews


def test_worker_and_adapter_share_exactly_two_calls_and_save(tmp_path):
    req=request();repo=Repository(tmp_path/'adapter.sqlite')
    repo.import_data([{'conversation_id':req.conversationId,'language':req.language,'channel':req.channel,'audio_url':None,'transcript':[{'speaker':m.speaker,'timestamp':m.timestamp,'text':m.text} for m in req.messages]}])
    fake=Fake(response(text='malformed'),response(answer(req)))
    adapter=judge(fake)
    service=EvaluationService(repo,adapter.evaluate,timeout=5)
    job=service.create(req.conversationId)
    asyncio.run(service.run(job['id']))
    assert service.get(job['id'])['status']=='completed'
    assert len(fake.calls)==2
    assert json.loads(fake.calls[1]['contents'])['repair_feedback']
    with repo.connect() as db:
        assert db.execute('select count(*) from evaluations').fetchone()[0]==1


def test_entrypoint_loads_and_missing_key_fails_safely(monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY',raising=False)
    monkeypatch.setenv('LLM_PROVIDER','gemini')
    callback=load_adapter('evaluator.v2:evaluate')
    with pytest.raises(AdapterFailure): asyncio.run(callback(request()))


def test_usage_journal_contains_only_safe_metadata(tmp_path,monkeypatch):
    from evaluator.v2 import UsageEvent,write_usage_event
    path=tmp_path/'usage.jsonl';monkeypatch.setenv('GEMINI_USAGE_LOG',str(path))
    write_usage_event(UsageEvent('hash','prompt-v','model','parsed',1.2,40,20))
    row=json.loads(path.read_text())
    assert row['input_tokens']==40 and row['cost_usd'] is None
    assert not {'api_key','contents','response','messages'} & set(row)


def test_provider_schema_is_compact_but_local_decimal_validation_remains_strict():
    source=ModelResponse.model_json_schema();compact=provider_schema(source)
    assert 'maxItems' in source['properties']['results']
    assert 'maxItems' not in compact['properties']['results']
    assert set(compact['$defs']['Evidence']['required'])==set(compact['$defs']['Evidence']['properties'])
    body=answer();body['results'][0]['value']='1e3'
    fake=Fake(response(body))
    with pytest.raises(json.JSONDecodeError): asyncio.run(judge(fake).evaluate(request()))
    assert len(fake.calls)==1
