# Приёмка дат звонков и аудиоплеера

8 ноября 2026, Asia/Qyzylorda. Работа реализована. Авторская приёмка пройдена после исправлений F01–F03 из независимого QA. Это отчёт по плееру и представлению дат, не общая приёмка параллельных auth/batch/evaluator изменений.

## Результат

- Числовые/пустые названия звонков отображаются как «Звонок от 3 ноября». Содержательное имя сохраняется: «Алло. · 3 ноября». Общий formatter применяется в списке и карточке; Dashboard получает его через общий ConversationTable. Поиск понимает и исходное, и отображаемое имя. Исходные title, date и ID не переписываются.
- Фактическая дата звонка вычисляется в Asia/Qyzylorda. Неизвестная/некорректная дата и дата с `dateSource: import` не подставляются в качестве даты разговора. Заголовок, подпись оператора и дата в таблице согласованы. Для date-only отсутствуют часы, для ISO timestamp сохраняется местное время.
- Запись выбранной карточки начинает загружаться сразу (`preload=auto`), без autoplay. В списке аудио не запрашивается. Длительность берётся из настоящих метаданных media, а не из приблизительной длительности транскрипта.
- Добавлены play/pause, полоса прогресса и перемотка, текущее/общее время, громкость/mute, загрузка, буферизация, ошибка и повтор. Управление кнопками и range доступно с клавиатуры, есть подписи aria, текстовый статус, видимый focus и области управления не меньше 44×44 CSS px.
- «К записи» и ручное смещение сохранены. Отложенная перемотка применяется после метаданных, выход за длительность объясняется сообщением. Смещение и уведомление сбрасываются при переходе к другому звонку. Смена смещения очищает старое уведомление.
- Cleanup останавливает старое аудио, снимает src, освобождает media и таймер. Отклонённый play promise после смены источника не меняет состояние нового плеера.
- Каждый эпизод loading/waiting получает отдельный watchdog 20 секунд. После задержки доступен повтор; ready/play/pause/error/unmount снимают ожидание. Повтор сохраняет текущую позицию, но не запускает звук автоматически.
- Блок выбора версии и «Оценить заново» не редактировался и не сворачивался. Его видимость подтверждена в desktop/mobile. Оценка не запускалась.
- После готовности общего mapping добавлен вывод `assessmentStatus: unassessable`: «Недостаточно данных» и `reviewReason`, нейтральный индикатор, «Оценка не выставлена». Не показываются N/A, значение/флаг по умолчанию, вклад или «исключён из расчёта». Истинный `assessed + value:null` остаётся N/A. Backend/viewmodels продолжают отвечать за отсутствие итогового балла у needs_review.

## Исправления QA

| Замечание | Исправление | Доказательство |
| --- | --- | --- |
| F01: поздняя буферизация без recovery | Отдельный таймер каждого waiting, отмена при восстановлении/паузе/cleanup; retry с восстановлением позиции | Browser clock: ready → +25 сек → waiting → +20 сек → retry; восстановилась позиция 7 сек, autoplay отсутствует. Отмена таймера после canplay/pause проверена |
| F02: import date в подписи оператора/таблице | Общие callDateLabel/callTimeLabel, одна timezone и provenance | Unit + browser fixtures: дата с переходом суток, import date, null; список, заголовок и подпись согласованы |
| F03: выдуманные 05:00 из YYYY-MM-DD | Время показывается только при наличии временной части; date-only остаётся датой | Unit: YYYY-MM-DD, Z, +05:00, null, invalid, import. Browser: 2026-11-08 без подписи времени |

## Изменённые этой работой файлы

- `src/components/AudioPlayer.tsx` — новый плеер.
- `src/components/AudioPlayer.css` — отдельные стили плеера и блока записи; существующий styles.css не редактировался.
- `src/features/conversationTitle.ts` — новые общие форматтеры отображения названия, даты и времени звонка.
- `src/features/ConversationDetail.tsx` — подключение плеера/formatter, сброс состояния записи, сохранение существующего seek.
- `src/features/Conversations.tsx` — общий заголовок/поиск и корректная дата/время.
- `src/features/conversationTitle.test.mjs` — 7 тестов форматирования и неизменности исходных данных.
- `tests/browser_call_player.py` — браузерная приёмка; блокирует все не-GET/HEAD/OPTIONS запросы к API.
- `tests/browser_call_player_auth.py` и `tests/fixtures/call-player-auth.tsx` — полностью изолированная приёмка интеграции с authMedia на синтетических сессии/записи.
- `docs/audit/call-player-acceptance.md` — этот отчёт.

