# Evaluation quality acceptance — 8 ноября 2026

## Решение

Реализованы offline-процесс экспертной разметки/сравнения, исключение неприменимых legacy-фактов **до сохранения job**, идентичность persisted/provider knowledge snapshot, criterion-level `unassessable → needs_review` без итогового балла, отдельный каталог источников и локальные аудиометрики. **Accuracy относительно скрытого эталона Freedom не установлена.** Реальные человеческие labels не предоставлены; ни DEV, ни синтетические ожидания не объявлены эталоном. Автоматическая дикция/интонация блокирована отсутствием audio input у GPT-6 Luna. Существующие DEV-результаты не пересчитывались и не повышались в статусе.

Платных API-вызовов: **0**. Другая AI-модель не подключалась. Не менялись `.env.local`, рабочая SQLite, DEV-результаты, app.py/repository.py/store, бюджетный модуль. После явного handoff PM изменены backend/evaluator.py, общие модели/codec, сгенерированные типы, UI types/viewmodels и только EvaluationHistory. Не выполнялись commit/push/publish и сообщения Telegram. Изменения других исполнителей сохранены.

## 1. Человеческий эталон

Модуль `evaluator/human_labels.py` и CLI `scripts/expert_evaluation.py` реализуют подготовку слепого пакета, строгий импорт человеческого JSON и сравнение сохранённого результата. Полный процесс и команды: [expert-labeling-workflow.md](../product/expert-labeling-workflow.md).

Фиксируются исходный разговор, происхождение real/synthetic, calibration/holdout, язык RU/KZ/EN, полный чек-лист, hash всего случая и hash рубрики. Пакет не содержит модельных ответов; repair_feedback запрещён. Дубли разговоров/текста и разные рубрики в одном отчёте отклоняются. Модельные predictions отделены и привязаны к той же версии; разные model/prompt/run в одном отчёте не смешиваются.

Для итогового человеческого эталона нужны два независимо работающих эксперта и третий арбитр. Импорт проверяет уникальность авторов и ID, наличие обеих исходных разметок, порядок времени, допустимые баллы, обязательные объяснения и ссылки на существующие сообщения. `unassessable` отделён от `assessed+null` (N/A). Подпись происхождения и независимость людей требуют внешнего аудита: сам JSON их не доказывает.

Отчёт даёт по языкам/типам источника/split и каждой паре экспертов:

- exact agreement и Cohen’s kappa по критериям;
- agreement критического признака и MAE итогового балла между экспертами;
- модель против adjudicated: расхождения по критериям, MAE/max error балла, TP/FP/TN/FN критических ошибок, precision/recall;
- число пар, пропуски предсказаний/арбитража и неоценимые критерии.

Нет labels — нет показателей точности. Нет положительных критических примеров — recall null. Unknown не превращается в N/A/0/правильный ответ. При N/A веса пересчитываются по применимым критериям; при unknown взвешенного критерия итоговый балл не сравнивается. Флаги имеют нулевой вес и сравниваются отдельно. Номинальная kappa не является weighted kappa; интервалы неопределённости и дизайн-эффекты выборки пока не реализованы.

Созданы три **синтетических** примера RU/KZ/EN и пустые шаблоны; никаких вымышленных подписей экспертов. [evaluation-unlabeled-baseline.json](evaluation-unlabeled-baseline.json) реально получен CLI без labels: `realAdjudicatedCases=0`, `accuracyClaim=not_established`, все сравнения отсутствуют. Этот файл демонстрирует корректный отказ от заявления accuracy, а не качество Luna.

## 2. Продуктовые основания и fail-closed

До исправления approved-файл KnowledgeFact содержал лишь `effective_date/product/jurisdiction/verified_by` без юрлица и конца периода, а EvaluationRequest v2 не содержал надёжной даты/компании/продукта разговора. Сервер передавал весь файл. Известная проблема: «approved» не означает «применимо к этому диалогу»; модель не должна определять это сама из языка, бренда или текста оператора.

