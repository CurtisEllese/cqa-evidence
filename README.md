# CQA Evidence

Conversation Quality Analytics для Freedom Broker CQA Hackathon: React/TypeScript/Vite, FastAPI/Pydantic и SQLite.

Приложение работает с 65 реальными диалогами DEV, версиями чек-листа, доказательствами и апелляциями. Активный API использует контракт **v2.0** и серверные задания оценки. Реальный оценщик требует подключения адаптера и провайдера; наличие API-ключа само по себе не создаёт оценки.

## Документация

- [Командный аудит 7 ноября: проверенная готовность, найденные ошибки и следующие задачи](docs/team-audit-2026-11-07.md)

- [Активный контракт v2.0 и отличия от v1](docs/contracts-v2.md)
- [Готовый адаптер Gemini: запуск и проверки](docs/gemini-evaluator.md)
- [Adalbala: контракт оценщика](docs/ADALBALA.md)
- [Backend v2: импорт, сохранность и проверка интеграции](docs/backend-v2.md)
- [Командная передача и маршруты API](docs/TEAM-HANDOFF.md)
- [Архитектура](docs/architecture.md)
- [Реализованный контур и оставшиеся шаги](docs/IMPLEMENTATION.md)
- [Официальные требования и точный расчёт](docs/requirements.md)
- [Проверка Gemini и ограничения этого smoke-теста](docs/gemini-check.md)
- [Подключение API организатора](docs/api-setup.md)
- [План проверки точности](docs/evaluation.md)
- [Визуальные источники Freedom](docs/BRAND.md)
- [План команды и владельцы задач](docs/team-plan.md)
- [Исследование готовых решений](docs/open-source.md)
- [Архивный контракт v1](contracts/README.md)

Опубликованный `contracts/v1.py` и его схемы остаются замороженной версией. Источник активных типов — `backend/contracts/v2`; `contracts/v2/*.schema.json` и `src/generated/contracts.ts` генерируются. Приложение не принимает смесь v1/v2.

## Установка: Python 3.11 и Node.js 22+

Поддерживаемый Python — **3.11**. Проверенный локальный runtime — 3.11.18. Для нового окружения Windows PowerShell с установленным Python 3.11:

```powershell
py -3.11 -m venv .venv311
.venv311/Scripts/python.exe -m pip install -r backend/requirements.txt
npm ci
if (-not (Test-Path .env.local)) { Copy-Item .env.example .env.local }
```

Если Python устанавливается через уже доступный uv:

```powershell
uv python install 3.11
uv venv .venv311 --python 3.11
uv pip install --python .venv311/Scripts/python.exe -r backend/requirements.txt
npm ci
if (-not (Test-Path .env.local)) { Copy-Item .env.example .env.local }
```

Это альтернативные способы подготовки одного окружения; не создавайте его повторно поверх работающего. На Linux/macOS используйте `python3.11 -m venv .venv311` и путь `.venv311/bin/python`. В окружении, созданном uv без pip, устанавливайте зависимости через `uv pip`.

В `.env.local` укажите `ORGANIZER_DEV_URL` из материалов организаторов. Не публикуйте эту подписанную ссылку и ключи. Существующий файл конфигурации не заменяйте. `DATABASE_PATH` по умолчанию указывает на `local-data/cqa.sqlite`.

```powershell
npm run dev
```

