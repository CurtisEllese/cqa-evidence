"""Organizer JSON 1.0, separate from the application's internal v2 contract.

Source: CQA_Student_Guide_Appendix_Results.pdf, pages 1–3.
Export original model decisions; appeals remain available in the workspace backup.
"""
from decimal import Decimal, ROUND_HALF_UP

from .contracts.v2 import to_wire_checklist
from .domain import initial_checklist

CRITERIA = {
    'greeting': 'contact', 'discovery': 'discovery',
    'identification': 'identification', 'consultation': 'consultation',
    'empathy': 'empathy', 'dialogue': 'dialogue',
    'closing': 'closure', 'gross-errors': 'critical',
}


def compatible(checklist):
    """Presentation/grouping changes are harmless; scoring/semantic changes are not."""
    expected = to_wire_checklist(initial_checklist())['criteria']
    actual = checklist['criteria']
    fields = ('id', 'description', 'type', 'weight', 'max', 'allowedValues', 'allowNA', 'naCondition')
    return len(actual) == len(expected) and {
        item['id']: {field: item.get(field) for field in fields} for item in actual
    } == {item['id']: {field: item.get(field) for field in fields} for item in expected}


def error_record(identifier, reason):
    return dict(conversation_id=identifier, status='error', criteria=[], score=None, error=reason)


def export_record(conversation):
    identifier = conversation['id']
    evaluation = conversation.get('evaluation')
    if not evaluation or evaluation['status'] != 'completed':
        status = evaluation['status'] if evaluation else 'not_evaluated'
        return error_record(identifier, 'Нет подтверждённой оценки: ' + status + '.')
    if not compatible(evaluation['checklist']):
        return error_record(identifier, 'Оценка выполнена по изменённому чек-листу. Для сдачи требуется исходный чек-лист организаторов.')
    # Source positions include clients, bots and system events, not just operators.
    positions = {message['id']: index + 1 for index, message in enumerate(conversation['transcript'])}
    by_id = {item['criterionId']: item for item in evaluation['results']}
    criteria = []
    for internal, official in CRITERIA.items():
        row = by_id[internal]
        turns = set()
        for evidence in row['evidence']:
            if evidence['verification_status'] != 'verified':
                return error_record(identifier, 'Доказательства оценки не подтверждены.')
            if evidence.get('message_id'):
                turns.add(positions[evidence['message_id']])
            if evidence.get('start_message_id') and evidence.get('end_message_id'):
                turns.update(range(positions[evidence['start_message_id']], positions[evidence['end_message_id']] + 1))
        value = row['value']
        if value is not None:
            number = Decimal(value)
            value = int(number) if official in ('consultation', 'dialogue') else number == 1
        rationale = row['explanation']
        if row.get('applicability_reason'):
            rationale += ' N/A: ' + row['applicability_reason']
        criteria.append(dict(criterion_id=official, value=value, rationale=rationale, evidence_turns=sorted(turns)))
    score = evaluation['score']
    if score is None or score['total'] is None:
        return error_record(identifier, 'Итоговый балл не определён.')
    return dict(conversation_id=identifier, status='ok', criteria=criteria,
                score=dict(final_score=float(Decimal(score['total']).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)),
                           critical_flag=next(item['value'] for item in criteria if item['criterion_id'] == 'critical')))


def build_submission(state, group_id, *, team_name='Spooky spooky', model_name='gpt-6-luna'):
    datasets = state.get('datasets') or ([state['dataset']] if state.get('dataset') else [])
    group = next((item for item in datasets if item['group'] == group_id), None)
    if group is None:
        raise KeyError('Набор не загружен. Сначала получите его через API организаторов.')
    ids = group['conversationIds']
    if len(ids) != len(set(ids)) or len(ids) != group['total']:
        raise ValueError('Состав набора повреждён: число записей или ID не совпадает.')
    conversations = {item['id']: item for item in state['data']['conversations']}
    results, models = [], set()
    for identifier in ids:
        conversation = conversations.get(identifier)
        if conversation is None:
            results.append(error_record(identifier, 'Исходный диалог отсутствует в рабочем пространстве.'))
            continue
        row = export_record(conversation)
        if row['status'] == 'ok':
            provenance = conversation['evaluation'].get('provenance')
            if not provenance or not provenance.get('model'):
                row = error_record(identifier, 'Модель исходной оценки не зафиксирована.')
            else:
                models.add(provenance['model'])
        results.append(row)
    return dict(schema_version='1.0', team_name=team_name,
                model=dict(mode='external_api', name=', '.join(sorted(models)) or model_name), results=results)


