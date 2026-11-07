"""Real browser + real FastAPI/SQLite queue, with gated fake provider and fresh temp DB.

Run `npm run build`, then `python tests/browser_durable_batch.py` using Python with Playwright.
Owns an isolated server on an ephemeral loopback port; never visits localhost:8000.
"""
from collections import Counter
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'tmp' / 'durable-browser'
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    checks, errors, starts = [], [], []
    process = None
    log = (ARTIFACTS / 'server.log').open('w', encoding='utf-8')

    def api(path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url + path, data=data, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=5) as response: return json.load(response)

    def until(fn, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                value = fn()
                if value: return value
            except (OSError, ValueError): pass
            time.sleep(.05)
        raise AssertionError('Timed out waiting for isolated test condition')

    with tempfile.TemporaryDirectory(prefix='run-', dir=ARTIFACTS) as folder:
        database = Path(folder) / 'acceptance.sqlite'
        def launch():
            nonlocal process
            env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'APP_ORIGINS': url}
            process = subprocess.Popen([str(ROOT / '.venv311/Scripts/python.exe'), str(ROOT / 'tests/durable_batch_server.py'), str(database), str(port)],
                                       cwd=ROOT, env=env, stdout=log, stderr=log,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            until(lambda: api('/api/health'))
        try:
            launch()
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={'width': 1440, 'height': 1100})
                context.on('page', lambda page: page.on('pageerror', lambda exc: errors.append(str(exc))))
                context.on('request', lambda request: starts.append(request.post_data_json) if request.method == 'POST' and request.url == url + '/api/batches' else None)
                page = context.new_page()
                page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
                page.goto(url + '/#conversations'); page.wait_for_load_state('networkidle')
                # Inspect the rendered surface before using its labelled controls.
                (ARTIFACTS / 'initial-body.txt').write_text(page.locator('body').inner_text(), encoding='utf-8')
                assert 'Оценка диалогов' in page.locator('body').inner_text(), {'errors': errors, 'html': page.content()[:2500]}
                panel = page.get_by_role('region', name='Оценка диалогов')
                panel.get_by_label('Набор диалогов').select_option('workspace:')
                panel.get_by_role('button', name='Оценить набор · 3', exact=True).click()
                until(lambda: len(api('/__test/calls')) == 1)
                identifier = api('/api/batches/current')['batch']['id']
                page.reload(); page.wait_for_load_state('networkidle')
                expect(panel.get_by_label('Ход оценки набора')).to_be_visible()
                expect(panel.get_by_label('Набор диалогов')).to_have_value('workspace:')
                assert api('/api/batches/current')['batch']['id'] == identifier
                assert len(starts) == 1
                checks.append('reload reattaches same batch without POST/evaluator replay')
                page.goto(url + '/#overview'); page.goto(url + '/#conversations')
                expect(panel.get_by_label('Ход оценки набора')).to_be_visible()
                assert len(starts) == 1
                checks.append('navigation reattaches same batch')
                other = context.new_page(); other.goto(url + '/#conversations'); other.wait_for_load_state('networkidle')
                expect(other.get_by_label('Ход оценки набора')).to_be_visible()
                assert api('/api/batches', starts[0])['id'] == identifier
                assert api('/api/batches', {**starts[0], 'requestId': 'second-tab-start'})['id'] == identifier
                assert len(api('/__test/calls')) == 1
                checks.append('two tabs and duplicate POST create one queue/provider call')
                panel.get_by_role('button', name='Остановить очередь', exact=True).click()
                until(lambda: api('/api/batches/current')['batch']['status'] == 'stopping')
                api('/__test/release', {})
                until(lambda: api('/api/batches/current')['batch']['status'] == 'paused')
                page.reload(); page.wait_for_load_state('networkidle')
                expect(panel.get_by_role('button', name='Продолжить оставшиеся · 2')).to_be_enabled()
                assert len(api('/__test/calls')) == 1
                checks.append('stop completes current only, persisted pause survives reload')
                api('/__test/hold', {})
                panel.get_by_role('button', name='Продолжить оставшиеся · 2').click()
                until(lambda: len(api('/__test/calls')) == 2)
                page.close(); other.close()
                api('/__test/release', {})
                until(lambda: api('/api/batches/current')['batch']['status'] == 'completed')
                page = context.new_page(); page.goto(url + '/#conversations'); page.wait_for_load_state('networkidle')
                panel = page.get_by_role('region', name='Оценка диалогов')
                expect(panel.get_by_label('Ход оценки набора')).to_contain_text('обработано 3 из 3')
                assert Counter(call['id'] for call in api('/__test/calls')) == {'batch-0': 1, 'batch-1': 1, 'batch-2': 1}
                checks.append('all pages closed: server finishes remaining; reopened page shows completion')
                # A deliberate new run, followed by hard backend death in fake provider I/O.
                api('/__test/hold', {})
                panel.get_by_label('Набор диалогов').select_option('workspace:')
                panel.get_by_label('Состав запуска').select_option('all')
                panel.get_by_role('button', name='Оценить набор · 3', exact=True).click()
                until(lambda: len(api('/__test/calls')) == 4)
                second_id = api('/api/batches/current')['batch']['id']
                try: api('/__test/crash', {})
                except OSError: pass
                process.wait(timeout=5); launch()
                recovered = api('/api/batches/current')['batch']
                assert recovered['id'] == second_id and recovered['status'] == 'paused'
                assert [i['status'] for i in recovered['items']] == ['uncertain', 'queued', 'queued']
                assert len(api('/__test/calls')) == 4
                assert api('/api/batches', starts[-1])['id'] == second_id
                assert len(api('/__test/calls')) == 4
                page.reload(); page.wait_for_load_state('networkidle')
                panel.get_by_text('Результаты запуска', exact=False).click()
                expect(panel.get_by_text('Статус нужно уточнить', exact=True)).to_be_visible()
                page.screenshot(path=str(ARTIFACTS / 'restart-uncertain.png'), full_page=True)
                expect(panel.get_by_role('button', name='Продолжить оставшиеся · 2')).to_be_enabled()
                panel.get_by_role('button', name='Продолжить оставшиеся · 2').click()
                until(lambda: len(api('/__test/calls')) == 5)
                api('/__test/release', {})
                until(lambda: api('/api/batches/current')['batch']['status'] == 'completed')
                assert Counter(call['id'] for call in api('/__test/calls')) == {'batch-0': 2, 'batch-1': 2, 'batch-2': 2}
                checks.append('hard restart marks in-flight unknown; explicit resume never repeats unknown call')
                panel.get_by_label('Состав запуска').select_option('all')
                expect(panel.get_by_role('button', name='Оценить набор · 2', exact=True)).to_be_enabled()
                checks.append('unknown outcome excluded from ordinary all-mode batch candidates')
                page.screenshot(path=str(ARTIFACTS / 'completed.png'), full_page=True)
                assert not errors, errors
                browser.close()
            report = {'checks': checks, 'pageErrors': errors, 'provider': 'fake only', 'paidCalls': 0,
                      'recordedCalls': api('/__test/calls'), 'database': 'temporary, destroyed after test', 'passed': True}
            (ARTIFACTS / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(report, ensure_ascii=True))
        finally:
            # Windows virtualenv launcher may be a parent of the actual runtime.
            # Stop the server from inside its own process, then reap the launcher.
            try: api('/__test/crash', {})
            except OSError: pass
            if process and process.poll() is None:
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate(); process.wait(timeout=10)
            log.close()


if __name__ == '__main__': main()
