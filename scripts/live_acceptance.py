"""Opt-in real provider acceptance on synthetic dialogs, isolated from the team DB.

Run: .venv311/bin/python scripts/live_acceptance.py --run
Calls the configured paid provider up to twice per case; requires server credentials.
This checks a working process, NOT agreement with human Ground Truth.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from backend.app import create_app


def run():
    report = {'checkedAt': datetime.now(timezone.utc).isoformat(), 'syntheticOnly': True,
              'humanGroundTruth': False, 'cases': [], 'checks': []}
    records = json.loads((ROOT / 'demo/jury-scenarios.json').read_text())
    with tempfile.TemporaryDirectory(prefix='cqa-acceptance-') as directory:
        db, cache = Path(directory) / 'acceptance.sqlite', Path(directory) / 'no-cache'
        app = create_app(db, cache, organizer_url='', evaluation_timeout=90)
        with TestClient(app) as client:
            imported = client.post('/api/import', json={'schema_version':'2.0', 'text':json.dumps(records)})
            assert imported.status_code == 200, 'Synthetic import failed'

            def evaluate(identifier, version=1):
                job = client.post('/api/evaluations', json={'schema_version':'2.0', 'conversationId':identifier, 'checklistVersion':version})
                assert job.status_code == 202, 'Evaluation did not start'
                result = client.get('/api/evaluations/' + job.json()['id']).json()
                report['cases'].append({'conversationId':identifier, 'status':result['status'],
                    'version':result['checklist']['version'], 'score':result['score']['total'] if result['score'] else None,
                    'critical':result['score']['critical'] if result['score'] else None, 'provenance':result.get('provenance'),
                    'criteria':[{k:r[k] for k in ('criterionId','value','explanation','applicability_reason')} for r in result['results']]})
                print(identifier, result['status'], flush=True)
                assert result['status'] == 'completed', 'Real evaluation incomplete; inspect the private report'
                assert result.get('provenance'), 'Provider metadata missing'
                return result

            original = [evaluate(item['conversation_id']) for item in records]
            assert original[0]['score']['critical'] is True, 'Explicit insult not flagged'
            assert original[2]['score']['critical'] is True, 'Explicit private paid service not flagged'
            report['checks'].append('RU/KZ/EN and call transcript evaluated; explicit critical cases detected')
            target = original[0]
            old_value = next(row['value'] for row in target['results'] if row['criterionId'] == 'consultation')
            new_value = '15' if old_value != '15' else '10'
            appeal = client.post('/api/appeals', json={'schema_version':'2.0', 'conversationId':records[0]['conversation_id'],
                'evaluationId':target['id'], 'criterionId':'consultation',
                'comment':'Синтетическая проверка механики апелляции, не экспертное исправление.'})
            assert appeal.status_code == 200
            appeal_id = appeal.json()['data']['appeals'][0]['id']
            decided = client.post('/api/appeals/' + appeal_id + '/decision', json={'schema_version':'2.0', 'status':'accepted',
                'decision':'Техническая проверка сохранения исходного решения и пересчёта.', 'newValue':new_value})
            assert decided.status_code == 200
            item = next(c for c in decided.json()['data']['conversations'] if c['id'] == records[0]['conversation_id'])
            assert item['scores']['original']['total'] == target['score']['total']
            assert item['scores']['final']['total'] != item['scores']['original']['total']
            report['appeal'] = {'original':item['scores']['original']['total'], 'final':item['scores']['final']['total']}

            checklist = client.get('/api/checklists').json()[0]
            criterion = checklist['criteria'][0]
            criterion.update(type='scale', max='5', allowedValues=['0','2','5'],
                description='5: оператор поздоровался и представился. 2: поздоровался, но не представился. 0: не поздоровался.')
            saved = client.post('/api/checklists/' + checklist['id'] + '/versions', json={
                'schema_version':'2.0', 'checklist':checklist, 'expectedVersion':1})
            assert saved.status_code == 200
            updated = evaluate(records[1]['conversation_id'], 2)
            assert next(r['value'] for r in updated['results'] if r['criterionId'] == criterion['id']) == '5'
            history = client.get('/api/conversations/' + records[1]['conversation_id'] + '/evaluations').json()
            assert len(history) == 2 and {r['checklist']['version'] for r in history} == {1,2}
            assert client.get('/api/evaluations/' + original[1]['id']).json() == original[1]
            before_restart = client.get('/api/export').json()
            report['checks'].append('Appeal recalculated, new semantic scale applied, original version remains accessible')
        with TestClient(create_app(db, cache, organizer_url='')) as restarted:
            assert restarted.get('/api/export').json() == before_restart
            assert restarted.get('/api/conversations/' + records[1]['conversation_id'] + '/evaluations').json() == history
        report['checks'].append('Restart preserves all evaluations, history, checklist versions and appeal')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Explicitly enable real provider calls')
    parser.add_argument('--output', default='tmp/qa/submission-live.json')
    args = parser.parse_args()
    if not args.run:
        parser.error('--run is required; this script uses the configured provider')
    report = run()
    destination = ROOT / args.output
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print('PASS: isolated live acceptance; report:', destination)