Dashboard.tsx и viewmodels.ts менять не потребовалось. App/store/API/backend, .env.local, рабочая SQLite и результаты DEV этой работой не редактировались. Ранее согласованная чистка интерфейса сохранена. В общем git diff этих файлов есть унаследованные изменения — их нельзя считать патчем только этой задачи. Git checkout/reset/commit/push не выполнялись. Модель, Telegram, публикации не вызывались; дополнительных расходов API этой задачей не создано.

## Проверки и точные outcomes

Команды выполнялись из корня проекта:

```text
node node_modules/typescript/bin/tsc --noEmit
node --test src/features/conversationTitle.test.mjs src/services/contracts.test.mjs src/features/batchPlan.test.mjs
node node_modules/vite/bin/vite.js build --outDir tmp/call-player/build
git diff --check -- src/features/ConversationDetail.tsx src/features/Conversations.tsx
python -u tests/browser_call_player.py --stub-missing-batch --stub-missing-auth
python -u tests/browser_call_player_auth.py
```

Результаты: TypeScript — exit 0; Node — 29 passed, 0 failed (включая 7 новых тестов; соседние batch/contracts suites параллельно расширились); Vite после auth-интеграции — exit 0, 1766 modules transformed; diff check — без ошибок whitespace. Production build сохранён в отдельную временную папку, общий dist не перезаписывался.

Браузер — Chromium headless, 13 пройденных групп проверок; `pageErrors: []`, `mutationsBlocked: []` (не было даже попыток mutating API request). Машинный результат: [acceptance.json](../../tmp/call-player/acceptance.json).

Реально загружено/декодировано/проиграно аудио `p4-ru-003`, 393.22 секунды (6:33). При этом исходный transcript duration показывает 06:39 — плеер корректно использует метаданные media. Переключение проверено также на второй реальной записи. До открытия карточки media-запросов не было; при открытии запрашивался только выбранный звонок. Это runtime-проверка playback/currentTime, а не субъективная оценка качества звука на слух.

Проверено: playback/pause кнопками и Space/Enter; seek мышью и ArrowRight/Home/End; volume/mute; переход по реплике с offset 3.5; out-of-range; отложенные metadata + pending seek 7; loading; waiting/canplay; поздняя буферизация/таймер; HTTP 503/retry; rejected play promise; освобождение источника при переходе и unmount; отсутствие autoplay после загрузки, перехода и retry. Ошибки сети и media events моделировались в браузере, восстановление использовало реальную запись.

Viewport: 1440×1000, 375×900 и landscape 900×375; reduced motion включён. Переполнения документа по ширине не было, плеер помещается, минимальные размеры его контролов и keyboard outline проверены. Видимость выбора версии/одиночной переоценки проверена, действие не нажималось.

## Скриншоты

- [Desktop 1440×1000, без full_page](../../tmp/call-player/desktop-1440.png).
- [Mobile 375×900](../../tmp/call-player/mobile-375.png).
- [Плеер крупно, desktop](../../tmp/call-player/player-1440.png).
- [Плеер крупно, mobile](../../tmp/call-player/player-375.png).
- [Загрузка](../../tmp/call-player/loading-1440.png).
- [Ошибка и retry](../../tmp/call-player/error-1440.png).
- [Недостаточно данных и настоящий N/A, desktop](../../tmp/call-player/insufficient-data-1440.png).
- [Недостаточно данных, mobile](../../tmp/call-player/insufficient-data-375.png).

Desktop/mobile/error снимки просмотрены автором. Светло-зелёный стиль сохранён; общий редизайн не выполнялся.

## Границы приёмки / дальнейшая интеграция PM

1. Приёмка использует отдельный Vite на `127.0.0.1:5197`, proxy к работающему `127.0.0.1:8000`. Рабочий backend не перезапускался. На время проверки параллельные доработки уже добавили frontend GET `/api/batches/current` и `/api/auth/config`, но работающий backend отвечал 404. По явным флагам теста эти отсутствующие GET заменялись пустым batch и локальной auth-конфигурацией. Реальные state/checklists/media не подменялись в основной приёмке. Финальные четыре date-fixture сценария отдельно подменяли только GET state в памяти браузера. После общего обновления backend PM следует запустить тест без обоих compatibility-флагов. Это только тестовые подстановки; в продукте fallback с Entra на local не добавлен.
2. Read-only здесь означает отсутствие mutating API requests и прямых изменений рабочих данных. GET audio может использовать существующий серверный media cache и внешний источник записи; это не полностью офлайн-тест. Реальные аудио запрашивались в рамках задания. Платных модельных вызовов нет.
3. Не заявляется проверка Safari/iOS, Firefox, VoiceOver/NVDA или реального телефона. На некоторых мобильных платформах браузер ограничивает программную громкость — требуется устройство для проверки. Проверены Chromium и мобильные viewport, клавиатура/aria-атрибуты.
4. Привязка к первой реплике по-прежнему приблизительна, как и была до задачи. Пользователь может задавать offset; истинные word-level media timestamps эта задача не создаёт.
5. Новые тесты не добавлены в package.json (вне владения задачи); PM может включить `src/features/conversationTitle.test.mjs` в общий test script. Отдельная команда выше воспроизводима.

