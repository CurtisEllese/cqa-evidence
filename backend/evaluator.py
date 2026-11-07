"""Same-process evaluator integration; provider adapters perform one request per call."""
from __future__ import annotations
import asyncio
import copy
import importlib
import inspect
import json
from decimal import Decimal
from typing import Protocol
from uuid import uuid4
from pydantic import ValidationError
from .contracts.v2.models import EvaluationRequest, KnowledgeFact, ModelResponse
from .contracts.v2 import to_wire_checklist
from .repository import Conflict, dump, now


class Evaluator(Protocol):
    async def __call__(self, request: EvaluationRequest) -> ModelResponse: ...


class AdapterFailure(Exception):
    """Adapters map provider failures here; never include provider text or secrets."""
    def __init__(self, code='PROVIDER_UNAVAILABLE', *, retryable=False, retry_after=0):
        self.code=code if code in ('PROVIDER_UNAVAILABLE','PROVIDER_RATE_LIMIT','MODEL_REFUSAL') else 'PROVIDER_UNAVAILABLE'
        self.retryable=bool(retryable)
        self.retry_after=max(0,float(retry_after))
        super().__init__(self.code)


def load_adapter(spec):
    if not spec: return None
    module,separator,name=spec.partition(':')
    if not separator or not module or not name: raise ValueError('EVALUATOR_ADAPTER должен иметь формат module:function.')
    adapter=getattr(importlib.import_module(module),name)
    if not callable(adapter) or not inspect.iscoroutinefunction(adapter): raise ValueError('Адаптер должен быть async-функцией.')
    return adapter


def error_payload(code,message,issues=(),retryable=False):
    return {'schema_version':'2.0','code':code,'message':message,'details':list(issues),'request_id':str(uuid4()),'retryable':retryable}


def issue(code,path,message): return {'code':code,'path':path,'message':message}


def inspect_output(raw,request):
    """Separate structural/value failures from unverifiable evidence. Never repair values silently."""
    try:
        if isinstance(raw,str): output=ModelResponse.model_validate_json(raw)
        elif isinstance(raw,ModelResponse): output=ModelResponse.model_validate(raw.model_dump())
        else: output=ModelResponse.model_validate(raw)
    except ValidationError as exc:
        issues=[issue('MODEL_OUTPUT_INVALID','/'+('/'.join(map(str,e['loc']))),'Ответ не соответствует схеме: '+e['type']) for e in exc.errors()]
        return [],issues,[]
    criteria={c.id:c for c in request.checklist.criteria}
    received={r.criterionId for r in output.results}
    errors=[];reviews=[];results=[]
    if received!=set(criteria): errors.append(issue('CRITERION_COVERAGE','/results','Требуется ровно один ответ для каждого критерия сохранённого чек-листа.'))
    messages={m.id:m for m in request.messages};positions={m.id:i for i,m in enumerate(request.messages)}
    for index,result in enumerate(output.results):
        path=f'/results/{index}';criterion=criteria.get(result.criterionId)
        if not criterion: continue
        value=Decimal(result.value) if result.value is not None else None
        if value is None:
            if not criterion.allowNA: errors.append(issue('NA_FORBIDDEN',path+'/value','N/A запрещено для этого критерия.'))
        elif value not in {Decimal(v) for v in criterion.allowedValues}:
            errors.append(issue('VALUE_NOT_ALLOWED',path+'/value','Значение отсутствует в шкале критерия.'))
        if value is not None and result.applicability_reason is not None:
            errors.append(issue('NA_INCONSISTENT',path+'/applicability_reason','Причина N/A допустима только для null.'))
        verified=[];evidence_ids=[]
        candidates=result.evidence
        if not candidates:
            reviews.append(issue('EVIDENCE_MISSING',path+'/evidence','Нет проверяемого доказательства.'))
        for evidence_index,ev in enumerate(candidates):
            evidence=ev.model_dump();reason=None;source=None;refs=[]
            if ev.kind=='not_applicable' and value is not None:
                errors.append(issue('NA_INCONSISTENT',path+'/evidence','Доказательство N/A несовместимо со значением.'))
            has_range=ev.start_message_id is not None or ev.end_message_id is not None
            if ev.kind=='absence' or has_range:
                if not ev.start_message_id or not ev.end_message_id:
                    reason='RANGE_INCOMPLETE'
                elif ev.start_message_id not in messages or ev.end_message_id not in messages:
                    reason='MESSAGE_NOT_FOUND'
                elif positions[ev.start_message_id]>positions[ev.end_message_id]:
                    reason='RANGE_REVERSED'
                else:
                    selected=request.messages[positions[ev.start_message_id]:positions[ev.end_message_id]+1]
                    refs=[m.id for m in selected];source='\n'.join(m.text for m in selected)
            elif ev.message_id is None:
                reason='EVIDENCE_MISSING'
            elif ev.message_id not in messages:
                reason='MESSAGE_NOT_FOUND'
            else:
                refs=[ev.message_id];source=messages[ev.message_id].text
            if ev.message_id is not None and ev.message_id not in messages:
                reason='MESSAGE_NOT_FOUND'
            if has_range and ev.message_id is not None and ev.message_id not in refs:
                reason='MESSAGE_OUTSIDE_RANGE'
            if ev.quote is not None and (ev.message_id not in messages or ev.quote not in messages[ev.message_id].text):
                reason='QUOTE_MISMATCH'
            evidence.update(source_text=source,verification_status='unverified' if reason else 'verified',reason=reason)
            verified.append(evidence)
            if reason: reviews.append(issue(reason,path+f'/evidence/{evidence_index}','Доказательство не подтверждено исходным диалогом.'))
            else: evidence_ids.extend(refs)
        results.append({'criterionId':result.criterionId,'value':None if value is None else float(value),'explanation':result.explanation,'applicability_reason':result.applicability_reason,'evidence':verified,'evidenceIds':list(dict.fromkeys(evidence_ids)),'critical':criterion.type=='flag' and value==1 and bool(verified) and all(e['verification_status']=='verified' for e in verified)})
    return results,errors,reviews


