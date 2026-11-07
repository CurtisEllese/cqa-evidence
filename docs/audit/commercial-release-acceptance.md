# Freedom Quality — независимая приёмка итогового стека

Дата: 8 ноября 2026, Asia/Qyzylorda. **QA gate локальной передачи: PASS. Установленный dist совпадает с независимо проверенной сборкой; сохранность данных подтверждена. Открытых F01–F04 нет.** Это не заявление готовности коммерческого внедрения в Freedom. Продуктовый код QA не изменял.

## Статусы поставки

| Область | Статус | Доказательство / ограничение |
|---|---|---|
| Локальная версия | **PASS, передача завершена** | 8 независимых player/date + 6 UI-integration групп, combined API local/entra, live список на 8000; dist побайтово совпадает с независимой сборкой; данные/ledger сохранены |
| Корпоративный API | **Offline acceptance PASS** | Финальный набор 209 passed / 0 skipped включает все 95 crypto/access/Dataverse тестов; combined API проверяет auth + batch + review + protected media |
| Реальный SSO / Dynamics | **NOT VERIFIED** | Нет tenant/environment/registrations; mock-токены не доказывают реальный вход. Dynamics — adapter-only, без iframe и customer data mapping |
| Operator workflow | **BLOCKED: ownership mapping** | Нет доверенной привязки tenant/object IDs к разговорам и фильтрованных projections; сервер закрывает доступ, UI объясняет ограничение |
| Factual accuracy | **NOT ESTABLISHED: no independent GT** | Workflow разметки не является экспертной разметкой Freedom; отсутствуют независимые реальные labels и утверждённый runtime-каталог scoped facts |
| Автоматическая дикция / новые речевые модели | **ОТЛОЖЕНО ПОЛЬЗОВАТЕЛЕМ** | Направление остановлено. Новые speech-модели не устанавливались; PCM/tempo helpers не объявляются автоматической оценкой дикции |

## Итоговая проверка после code freeze

| Проверка | Результат | Evidence |
|---|---|---|
| Независимый финальный targeted Python | **209 passed in 32.05s; 0 failed, 0 skipped; exit 0** | `tmp/qa-release/pytest.txt`, `pytest-result.json`; `blockedHTTPCalls=[]`, `changedDuringRun=[]` |
| Все текущие JS test files | **35 passed, 0 failed, 0 skipped** | `tmp/qa-release/js-tests.txt`; contracts, batchPlan, conversationTitle, auth/media |
| Генерация контрактов | **41 artifacts checked** | `scripts/generate_contracts.py --check`, exit 0; generated файлы не переписывались |
| Типы и production build | **PASS** | `tsc --noEmit` на frozen snapshot; Vite 1766 modules, отдельный `tmp/qa-release/build`, включая auth-redirect bridge |
| Независимый плеер/date | **8 групп PASS** | `tmp/qa-release/independent-results.json`, ноль page errors / unexpected requests |
| Независимая UI-интеграция | **6 групп PASS** | `tmp/qa-release/integration-results.json`, ноль page errors / unauthorized / unexpected requests |
| Combined actual API | **local + entra PASS** | `tmp/qa-release/api-results.json`: четыре synthetic adapter calls, строго один на разговор; disposable SQLite |
| Реальный обновлённый localhost:8000 | **2 read-only группы PASS** | `tmp/qa-release/live-results.json`: без API stubs, ошибок страницы, HTTP >=400 и запрещённых запросов; desktop/375px |

Финальный targeted набор включает `test_enterprise_access.py`, `test_dynamics_adapter.py`, `test_batches.py`, `test_evaluation_context.py`, `test_assessment_review.py`, `test_scoped_knowledge.py`, `test_human_labels.py`, `test_audio_metrics.py`. Crypto не пропущены: использованы проектные PyJWT/cryptography/MSAL из уже подготовленного `tmp/enterprise-security-deps`. Глобальная среда не менялась. Выбор тестов сохранён в `pytest-result.json`; команда — `.venv311/Scripts/python.exe -X utf8 -B tmp/qa-independent-batch/run_full.py` с указанными восемью путями. Несмотря на имя harness, этот финальный запуск — **targeted subset**, не полный suite.

