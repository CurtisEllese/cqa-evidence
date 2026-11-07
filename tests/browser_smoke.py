"""Full-stack browser checks against a private disposable SQLite copy of DEV."""
import json
import os
import socket
import subprocess
import time
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import quote
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'tmp/qa';OUT.mkdir(parents=True,exist_ok=True)
DB=OUT/f'browser-{time.time_ns()}.sqlite'
PYTHON=ROOT/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]
BACK_PORT,UI_PORT=free_port(),free_port()
URL=f'http://127.0.0.1:{UI_PORT}/';BACK=f'http://127.0.0.1:{BACK_PORT}'
env={**os.environ,'DATABASE_PATH':str(DB),'BACKEND_URL':BACK,'APP_ORIGINS':URL.rstrip('/')}
flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
processes=[];checks=[]
def passed(name):
    checks.append(name);print('PASS:',name,flush=True)
def ready(url):
    for _ in range(100):
        try:
            with urllib.request.urlopen(url,timeout=1) as response:
                if response.status==200:return
        except Exception:time.sleep(.1)
    raise RuntimeError('Test server did not start')
def state(page):return page.request.get(URL+'api/state').json()
try:
    processes.append(subprocess.Popen([str(PYTHON),'-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',str(BACK_PORT)],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags))
    ready(BACK+'/api/health')
    processes.append(subprocess.Popen(['node','node_modules/vite/bin/vite.js','--host','127.0.0.1','--port',str(UI_PORT),'--strictPort'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags))
    ready(URL)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--disable-gpu'])
        context=browser.new_context(viewport={'width':1440,'height':1080},accept_downloads=True)
        context.add_init_script("localStorage.setItem('freedom-quality-v1','{}')")
        page=context.new_page();errors=[];requests=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('request',lambda req:requests.append(req.url))
        page.goto(URL);expect(page.get_by_role('heading',name='Оценок пока нет')).to_be_visible()
        saved=state(page);rows=saved['data']['conversations']
        assert len(rows)==65 and len({r['id'] for r in rows})==65
        assert Counter(r['language'] for r in rows)=={'RU':35,'KZ':15,'EN':15}
        assert Counter(r['channel'] for r in rows)=={'agent chat':60,'voice call':5}
        assert sum(len(r['transcript']) for r in rows)==1268
        assert all(r['evaluation'] is None for r in rows)
        assert page.evaluate("localStorage.getItem('freedom-quality-v1')") is None
        assert page.evaluate("localStorage.getItem('freedom-quality-organizer-v2')") is None
        page.screenshot(path=str(OUT/'backend-overview-desktop.png'),full_page=True)
        passed('65 real dialogs from SQLite; no browser persistence or fabricated scores')

        page.goto(URL+'#conversations');expect(page.locator('.table-footer')).to_contain_text('из 65 диалогов')
        page.get_by_label('Язык диалога').select_option('RU');expect(page.locator('.table-footer')).to_contain_text('из 35 диалогов')
        page.get_by_label('Язык диалога').select_option('all')
        page.get_by_role('button',name='Фильтры',exact=True).click();page.get_by_label('Канал',exact=True).select_option('voice call')
        expect(page.locator('tbody tr')).to_have_count(5)
        passed('Searchable server dataset renders language and channel filters')

        call=next(r for r in rows if r['id']=='p4-ru-005')
        page.goto(URL+'#'+quote('dialog/'+call['id'],safe=''))
        expect(page.locator('audio')).to_be_visible()
        page.locator('audio').evaluate('(a)=>{a.muted=true;a.load()}')
        page.wait_for_function('document.querySelector("audio").readyState >= 2',timeout=60000)
        page.locator('audio').evaluate('(a)=>a.play()')
        page.wait_for_function('document.querySelector("audio").currentTime > 0',timeout=15000)
        assert page.locator('audio').evaluate('(a)=>a.duration')>0
        page.locator('audio').evaluate('(a)=>a.pause()')
        for call in (r for r in rows if r['channel']=='voice call'):
            response=page.request.get(URL.rstrip('/')+call['audioUrl'],headers={'Range':'bytes=0-31'},timeout=60000)
            assert response.status==206 and response.headers['content-type'].startswith('audio/') and len(response.body())==32
        assert all('sig=' not in u and 'powerplatform.com' not in u for u in requests)
        page.screenshot(path=str(OUT/'backend-audio-desktop.png'),full_page=True)
        passed('Real p4-ru-005 plays; all five recordings stream with byte ranges and private upstream links')

        page.goto(URL+'#checklist');expect(page.locator('.checklist-criterion')).to_have_count(8)
        page.get_by_label('Название чек-листа').fill('QA server-owned checklist')
        page.get_by_role('button',name='Сохранить версию').click()
        expect(page.locator('.checklist-version-row')).to_contain_text('Версия 2')
        other=browser.new_context();second=other.new_page();second.goto(URL+'#checklist')
        expect(second.get_by_label('Название чек-листа')).to_have_value('QA server-owned checklist')
        second.get_by_label('Название чек-листа').fill('QA concurrent version');second.get_by_role('button',name='Сохранить версию').click()
        expect(second.locator('.checklist-version-row')).to_contain_text('Версия 3')
        page.get_by_label('Название чек-листа').fill('QA stale draft');page.get_by_role('button',name='Сохранить версию').click()
        expect(page.get_by_text('Чек-лист уже изменён.',exact=False)).to_be_visible()
        expect(page.get_by_label('Название чек-листа')).to_have_value('QA stale draft')
        page.reload();expect(page.get_by_label('Название чек-листа')).to_have_value('QA concurrent version')
        other.close();passed('Checklist persists across browsers; stale saves rejected without losing draft')

        page.goto(URL+'#conversations');page.get_by_role('button',name='Загрузить диалоги',exact=True).click()
        page.locator('#json-input').fill('{broken');page.get_by_role('button',name='Загрузить',exact=True).click()
        expect(page.locator('.form-error')).to_be_visible();expect(page.get_by_role('dialog')).to_be_visible()
        synthetic={'conversation_id':'browser-test-only','channel':'agent chat','language':'EN','transcript':[{'speaker':'client','text':'Synthetic browser import fixture','timestamp':'00:00'}],'audio_url':None}
        page.locator('#json-input').fill(json.dumps([synthetic]));page.get_by_role('button',name='Загрузить',exact=True).click()
        expect(page.get_by_role('dialog')).not_to_be_visible();expect(page.locator('.table-footer')).to_contain_text('из 66 диалогов')
        with page.expect_download() as download:
            page.get_by_role('button',name='Экспорт набора',exact=True).click()
        exported=json.loads(Path(download.value.path()).read_text(encoding='utf-8'))
        assert len(exported['data']['conversations'])==66
        passed('Server validates imports, preserves form errors, deduplicates and exports persisted data')

        # Synthetic evaluation exists only in this disposable test database.
        import sys
        sys.path.insert(0,str(ROOT))
        from backend.repository import Repository
        repo=Repository(DB);snapshot=repo.state();rubric=snapshot['data']['checklist']
        fixture=next(c for c in snapshot['data']['conversations'] if c['id']=='browser-test-only')
        values={'greeting':1,'discovery':None,'identification':None,'consultation':15,'empathy':1,'dialogue':7,'closing':0,'gross-errors':0}
        repo.record_evaluation(fixture['id'],{'checklist':rubric,'results':[{'criterionId':c['id'],'value':values[c['id']],'explanation':'Synthetic browser test evidence','evidenceIds':[fixture['transcript'][0]['id']],'critical':False} for c in rubric['criteria']]},fixture['contentHash'])
        page.goto(URL+'#dialog%2Fbrowser-test-only');page.reload()
        expect(page.locator('.score-large')).to_contain_text('77,69')
        closing=page.locator('.result-card').filter(has_text='Завершение')
        closing.get_by_role('button',name='Оспорить',exact=True).click()
        page.get_by_label('Почему оценку нужно пересмотреть?').fill('Проверяем серверную апелляцию в тестовой базе.')
        page.get_by_role('button',name='Отправить апелляцию').click();expect(page.get_by_role('dialog')).not_to_be_visible()
        page.get_by_role('navigation').get_by_role('button',name='Апелляции').click()
        page.get_by_role('button',name='Рассмотреть',exact=True).click()
        page.get_by_label('Новое значение',exact=True).select_option('1')
        page.get_by_label('Обоснование решения').fill('Тестовое решение с серверным пересчётом.')
        expect(page.locator('.review-total')).to_contain_text('85,38')
        page.get_by_role('button',name='Сохранить решение').click();expect(page.get_by_role('dialog')).not_to_be_visible()
        page.goto(URL+'#dialog%2Fbrowser-test-only');expect(page.locator('.score-large')).to_contain_text('85,38')
        page.reload();expect(page.locator('.score-large')).to_contain_text('85,38')
        passed('Evidence, appeal submission, server preview and persistent decision work end to end')

        page.goto(URL+'#overview')
        page.route('**/api/organizer/refresh',lambda route:route.fulfill(status=502,json={'error':'Проверка недоступности API'}))
        page.get_by_role('button',name='Обновить DEV',exact=True).click()
        expect(page.get_by_text('Проверка недоступности API')).to_be_visible()
        assert len(state(page)['data']['conversations'])==66
        page.unroute('**/api/organizer/refresh')
        page.reload();expect(page.get_by_role('heading',name='Качество начинается с диалога')).to_be_visible()
        passed('Refresh failure preserves persisted conversations and remains visible')

        mobile=browser.new_context(viewport={'width':375,'height':812},is_mobile=True,has_touch=True,reduced_motion='reduce')
        mp=mobile.new_page();mp.on('pageerror',lambda error:errors.append(str(error)))
        for route in ['overview','conversations','checklist','appeals',quote('dialog/p4-ru-005',safe='')]:
            mp.goto(URL+'#'+route);expect(mp.locator('main h1')).to_be_visible();mp.wait_for_timeout(150)
            assert mp.evaluate('document.documentElement.scrollWidth <= 375'),route
        mp.screenshot(path=str(OUT/'backend-audio-mobile.png'),full_page=True)
        assert not errors,errors
        passed('Direct routes and mobile layouts work without browser runtime exceptions')
        browser.close()
finally:
    for process in reversed(processes):
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait()
(OUT/'backend-browser-report.json').write_text(json.dumps({'passed':checks,'count':len(checks)},ensure_ascii=False,indent=2),encoding='utf-8')
print(f'Completed {len(checks)} full-stack browser groups.',flush=True)