В `backend/knowledge.py` добавлены `KnowledgeContext`, `ScopedKnowledgeFact`, `KnowledgeSelection`, `load_scoped_knowledge`, `select_applicable_facts`. Выборка выполняется детерминированно по точному совпадению юрлица/страны-юрисдикции/продукта и включению реальной даты разговора в доказанный интервал. Нет контекста, экспертного допуска, receipt, одной из границ, доказательства периода или совпадения scope — нет runtime-факта. Присутствуют ID и причины исключения; содержание приватных фактов не попадает в ошибки загрузки.

**Активная защита:** `EvaluationService.create` вызывает `evaluation_knowledge` до INSERT evaluation_jobs. Legacy-факты исключаются с `knowledge_context.status=excluded`, причиной `legacy_scope_unverified` и количеством исключённых записей. При отсутствии файла сохраняется `not_configured`. Новый `KnowledgeAudit` фиксирует версию политики, причину, scope, ID допущенных фактов и число исключённых. HTTP mock проверяет равенство **всего** сохранённого запроса и provider input, а не только knowledgeFacts. Адаптер больше не молча очищает вход: несовместимый detached request отклоняется до reserve/HTTP с `KNOWLEDGE_CONTEXT_INVALID`. Старые queued jobs с legacy-фактами очищаются и сохраняются до отправки; completed записи не затрагиваются.

`PROMPT_VERSION` — `openai-cqa-v2-wire2-na3-knowledge-review2`; fingerprint включает фактический запрос и knowledge audit. Источники — доказательства, не инструкции. Промпт не выводит продуктовую ошибку/правильность из неподтверждённых утверждений, требует явное воздержание по затронутому критерию и сохраняет возможность оценивать наблюдаемые действия.

### Точный интерфейс для владельца backend

```python
records = load_scoped_knowledge(path)  # tuple[ScopedKnowledgeFact, ...]
context = KnowledgeContext(
    entity=confirmed_legal_entity,
    jurisdiction=confirmed_country_and_regime,
    product=confirmed_product,
    conversation_date=actual_conversation_date,  # YYYY-MM-DD, never import date
    date_source="conversation",
    verified_by=metadata_owner,
    verification_reference=metadata_receipt,
)  # use None if any applicability field lacks independent confirmation
selection = select_applicable_facts(records, context)
# selection.facts: tuple[KnowledgeFact, ...]
# selection.included_ids: tuple[str, ...]
# selection.excluded: tuple[{"id": str, "reason": str}, ...]
```

Hook уже встроен в `backend/evaluator.py`. Для положительного scoped-пути сервис принимает `scoped_knowledge_facts=records`, `knowledge_contexts={(conversation_id, content_hash): context}`. Связь с hash обязательна: подтверждение старой версии разговора не применяется к новой с тем же ID. Настройки копируются, каждый job получает собственный неизменный снимок. Интеграционный mock проверяет допуск синтетического экспертно подтверждённого факта, совпадение snapshot/payload, исключение stale scope и старого queued legacy-контекста.

В приложении пока нет источника подтверждённых metadata и approved catalog: app.py не изменялся, действующий legacy-файл **безопасно исключается**. Чтобы активировать реальные scoped facts, владелец API должен загрузить утверждённый каталог и отдельно подтверждённые `entity`, `jurisdiction`, `product`, `conversation_date`, `date_source=conversation`, `verified_by`, `verification_reference`, затем передать два новых аргумента сервису. Это не блокирует работающую защиту; положительная конфигурация ожидает реальных оснований. Дата импорта и временá отдельных реплик не заменяют дату разговора. Alias-таблицу юрлиц должен утверждать эксперт; fuzzy matching недопустим.

Для каждой записи каталога обязательны `id/text/source/source_locator/source_published_at/observed_at/entity/jurisdiction/product/valid_from/valid_to/validity_evidence/review_status/verified_by/approval_reference/limits`. Для `expert_approved` обязательны обе границы, доказательство интервала, эксперт и receipt. `source_verified` — только подтверждение прочитанного источника. Каталог и legacy-массив различаются и не взаимозаменяемы.

Расхождение сохранённого knowledge-контекста и provider input устранено. Legacy passthrough-тесты заменены проверками исключения до записи, устойчивости после restart и ремонта ответа. Реальная проверка личности эксперта/receipt и достоверности метаданных принадлежит владельцу источника; строка `verified_by` сама по себе не является криптографическим доказательством.