### Полный общий suite: проверенное авторское evidence

Независимо прочитаны **сырые command outputs** завершённого security-чата, сохранены в `tmp/qa-release/author-full-suite-evidence.txt`. Полный suite: **657 passed, 1 failed, 0 skipped in 130.92s**. Единственный сбой — Hypothesis `FailedHealthCheck`: медленная генерация входов (3 valid / 1.80s), а не несовпадение расчёта. Только этот неизменённый тест повторён с тем же seed `247958206421654271502060309455306256797`: **1 passed in 0.85s**. Health check не отключался. Нельзя описывать это как «658 прошли одним запуском»; остаётся наблюдение о чувствительности runtime property-test к нагрузке.

Quality-владелец дополнительно получил 623 passed / 35 skipped: optional corporate dependencies отсутствовали в его окружении. Пропуски не оставлены в security acceptance: независимые 95 и финальные 209 выполнялись с библиотеками, без skips. По согласованию PM ещё один полный suite после авторских прогонов не повторялся; финальная независимая проверка сосредоточена на изменённых областях и совместном поведении.

Первая независимая попытка полного запуска остановилась на collection: прямой Python без UTF-8 прочёл fixture как cp1251. Это **ошибка команды QA harness**, не регрессия продукта. Она сохранена в `initial-encoding-error.txt/json`, затем исправлен запуск и успешно выполнен финальный 209-test subset. Fixture/product files ради кодировки не изменялись.

## Что именно принято

- **F01–F03 закрыты:** late waiting после исходного watchdog получает ограниченный retry; retry восстанавливает 7 секунд без autoplay; canplay снимает ожидание. Import-only/unknown date не выдаётся за дату разговора; date-only не создаёт 05:00; ISO timestamp использует Asia/Qyzylorda.
- **F04 закрыт независимым probe:** PROVIDER_UNAVAILABLE и timeout сохраняются как uncertain/non-retryable; новый batch отклоняется; ровно один stub-вызов. Повтор alias с другим составом отклоняется. История — `commercial-review-findings.md`, данные — `tmp/qa-independent-batch/repro-results.json`.
- **Плеер:** PCM metadata/play/pause, Space/Enter/Arrow, mute/volume, seek до metadata, out-of-range, 503/retry, cleanup и отсутствие autoplay. На 320/375 px controls не меньше 44px; focus проверен настоящей клавиатурой. Таблица прокручивается горизонтально, правое действие достижимо.
- **Unassessable:** в карточке и истории «Недостаточно данных» с причиной; отдельно проверен flag-критерий без выдуманного нарушения. Нет N/A, итогового балла или подтверждённого экспорта для unassessable assessment. Настоящий N/A сохранён. «Оценить заново» и выбор версии раскрыты. Мобильные карточка и история визуально просмотрены (`missing-card-mobile.png`, `review-history-mobile.png`).
- **Auth/media:** реальная production-сборка и фактическая инициализация MSAL не запрашивают workspace до входа; invalid config закрыт. В отдельной auth-сборке заменён **только SDK** для synthetic sign-in: реальные AuthGate/auth/authMedia/AudioPlayer выполняют bearer fetch → blob, seek/play, revoke при смене звонка и истечении сессии. После token failure workspace размонтируется, blobs освобождаются, anonymous audio request отсутствует. Это проверка композиции, не реального Microsoft login.
- **Combined API:** реальные handlers/middleware/repository/worker на temp DB, injected verifier/adapter/media boundary. Anonymous media/export/batch/state → 401; operator без mapping → 403; reviewer получает audio и Range. Queue получает два разговора, повторный POST возвращает тот же batch, calls не дублируются. Needs-review/no-score сохраняется в state/history, snapshot содержит пустой knowledgeFacts и `knowledge_context.status=not_configured`.
- **Scope/audit:** targeted tests проверяют entity/jurisdiction/product/date/approval, исключение неподтверждённого legacy knowledge до persistence/provider, соответствие snapshot и отправки, отказ старому content hash, независимые labels/holdout limits. Actor audit атомарно сохраняется из verified principal, DTO возвращает createdBy/decidedBy; spoof fields отвергаются, audit failure откатывает мутацию. Это не подтверждает реальные тарифы или личности экспертов.
- **После restart:** настоящий список открывается без stubs; `/api/auth/config` сообщает local/no-registration, `/api/batches/current` и health отвечают 200. Нет page errors, ответов >=400, внешних запросов или audio download. Реальные карточки звонков не открывались, чтобы не загружать внешнее media.