Команда запускает UI на [127.0.0.1:5173](http://127.0.0.1:5173) и API на [127.0.0.1:8000](http://127.0.0.1:8000). Документация API: [127.0.0.1:8000/api/docs](http://127.0.0.1:8000/api/docs). После изменения Python или `.env.local` перезапустите процесс; Vite обновляет интерфейс автоматически. npm-обёртка предпочитает `.venv311` и проверяет Python 3.11.

## Проверки и готовая сборка

```powershell
.venv311/Scripts/python.exe scripts/generate_contracts.py --check
npm test
npm run build
npm start
```

`npm start` запускает FastAPI, который раздаёт `dist` и API по [одному локальному адресу](http://127.0.0.1:8000). Отдельные процессы: `npm run backend` и `npm run dev:ui`. Для `npm run preview` нужен backend на 8000.

После изменения Pydantic-моделей выполните генератор без `--check`, затем проверку и тесты. Сгенерированные файлы включайте в тот же PR. pytest/Hypothesis проверяют активный контракт, расчёт, задания, API/SQLite и аудио; тесты замороженного v1 сохраняются отдельно. Тестовые модели не подставляются в рабочие данные.

Дополнительная браузерная проверка: `.venv311/Scripts/python.exe tests/browser_smoke.py`. Для неё нужны Python Playwright и Chromium. Она использует отдельную базу в `tmp/qa`; проверки реального аудио зависят от доступности источника. Наличие ключа провайдера для обычных контрактных тестов не требуется.

## Подключение модели

Адаптер — асинхронная Python-функция внутри приложения:

```python
async def evaluate(request: EvaluationRequest) -> ModelResponse:
    ...
```

Для реализованного Gemini укажите `EVALUATOR_ADAPTER=evaluator.v2:evaluate`; пример подключения и чек-лист приёмки — в [ADALBALA.md](docs/ADALBALA.md). Без адаптера POST `/api/evaluations` возвращает 503. Настроенный контур возвращает 202 с ID задачи; её состояние читается через GET `/api/evaluations/{id}`.

Модель возвращает решения критериев и доказательства. Backend проверяет ссылки/цитаты, шкалы и N/A, рассчитывает итог и сохраняет результат. Непроверенные доказательства дают `needs_review` без итогового балла. Gemini-адаптер реализован по активному v2; ключ и модель задаются только на сервере. Проверки подключения не заменяют экспертную проверку точности.

## Данные и сохранность

DEV: 60 чатов и 5 звонков; RU 35, KZ 15, EN 15. Эталонных оценок в API нет. Диалоги, версии чек-листа, исходные оценки, задания и решения апелляций сохраняются сервером в SQLite. В браузере остаются формы, поиск/фильтры и временный снимок состояния; окончательная арифметика выполняется сервером через Decimal.

Без `ORGANIZER_DEV_URL` приложение открывает пустую рабочую область для ручного импорта. Уже импортированные диалоги и оценки доступны после перезапуска без обращения к организатору. Принудительно загрузить DEV можно кнопкой обновления; ошибка источника не подменяет сохранённый результат.

Источники звонков — HTML-плееры. Сервер извлекает файл и передаёт аудио с поддержкой перемотки; ссылки ручного импорта не загружаются сервером автоматически. Исходные оценки не переписываются апелляциями.

`.env.local`, `local-data`, `tmp`, аудио и скриншоты не должны попадать в Git. Для резервной копии остановите backend и скопируйте SQLite либо используйте SQLite Backup API; копирование только основного файла при активной WAL-записи недостаточно.

Запуск предназначен для localhost. Для общего размещения нужны авторизация, права на решения, HTTPS и резервное копирование. Ограничения Origin/localhost не заменяют авторизацию.

## Командная работа

Актуальные подробные задания и зависимости: [ТЗ каждому участнику по приоритету](docs/tasks/README.md). Они заменяют первоначальный план разработки с нуля.

AI/evaluation — Adalbala, backend/data/scoring — naki707, frontend/appeals — Krutoi312; интеграция и контракт согласуются между потоками. Изменение wire-формата проходит через Pydantic, генератор, mapper и тесты в одном PR. Не подменяйте контракт формой текущего UI и не исправляйте generated-файлы вручную.

Задачи: [AI](https://github.com/CurtisEllese/cqa-evidence/issues/1), [backend](https://github.com/CurtisEllese/cqa-evidence/issues/2), [frontend](https://github.com/CurtisEllese/cqa-evidence/issues/3), [интеграция](https://github.com/CurtisEllese/cqa-evidence/issues/4).