Контрактный пробел воздержания закрыт: `ModelCriterionResult.assessment_status` (`assessed` по умолчанию / `unassessable`) и `review_reason`. Неоценимый результат требует `value=null`, `applicability_reason=null`, непустую review_reason; N/A evidence запрещено. Структурная ошибка вызывает стандартный repair, но валидное воздержание — `CRITERION_UNASSESSABLE` и сразу `needs_review`, без повтора/выдуманного значения. Ссылки на сообщения продолжают проверяться; неизвестные ID остаются отдельным review reason. Остальные критерии могут иметь обычные подтверждённые значения: пустой справочник не превращает всё в N/A.

Codec передаёт marker/reason, Score и completed EvaluationResult запрещают неоценимые критерии. `needs_review` не записывается в таблицу подтверждённых evaluations, wire score равен null, официальный export возвращает `status=error`. Старые записи без новых полей трактуются как assessed без изменения исходного storage; отдельный тест проверяет совместимость. Корректная цитата не доказывает истинность продуктового утверждения: выбор момента воздержания остаётся семантическим качеством модели и требует реальной экспертной валидации, но marker и безопасный маршрут теперь исполняются кодом.

По отдельному запросу PM в AppealDTO добавлены nullable `createdBy/decidedBy`; codec передаёт реальные значения из repository, для старых записей — null, без вымышленных авторов. Запись actor_id в repository выполняет security-владелец.

После отдельного UI handoff реализован mapping `assessment_status → assessmentStatus`, `review_reason → reviewReason` в viewmodels, с обратной совместимостью `assessed/null`. EvaluationHistory показывает «Недостаточно данных» и причину для unassessable, настоящий N/A остаётся N/A. Клиент также скрывает stale total, если некорректный completed ответ содержит unassessable. Два JS-теста проверяют эти границы. Для ConversationDetail PM передал тот же контракт другому владельцу; этот файл здесь не менялся.

### Проверка источников

[knowledge-reviewed.json](../product/knowledge-reviewed.json) содержит четыре подтверждённых наблюдения официальных источников, **0 expert_approved/runtime-фактов**. Это полезная база оснований для эксперта, а не искусственно «зелёный» справочник. [knowledge-sources.md](../product/knowledge-sources.md) дополнен результатами повторной проверки.

