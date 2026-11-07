# Локальная передача — 2026-11-08

**Статус: READY для локальной передачи пользователю.** Завершено 8 ноября 2026, 02:08:33, Asia/Qyzylorda (UTC+05:00). Это локальный rollout, не публичный deploy. Изменений исходного кода, git commit/push и платных вызовов не было.

## Работающий сервер

- Адрес: <http://127.0.0.1:8000>.
- Новый runtime PID: **33988**; родитель Python launcher: **30204**.
- Управляющая exec-сессия этой задачи: **38759**. Сервер оставлен работающим.
- Запуск из корня проекта: `npm start` → `npm run backend` → `node scripts/python.mjs -m backend.main`.
- Проверено: слушает только `127.0.0.1:8000`; startup complete. Режим доступа: `local`, `authenticationRequired=false`. Реальный Entra login не выполнялся.
- Запуск выполнен с pipe output, без отдельного видимого окна.

Старый PID **5244** был проверен через реальный listener 8000 и CIM. Его команда: `python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --no-access-log`. Родитель 17676 — `.venv311/Scripts/python.exe` этого проекта, выше 17440 — `node scripts/python.mjs` с теми же параметрами; отдельная PowerShell-консоль 144 содержала только эту цепочку запуска. Прежняя exec-сессия 82721 не была доступна из данного чата.

После снимка и повторной проверки отсутствия активных оценок в проверенную отдельную консоль отправлен штатный Ctrl+C. PID 5244 завершился, порт освободился; только затем запущен новый сервер. `Stop-Process`, `taskkill /F`, общий kill Python и остановка посторонних процессов не применялись. Дополнительный Ctrl+C PM в старую сессию не затронул новый сервер.

## Снимок до остановки

Создан в **02:02:46 UTC+05:00**:

`local-data/backups/local-rollout-20261108-020246/`

Папка игнорируется Git через `local-data/`.

- `app.sqlite` — снимок рабочей `local-data/cqa.sqlite` через Python `sqlite3.Connection.backup()`.
- `budget.sqlite` — снимок `local-data/openai-budget.sqlite3` тем же SQLite Backup API.
- `results.json`, `openai-usage.jsonl` — сохранённые файлы DEV и журнала.
- `before.json` — 65 ID оценок/статусов, контрольные суммы таблиц и файлов, итоги бюджета.
- `after.json` — сверка после restart и объяснение новых полей wire-контракта.
- `final.json` — повторная сверка после всей браузерной проверки.

Исходные SQLite открывались с `mode=ro`; Backup API создавал согласованный снимок с учётом WAL. `PRAGMA quick_check` на обеих исходных и обеих резервных БД: `ok`. Копирование только основного SQLite-файла вместо Backup API не использовалось. Секреты не выводились.

SHA-256 резервных SQLite:

| Файл | SHA-256 |
|---|---|
| app.sqlite | `7763836a1ee362f3c570a4e35e4cf7bc75176abe6490cb6c13b5fcc63f546473` |
| budget.sqlite | `03f59696d615d5d083e4dc441cd1a578d3e9d963675defc843c6b223351e4699` |

## Сохранность данных

До restart, после restart и после браузера:

| Проверка | Результат |
|---|---|
| Диалоги / оценки DEV | **65 / 65**, все `completed` |
| ID оценок и статусы | Полное совпадение всех 65 пар |
| Ревизия workspace | **267**, не изменилась |
| evaluations, evaluation_jobs, evaluation_attempts | Все строки и содержимое совпадают по SHA-256 |
| conversations, checklists, appeals | Все строки и содержимое совпадают по SHA-256 |
| Бюджетные попытки | **71**, неизменно |
| Учтённый расход | **$0.09511375**, неизменно |
| Неопределённые/зарезервированные попытки бюджета | **0**, неизменно |
| Платные вызовы при rollout | **0** |
| DEV JSON и usage journal | Побайтовые SHA-256 совпадают |

Контрольные суммы:

- `evaluations`: `7446ff77ab3b43c4b4d3a37861d7f66d9dc4f16d4f4b330b3b06d3ce7e1cbc29`.
- Все строки бюджетного ledger: `e158df1d27f6bb638bf87db90c109ba19a44869b4676a52934bee0fff2db69ef`.
- `results/dev/results.json`: `c6545b5848b35216efa75b9be22618b801a3939150c72e26c3e9c3a9c0bbfaf1`, 238749 байт.
- `local-data/openai-usage.jsonl`: `4eca449f388aacc33fa2a51d1a016a794e0caf6f391cb9a177812f629515d20d`, 27054 байта.

