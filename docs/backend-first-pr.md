# Backend: первый сквозной сценарий

Источник wire-контракта — `contracts/v1.py` из main cb676f2. Переиспользован FastAPI/SQLite backend из feat/ui 4953e82. Код провайдеров и апелляции для новых контрактных оценок в этот PR не входят.

## Что работает

- Импорт через существующий `POST /api/import` (`{"text":"JSON-массив либо ответ организаторов"}`); исходный порядок, роли и timestamps сохраняются. ID `conversation-id-m1` включают bot/system.
- Полный неизменяемый Checklist v1 сохраняется `POST /api/checklists`, следующая версия — `POST /api/checklists/{id}/versions`. Существующий редактор сохраняет совместимость через явный mapper.
- `POST /api/evaluations` принимает только `conversation_id`, `checklist_id`, `checklist_version`, возвращает HTTP 202 + queued EvaluationJob. `GET /api/evaluations/{id}` возвращает job envelope; export — тот же envelope.
- Адаптер вызывается через `Evaluator.evaluate(EvaluatorInput) -> AdapterResult`. Backend повторно проверяет типы, покрытие критериев, ступени, N/A, ссылки и цитаты; использует только серверную арифметику Decimal.
- Сохраняются исходный transcript, полный чек-лист, решения модели отдельно от расчёта, engine_version, provenance и исходный/финальный снимки. Смена текущего transcript не переписывает старый job.
- Неизвестное доказательство означает needs_review, confirmed total=null и отдельно provisional_total_score. UI явно показывает предварительный статус. Ошибочные значения означают failed без оценки.
- Повтор идентичного AdapterResult при одинаковых входах и версии движка повторно использует сохранённый снимок. Это идемпотентность сохранения, не обещание детерминизма LLM и не кэш запроса к провайдеру.
- Очередь имеет две одновременно выполняемые задачи. Состояния сохраняются; незавершённые задачи после перезапуска становятся failed с retryable=true. Самопроизвольного повторного платного вызова нет.

## Структура и хранение

`backend/domain/scoring.py` — чистые dataclass-типы, реестр стратегий и Decimal-расчёт, без Pydantic/БД/HTTP. `backend/application/` — валидация через общий контракт, порт адаптера и use case. `backend/infrastructure/evaluations.py` — адаптер к существующим SQLite-транзакциям. `backend/api/evaluations.py` — HTTP. Прежний `backend/domain.py` перенесён в пакет с сохранением импортов; старые операции сохранены.

Новые таблицы добавляются без удаления старых: `checklist_snapshots` (полное определение, hash и fingerprints), `transcript_snapshots`, `evaluation_jobs` (исходный EvaluatorInput и состояние), `evaluation_snapshots` (ответ модели, результат, версия движка). Снимки не обновляются; будущие решения проверяющего должны добавляться отдельными записями.

## Запуск и проверка за две минуты

Python 3.11, uv, Node.js 22+, npm, make. `make setup` устанавливает зафиксированные Python-зависимости и npm lock. `make test` запускает backend и контрактные тесты; `make build` собирает интерфейс; `make run` запускает сервер на http://127.0.0.1:8000.

Синтетические JSON-примеры находятся только в `backend/tests/fixtures/`. После `make run`:

```bash
curl -sS http://127.0.0.1:8000/api/health
curl -sS -H 'Content-Type: application/json' --data-binary @backend/tests/fixtures/import-request.json http://127.0.0.1:8000/api/import
curl -sS http://127.0.0.1:8000/api/checklists
curl -sS -H 'Content-Type: application/json' --data-binary @backend/tests/fixtures/evaluate-request.json http://127.0.0.1:8000/api/evaluations
# После получения HTTP 202 подставьте настоящий id из ответа:
curl -sS http://127.0.0.1:8000/api/evaluations/JOB_ID
```

Без переданного действующего адаптера последний POST честно возвращает 503 PROVIDER_UNAVAILABLE, evaluationAvailable=false. Это не демонстрационный оценщик. Автоматический тест `test_full_http_pipeline_restart_and_idempotence` проходит весь поток с тестовым адаптером, включая 85.38, БД и рестарт. Он не подтверждает доступность провайдера или точность модели.

## Подключение Adalbala

В точке сборки приложения передать экземпляр согласованного `Evaluator`:

