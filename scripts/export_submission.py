"""Save and independently validate the organizer results.json; never calls a model."""
import argparse
import json
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.submission import validate_submission


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('dev', 'prod'), required=True)
    parser.add_argument('--group', help='Exact organizer group ID; DEV by default for dev')
    args = parser.parse_args()
    group = args.group or ('DEV' if args.stage == 'dev' else None)
    if not group:
        parser.error('Укажите --group с выданным организаторами ID контрольной группы.')
    with httpx.Client(base_url='http://127.0.0.1:8000', timeout=60) as client:
        response = client.get('/api/state')
        response.raise_for_status()
        state = response.json()
        dataset = next((item for item in state.get('datasets', []) if item['group'] == group), None)
        if dataset is None:
            raise SystemExit('Набор ещё не загружен.')
        expected_count = 65 if args.stage == 'dev' else 10
        if dataset['total'] != expected_count:
            raise SystemExit(f'Для {args.stage} ожидается {expected_count} записей; получено {dataset["total"]}. Файл не сохранён.')
        response = client.get('/api/submission', params={'groupId': group})
        response.raise_for_status()
        payload = response.json()
    lengths = {item['id']: len(item['transcript']) for item in state['data']['conversations']}
    report = validate_submission(payload, dataset['conversationIds'], lengths)
    destination = ROOT / 'results' / args.stage / 'results.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(destination)
    print(f'{destination}: {report["ok"]} оценено, {report["errors"]} ошибок, {report["total"]} всего.')
    if report['errors']:
        print('Формат корректен, но есть ошибки оценки. Исправьте их и выгрузите файл повторно.')


if __name__ == '__main__':
    main()