Ожидаемое изменение схемы при startup: добавлены пустые таблицы `evaluation_batches`, `batch_items`, `batch_start_requests`, `enterprise_actor_audit`; в каждой 0 строк. Старые результаты не переписаны.

Wire JSON новых ответов отличается только добавленными полями `assessment_status="assessed"`, `review_reason=null` в `evaluation.results` и `evaluation.score.results`. После удаления только этих новых значений по умолчанию полный canonical SHA-256 всех 65 API-оценок точно равен исходному: `3f61e32188d91f93593220fe14ba2d4ef3f4bdd799587fc45531118be77ff360`. Это изменение представления контракта, не оценок, доказательств или баллов.

## Проверка API

Без POST оценок, импорта и организатора:

| GET | HTTP | Проверенное содержимое |
|---|---|---|
| `/api/health` | 200 | `status=ok`, `database=sqlite`, `evaluationAvailable=true` |
| `/api/auth/config` | 200 | `mode=local`, `authenticationRequired=false` |
| `/api/state` | 200 | 65 сохранённых completed, ревизия 267 |
| `/api/batches/current` | 200 | `batch=null`, новых задач не создавалось |

Старый backend до restart отвечал 404 на auth/config и batches/current; новый обслуживает их без compatibility-stubs.

## Финальная сборка и браузер

Первый read-only browser run обнаружил несоответствие артефакта: общий `dist` имел время 01:57, финальная `ConversationDetail.tsx` — 01:58. Даты, реальный плеер и одиночная переоценка прошли основные проверки, но синтетический unassessable fixture показывал старое `N/A`.

Исходники не менялись. Выполнен `npm run build` из замороженного дерева: **exit 0, 1766 modules**. Пять SHA-256 voice-файлов точно сверены с финальным разделом `docs/audit/call-player-acceptance.md`; все совпали. Backend повторно не перезапускался: он раздаёт обновлённый `dist`.

Финальный `dist/index.html` SHA-256: `a733e3040d65db1d2c39442d9ddd7224c620094837a887e8d836257e2528e885`. Главный bundle: `main-Be5PfcJT.js`. Полный manifest хэшей — `tmp/local-rollout-build.json`.

Повторная команда:

```text
python -u tests/browser_call_player.py --url http://127.0.0.1:8000
```

**13/13 групп passed**, `pageErrors=[]`, `mutationsBlocked=[]`, `compatibilityStubs=[]`.

- Реальная страница и API нового сервера, без Vite и без подстановок auth/batch.
- Согласованный заголовок списка/карточки: «Звонок от 3 ноября», корректные отображения даты.
- Реальная запись `p4-ru-003`: загрузка, декодирование, playback/pause, duration **393.22 s**, seek, клавиатура, громкость/mute. До открытия карточки список не запрашивает аудио; autoplay отсутствует.
- Смена звонка, очистка media, offset/переход по реплике, границы времени, loading/buffering/retry, мобильный viewport 375 px и landscape.
- Выбор версии и кнопка «Оценить заново» остаются видимыми. Кнопка оценки не нажималась.
- Дополнительные browser-only fixtures проверяют крайние даты и «Недостаточно данных» отдельно от настоящего N/A. Они подменяют лишь GET-ответы внутри браузера, не рабочую базу и не compatibility-поведение сервера. Media error cases также моделируют ошибки в браузере; восстановление использует реальное аудио.
- Маршрутный guard блокировал бы любой mutating API request; ни одной такой попытки не было. GET аудио мог использовать существующий media cache и внешний источник записи; модельные сервисы не вызывались.

Артефакты финального прогона:

- [Итоговая сохранность данных](../../tmp/local-rollout/final.json).
- [Браузерный отчёт](../../tmp/local-rollout/browser-acceptance.json).
- [Desktop, реальный звонок и одиночная переоценка](../../tmp/local-rollout/desktop-1440.png).
- [Mobile](../../tmp/local-rollout/mobile-375.png).
- [Недостаточно данных — browser fixture](../../tmp/local-rollout/insufficient-data-1440.png).

Desktop screenshot просмотрен: дата, плеер, контролы и блок одиночной переоценки находятся на своих местах.

## Границы и изменённые артефакты

Production исходники и конфигурация не редактировались; код остаётся frozen. Изменены только результаты сборки `dist`, операционные snapshots/отчёты в игнорируемых `local-data/backups` и `tmp`, а также этот документ. Новые таблицы созданы штатным startup актуального приложения. Рабочие оценки и бюджет сохранены без изменений.

Данная приёмка подтверждает локальную передачу. Она не выполняет публичное размещение, Entra login, новый DEV-run, отправку результатов или git commit/push. Сервер PID 33988 / session 38759 оставлен работающим для пользователя.
