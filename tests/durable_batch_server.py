"""Isolated browser-test server. Injects only a fake evaluator; never use production DB."""
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app import create_app
from backend.security import SecuritySettings
from backend.tests.test_batches import records, output
import uvicorn


def main():
    database = Path(sys.argv[1]).resolve()
    if not database.is_relative_to((ROOT / 'tmp').resolve()):
        raise ValueError('Browser acceptance DB must be below workspace tmp/')
    calls_path = database.with_suffix('.calls.jsonl')
    gate = asyncio.Event()

    async def fake(request):
        with calls_path.open('a', encoding='utf-8') as f:
            f.write(json.dumps({'id': request.conversationId, 'version': request.checklist.version}) + '\n')
        await gate.wait()
        return output(request)

    settings = SecuritySettings(origins=(f'http://127.0.0.1:{sys.argv[2]}',))
    app = create_app(database, database.with_suffix('.missing-cache'), organizer_url='', evaluator=fake, evaluation_timeout=300, security_settings=settings)
    if not app.state.repo.has_conversations(): app.state.repo.import_data(records())

    @app.post('/__test/release')
    async def release(): gate.set(); return {'ok': True}

    @app.post('/__test/hold')
    async def hold(): gate.clear(); return {'ok': True}

    @app.get('/__test/calls')
    def calls():
        return [json.loads(line) for line in calls_path.read_text(encoding='utf-8').splitlines()] if calls_path.exists() else []

    @app.post('/__test/crash')
    async def crash(): os._exit(0)  # Abrupt loss during fake provider I/O, only in this test harness.

    # Insert harness routes before the SPA catch-all (the production app has none of these routes).
    app.router.routes.sort(key=lambda route: 0 if getattr(route, 'path', '').startswith('/__test/') else 1)
    uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[2]), log_level='warning')


if __name__ == '__main__': main()
