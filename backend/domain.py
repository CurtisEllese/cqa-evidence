"""Server-owned normalization, immutable rubric validation and deterministic scoring."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_RECORDS = 1000
MAX_CRITERIA = 100
MAX_NUMBER = 1_000_000
CHANNELS = ("agent chat", "voice call")
LANGUAGES = ("RU", "KZ", "EN")
SPEAKERS = ("operator", "client", "bot", "system")
CHAT_TIMESTAMP = re.compile(r"[0-9]{2,}:[0-5][0-9]\Z")
ISO_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]+)?(?:Z|[+-](?:[01][0-9]|2[0-3]):?[0-5][0-9])\Z")
ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


def _text(value, label, maximum=20_000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label}: требуется непустой текст длиной не более {maximum} символов.")
    return value


def _number(value, label, minimum=0, maximum=MAX_NUMBER):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum or not math.isfinite(value):
        raise ValueError(f"{label}: требуется конечное число от {minimum} до {maximum}.")
    return value


def _date(value, allow_date_only=False):
    if not isinstance(value, str):
        raise ValueError("date/timestamp: требуется дата ISO 8601.")
    try:
        if allow_date_only and ISO_DATE.fullmatch(value):
            return date.fromisoformat(value)
        if ISO_TIMESTAMP.fullmatch(value):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass
    raise ValueError("date/timestamp: требуется корректная дата ISO 8601 с часовым поясом.")


def initial_checklist() -> dict:
    """Load a fresh copy of the official rubric, never shared mutable process state."""
    payload = json.loads(Path(__file__).with_name("rubric.json").read_text(encoding="utf-8"))
    return validate_checklist(payload)


def validate_checklist(checklist: dict) -> dict:
    """Validate and return a deep copy; unknown metadata is retained."""
    if not isinstance(checklist, dict):
        raise ValueError("Чек-лист должен быть объектом.")
    _text(checklist.get("id"), "ID чек-листа", 512)
    _text(checklist.get("name"), "Название чек-листа", 500)
    version = checklist.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= MAX_NUMBER:
        raise ValueError("Версия чек-листа должна быть положительным целым числом.")
    criteria = checklist.get("criteria")
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= MAX_CRITERIA:
        raise ValueError(f"Чек-лист должен содержать от 1 до {MAX_CRITERIA} критериев.")
    result = deepcopy(checklist)
    seen = set()
    for criterion in result["criteria"]:
        if not isinstance(criterion, dict):
            raise ValueError("Критерий должен быть объектом.")
        identifier = _text(criterion.get("id"), "ID критерия", 512)
        if identifier != identifier.strip() or identifier in seen:
            raise ValueError("Идентификаторы критериев должны быть уникальными и без пробелов по краям.")
        seen.add(identifier)
        _text(criterion.get("title"), "Название критерия", 500)
        _text(criterion.get("description"), "Описание критерия")
        if "section" not in criterion:
            criterion["section"] = "Общие критерии"
        _text(criterion["section"], "Секция критерия", 500)
        kind = criterion.get("type")
        if kind not in ("binary", "scale", "flag"):
            raise ValueError("Тип критерия должен быть binary, scale или flag.")
        weight = _number(criterion.get("weight"), "Вес критерия")
        maximum = _number(criterion.get("max"), "Максимум шкалы")
        if maximum <= 0:
            raise ValueError("Максимум шкалы должен быть больше нуля.")
        if type(criterion.get("allowNA")) is not bool:
            raise ValueError("allowNA должен быть логическим значением.")
        if criterion["allowNA"]:
            _text(criterion.get("naCondition"), "Условие N/A")
        elif "naCondition" in criterion and not isinstance(criterion["naCondition"], str):
            raise ValueError("Условие N/A должно быть текстом.")
        if kind in ("binary", "flag"):
            if maximum != 1:
                raise ValueError("Максимум binary/flag должен быть равен 1.")
            criterion.setdefault("allowedValues", [0, 1])
        if kind == "flag" and (weight != 0 or criterion["allowNA"]):
            raise ValueError("Флаг должен иметь вес 0, максимум 1 и запрещать N/A.")
        values = criterion.get("allowedValues")
        if not isinstance(values, list) or not 2 <= len(values) <= 1000:
            raise ValueError("allowedValues должен содержать от 2 до 1000 значений шкалы.")
        for value in values:
            _number(value, "Значение шкалы", maximum=maximum)
        if len(set(values)) != len(values) or 0 not in values or maximum not in values:
            raise ValueError("Значения шкалы должны быть уникальными и включать 0 и максимум.")
        if kind in ("binary", "flag") and set(values) != {0, 1}:
            raise ValueError("Для binary/flag допустимы только значения 0 и 1.")
    return result


def _import_payload(payload):
    try:
        if isinstance(payload, bytes):
            if len(payload) > MAX_IMPORT_BYTES:
                raise ValueError("Файл слишком большой. Максимальный размер — 5 МБ.")
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            if len(payload.encode("utf-8")) > MAX_IMPORT_BYTES:
                raise ValueError("Файл слишком большой. Максимальный размер — 5 МБ.")
            payload = json.loads(payload, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("JSON содержит неконечное число.")))
        else:
            serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            if len(serialized.encode("utf-8")) > MAX_IMPORT_BYTES:
                raise ValueError("Файл слишком большой. Максимальный размер — 5 МБ.")
    except (UnicodeError, json.JSONDecodeError, TypeError, OverflowError, RecursionError) as error:
        raise ValueError("Не удалось прочитать JSON. Проверьте синтаксис файла.") from error
    return payload


def normalize_import(payload, existing_ids=()) -> list[dict]:
    """Validate every record atomically, then skip existing/repeated IDs in input order."""
    payload = _import_payload(payload)
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        if payload.get("isSuccess") is False:
            raise ValueError("Источник вернул isSuccess: false. Такой ответ нельзя импортировать.")
        data = payload.get("data")
        if payload.get("isSuccess") is not True or not isinstance(data, dict) or not isinstance(data.get("records"), list):
            raise ValueError("Ожидается isSuccess: true и массив data.records.")
        records = data["records"]
    else:
        raise ValueError("Ожидается массив записей или объект ответа организаторов.")
    if not records:
        raise ValueError("В файле нет диалогов для импорта.")
    if len(records) > MAX_RECORDS:
        raise ValueError("За один раз можно импортировать не более 1000 диалогов.")
    normalized = []
    for index, record in enumerate(records, start=1):
        try:
            normalized.append(_normalize_record(record))
        except (ValueError, TypeError, OverflowError) as error:
            raise ValueError(f"Запись {index}: {error}") from error
    seen = set(existing_ids)
    unique = []
    for conversation in normalized:
        if conversation["id"] not in seen:
            seen.add(conversation["id"])
            unique.append(conversation)
    return unique


def normalize_organizer_dataset(payload: object, group_id: str) -> list[dict]:
    """Reject an inconsistent organizer envelope instead of silently deduplicating it."""
    payload = _import_payload(payload)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("isSuccess") is not True or not isinstance(data, dict):
        raise ValueError("Источник должен вернуть успешный ответ с данными набора.")
    records, total = data.get("records"), data.get("total_records")
    if data.get("dataset_group_id") != group_id:
        raise ValueError("Источник вернул другую группу.")
    if not isinstance(records, list) or type(total) is not int or total != len(records):
        raise ValueError("Количество записей не соответствует ответу источника.")
    conversations = normalize_import(records) if records else []
    if len(conversations) != total:
        raise ValueError("В наборе обнаружены повторяющиеся ID диалогов.")
    return conversations


def _normalize_record(record):
    if not isinstance(record, dict):
        raise ValueError("ожидается объект диалога.")
    raw_identifier = record.get("conversation_id")
    if raw_identifier is None:
        raw_identifier = record.get("id")
    identifier = _text(raw_identifier, "conversation_id", 512).strip()
    channel, language = record.get("channel"), record.get("language")
    if channel not in CHANNELS:
        raise ValueError("channel должен быть «agent chat» или «voice call».")
    if language not in LANGUAGES:
        raise ValueError("language должен быть RU, KZ или EN.")
    messages = record.get("transcript")
    if not isinstance(messages, list) or not messages:
        raise ValueError("transcript должен содержать хотя бы одну реплику.")
    transcript, times = [], []
    for index, message in enumerate(messages, start=1):
        if not isinstance(message, dict):
            raise ValueError(f"Реплика {index} должна быть объектом.")
        if message.get("speaker") not in SPEAKERS:
            raise ValueError(f"В реплике {index} неизвестный speaker.")
        text = _text(message.get("text"), f"Текст реплики {index}", MAX_IMPORT_BYTES)
        timestamp = _text(message.get("timestamp"), f"timestamp реплики {index}", 128)
        if channel == "voice call":
            parsed = _date(timestamp)
        else:
            if not CHAT_TIMESTAMP.fullmatch(timestamp):
                raise ValueError(f"timestamp реплики {index} должен быть MM:SS, секунды от 00 до 59.")
            minutes, seconds = timestamp.split(":")
            parsed = int(minutes) * 60 + int(seconds)
            if parsed > 2**53 - 1:
                raise ValueError("timestamp выходит за допустимый диапазон.")
        times.append(parsed)
        transcript.append({"id": f"{identifier}-m{index}", "timestamp": timestamp, "speaker": message["speaker"], "text": text})
    audio = record.get("audio_url")
    if audio is None:
        audio = record.get("audioUrl")
    if audio is None or isinstance(audio, str) and not audio.strip():
        audio = None
    else:
        if not isinstance(audio, str) or any(ord(char) < 32 for char in audio):
            raise ValueError("audio_url должен быть безопасной HTTP(S)-ссылкой или null.")
        try:
            parsed_url = urlsplit(audio)
            if parsed_url.scheme not in ("http", "https") or not parsed_url.hostname or parsed_url.username or parsed_url.password or "\\" in audio or any(char.isspace() for char in parsed_url.netloc):
                raise ValueError("unsafe URL")
            _ = parsed_url.port
        except ValueError as error:
            raise ValueError("audio_url должен быть безопасной HTTP(S)-ссылкой без логина и пароля.") from error
    event_date = record.get("date")
    if event_date is not None:
        _date(event_date, allow_date_only=True)
    elif channel == "voice call":
        earliest = min(range(len(times)), key=times.__getitem__)
        event_date = transcript[earliest]["timestamp"]
    elapsed = times[-1] - times[0]
    elapsed = max(0, math.floor(elapsed.total_seconds() if channel == "voice call" else elapsed))
    title = next((message["text"] for message in transcript if message["speaker"] == "client"), transcript[0]["text"])
    operator = record.get("operator")
    known_operator = isinstance(operator, str) and bool(operator.strip())
    operator = operator.strip() if known_operator else "Оператор не указан"
    return {
        "id": identifier, "title": title if len(title) <= 78 else title[:75] + "…",
        "operator": operator, "initials": "".join(part[0] for part in operator.split()[:2]).upper() if known_operator else "—",
        "language": language, "channel": channel, "date": event_date, "dateSource": "conversation",
        "duration": f"{elapsed // 60:02d}:{elapsed % 60:02d}", "transcript": transcript, "audioUrl": audio, "evaluation": None,
    }


def _validate_value(criterion, value):
    if value is None:
        if criterion["allowNA"]:
            return
    elif not isinstance(value, bool) and isinstance(value, (int, float)) and 0 <= value <= criterion["max"] and math.isfinite(value) and value in criterion["allowedValues"]:
        return
    raise ValueError(f"Недопустимое значение критерия «{criterion['title']}»: {'N/A' if value is None else value}.")


def _validated_results(evaluation):
    if not isinstance(evaluation, dict):
        raise ValueError("Оценка должна быть объектом.")
    _text(evaluation.get("id"), "ID оценки", 512)
    if evaluation.get("source") != "api":
        raise ValueError("Источник оценки должен быть api.")
    _date(evaluation.get("createdAt"))
    checklist = validate_checklist(evaluation.get("checklist"))
    criteria = {criterion["id"]: criterion for criterion in checklist["criteria"]}
    source = evaluation.get("results")
    if not isinstance(source, list) or len(source) != len(criteria):
        raise ValueError("Результат должен содержать ровно один ответ для каждого критерия.")
    results, seen = [], set()
    for item in source:
        if not isinstance(item, dict) or not isinstance(item.get("criterionId"), str):
            raise ValueError("Результат критерия должен быть объектом с criterionId.")
        identifier = item["criterionId"]
        if identifier in seen or identifier not in criteria:
            raise ValueError("Результат должен содержать ровно один ответ для каждого критерия.")
        seen.add(identifier)
        if "value" not in item:
            raise ValueError("В результате критерия отсутствует value.")
        _validate_value(criteria[identifier], item["value"])
        _text(item.get("explanation"), "Обоснование оценки")
        evidence = item.get("evidenceIds")
        if not isinstance(evidence, list) or any(not isinstance(identifier, str) or not identifier for identifier in evidence) or len(set(evidence)) != len(evidence):
            raise ValueError("evidenceIds должен быть массивом уникальных ID реплик.")
        if "critical" in item and type(item["critical"]) is not bool:
            raise ValueError("critical должен быть логическим значением.")
        result = deepcopy(item)
        result["critical"] = criteria[identifier]["type"] == "flag" and result["value"] == 1
        results.append(result)
    return checklist, results


def validate_evaluation(evaluation: dict, conversation: dict) -> dict:
    """Validate complete criterion coverage and every evidence reference against this dialog."""
    checklist, results = _validated_results(evaluation)
    if not isinstance(conversation, dict) or not isinstance(conversation.get("transcript"), list) or not conversation["transcript"]:
        raise ValueError("Для проверки доказательств требуется диалог с репликами.")
    message_ids = set()
    for message in conversation["transcript"]:
        if not isinstance(message, dict) or not isinstance(message.get("id"), str) or not message["id"] or message["id"] in message_ids:
            raise ValueError("Реплики диалога должны иметь уникальные ID.")
        message_ids.add(message["id"])
    for result in results:
        if not result["evidenceIds"]:
            raise ValueError("Для каждого критерия требуется доказательство или проверенный участок диалога в evidenceIds.")
        if any(identifier not in message_ids for identifier in result["evidenceIds"]):
            raise ValueError("Доказательство ссылается на несуществующую реплику диалога.")
    validated = deepcopy(evaluation)
    validated["checklist"], validated["results"] = checklist, results
    return validated


def score_evaluation(evaluation: dict, appeals=(), *, wire_decimals=False) -> dict:
    """Score a saved rubric with accepted version-matching decisions, never mutating inputs."""
    checklist, results = _validated_results(evaluation)
    criteria = {criterion["id"]: criterion for criterion in checklist["criteria"]}
    by_id = {result["criterionId"]: result for result in results}
    for appeal in appeals:
        if not isinstance(appeal, dict):
            raise ValueError("Апелляция должна быть объектом.")
        if appeal.get("status") != "accepted" or appeal.get("evaluationId") != evaluation["id"] or "newValue" not in appeal:
            continue
        identifier = appeal.get("criterionId")
        if not isinstance(identifier, str) or identifier not in criteria:
            continue
        criterion, result = criteria[identifier], by_id[identifier]
        _validate_value(criterion, appeal["newValue"])
        result["value"] = appeal["newValue"]
        result["critical"] = criterion["type"] == "flag" and result["value"] == 1
        decision = appeal.get("decision")
        if decision is not None and not isinstance(decision, str):
            raise ValueError("Решение по апелляции должно быть текстом.")
        result["explanation"] = decision or "Оценка изменена по принятой апелляции."
        result["applicability_reason"] = result["explanation"] if result["value"] is None else None
        # The accepted decision changes applicability, while the original snapshot remains immutable.
        for evidence in result.get("evidence", []):
            if result["value"] is None:
                evidence["kind"] = "not_applicable"
            elif evidence.get("kind") == "not_applicable":
                evidence["kind"] = "absence" if evidence.get("start_message_id") else "message"
    earned, possible = Decimal(0), Decimal(0)
    applicable, critical = 0, False
    with localcontext() as context:
        context.prec = 50
        for identifier, criterion in criteria.items():
            value = by_id[identifier]["value"]
            if criterion["type"] == "flag":
                critical = critical or value == 1
            elif value is not None and criterion["weight"] > 0:
                weight = Decimal(str(criterion["weight"]))
                earned += Decimal(str(value)) / Decimal(str(criterion["max"])) * weight
                possible += weight
                applicable += 1
        total = None if not possible else (earned / possible * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        breakdown = {}
        for identifier, criterion in criteria.items():
            value = by_id[identifier]['value']
            if criterion['type'] == 'flag' or value is None:
                continue
            weight = Decimal(str(criterion['weight']))
            points = Decimal(str(value)) / Decimal(str(criterion['max'])) * weight
            # Display-only rounding never feeds back into the total calculation.
            display = lambda number: format(number.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), 'f')
            breakdown[identifier] = {
                'weightedPoints': display(points),
                'normalizedWeight': display(weight / possible * 100 if possible else Decimal(0)),
                'contribution': display(points / possible * 100 if possible else Decimal(0)),
            }
    convert = (lambda value: format(value, "f")) if wire_decimals else float
    return {"total": None if total is None else convert(total), "earned": convert(earned), "possible": convert(possible), "applicable": applicable, "critical": critical, "results": results, "breakdown": breakdown}
