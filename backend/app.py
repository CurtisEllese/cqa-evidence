"""Local FastAPI application and API boundary for the CQA team."""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path
from urllib.parse import urlparse
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictFloat
from .repository import Repository, Conflict
from .media import audio_response, AudioProxyError
from contracts.v1 import Checklist, Evaluator, ValidationIssue
from backend.application.evaluations import EvaluationService, failure
from backend.infrastructure.evaluations import SQLiteEvaluations
from backend.api.evaluations import evaluation_router

ROOT=Path(__file__).resolve().parent.parent
load_dotenv(ROOT/'.env.local')
load_dotenv(ROOT/'.env')
MAX_BODY=5*1024*1024

class ImportBody(BaseModel):
    text: str
class VersionBody(BaseModel):
    checklist: dict
    expectedVersion: StrictInt
class MigrationBody(BaseModel):
    data: dict
class AppealBody(BaseModel):
    conversationId: str
    evaluationId: str
    criterionId: str
    comment: str = Field(min_length=10,max_length=2000)
class DecisionBody(BaseModel):
    status: str
    decision: str = Field(default='',max_length=2000)
    model_config = ConfigDict(allow_inf_nan=False)
    newValue: StrictFloat | StrictInt | None = None


def create_app(database_path=None,cache_path=None,organizer_url=None, *, evaluator: Evaluator | None = None,
               clock=None, identifiers=None, knowledge_context: str = "", prompt_version: str = "v1",
               knowledge_version: str = "none", evaluation_timeout: float = 120):
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
    clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
    identifiers = identifiers or (lambda: str(uuid4()))
    evaluation_store = SQLiteEvaluations(repo)
    service = EvaluationService(evaluation_store, evaluator, clock, identifiers,
                                knowledge_context=knowledge_context, prompt_version=prompt_version,
                                knowledge_version=knowledge_version, timeout_seconds=evaluation_timeout)

    @asynccontextmanager
    async def lifespan(app):
        service.recover()
        yield
        await service.close()

    def error_response(request, status, code, message, details=None):
        body = failure(getattr(request.state, 'request_id', None) or identifiers(), code, message, details=details)
        return JSONResponse(body.model_dump(), status_code=status)

    def workspace_state():
        state = repo.state()
        state['evaluationAvailable'] = service.available
        for item in state['data']['conversations']:
            evaluation = evaluation_store.latest_for(item)
            if evaluation is not None:
                item['wire_evaluation'] = evaluation.model_dump()
        return state

    app=FastAPI(lifespan=lifespan, title='CQA Evidence API',version='0.2.0',docs_url='/api/docs',openapi_url='/api/openapi.json',redoc_url=None)
    app.state.repo=repo
    app.state.evaluation_store=evaluation_store
    app.state.evaluation_service=service
    app.include_router(evaluation_router(service, evaluation_store))
    refresh_lock=asyncio.Lock()
    allowed_origins=set(os.getenv('APP_ORIGINS','http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:4173,http://localhost:4173,http://127.0.0.1:8000,http://localhost:8000').split(','))

    @app.middleware('http')
    async def boundary(request,call_next):
        request.state.request_id = identifiers()
        if request.url.path.startswith('/api/'):
            origin=request.headers.get('origin')
            if origin and origin not in allowed_origins: return error_response(request,403,'INVALID_REQUEST','Источник запроса не разрешён.')
            host=request.url.hostname
            if host not in ('127.0.0.1','localhost','testserver','::1'): return error_response(request,403,'INVALID_REQUEST','Сервер доступен только локально.')
            if request.method in ('POST','PUT','PATCH'):
                try:
                    if int(request.headers.get('content-length','0'))>MAX_BODY: return error_response(request,413,'INVALID_REQUEST','Максимальный размер запроса — 5 МБ.')
                except ValueError: return error_response(request,400,'INVALID_REQUEST','Некорректный размер запроса.')
                chunks=[];length=0
                async for chunk in request.stream():
                    length+=len(chunk)
                    if length>MAX_BODY: return error_response(request,413,'INVALID_REQUEST','Максимальный размер запроса — 5 МБ.')
                    chunks.append(chunk)
                request._body=b''.join(chunks)
        response=await call_next(request)
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control']='no-store'
            response.headers['X-Content-Type-Options']='nosniff'
        return response

    @app.exception_handler(Conflict)
    async def conflict_handler(request,exc):
        return error_response(request,409,'REVISION_CONFLICT',str(exc))
    @app.exception_handler(ValueError)
    async def value_handler(request,exc):
        return error_response(request,422,'INVALID_REQUEST','Проверьте значения и совместимость полей запроса.')
    @app.exception_handler(KeyError)
    async def key_handler(request,exc):
        return error_response(request,404,'NOT_FOUND',str(exc.args[0]))
    @app.exception_handler(RequestValidationError)
    async def request_error(request,exc):
        details = [ValidationIssue(code='INVALID_TYPE',criterion_id=None,
                   path='/'+'/'.join(map(str,e['loc'])),message='Поле не соответствует схеме.') for e in exc.errors()]
        return error_response(request,422,'INVALID_REQUEST','Проверьте поля запроса.',details)
    @app.exception_handler(HTTPException)
    async def http_error(request,exc):
        code = 'NOT_FOUND' if exc.status_code == 404 else 'PROVIDER_UNAVAILABLE' if exc.status_code >= 500 else 'INVALID_REQUEST'
        return error_response(request,exc.status_code,code,str(exc.detail))
    @app.exception_handler(Exception)
    async def unexpected_error(request,exc):
        return error_response(request,500,'INTERNAL_ERROR','Не удалось обработать запрос.')

    async def refresh():
        if not source_url or urlparse(source_url).scheme!='https': raise HTTPException(503,'Укажите ORGANIZER_DEV_URL в .env.local и перезапустите бэкенд.')
        try:
            endpoint=httpx.URL(source_url).copy_merge_params({'group_id':'DEV'})
            async with httpx.AsyncClient(timeout=30,follow_redirects=False) as client:
                async with client.stream('GET',endpoint,headers={'Accept':'application/json'}) as response:
                    response.raise_for_status();chunks=[];length=0
                    async for chunk in response.aiter_bytes():
                        length+=len(chunk)
                        if length>MAX_BODY: raise ValueError('oversized')
                        chunks.append(chunk)
            payload=json.loads(b''.join(chunks))
            repo.replace_dev(payload)
        except (httpx.HTTPError,ValueError,TypeError,KeyError):
            raise HTTPException(502,'Не удалось получить проверенный набор DEV. Сохранённые данные не изменены.') from None

    async def ensure_dataset():
        if not repo.has_dataset():
            async with refresh_lock:
                if not repo.has_dataset(): await refresh()

    @app.get('/api/health')
    def health(): return {'status':'ok','database':'sqlite','evaluationAvailable':service.available}

    @app.get('/api/state')
    async def state():
        if source_url:
            await ensure_dataset()
        return workspace_state()

    @app.post('/api/organizer/refresh')
    async def organizer_refresh():
        async with refresh_lock: await refresh()
        return workspace_state()

    @app.post('/api/import')
    def import_data(body:ImportBody):
        imported=repo.import_data(body.text)
        return {**workspace_state(),'imported':imported}

    @app.post('/api/migrate')
    def migrate(body:MigrationBody):
        repo.migrate(body.data);return workspace_state()

    @app.get('/api/checklists')
    def checklists():
        return evaluation_store.list_checklists()

    @app.post('/api/checklists/{checklist_id}/versions')
    def save_checklist(checklist_id:str,body:VersionBody | Checklist):
        if isinstance(body, Checklist):
            if body.id != checklist_id: raise ValueError('Идентификатор чек-листа не совпадает.')
            return evaluation_store.save_checklist(body, clock())
        if body.checklist.get('id')!=checklist_id: raise ValueError('Идентификатор чек-листа не совпадает.')
        repo.save_checklist(body.checklist,body.expectedVersion);return workspace_state()

    @app.get('/api/conversations')
    def conversations(q:str='',language:str='',channel:str='',offset:int=0,limit:int=100):
        if offset<0 or not 1<=limit<=1000: raise ValueError('Некорректная пагинация.')
        rows=workspace_state()['data']['conversations']
        rows=[r for r in rows if (not language or r['language']==language) and (not channel or r['channel']==channel) and (not q or q.lower() in (r['id']+' '+r['title']+' '+r['operator']).lower())]
        return {'total':len(rows),'items':rows[offset:offset+limit]}

    def find_conversation(conversation_id):
        item=next((r for r in workspace_state()['data']['conversations'] if r['id']==conversation_id),None)
        if item is None: raise KeyError('Диалог не найден.')
        return item

    @app.get('/api/conversations/{conversation_id}')
    def conversation(conversation_id:str): return find_conversation(conversation_id)

    @app.post('/api/appeals')
    def create_appeal(body:AppealBody):
        repo.create_appeal(body.model_dump());return workspace_state()

    @app.post('/api/appeals/{appeal_id}/decision')
    def decide_appeal(appeal_id:str,body:DecisionBody):
        repo.decide_appeal(appeal_id,body.model_dump(exclude_unset=True));return workspace_state()

    @app.post('/api/appeals/{appeal_id}/preview')
    def preview_appeal(appeal_id:str,body:DecisionBody):
        return repo.decide_appeal(appeal_id,body.model_dump(exclude_unset=True),preview=True)

    def download(value,name):
        return JSONResponse(value,headers={'Content-Disposition':f'attachment; filename="{name}.json"'})

    @app.get('/api/export')
    def export(): return download(workspace_state(),'cqa-workspace')

    @app.get('/api/conversations/{conversation_id}/export')
    def export_conversation(conversation_id:str):
        state=repo.state();item=find_conversation(conversation_id)
        return download({'conversation':item,'appeals':[a for a in state['data']['appeals'] if a['conversationId']==conversation_id]},'cqa-dialog')

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