## Снимок, воспроизводимость и расходы

`tmp/qa-release/snapshot-hashes.json` содержит hashes проверенного frontend. После проверок сравнение с рабочими исходниками дало `changedSinceBrowserSnapshot=[]`. Python runner сохранил backend/evaluator/tests hashes и `changedDuringRun=[]`. Production и SDK-mock сборки находятся в отдельных build/auth-build; shared dist QA не менял. Mock SDK никогда не переносился в product source/build.

QA использовал пустые model/organizer config и HTTP guards, synthetic PCM, MockTransport/injected callbacks и disposable SQLite. Browser fixtures блокируют сторонние адреса и изменения; live browser разрешал только GET/HEAD к 127.0.0.1:8000, запрещал audio. **Платных модельных вызовов этим QA — 0.** Четыре вызова combined API — локальный synthetic function. Сохранность общего ledger до/после restart отдельно документирует release-владелец.

### Закрытие установленного rollout

Прочитан [отчёт локальной передачи](local-rollout-2026-11-08.md) и исходный `tmp/local-rollout/final.json`. В нём: `all_saved_tables_unchanged=true`, `budget_unchanged=true`, 65 разговоров/65 completed, 71 бюджетная попытка, **$0.09511375**, DEV JSON и usage journal неизменны, paid calls during rollout = **0**. Release-владелец создал согласованные snapshots через SQLite Backup API, штатно остановил старый процесс и оставил новый PID 33988 / session 38759 на 127.0.0.1:8000.

Отставший общий dist пересобран без правок исходников. **QA самостоятельно сравнил каждый файл своей независимо проверенной production-сборки с dist по SHA-256: `distDifferentFromIndependentBuild=[]`.** После пересборки повторён собственный read-only live browser: PASS, без ошибок/изменений/аудио. Дополнительно release-владелец подтвердил 13 browser-групп, включая реальное аудио; крайние даты и unassessable у него проверены browser-only GET fixtures. Это не следует описывать как отсутствие любых fixtures: отсутствовали compatibility stubs auth/batch, рабочая БД не менялась. Итого установленный интерфейс соответствует принятому снимку, финальный локальный gate закрыт.

Изменены QA только `docs/audit/commercial-acceptance-plan.md`, `commercial-review-findings.md`, этот отчёт и harness/fixtures/logs/screenshots под `tmp/qa-*`. Рабочая БД, DEV, `.env.local`, продуктовый код и Git QA не менялись. Release/restart выполняет другой уполномоченный исполнитель.

## Остающиеся ограничения

Реальный tenant SSO/Dataverse, ownership mapping, human GT, утверждённые исторические продуктовые факты и эксплуатационное развёртывание не приняты. Автоматическая дикция отложена пользователем. Production browser-снимок не доказывает отдельный dev StrictMode цикл; авторский отдельный сценарий не смешивается с независимыми 14 browser-группами. Ни offline tests, ни 65 прежних DEV-оценок не являются доказательством точности Freedom или коммерческой production readiness.
