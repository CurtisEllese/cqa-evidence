"""Transactional SQLite storage. Browser clients never own persisted decisions."""
import copy
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4
from .domain import initial_checklist, normalize_import, score_evaluation, validate_checklist, validate_evaluation


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def content_hash(conversation):
    content = {key: conversation.get(key) for key in ('transcript', 'language', 'channel')}
    return hashlib.sha256(dump(content).encode()).hexdigest()


class Conflict(ValueError):
    pass


class Repository:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS checklists (version INTEGER PRIMARY KEY, body TEXT NOT NULL, created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, body TEXT NOT NULL, source TEXT NOT NULL, content_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
              CREATE TABLE IF NOT EXISTS evaluations (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id), content_hash TEXT NOT NULL, checklist_version INTEGER NOT NULL REFERENCES checklists(version), body TEXT NOT NULL, created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS appeals (id TEXT PRIMARY KEY, evaluation_id TEXT NOT NULL REFERENCES evaluations(id), criterion_id TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(evaluation_id,criterion_id));
              CREATE INDEX IF NOT EXISTS evaluations_conversation ON evaluations(conversation_id,created_at);
            ''')
            db.execute('INSERT OR IGNORE INTO checklists VALUES(1,?,?)', (dump(initial_checklist()),now()))
            db.execute("INSERT OR IGNORE INTO metadata VALUES('revision','0')")

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def bump(db):
        db.execute("UPDATE metadata SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")

    @staticmethod
    def checklist(db):
        return json.loads(db.execute('SELECT body FROM checklists ORDER BY version DESC LIMIT 1').fetchone()[0])

    @staticmethod
    def metadata(db,key):
        row = db.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def set_metadata(db,key,value):
        db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',(key,dump(value)))

    def has_dataset(self):
        with self.connect() as db:
            return self.metadata(db,'dataset') is not None

    @staticmethod
    def put_conversations(db, conversations, source):
        for item in conversations:
            clean = copy.deepcopy(item); clean['evaluation'] = None
            db.execute('INSERT INTO conversations VALUES(?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET body=excluded.body,source=excluded.source,content_hash=excluded.content_hash,active=1',
                       (item['id'],dump(clean),source,content_hash(clean)))

    def replace_dev(self,payload,fetched_at=None,source='live'):
        data = payload.get('data') if isinstance(payload,dict) else None
        records = data.get('records') if isinstance(data,dict) else None
        if not isinstance(payload,dict) or payload.get('isSuccess') is not True or not isinstance(data,dict) or data.get('dataset_group_id') != 'DEV' or not isinstance(records,list) or len(records)!=65 or data.get('total_records')!=65:
            raise ValueError('Источник должен вернуть набор DEV из 65 диалогов.')
        conversations = normalize_import(payload)
        if len(conversations)!=65: raise ValueError('В DEV должны быть 65 уникальных диалогов.')
        timestamp=fetched_at or now()
        try: datetime.fromisoformat(timestamp.replace('Z','+00:00'))
        except (ValueError,AttributeError): raise ValueError('Некорректная дата загрузки DEV.') from None
        with self.connect(write=True) as db:
            db.execute("UPDATE conversations SET active=0 WHERE source='DEV'")
            self.put_conversations(db,conversations,'DEV')
            self.set_metadata(db,'dataset',{'group':'DEV','fetchedAt':timestamp,'source':source,'total':65,'conversationIds':[c['id'] for c in conversations]})
            self.bump(db)

    def import_data(self,text):
        with self.connect(write=True) as db:
            existing=[r[0] for r in db.execute('SELECT id FROM conversations WHERE active=1')]
            items=normalize_import(text,existing)
            if not items: raise ValueError('Все диалоги уже загружены. Выберите другой набор.')
            self.put_conversations(db,items,'manual'); self.bump(db)
            return len(items)

    def save_checklist(self,checklist,expected_version):
        clean=validate_checklist(checklist)
        with self.connect(write=True) as db:
            current=self.checklist(db)
            if expected_version!=current['version']: raise Conflict('Чек-лист уже изменён. Обновите страницу и повторите изменения.')
            clean['id']=current['id'];clean['version']=current['version']+1
            db.execute('INSERT INTO checklists VALUES(?,?,?)',(clean['version'],dump(clean),now()));self.bump(db)
        return clean

    def record_evaluation(self,conversation_id,evaluation,expected_hash):
        """Integration point for the backend model worker; no browser result-upload endpoint."""
        with self.connect(write=True) as db:
            row=db.execute('SELECT * FROM conversations WHERE id=? AND active=1',(conversation_id,)).fetchone()
            if not row: raise KeyError('Диалог не найден.')
            if expected_hash!=row['content_hash']: raise Conflict('Диалог изменился во время оценки.')
            version=evaluation.get('checklist',{}).get('version')
            stored=db.execute('SELECT body FROM checklists WHERE version=?',(version,)).fetchone()
            if not stored or json.loads(stored[0])!=evaluation.get('checklist'): raise ValueError('Оценка должна ссылаться на сохранённую версию чек-листа.')
            clean=copy.deepcopy(evaluation);clean['id']=str(uuid4());clean['createdAt']=now();clean['source']='api'
            clean=validate_evaluation(clean,json.loads(row['body']))
            db.execute('INSERT INTO evaluations VALUES(?,?,?,?,?,?)',(clean['id'],conversation_id,row['content_hash'],version,dump(clean),clean['createdAt']));self.bump(db)
            return clean

    @staticmethod
    def evaluation(db,evaluation_id):
        row=db.execute('SELECT * FROM evaluations WHERE id=?',(evaluation_id,)).fetchone()
        if not row: raise KeyError('Оценка не найдена.')
        return row,json.loads(row['body'])

    def create_appeal(self,body):
        comment=body.get('comment','')
        if not isinstance(comment,str) or not 10<=len(comment.strip())<=2000: raise ValueError('Обоснование должно содержать от 10 до 2000 символов.')
        with self.connect(write=True) as db:
            row,evaluation=self.evaluation(db,body.get('evaluationId'))
            if row['conversation_id']!=body.get('conversationId'): raise ValueError('Оценка не относится к этому диалогу.')
            current=db.execute('SELECT content_hash,active FROM conversations WHERE id=?',(row['conversation_id'],)).fetchone()
            if not current['active'] or current['content_hash']!=row['content_hash']: raise Conflict('Диалог изменился; эта оценка находится в истории.')
            result=next((r for r in evaluation['results'] if r['criterionId']==body.get('criterionId')),None)
            if not result: raise ValueError('Критерий отсутствует в оценке.')
            if db.execute('SELECT 1 FROM appeals WHERE evaluation_id=? AND criterion_id=?',(evaluation['id'],body['criterionId'])).fetchone(): raise Conflict('Апелляция по этому критерию уже существует.')
            appeal={'id':str(uuid4()),'conversationId':row['conversation_id'],'evaluationId':evaluation['id'],'criterionId':body['criterionId'],'comment':comment.strip(),'status':'pending','createdAt':now(),'originalValue':result['value']}
            db.execute('INSERT INTO appeals VALUES(?,?,?,?,?)',(appeal['id'],evaluation['id'],appeal['criterionId'],dump(appeal),appeal['createdAt']));self.bump(db)
            return appeal

    def decide_appeal(self,appeal_id,body,preview=False):
        with self.connect(write=not preview) as db:
            found=db.execute('SELECT body FROM appeals WHERE id=?',(appeal_id,)).fetchone()
            if not found: raise KeyError('Апелляция не найдена.')
            appeal=json.loads(found[0])
            if appeal['status']!='pending': raise Conflict('Решение уже сохранено и не может быть перезаписано.')
            status=body.get('status');decision=body.get('decision','')
            if status not in ('accepted','rejected'): raise ValueError('Неизвестное решение.')
            if not isinstance(decision,str) or (not preview and not 10<=len(decision.strip())<=2000): raise ValueError('Обоснование должно содержать от 10 до 2000 символов.')
            if status=='accepted' and 'newValue' not in body: raise ValueError('Укажите новое значение критерия.')
            changed={**appeal,'status':status,'decision':decision.strip(),'newValue':body.get('newValue') if status=='accepted' else appeal['originalValue']}
            _,evaluation=self.evaluation(db,appeal['evaluationId'])
            others=[json.loads(r[0]) for r in db.execute('SELECT body FROM appeals WHERE evaluation_id=? AND id<>?',(evaluation['id'],appeal_id))]
            score=score_evaluation(evaluation,[*others,changed])
            if not preview:
                changed['decidedAt']=now();db.execute('UPDATE appeals SET body=? WHERE id=?',(dump(changed),appeal_id));self.bump(db)
            return score

    def audio_source(self,conversation_id):
        with self.connect() as db:
            row=db.execute('SELECT body,source FROM conversations WHERE id=? AND active=1',(conversation_id,)).fetchone()
            if not row: raise KeyError('Диалог не найден.')
            return json.loads(row['body']).get('audioUrl'),row['source']=='DEV'

    def state(self):
        with self.connect() as db:
            appeals=[json.loads(r[0]) for r in db.execute('SELECT body FROM appeals ORDER BY created_at DESC')]
            conversations=[]
            for row in db.execute('SELECT * FROM conversations WHERE active=1 ORDER BY rowid'):
                item=json.loads(row['body'])
                found=db.execute('SELECT body FROM evaluations WHERE conversation_id=? AND content_hash=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(item['id'],row['content_hash'])).fetchone()
                item['evaluation']=json.loads(found[0]) if found else None
                item['contentHash']=row['content_hash']
                if item.get('audioUrl'):
                    path=f"/api/conversations/{quote(item['id'],safe='')}/audio"
                    item['audioPageUrl']=path+'-page' if row['source']=='DEV' else item['audioUrl']
                    item['audioUrl']=path if row['source']=='DEV' else item['audioUrl']
                if found:
                    evaluation=item['evaluation'];sections={}
                    for section in dict.fromkeys(c['section'] for c in evaluation['checklist']['criteria']):
                        subset=copy.deepcopy(evaluation);subset['checklist']['criteria']=[c for c in subset['checklist']['criteria'] if c['section']==section]
                        ids={c['id'] for c in subset['checklist']['criteria']};subset['results']=[r for r in subset['results'] if r['criterionId'] in ids]
                        sections[section]=score_evaluation(subset,appeals)
                    item['scores']={'original':score_evaluation(evaluation),'final':score_evaluation(evaluation,appeals),'sections':sections}
                conversations.append(item)
            return {'data':{'conversations':conversations,'checklist':self.checklist(db),'appeals':appeals},'dataset':self.metadata(db,'dataset'),'evaluationAvailable':False,'revision':self.metadata(db,'revision')}

    def migrate(self,data):
        """One-way idempotent migration of old local edits. Never accept browser-made evaluations."""
        if not isinstance(data,dict) or not isinstance(data.get('conversations'),list) or any(not isinstance(c,dict) for c in data['conversations']): raise ValueError('Некорректные локальные данные.')
        if data.get('appeals') or any(c.get('evaluation') for c in data['conversations']): raise Conflict('Локальные оценки требуют отдельного переноса. Исходные данные оставлены в браузере.')
        digest=hashlib.sha256(dump(data).encode()).hexdigest()
        with self.connect(write=True) as db:
            if self.metadata(db,'migration:'+digest): return
            current=self.checklist(db);old=data.get('checklist')
            edited=isinstance(old,dict) and old.get('version',1)>1
            if edited:
                old=validate_checklist(old)
                if old['criteria']!=current['criteria'] or old['name']!=current['name']:
                    if current['version']!=1: raise Conflict('На сервере уже изменён чек-лист. Локальная версия оставлена в браузере для ручного переноса.')
                    old['id']=current['id'];old['version']=2
                    db.execute('INSERT INTO checklists VALUES(?,?,?)',(2,dump(old),now()))
            existing={r['id']:json.loads(r['body']) for r in db.execute('SELECT id,body FROM conversations')};records=[]
            for c in data['conversations']:
                if str(c.get('id','')).startswith('demo-'): continue
                if c.get('id') in existing:
                    saved=existing[c['id']]
                    fields=('transcript','language','channel','audioUrl','date','operator')
                    if any(c.get(key)!=saved.get(key) for key in fields):
                        raise Conflict('Локальная версия диалога отличается от серверной. Данные сохранены в браузере для ручного переноса.')
                    continue
                record={k:c.get(k) for k in ('id','language','channel','operator','transcript')}
                if c.get('audioUrl'): record['audio_url']=c['audioUrl']
                if c.get('date'): record['date']=c['date']
                records.append(record)
            if records: self.put_conversations(db,normalize_import(records),'manual')
            self.set_metadata(db,'migration:'+digest,True);self.bump(db)