## Источники и выбор подхода

Прочитан обязательный `C:/Users/plddo/.codex/skills/find-skills/SKILL.md`. Проверен [каталог skills.sh](https://skills.sh/) и выполнен целевой web-поиск `site:skills.sh vercel-labs react best practices`. [Официальный Vercel skill](https://skills.sh/vercel-labs/agent-skills/vercel-react-best-practices) имел около 879K установок; [официальный репозиторий](https://github.com/vercel-labs/agent-skills) — около 33.7K stars на момент просмотра. Оценены происхождение, популярность и соответствие React 19; использована уже установленная локальная версия, без установки пакетов.

Прочитаны локальные UI/UX Pro Max, Vercel React Best Practices и Webapp Testing. Целевой локальный UX-поиск `keyboard accessible media controls --domain ux` вернул подходящие правила focus states / native button semantics / отсутствия autoplay. Применены только относящиеся к компоненту правила: native button/range, aria names, focus, touch target, текстовые feedback states; стиль взят из существующего продукта. React guidance: частые media updates изолированы в плеере, lifecycle cleanup, refs для временных значений; без новых зависимостей. Браузерный тест — Python Playwright, сначала чтение живого DOM, затем действия.

Исходные данные и контракт прочитаны из domain/types, viewmodels, audioTiming и read-only API `/api/state`. Замечания F01–F03 взяты из [независимого QA](commercial-review-findings.md). Внешние материалы использовались как справочные источники, не как разрешения на действия.

## Интеграция с корпоративным аудио

AudioPlayer использует принадлежащий security-разработчику `loadAuthorizedAudio(src, signal)`. Helper не изменялся. Локальный режим получает исходный URL/preload, корпоративный — авторизованный Blob URL. До завершения получения источника play недоступен; после получения autoplay не выполняется. На retry/смену src/unmount вызываются AbortController.abort и dispose, запоздалый результат после abort немедленно освобождается. Ошибка получения доступа отображается с retry, прямой публичный src не подставляется. Pending seek применяется после получения metadata.

Ссылка «Открыть запись» появляется после получения источника. Для корпоративной записи используется уже авторизованный Blob URL, для локальной сохранён исходный audio-page. Во время отказа доступа обходной ссылки нет.

Изолированный browser fixture использует настоящий authMedia helper, но подменяет только auth session dependency: фиктивный bearer принимается только его синтетическим роутером; никаких настоящих аккаунтов, рабочих state/API/audio или внешних запросов. 7 групп прошли: authorized fetch→blob, отсутствие anonymous src, смена источника/revoke, delayed authorized fetch+pending seek, abort незавершённого fetch, 403/retry без public fallback, dispose всех Blob URL/StrictMode, локальный исходный URL. `pageErrors: []`, `unauthorizedRequests: []`. Результат: [auth-acceptance.json](../../tmp/call-player/auth-acceptance.json). Реальный Entra login/token flow этой проверкой не подтверждается — это зона отдельной security-приёмки.

После интеграции повторена основная приёмка на реальном аудио с обоими compatibility-флагами. Скриншоты обновлены тем же тестом.

Дополнительное исследование автоматической дикции сохранено в [automatic-voice-options.md](../product/automatic-voice-options.md) и **остановлено по указанию пользователя**. Новые speech-модели не подключены; evaluator не менялся.

Финальные SHA-256 основных файлов после auth-интеграции и отображения недостаточных данных:

```text
AudioPlayer.tsx       FB66CF0FF39CD8A8AC0B4C4303A00692B1D1BE059A103785013D9F0213EC058E
AudioPlayer.css       EF0D0A69515AC2B5CF8613E2A3A301297764193AF675DB44F6E3A923D6D5B7A3
conversationTitle.ts 125A20DEB6399D2730CD1E660EB84E1753B541BC687F74F48737E59E84130BCF
ConversationDetail   AABF8FEB8233A9C4EF6E1EE78DE3E47FDD3DC73E96FBF8C6F291712A04D99A4B
Conversations        822A21337F3988E870578E4F6428E0AEC5303338F5AF8CFF856F50BE3D08CCBC
```