| Источник | Что подтверждено | Что не подтверждено |
| --- | --- | --- |
| [FAQ отчёта](https://fbroker.kz/faq/question/how-to-obtain-a-broker-s-report-3814) | Содержание отчёта по счёту, выбираемый период; сайт указывает FFG PLC, Казахстан/МФЦА | Исторический период действия, комиссия/тариф конкретного клиента, принадлежность DEV |
| [FAQ изменения данных](https://fbroker.kz/faq/question/how-can-i-update-my-data-3815) | Мобильный/веб-пути различаются; описание выполнения поручения | Версия интерфейса в старом разговоре; обязанность перечислять полный процесс при вопросе о разделе |
| [Регламент с 10 июня 2025](https://fbroker.kz/en/news/vstuplenie-v-silu-novoy-redakcii-reglamenta-i-prilozheniy-k-nemu-) | Датированное объявление об изменении для неторговых субсчетов D-счёта | Срок дальнейшего действия услуги, доступность конкретному клиенту, текущие условия |
| [FRHC Financial Information](https://ir.freedomholdingcorp.com/financial-information) | Отдельный индекс корпоративной отчётности холдинга | Эквивалентность клиентскому брокерскому отчёту, конкретные финансовые цифры |

У FAQ датой обновления указано 22.09.2026, но это не effective_from. Для исторического объявления в каталоге намеренно оставлен узкий день события, а не бессрочный тариф. Повторное открытие [тарифа](https://fbroker.kz/faq/question/how-to-find-out-your-tariff-2709) и [1042-S](https://fbroker.kz/faq/question/what-is-1042-s-form) завершалось timeout; прежние кандидаты не повышены в статусе. Современные ставки не применены к старым диалогам. Нет подтверждений причин блокировки позиций, индивидуальных комиссий, торгуемости или полномочий менеджера.

## 3. Аудио

[Официальная карточка GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna), реально открытая 8 ноября, прямо обозначает audio как неподдерживаемую модальность. Общий список endpoints на странице не меняет этого. [Audio and voice](https://developers.openai.com/api/docs/guides/audio) описывает отдельные модели/маршруты. Подмена на другой платный AI не выполнена.

`evaluator/audio_metrics.py` измеряет локальный PCM-сигнал и может вычислить слова/минуту по проверенным человеком интервалам оператора. Проверены численные значения RMS синусоиды, тишина, два канала с противоположной фазой, граница полного масштаба, неполное окно, повреждённый/неподдерживаемый WAV, временные границы и отсутствие доказанных word timings. Поля оценок голоса/дикции остаются null. [Контракт аудио](../product/audio-evaluation-contract.md) описывает формат и ограничения.

Реальное аудио в этой задаче не анализировалось: нет проверенной привязки файла/говорящего/слов и экспертной рубрики дикции. Модуль не интегрирован в API/UI/scoring. **Уточнённая цель пользователя — автоматическая оценка каждого звонка без операционного ручного прослушивания; ручной tempo helper её не закрывает.** Независимая экспертная разметка всё равно нужна для валидации автоматической системы. Другой исполнитель исследует варианты в docs/product/automatic-voice-options.md. Ограничение только Luna не отменено; другая нейромодель не добавлена. Сигнальные эвристики не объявлены объективной дикцией/интонацией.

## Проверки

Offline-проверки запускаются через Python 3.11, никаких сетевых запросов к модели в них нет. MockTransport заменяет HTTP-провайдера. Обычный ограниченный запуск зависал в Windows `socket._fallback_socketpair` при создании asyncio loop, до адаптера; стек зафиксирован faulthandler. Повторный запуск с разрешённым внутренним локальным соединением выявил ожидаемую несовместимость старого теста wire payload, который требовал передавать весь legacy-файл. Проверка обновлена под fail-closed поведение.

Финальные выполненные проверки перед code freeze:

| Команда | Результат |
| --- | --- |
| `node scripts/python.mjs -m pytest -q --basetemp=.pytest-evaluation-quality --tb=short --maxfail=3 -o faulthandler_timeout=40` | **623 passed, 35 skipped**, 127.92 s. Пропуски — необязательные enterprise jwt/msal dependencies. Все оценочные/knowledge/audio/mock-проверки выполнены. |
| `node --test src/services/contracts.test.mjs src/features/batchPlan.test.mjs` | **22 passed**, включая N/A vs unassessable и подавление stale total. |
| `node scripts/python.mjs scripts/generate_contracts.py --check` | **41 contract artifacts checked**, согласованы с актуальными моделями, включая batch и audit identity. |
| `npm run build` | TypeScript и Vite build завершились успешно. |
| `git diff --check` | Exit 0; только уведомления Git о переводе LF/CRLF. |

Первый полный прогон был остановлен: старый бюджетный тест ожидал HTTP при теперь отклоняемом unscoped input. Его fixture очищен от не относящихся к бюджету product facts и ожидание ограничено пятью секундами; budget.py не изменялся. В тестах restart первый TestClient закрывается до второго backend, чтобы не обходить новый single-worker lock. Повторный полный прогон выше завершён успешно. Рабочая база не использовалась; pytest-папка отдельная, HTTP только MockTransport. Независимый QA PM может выполнить финальный suite после завершения параллельных работ.

## Изменённые файлы этого исполнителя

- `evaluator/human_labels.py`, `scripts/expert_evaluation.py` — импорт/пакеты/сравнение.
- `evaluator/fixtures/annotation-cases.synthetic.json`, `evaluator/fixtures/annotation-labels.blank.json` — три синтетических случая и незаполненные шаблоны.
- `evaluator/audio_metrics.py` — отдельный локальный модуль.
- `backend/knowledge.py`, `backend/evaluator.py` — scoped-контракт, loader, selector, сохранение безопасного контекста и встроенный маршрут review.
- `backend/contracts/v2/models.py`, `backend/contracts/v2/codecs.py` — статус критерия, knowledge audit и поля авторства апелляций; JSON Schema/TypeScript обновлены только генератором, включая чужие batch-контракты.
- `evaluator/openai_v2.py`, `evaluator/prompt.py` — fail-closed отправка и прозрачные ограничения.
- `tests/test_human_labels.py`, `tests/test_audio_metrics.py`, `tests/test_scoped_knowledge.py`, `tests/test_assessment_review.py`, `tests/test_openai_v2.py`, `tests/test_budget.py`, `backend/tests/test_evaluation_context.py` — offline проверки.
- `src/domain/types.ts`, `src/services/viewmodels.ts`, `src/services/contracts.test.mjs`, `src/features/EvaluationHistory.tsx` — исключительно согласованный marker/reason mapping и отображение истории.
- Сгенерированные `src/generated/contracts.ts` и `contracts/v2/{AppealDTO,ConversationDTO,ConversationExport,ConversationPage,ConversationScores,CriterionResult,EvaluationRequest,EvaluationResult,ModelCriterionResult,ModelResponse,Score,WorkspaceData,WorkspaceState,KnowledgeAudit,KnowledgeScope}.schema.json`, `contracts/v2/index.json`. Генератор сохранил уже добавленные другим исполнителем batch artifacts.
- `docs/product/knowledge-reviewed.json`, `docs/product/knowledge-sources.md`, `docs/product/expert-labeling-workflow.md`, `docs/product/audio-evaluation-contract.md`.
- `docs/audit/evaluation-unlabeled-baseline.json`, этот отчёт.

## Поиск и отбор подходов

Прочитаны локальные find-skills, llm-evaluation, prompt-engineering-patterns, openai-docs и verification-before-completion. Выполнен целевой поиск leaderboard/skills.sh по llm-evaluation и audio signal analysis; попытка `npx --no-install skills find llm-evaluation` завершилась ENOTFOUND registry.npmjs.org, без установки. Поиск продолжен через доступный веб-поиск.

[wshobson/agents llm-evaluation](https://www.skills.sh/wshobson/agents/llm-evaluation) соответствует локальному материалу и тематике (каталог показывал около 10.8K установок/39.4K звёзд); использованы идеи человеческого эталона, раздельных метрик и прозрачных источников. Примеры библиотек/моделей из skills не скопированы и не меняют ограничение Luna. Локальный prompt skill применён для структурированных выходов и явного разделения доказательств/инструкций.

[audio-analysis](https://www.skills.sh/bbeierle12/skill-mcp-claude/audio-analysis) просмотрен: пример браузерного FFT на Tone.js, около 146 установок/9 звёзд; не решает экспертную оценку дикции и не подходит Python backend. Decentraland audio-analysis предназначен для визуализаторов сцены; найденный call recording analyzer имел несоответствующее название/минимальное распространение. Они не установлены. Стандартные локальные численные измерения с известными тестовыми сигналами лучше соответствуют разрешённой узкой задаче.

## Что остаётся для приёмки владельцем

1. Реальные слепые expert A/B/C labels и подтверждённая выборка RU/KZ/EN; скрытого эталона нет. До этого качество модели остаётся неустановленным.
2. Scope/дата/юрлицо разговоров, архивные правила нужного периода, экспертные approval receipts. Backend hook и versioned provenance реализованы; app-конфигурация положительного scoped-пути ждёт реальных данных. Пока legacy runtime-факты исключены.
3. History и mapping уже показывают `unassessable` как «Недостаточно данных» с причиной; ConversationDetail интегрирует другой владелец по переданным полям. Требуется реальная проверка того, как часто Luna выбирает воздержание правильно.
4. Автоматическая дикция/дополнительные модели **отложены пользователем**. Разработка этой ветки остановлена; уже созданные локальные signal/tempo helpers не подключены к runtime и не являются оценкой голоса.

Code freeze этого исполнителя: после указанных проверок только финализирована документация. Полноценная продуктовая accuracy не объявляется; автоматические тесты подтверждают исполнение контрактов и защитных границ.
