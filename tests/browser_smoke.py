"""Acceptance checks against the configured real DEV dataset. No private fixture is tracked."""
import json
from collections import Counter
from pathlib import Path
from urllib.parse import quote
from playwright.sync_api import sync_playwright, expect

OUT = Path('tmp/qa'); OUT.mkdir(parents=True, exist_ok=True)
URL = 'http://127.0.0.1:5173/'
KEY = 'freedom-quality-organizer-v2'
checks = []
def passed(name):
    checks.append(name); print('PASS:', name, flush=True)
def state(page):
    return page.evaluate('(key) => JSON.parse(localStorage.getItem(key))', KEY)
def loaded(page):
    page.wait_for_function('(key) => JSON.parse(localStorage.getItem(key))?.dataset?.total === 65', arg=KEY)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--disable-gpu'])
    context = browser.new_context(viewport={'width':1440,'height':1080}, accept_downloads=True)
    context.add_init_script("localStorage.setItem('freedom-quality-v1', JSON.stringify({conversations:[{id:'demo-obsolete'}]}))")
    page = context.new_page(); errors=[]; requests=[]
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('request', lambda req: requests.append(req.url))
    page.goto(URL); loaded(page)
    expect(page.get_by_role('heading',name='Оценок пока нет')).to_be_visible()
    saved=state(page); rows=saved['data']['conversations']
    assert len(rows)==65 and len(set(r['id'] for r in rows))==65
    assert Counter(r['language'] for r in rows)=={'RU':35,'KZ':15,'EN':15}
    assert Counter(r['channel'] for r in rows)=={'agent chat':60,'voice call':5}
    assert sum(len(r['transcript']) for r in rows)==1268
    assert all(r['evaluation'] is None and not r['id'].startswith('demo-') for r in rows)
    assert not saved['data']['appeals']
    assert page.evaluate("localStorage.getItem('freedom-quality-v1')") is None
    source=page.request.get(URL+'api/organizer/dataset').json()['payload']['data']['records']
    for row in rows:
        original=next(r for r in source if r['conversation_id']==row['id'])
        assert [(m['timestamp'],m['speaker'],m['text']) for m in row['transcript']]==[(m['timestamp'],m['speaker'],m['text']) for m in original['transcript']]
        assert row['audioUrl']==original.get('audio_url')
    passed('65 unique real dialogs; exact language/channel totals and all 1268 messages; legacy demo removed')
    page.screenshot(path=str(OUT/'overview-desktop.png'),full_page=True)

    page.get_by_role('button',name='Обновить DEV',exact=True).click()
    expect(page.locator('.toast')).to_contain_text('65 диалогов',timeout=45000)
    assert state(page)['dataset']['source']=='live' and len(state(page)['data']['conversations'])==65
    assert all('environment.api.powerplatform.com' not in u and 'sig=' not in u for u in requests)
    passed('Live server refresh keeps 65 unique dialogs and hides organizer credentials from browser requests')

    page.goto(URL+'#conversations')
    expect(page.locator('.table-footer')).to_contain_text('из 65 диалогов')
    page.get_by_label('Язык диалога').select_option('RU')
    expect(page.locator('.table-footer')).to_contain_text('из 35 диалогов')
    page.get_by_label('Язык диалога').select_option('all')
    page.get_by_role('button',name='Фильтры',exact=True).click()
    page.get_by_label('Канал',exact=True).select_option('voice call')
    expect(page.locator('tbody tr')).to_have_count(5)
    page.get_by_role('button',name='Сбросить',exact=True).click()
    ids=[]
    while True:
        ids.extend(page.locator('.row-open').evaluate_all('(els)=>els.map(e=>e.getAttribute("aria-label"))'))
        next_button=page.get_by_role('button',name='Следующая страница')
        if next_button.is_disabled(): break
        next_button.click()
    assert len(ids)==len(set(ids))==65
    chat=next(r for r in rows if r['channel']=='agent chat')
    call=next(r for r in rows if r['channel']=='voice call')
    page.get_by_label('Поиск диалогов',exact=True).fill(chat['id'])
    expect(page.get_by_label('Глобальный поиск диалога')).to_have_value(chat['id'])
    expect(page.locator('tbody tr')).to_have_count(1)
    page.get_by_role('button',name='Очистить поиск',exact=True).click()
    passed('Filters, synchronized search, and pagination expose every dialog exactly once')

    for row in [chat,call]:
        page.goto(URL+'#'+quote('dialog/'+row['id'],safe=''))
        expect(page.get_by_role('heading',level=1)).to_have_text(row['title'])
        expect(page.get_by_role('button',name='Оценка пока недоступна',exact=True)).to_be_disabled()
        expect(page.locator('.message')).to_have_count(len(row['transcript']))
        if row['audioUrl']:
            assert page.locator('a[href="'+row['audioUrl']+'"]').count()==1
    page.screenshot(path=str(OUT/'dialog-desktop.png'),full_page=True)
    passed('Real text and voice details preserve transcript and audio links; no invented evaluation')

    page.goto(URL+'#checklist')
    expect(page.locator('.checklist-criterion')).to_have_count(8)
    page.get_by_label('Название чек-листа').fill('Несохранённый черновик')
    page.get_by_role('navigation').get_by_role('button',name='Диалоги',exact=True).click()
    expect(page.get_by_role('dialog')).to_be_visible()
    page.get_by_role('button',name='Уйти без сохранения').click()
    passed('Official eight-criterion rubric and unsaved-edit navigation guard')

    page.goto(URL+'#overview')
    page.route('**/api/organizer/dataset',lambda route: route.fulfill(status=502,json={'error':'Проверка недоступности API'}))
    page.get_by_role('button',name='Обновить DEV',exact=True).click()
    expect(page.get_by_text('Проверка недоступности API')).to_be_visible()
    assert len(state(page)['data']['conversations'])==65
    page.unroute('**/api/organizer/dataset')
    passed('API failure remains visible and preserves the last successful dataset')

    mobile=browser.new_context(viewport={'width':375,'height':812},is_mobile=True,has_touch=True,reduced_motion='reduce')
    mp=mobile.new_page();mp.on('pageerror',lambda error:errors.append(str(error)))
    mp.goto(URL);loaded(mp)
    for route in ['overview','conversations','checklist','appeals',quote('dialog/'+chat['id'],safe='')]:
        mp.goto(URL+'#'+route);mp.wait_for_timeout(180)
        assert mp.evaluate('document.documentElement.scrollWidth <= 375'),route
        assert mp.locator('main h1').is_visible(),route
    mp.screenshot(path=str(OUT/'dialog-mobile.png'),full_page=True)
    mp.goto(URL+'#overview');mp.screenshot(path=str(OUT/'overview-mobile.png'),full_page=True)
    assert not errors,errors
    passed('All 375px routes fit viewport; desktop and mobile have no runtime errors')
    browser.close()
(OUT/'browser-report.json').write_text(json.dumps({'passed':checks,'count':len(checks)},ensure_ascii=False,indent=2),encoding='utf8')
print(f'Completed {len(checks)} acceptance groups.',flush=True)