class EvaluationService:
    def __init__(self,repo,adapter=None,timeout=60,*,knowledge_facts: tuple[KnowledgeFact, ...] = ()):
        self.repo=repo;self.adapter=adapter;self.timeout=timeout;self.limit=asyncio.Semaphore(1)
        self._knowledge_facts = tuple(fact.model_copy(deep=True) for fact in knowledge_facts)
        with repo.connect(write=True) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS evaluation_jobs(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id), content_hash TEXT NOT NULL, request TEXT NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evaluation_attempts(job_id TEXT NOT NULL REFERENCES evaluation_jobs(id), attempt INTEGER NOT NULL, output TEXT, issues TEXT NOT NULL, PRIMARY KEY(job_id,attempt));
            ''')

    def recover(self):
        with self.repo.connect(write=True) as db:
            for row in db.execute("SELECT id,result FROM evaluation_jobs WHERE status IN ('queued','running')").fetchall():
                saved=db.execute('SELECT body FROM evaluations WHERE id=?',(row['id'],)).fetchone()
                result=json.loads(saved[0] if saved else row['result'])
                if saved: result.update(status='completed',error=None)
                else: result.update(status='retryable',error=error_payload('WORKER_INTERRUPTED','Обработка прервана перезапуском сервера. Запустите оценку снова.',retryable=True),results=[])
                db.execute('UPDATE evaluation_jobs SET status=?,result=?,updated_at=? WHERE id=?',(result['status'],dump(result),now(),row['id']))
                self.repo.bump(db)

    def create(self,conversation_id,checklist_version: int | None = None):
        with self.repo.connect(write=True) as db:
            row=db.execute('SELECT * FROM conversations WHERE id=? AND active=1',(conversation_id,)).fetchone()
            if not row: raise KeyError('Диалог не найден.')
            running=db.execute("SELECT 1 FROM evaluation_jobs WHERE conversation_id=? AND status IN ('queued','running')",(conversation_id,)).fetchone()
            if running: raise Conflict('Оценка этого диалога уже выполняется.')
            appealed=db.execute('SELECT 1 FROM appeals a JOIN evaluations e ON a.evaluation_id=e.id WHERE e.conversation_id=? LIMIT 1',(conversation_id,)).fetchone()
            if appealed: raise Conflict('У диалога есть апелляция. Повторная оценка недоступна; история сохранена.')
            conversation=json.loads(row['body']);checklist=self.repo.checklist(db,checklist_version)
            request=EvaluationRequest.model_validate({'schema_version':'2.0','conversationId':conversation_id,'conversationHash':row['content_hash'],'language':conversation['language'],'channel':conversation['channel'],'checklist':to_wire_checklist(checklist),'messages':[{**m,'message_index':i} for i,m in enumerate(conversation['transcript'])],'knowledgeFacts':[fact.model_dump() for fact in self._knowledge_facts],'repair_feedback':None})
            identifier=str(uuid4());timestamp=now()
            result={'id':identifier,'conversationId':conversation_id,'conversationHash':row['content_hash'],'language':conversation['language'],'channel':conversation['channel'],'checklist':checklist,'results':[],'createdAt':timestamp,'source':'api','status':'queued','error':None}
            db.execute('INSERT INTO evaluation_jobs VALUES(?,?,?,?,?,?,?,?)',(identifier,conversation_id,row['content_hash'],request.model_dump_json(), 'queued',dump(result),timestamp,timestamp));self.repo.bump(db)
            return result

    def get(self,identifier):
        with self.repo.connect() as db:
            row=db.execute('SELECT result FROM evaluation_jobs WHERE id=?',(identifier,)).fetchone()
            if not row: raise KeyError('Задача оценки не найдена.')
            return json.loads(row['result'])

    def update(self,identifier,status,*,results=None,error=None,snapshot=None):
        with self.repo.connect(write=True) as db:
            row=db.execute('SELECT result FROM evaluation_jobs WHERE id=?',(identifier,)).fetchone()
            body=copy.deepcopy(snapshot) if snapshot is not None else json.loads(row['result'])
            body.update(status=status,error=error)
            if results is not None: body['results']=results
            db.execute('UPDATE evaluation_jobs SET status=?,result=?,updated_at=? WHERE id=?',(status,dump(body),now(),identifier));self.repo.bump(db)
        return body

    def record_attempt(self,identifier,attempt,*,output=None,issues=()):
        with self.repo.connect(write=True) as db:
            db.execute('''INSERT INTO evaluation_attempts VALUES(?,?,?,?)
                ON CONFLICT(job_id,attempt) DO UPDATE SET output=excluded.output,issues=excluded.issues''',
                (identifier,attempt,output,dump(list(issues))))

    async def run(self,identifier):
        async with self.limit:
            self.update(identifier,'running')
            with self.repo.connect() as db:
                request=EvaluationRequest.model_validate_json(db.execute('SELECT request FROM evaluation_jobs WHERE id=?',(identifier,)).fetchone()[0])
            for attempt in (1,2):
                raw=None;errors=[];reviews=[]
                try:
                    # Count the attempt before I/O, including timeouts and interrupted requests.
                    self.record_attempt(identifier,attempt)
                    # A new deep snapshot per call prevents adapters mutating backend evidence/validation inputs.
                    try:
                        raw=await asyncio.wait_for(self.adapter(request.model_copy(deep=True)),timeout=self.timeout)
                    except (ValidationError,json.JSONDecodeError):
                        # Typed adapters may reject provider JSON before returning it.
                        raw=None
                    results,errors,reviews=inspect_output(raw,request)
                    serialized=raw.model_dump() if hasattr(raw,'model_dump') else raw
                    try: serialized=dump(serialized)
                    except (ValueError,TypeError): serialized='null'
                    self.record_attempt(identifier,attempt,output=serialized,issues=errors+reviews)
                    if errors:
                        if attempt==1:
                            request=EvaluationRequest.model_validate({**request.model_dump(),'repair_feedback':errors});continue
                        self.update(identifier,'failed_validation',results=[],error=error_payload('MODEL_OUTPUT_INVALID','Ответ модели не прошёл проверку после повторной попытки.',errors));return
                    if reviews:
                        # No fabricated final arithmetic; candidate answers remain reviewable.
                        self.update(identifier,'needs_review',results=results);return
                    result=self.get(identifier);result['results']=results;result['status']='completed'
                    saved=self.repo.record_evaluation(request.conversationId,result,request.conversationHash,evaluation_id=identifier)
                    self.update(identifier,'completed',snapshot=saved);return
                except Conflict:
                    self.update(identifier,'failed_validation',results=[],error=error_payload('INPUT_CHANGED','Диалог изменился во время оценки; результат не применён.'));return
                except (AdapterFailure,asyncio.TimeoutError) as exc:
                    retryable=not isinstance(exc,AdapterFailure) or exc.retryable
                    code=exc.code if isinstance(exc,AdapterFailure) else 'PROVIDER_TIMEOUT'
                    self.record_attempt(identifier,attempt,issues=[issue(code,'','Сервис модели не выполнил попытку оценки.')])
                    delay=exc.retry_after if isinstance(exc,AdapterFailure) else 0
                    # Long provider backoff is returned as a retryable failure, never shortened.
                    if attempt==1 and retryable and delay<=30:
                        await asyncio.sleep(delay);continue
                    self.update(identifier,'failed',results=[],error=error_payload(code,'Сервис модели не выполнил оценку.',retryable=retryable));return
                except asyncio.CancelledError:
                    raise # recovery marks the persisted job retryable on next startup
                except Exception:
                    self.record_attempt(identifier,attempt,issues=[issue('EVALUATOR_ERROR','','Не удалось обработать попытку оценки.')])
                    self.update(identifier,'failed',results=[],error=error_payload('EVALUATOR_ERROR','Не удалось обработать ответ оценщика. Подробности доступны команде сервера.'));return