def validate_submission(payload, expected_ids=None, transcript_lengths=None):
    """Independent final checks, including weighted arithmetic and complete source IDs."""
    def require(condition, message):
        if not condition:
            raise ValueError(message)
    require(set(payload) == {'schema_version', 'team_name', 'model', 'results'}, 'Неверные корневые поля.')
    require(payload['schema_version'] == '1.0', 'Неверная версия файла сдачи.')
    require(isinstance(payload['team_name'], str) and payload['team_name'].strip(), 'Не указана команда.')
    model = payload['model']
    require(set(model) == {'mode', 'name'} and model['mode'] in ('local', 'external_api', 'hybrid') and isinstance(model['name'], str) and model['name'].strip(), 'Не указана модель.')
    require(isinstance(payload['results'], list), 'results должен быть массивом.')
    ids = [item['conversation_id'] for item in payload['results']]
    require(all(isinstance(identifier, str) and identifier for identifier in ids) and len(ids) == len(set(ids)), 'Пустые или повторяющиеся ID.')
    if expected_ids is not None:
        require(set(ids) == set(expected_ids) and len(ids) == len(expected_ids), 'Состав файла не совпадает с исходным набором.')
    weights = dict(contact=10, discovery=15, identification=20, consultation=20, empathy=15, dialogue=15, closure=5)
    for item in payload['results']:
        if item['status'] == 'error':
            require(set(item) == {'conversation_id', 'status', 'criteria', 'score', 'error'} and item['criteria'] == [] and item['score'] is None and isinstance(item['error'], str) and item['error'].strip(), 'Некорректная запись ошибки.')
            continue
        require(item['status'] == 'ok' and set(item) == {'conversation_id', 'status', 'criteria', 'score'}, 'Некорректный статус/поля результата.')
        rows = item['criteria']
        require(len(rows) == 8 and {row['criterion_id'] for row in rows} == set(CRITERIA.values()), 'Требуются все восемь критериев ровно один раз.')
        earned, possible = Decimal(0), Decimal(0)
        values = {}
        for row in rows:
            require(set(row) == {'criterion_id', 'value', 'rationale', 'evidence_turns'}, 'Неверные поля критерия.')
            key, value = row['criterion_id'], row['value']
            values[key] = value
            if value is None:
                require(key in ('discovery', 'identification', 'consultation', 'closure'), 'N/A не разрешён для критерия.')
            elif key in ('consultation', 'dialogue'):
                require(type(value) is int and value in ((0, 5, 10, 15, 20) if key == 'consultation' else (0, 4, 7, 10)), 'Неверное значение шкалы.')
            else:
                require(type(value) is bool, 'Бинарный критерий должен быть true/false, не числом.')
            require(isinstance(row['rationale'], str) and row['rationale'].strip(), 'Нет объяснения.')
            turns = row['evidence_turns']
            require(isinstance(turns, list) and turns and all(type(turn) is int and turn >= 1 for turn in turns) and len(turns) == len(set(turns)), 'Неверные номера реплик.')
            if transcript_lengths is not None:
                require(item['conversation_id'] in transcript_lengths and max(turns) <= transcript_lengths[item['conversation_id']], 'Доказательство выходит за границы исходного транскрипта.')
            if value is not None and key in weights:
                weight = Decimal(weights[key])
                maximum = 20 if key == 'consultation' else 10 if key == 'dialogue' else 1
                earned += Decimal(int(value)) / maximum * weight
                possible += weight
        score = item['score']
        require(set(score) == {'final_score', 'critical_flag'}, 'Неверные поля итога.')
        require(type(score['critical_flag']) is bool and score['critical_flag'] == values['critical'], 'Грубая ошибка не совпадает с флагом.')
        require(type(score['final_score']) in (int, float) and 0 <= score['final_score'] <= 100, 'Итог вне диапазона.')
        expected = (earned / possible * 100).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        require(Decimal(str(score['final_score'])) == expected, 'Итог не совпадает с официальной формулой.')
    return {'total': len(ids), 'ok': sum(item['status'] == 'ok' for item in payload['results']), 'errors': sum(item['status'] == 'error' for item in payload['results'])}