```python
app = create_app(
    evaluator=adapter,
    knowledge_context=verified_product_facts,
    prompt_version=prompt_version,
    knowledge_version=knowledge_version,
)
```

`backend/main.py` пока запускает приложение без адаптера. Его импорты и выбор модели завершает AI-поток после добавления своего модуля. API-ключ остаётся в локальном `.env`/`.env.local` backend и не передаётся браузеру; наличие ключа само по себе не подключает оценщик.

Адаптер владеет единым бюджетом максимум двух HTTP-вызовов провайдера, включая repair/retry, и возвращает суммарный usage/cost. Backend вызывает evaluate ровно один раз; независимого внешнего retry нет. Типизированные ошибки: `ProviderFailure("PROVIDER_RATE_LIMIT", retry_after_seconds=...)`, `PROVIDER_UNAVAILABLE`, `MODEL_REFUSAL`, `MODEL_OUTPUT_INVALID`. Неизвестные исключения скрываются за INTERNAL_ERROR без исходного текста. Тайм-аут backend по умолчанию 120 секунд, настраивается в create_app.

## Решения и проверка

| Решение | Причина | Проверка |
|---|---|---|
| Реестр binary/scale/flag | Движок не зависит от ID критериев | test_strategy_registration_is_instance_local |
| Шкалы из данных | Новая версия меняет допустимые значения адаптера | test_api_saves_new_scale_and_delivers_it_to_adapter |
| Неизменяемые версии и fingerprints | Сохранить исторический смысл оценки | test_version_fingerprints_and_old_input_are_immutable |
| Общий список ошибок контракта | Все структурные ошибки видны вместе | test_structural_errors_block_scores; test_contracts.py |
| Проверка цитат по исходному сообщению | Выдуманное доказательство не подтверждает итог | test_unverified_evidence_never_becomes_confirmed_total |
| Диапазон/причина N/A | Поддержать отсутствие действия и неприменимость | test_absence_range_inclusive; test_na_is_valid_only_with_condition_reason_and_reference |
| Снимок результата и transcript | Не изменять прежние оценки при новом импорте | test_changed_input_keeps_original_job_and_transcript |
| Decimal-строки и HALF_UP | Одна серверная арифметика | test_control_example_and_sections; test_half_up_and_null_denominator |
| Идемпотентность сохранения | Не создавать повторный снимок одного результата | test_full_http_pipeline_restart_and_idempotence |

Property-based тест проверяет диапазон 0..100/null, детерминизм, перестановку критериев, N/A и максимум. Только синтетические данные; результаты тестов не являются измерением совпадения с Ground Truth.

## Уточнения относительно исходного промпта

- Контракт имеет приоритет: binary, integer/null, ID строкой с нумерацией с 1; weight и расчётные поля — строки. Код ошибок также берётся из contracts/v1.py.
- Цитата проверяется как точная подстрока по контракту; NFKC/casefold из раннего промпта не применяется. Казахские буквы сохраняются.
- Непроверенное доказательство блокирует подтверждённый итог; предварительное число хранится отдельно. Все значения не приводятся из строк, float или bool к int.
- Секции нормируются внутри секции до 100. Это соглашение команды, не дополнительное правило организаторов; проценты секций не складываются.
- Wire v1 ограничен тремя типами. Добавление внутренней стратегии не означает автоматического расширения опубликованной JSON Schema.
- `/api/state` остаётся совместимым UI-envelope, новый подтверждённый источник результата — вложенный wire_evaluation и canonical GET job. UI mapper преобразует числа только для отображения. Существующие записи старого формата остаются в SQLite; новый GET evaluations обслуживает только контрактные jobs. Историю legacy-оценок можно найти в старых таблицах, автоматическая миграция в v1 не заявляется.
- Апелляции новой схемы, решения проверяющего, частичный пересчёт и кэш LLM-вызовов — следующий PR. Кнопка апелляции для новых v1 результатов отключена до реализации; прежние UI-операции не удалены.
- Приложение рассчитано на один локальный процесс. Авторизацию, несколько воркеров и публичное размещение этот PR не добавляет.

Открытая интеграционная зависимость: фактический адаптер Adalbala и выбранная модель. После его подключения нужен отдельный реальный сквозной прогон и проверка точности на ручной разметке.
