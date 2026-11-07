"""Local FastAPI application and API boundary for the CQA team."""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from .contracts.v2 import Health, ConversationExport, ApiError
from .contracts.v2 import (ImportRequest, VersionRequest, MigrationRequest, CreateAppealRequest, DecideAppealRequest, StartEvaluationRequest, RefreshRequest, WorkspaceState, ConversationDTO, ConversationPage, EvaluationResult, Score, Checklist, from_wire_checklist, internal_number, to_wire_checklist, to_wire_conversation, to_wire_evaluation, to_wire_score, to_wire_state, to_wire_appeal)
from .evaluator import EvaluationService, load_adapter, error_payload
from .repository import Repository, Conflict
from .media import audio_response, AudioProxyError
from .domain import MAX_IMPORT_BYTES
from .knowledge import load_knowledge_facts
from .submission import build_submission

ROOT=Path(__file__).resolve().parent.parent
load_dotenv(ROOT/'.env.local')
load_dotenv(ROOT/'.env')
MAX_BODY=5*1024*1024
# A UTF-8 source byte can occupy up to six bytes as a JSON \uXXXX escape.
# Keep transport bounded while leaving room for the versioned import envelope.
MAX_IMPORT_BODY=6*MAX_IMPORT_BYTES+1024

def create_app(database_path=None,cache_path=None,organizer_url=None,*,evaluator=None,evaluation_timeout=60):
    facts_path = os.getenv('KNOWLEDGE_FACTS_PATH', '').strip()
    facts = load_knowledge_facts(ROOT / facts_path if facts_path else None)
    db_path=database_path or os.getenv('DATABASE_PATH',str(ROOT/'local-data/cqa.sqlite'))
    repo=Repository(db_path)
    source_url=organizer_url if organizer_url is not None else os.getenv('ORGANIZER_DEV_URL','')
    snapshot_path=Path(cache_path) if cache_path is not None else ROOT/'local-data/organizer-DEV.json'
    if not repo.has_dataset() and snapshot_path.is_file():
        try:
            snapshot=json.loads(snapshot_path.read_text(encoding='utf-8-sig'))
            repo.replace_dev(snapshot['payload'],snapshot['fetchedAt'],'cache')
        except (ValueError,KeyError,TypeError):
            pass # Corrupt old cache is refetched; never fabricate records.
    service=EvaluationService(repo,evaluator or load_adapter(os.getenv('EVALUATOR_ADAPTER','')),timeout=evaluation_timeout,knowledge_facts=facts)
    @asynccontextmanager
    async def lifespan(app):
        service.recover()
        yield
    app=FastAPI(title='CQA Evidence API',version='2.0',lifespan=lifespan,docs_url='/api/docs',openapi_url='/api/openapi.json',redoc_url=None,responses={code:{'model':ApiError} for code in (400,403,404,405,409,413,422,500,502,503)})
    app.state.repo=repo
    app.state.evaluator=service
    def workspace():
        return to_wire_state({**repo.state(wire_decimals=True),'evaluationAvailable':service.adapter is not None})
    def failure(code,message,status,details=(),headers=None):
        return JSONResponse(error_payload(code,message,details,retryable=status in (429,502,503,504)),status_code=status,headers=headers)
    refresh_lock=asyncio.Lock()
    allowed_origins=set(os.getenv('APP_ORIGINS','http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:4173,http://localhost:4173,http://127.0.0.1:8000,http://localhost:8000').split(','))

    @app.middleware('http')
    async def boundary(request,call_next):
        if request.url.path.startswith('/api/'):
            origin=request.headers.get('origin')
            if origin and origin not in allowed_origins: return failure('ORIGIN_FORBIDDEN','Источник запроса не разрешён.',403)
            host=request.url.hostname
            if host not in ('127.0.0.1','localhost','testserver','::1'): return failure('HOST_FORBIDDEN','Сервер доступен только локально.',403)
            if request.method in ('POST','PUT','PATCH'):
                is_import=request.method=='POST' and request.url.path=='/api/import'
                body_limit=MAX_IMPORT_BODY if is_import else MAX_BODY
                size_message='Слишком большой запрос импорта.' if is_import else 'Максимальный размер запроса — 5 МБ.'
                try:
                    if int(request.headers.get('content-length','0'))>body_limit: return failure('REQUEST_TOO_LARGE',size_message,413)
                except ValueError: return failure('INVALID_REQUEST','Некорректный размер запроса.',400)
                chunks=[];length=0
                async for chunk in request.stream():
                    length+=len(chunk)
                    if length>body_limit: return failure('REQUEST_TOO_LARGE',size_message,413)
                    chunks.append(chunk)
                request._body=b''.join(chunks)
        response=await call_next(request)
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control']='no-store'
            response.headers['X-Content-Type-Options']='nosniff'
        return response

    @app.exception_handler(Conflict)
    async def conflict_handler(request,exc): return failure('CONFLICT',str(exc),409)
    @app.exception_handler(ValueError)
    async def value_handler(request,exc): return failure('INVALID_VALUE',str(exc),422)
    @app.exception_handler(KeyError)
    async def key_handler(request,exc): return failure('NOT_FOUND',str(exc.args[0]),404)
    @app.exception_handler(RequestValidationError)
    async def request_error(request,exc): return failure('INVALID_REQUEST','Проверьте поля запроса.',422,[{'code':e['type'],'path':'/'+('/'.join(map(str,e['loc']))),'message':'Поле не соответствует контракту.'} for e in exc.errors()])
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request,exc): return failure({404:'NOT_FOUND',503:'SERVICE_UNAVAILABLE',502:'UPSTREAM_ERROR'}.get(exc.status_code,'HTTP_ERROR'),str(exc.detail),exc.status_code,headers=exc.headers)
    @app.exception_handler(Exception)
    async def unexpected_error(request,exc): return failure('INTERNAL_ERROR','Не удалось обработать запрос.',500)

    async def refresh(group_id: str = 'DEV'):
        if not source_url or urlparse(source_url).scheme!='https': raise HTTPException(503,'Укажите ORGANIZER_DEV_URL в .env.local и перезапустите бэкенд.')
        try:
            endpoint=httpx.URL(source_url).copy_merge_params({'group_id':group_id})
            async with httpx.AsyncClient(timeout=30,follow_redirects=False) as client:
                async with client.stream('GET',endpoint,headers={'Accept':'application/json'}) as response:
                    response.raise_for_status();chunks=[];length=0
                    async for chunk in response.aiter_bytes():
                        length+=len(chunk)
                        if length>MAX_BODY: raise ValueError('oversized')
                        chunks.append(chunk)
            payload=json.loads(b''.join(chunks))
            repo.save_dataset(payload,group_id)
        except Conflict:
            raise
        except (httpx.HTTPError,ValueError,TypeError,KeyError):
            raise HTTPException(502,'Не удалось получить проверенный набор указанной группы. Сохранённые данные не изменены.') from None

    async def ensure_dataset():
        # Manual imports must remain usable without the organizer service, including after restart.
        if source_url and not repo.has_dataset() and not repo.has_conversations():
            async with refresh_lock:
                if not repo.has_dataset() and not repo.has_conversations(): await refresh()

    @app.get('/api/health',response_model=Health)
    def health(): return {'schema_version':'2.0','status':'ok','database':'sqlite','evaluationAvailable':service.adapter is not None}

    @app.get('/api/state',response_model=WorkspaceState,response_model_exclude_unset=True)
    async def state():
        await ensure_dataset();return workspace()

    @app.post('/api/organizer/refresh',response_model=WorkspaceState,response_model_exclude_unset=True)
    async def organizer_refresh(body:RefreshRequest):
        async with refresh_lock: await refresh(body.groupId)
        return workspace()

    @app.post('/api/import',response_model=WorkspaceState,response_model_exclude_unset=True)
    def import_data(body:ImportRequest):
        if len(body.text.encode('utf-8'))>MAX_IMPORT_BYTES:
            return failure('REQUEST_TOO_LARGE','Размер JSON не должен превышать 5 МБ.',413)
        imported=repo.import_data(body.text)
        return {**workspace(),'imported':imported}

    @app.post('/api/migrate',response_model=WorkspaceState,response_model_exclude_unset=True)
    def migrate(body:MigrationRequest):
        repo.migrate(body.data);return workspace()

    @app.get('/api/checklists',response_model=list[Checklist],response_model_exclude_unset=True)
    def checklists():
        with repo.connect() as db: return [to_wire_checklist(json.loads(r[0])) for r in db.execute('SELECT body FROM checklists ORDER BY version DESC')]

    @app.post('/api/checklists/{checklist_id}/versions',response_model=WorkspaceState,response_model_exclude_unset=True)
    def save_checklist(checklist_id:str,body:VersionRequest):
        if body.checklist.id!=checklist_id: raise ValueError('Идентификатор чек-листа не совпадает.')
        repo.save_checklist(from_wire_checklist(body.checklist),body.expectedVersion);return workspace()

    @app.get('/api/conversations',response_model=ConversationPage,response_model_exclude_unset=True)
    def conversations(q:str='',language:str='',channel:str='',offset:int=0,limit:int=100):
        if offset<0 or not 1<=limit<=1000: raise ValueError('Некорректная пагинация.')
        rows=workspace()['data']['conversations']
        rows=[r for r in rows if (not language or r['language']==language) and (not channel or r['channel']==channel) and (not q or q.lower() in (r['id']+' '+r['title']+' '+r['operator']).lower())]
        return {'schema_version':'2.0','total':len(rows),'items':rows[offset:offset+limit]}

    def find_conversation(conversation_id):
        item=next((r for r in repo.state(wire_decimals=True)['data']['conversations'] if r['id']==conversation_id),None)
        if item is None: raise KeyError('Диалог не найден.')
        return item

    @app.get('/api/conversations/{conversation_id}',response_model=ConversationDTO,response_model_exclude_unset=True)
    def conversation(conversation_id:str): return to_wire_conversation(find_conversation(conversation_id))

    @app.post('/api/evaluations',status_code=202,response_model=EvaluationResult,response_model_exclude_unset=True)
    def evaluate(body:StartEvaluationRequest,background:BackgroundTasks):
        find_conversation(body.conversationId)
        if service.adapter is None: raise HTTPException(503,'Сервис ИИ-оценки ещё не подключён. Команде нужны ключ API и выбранная модель.')
        result=service.create(body.conversationId,body.checklistVersion)
        background.add_task(service.run,result['id'])
        return to_wire_evaluation(result)

    @app.get('/api/evaluations/{evaluation_id}',response_model=EvaluationResult,response_model_exclude_unset=True)
    def evaluation(evaluation_id:str):
        with repo.connect() as db:
            job=db.execute('SELECT request FROM evaluation_jobs WHERE id=?',(evaluation_id,)).fetchone()
            if job:
                snapshot=json.loads(job[0]);result=service.get(evaluation_id)
                return to_wire_evaluation(result,messages=snapshot['messages'])
            row,result=repo.evaluation(db,evaluation_id)
            current=db.execute('SELECT body,content_hash FROM conversations WHERE id=?',(row['conversation_id'],)).fetchone()
            messages=result.get('messagesSnapshot',json.loads(current['body'])['transcript'] if current and current['content_hash']==row['content_hash'] else [])
            return to_wire_evaluation(result,row['conversation_id'],row['content_hash'],messages)

    @app.post('/api/appeals',response_model=WorkspaceState,response_model_exclude_unset=True)
    def create_appeal(body:CreateAppealRequest):
        repo.create_appeal(body.model_dump(exclude={'schema_version'}));return workspace()

    def decision_body(body):
        value=body.model_dump(exclude_unset=True,exclude={'schema_version'})
        if value.get('newValue') is not None: value['newValue']=internal_number(value['newValue'])
        return value

    @app.post('/api/appeals/{appeal_id}/decision',response_model=WorkspaceState,response_model_exclude_unset=True)
    def decide_appeal(appeal_id:str,body:DecideAppealRequest):
        repo.decide_appeal(appeal_id,decision_body(body));return workspace()

    @app.post('/api/appeals/{appeal_id}/preview',response_model=Score,response_model_exclude_unset=True)
    def preview_appeal(appeal_id:str,body:DecideAppealRequest):
        with repo.connect() as db:
            found=db.execute('SELECT body FROM appeals WHERE id=?',(appeal_id,)).fetchone()
            if not found: raise KeyError('Апелляция не найдена.')
            row,result=repo.evaluation(db,json.loads(found[0])['evaluationId'])
            current=db.execute('SELECT body FROM conversations WHERE id=?',(row['conversation_id'],)).fetchone()
            messages=result.get('messagesSnapshot',json.loads(current[0])['transcript'])
        return to_wire_score(repo.decide_appeal(appeal_id,decision_body(body),preview=True,wire_decimals=True),messages)

    def download(value,name):
        return JSONResponse(value,headers={'Content-Disposition':f'attachment; filename="{name}.json"'})

    @app.get('/api/export',response_model=WorkspaceState)
    def export(): return download(workspace(),'cqa-workspace')

    @app.get('/api/submission')
    def export_submission(groupId: str = 'DEV'):
        return download(build_submission(workspace(), groupId), 'results')

    @app.get('/api/conversations/{conversation_id}/export',response_model=ConversationExport)
    def export_conversation(conversation_id:str):
        state=workspace();item=conversation(conversation_id)
        return download({'schema_version':'2.0','conversation':item,'appeals':[a for a in state['data']['appeals'] if a['conversationId']==conversation_id]},'cqa-dialog')

    @app.get('/api/evaluations/{evaluation_id}/export',response_model=EvaluationResult)
    def export_evaluation(evaluation_id:str): return download(evaluation(evaluation_id),'cqa-evaluation')

    @app.get('/api/conversations/{conversation_id}/evaluations',response_model=list[EvaluationResult])
    def evaluation_history(conversation_id:str):
        # Include failed jobs so a new unsuccessful attempt never hides earlier success.
        with repo.connect() as db:
            if not db.execute('SELECT 1 FROM conversations WHERE id=?', (conversation_id,)).fetchone():
                raise KeyError('Диалог не найден.')
            identifiers = [row[0] for row in db.execute('''
                SELECT id, MAX(created_at) AS created_at FROM (
                    SELECT id, created_at FROM evaluations WHERE conversation_id=?
                    UNION ALL SELECT id, created_at FROM evaluation_jobs WHERE conversation_id=?
                ) GROUP BY id ORDER BY created_at DESC, id DESC''', (conversation_id, conversation_id))]
        return [evaluation(identifier) for identifier in identifiers]

    @app.get('/api/conversations/{conversation_id}/audio')
    async def audio(conversation_id:str,request:Request):
        url,trusted=repo.audio_source(conversation_id)
        if not url: raise HTTPException(404,'Запись не предоставлена заказчиком.')
        try: return await audio_response(url,urlparse(source_url).hostname or '',request.headers.get('range'),trusted=trusted)
        except AudioProxyError as exc: raise HTTPException(exc.status_code,exc.detail) from None

    @app.get('/api/conversations/{conversation_id}/audio-page')
    def audio_page(conversation_id:str):
        item=find_conversation(conversation_id)
        if not item.get('audioUrl'): raise HTTPException(404,'Запись не предоставлена заказчиком.')
        from html import escape
        src=escape(item['audioUrl'],quote=True)
        return HTMLResponse(f'<!doctype html><html lang="ru"><meta charset="utf-8"><title>Запись разговора</title><body><h1>Запись разговора</h1><audio controls preload="metadata" src="{src}" style="width:100%"></audio></body></html>')

    @app.get('/{path:path}',include_in_schema=False)
    def frontend(path:str):
        if path.startswith('api/'): raise HTTPException(404,'API-маршрут не найден.')
        dist=(ROOT/'dist').resolve();target=(dist/path).resolve()
        if not target.is_relative_to(dist): raise HTTPException(404,'Файл не найден.')
        if target.is_file(): return FileResponse(target)
        if (dist/'index.html').is_file(): return FileResponse(dist/'index.html')
        raise HTTPException(404,'Соберите фронтенд командой npm run build или запустите npm run dev.')
    return app
