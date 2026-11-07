# Freedom Quality — независимое read-only ревью изменений

> Текущий статус после независимой повторной проверки: **F01–F04 исправлены и закрыты**. Исторические описания ниже сохранены как исходные воспроизведения, а не открытые дефекты. Итоговая интеграционная приёмка ведётся отдельно в `commercial-release-acceptance.md` после завершения исполнителей.

## Независимая повторная проверка F01–F04

Изолированный production-снимок frontend (`tmp/qa-independent-player/snapshot-hashes.json`) собран в собственный `build`, рабочий `dist` не затронут. Chromium получил только синтетические API-ответы и 12-секундный локальный PCM WAV с корректным HTTP Range/206. Все 8 групп в `tmp/qa-independent-player/independent-results.json` прошли; `pageErrors=[]`, `unexpectedRequests=[]`.

- **F01 закрыт:** ready → 25 секунд → waiting → 20 секунд показывает retry; retry восстанавливает позицию 7 секунд без autoplay; canplay снимает watchdog.
- **F02/F03 закрыты:** неизвестная/импортная дата не выдаётся за дату разговора в списке и карточке; date-only не создаёт время; timestamp отображается в Asia/Qyzylorda.
- Проверены реальные PCM metadata/play/pause, Space/Enter/Arrow, volume/mute, seek до metadata, out-of-range, 503/retry и cleanup. На 320/375 px видны controls и раскрытый блок одиночной оценки; проверен keyboard focus. Мобильная таблица действительно прокручивается горизонтально (339 px viewport / 835 px content), правое действие достижимо фокусом.
- **F04 закрыт:** повторный `repro.py` на текущей очереди дал для `PROVIDER_UNAVAILABLE` и timeout `uncertain`, `retryable=false`, отклонение нового batch и **ровно 1** stub-вызов. Подмена состава при повторном alias также отклонена. Свежие данные: `tmp/qa-independent-batch/repro-results.json`.

Первоначальная неудача pending seek была ошибкой fixture: HTTP 200 без настоящей Range-реализации. После правильного 206 событие metadata установило 3 секунды, seeked/canplay сохранили 3 секунды. Отдельно исправлена имитация focus-visible после мыши: проверка начинается настоящим Tab. Эти два сбоя не записываются как дефекты продукта.

Граница доказательства: этот снимок плеера предшествовал подключению `authMedia`; защищённый media и финальные quality/UI изменения требуют итогового снимка. Production-сборка не доказывает отдельный dev StrictMode цикл. Внешние media/model API, рабочая БД и DEV-результаты не использовались.

Промежуточный auth-прогон без optional packages: 60 passed / 35 skipped. Он **не принят как полный security PASS**; повтор выполняется с уже установленными `tmp/enterprise-security-deps` (PyJWT/cryptography/MSAL). Cancel теперь присутствует в policy; app передаёт verified principal в атомарный audit repository. Dynamics ограничен adapter-only, iframe не обещается. Operator workflow остаётся закрытым до trusted ownership mapping; это незавершённый сценарий, а не разрешение доступа по отображаемому имени.

Финальная повторная проверка завершена: **209 targeted Python / 35 JS / 14 независимых browser-групп PASS**, включая authMedia и unassessable. Весь crypto-набор выполнен без skips. Подробности и ограничения — [итоговая приёмка](commercial-release-acceptance.md). Все «ещё не проверено» ниже относятся к явно историческим этапам.

## Исторический исходный этап

Дата: 8 ноября 2026, Asia/Qyzylorda. Этап: **player/date, до завершения авторских проверок**. На этом этапе были выполнены статическое ревью и одна изолированная проверка форматирования; браузерная приёмка ещё не запускалась. Код не исправлялся, владельцем исправлений оставался автор, задачи передавал PM.

Связанный [план приёмки](commercial-acceptance-plan.md) содержит общие A/Q/U/E/R-сценарии. Исходные B01–B03 из плана относятся к более раннему снимку и здесь не переоцениваются: параллельные auth/batch/eval изменения ещё не приняты.

## Найденные дефекты

### F01 — P2: восстановление после поздней буферизации не ограничено по времени

Места: `src/components/AudioPlayer.tsx:52`, `:140`, `:239` в прочитанном снимке.

Таймер на 20 секунд создаётся только при mount/смене src/retry. Если источник к моменту его срабатывания уже готов, таймер заканчивается без изменения статуса. Поздний `waiting` переводит UI в `buffering`, но не запускает новый таймер. Кнопка повтора доступна только при `error`/`stalled`. При медленной передаче, которая не даёт `canplay`/`playing` и не выдаёт `stalled`/`error`, интерфейс может оставаться на «Буферизация…» без кнопки повторной загрузки неограниченно долго.

Это конкретный пробел в гарантии bounded recovery, а не утверждение, что любое временное ожидание ломает плеер. `waiting` означает нехватку данных для продолжения проигрывания, `stalled` — отдельное условие получения media; их нельзя считать одним гарантированным переходом. Источники: [MDN waiting](https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/waiting_event), [MDN stalled](https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/stalled_event).

Детерминированный сценарий для автора: локальный media stub → ready → дать пройти начальному 20-секундному таймеру → удерживать очередной media range / моделировать `waiting` без последующих recovery/error events → продвинуть тестовые часы ещё на 20 секунд. По текущему коду статус остаётся `buffering`, retry не отображается. Runtime-воспроизведение в этом ревью **не выполнялось**.

Ожидание: ограниченный период для каждого эпизода loading/buffering или доступная ручная повторная загрузка; watchdog снимается при восстановлении, cleanup и смене источника. Не включать автоматическое воспроизведение при retry.

### F02 — P2: источник даты учитывается в новом title, но игнорируется в метаданных

Места: `src/features/conversationTitle.ts:18`, `src/features/ConversationDetail.tsx:210`, `src/features/Conversations.tsx:336`.

Новый title правильно исключает `dateSource: "import"`, но подпись под оператором и колонка даты используют любую `date` без проверки источника. Для допустимого контрактом объекта с неизвестной датой звонка и известной датой импорта заголовок говорит «Звонок · дата не указана», а рядом показывается «8 ноября 2026 г.» без объяснения, что это импорт. Это непоследовательная атрибуция даты, а не ошибка преобразования часового пояса.

Доказательство: выполнена локальная Node-проверка без app/API/DB. Реальная `conversationTitle.ts` транспилирована в памяти и вызвана с `{title:"2", channel:"voice call", date:"2026-11-08", dateSource:"import"}`; рядом вычислены точные выражения форматирования из текущих JSX. Получено:

```json
{"title":"Звонок · дата не указана","detailDate":"8 ноября 2026 г.","tableTime":"05:00"}
```

Воспроизведение в браузере: передать такой объект через изолированный fixture API и открыть list/detail. Рабочую базу для этого не менять. Наличие такого объекта в текущем DEV **не утверждается**; `dateSource: import` предусмотрен DTO и явно обработан автором нового formatter.

Ожидание: метаданные используют ту же семантику источника; дата импорта либо отдельно подписана, либо дата разговора обозначена неизвестной. Не скрывать блок переоценки и не менять исходный объект.

### F03 — P2: date-only создаёт выдуманное время разговора в таблице

Место: `src/features/Conversations.tsx:344–350`.

Backend допускает календарную дату без времени (`backend/domain.py:_date(..., allow_date_only=True)`; соответствующий fixture уже есть в `backend/tests/test_domain.py`). Таблица при любой непустой `item.date` выводит часы и минуты. Для `2026-11-08` выражение `new Date(...)` плюс `Asia/Qyzylorda` выводит **05:00**, хотя входные данные не сообщали никакого времени.

Изолированная Node-проверка выражения дала `05:00`; это воспроизведение formatter, не браузерного UI. Дефект существующей даты остался в области player/date, где теперь меняется представление дат. Его не следует приписывать новому `conversationTitle` как введённую им регрессию.

Ожидание: для YYYY-MM-DD показывать только календарную дату, без часов; для полноценного ISO timestamp сохранить согласованный timezone. Регрессия: date-only + Z + offset + null, проверка list/detail. Одного теста нового title недостаточно, поскольку ошибка находится в другом renderer.

## Что подтверждает чтение кода, но ещё не runtime PASS

| Область | Что присутствует | Что ещё нужно доказать |
|---|---|---|
| Preload | `preload="auto"`, effect назначает src и вызывает load; до metadata вместо длительности используется `—:—`. | Реальный metadata/duration и задержанная загрузка на локальном media fixture, без обращения к внешней записи. |
| Autoplay | Автоматического `play()` в effect/metadata/retry нет; вызов находится в обработчике кнопки. | На mount, retry, seek и смене звонка media остаётся paused. |
| Cleanup | Effect захватывает конкретный element; cleanup очищает timeout, pause, удаляет src, вызывает load. Generation отменяет обработку устаревшего reject от play. | React StrictMode mount → cleanup → mount, быстрые переходы и поздние media events; нет постороннего звука/ошибок. Два dev-запуска эффекта сами по себе не объявляются дефектом. |
| Pending seek | `pendingSeek` применяется из metadata; выход за duration сообщает ошибку; при id change offset/notice/seek очищаются. | Seek до metadata, локальный файл с известной длительностью, смена звонка до завершения загрузки; не переносится старый seek. |
| Keyboard/a11y | Native button/range, aria-label/valuetext, role=status; CSS focus-visible и минимум 44 px; retry доступен кнопкой. | Настоящие Tab/Space/Enter/Arrow/Home/End, доступность на 320/375 px и zoom, состояние фокуса после retry. |
| Одиночная переоценка | `EvaluationControls` остался непосредственно в карточке, без добавленного details/menu. | Видимость desktop/mobile и работа выбранной версии на mock; авторский скриншот не заменяет независимую проверку. |
| Название | Числовое имя заменяется только при показе, ID и исходный title не переписываются; список использует общий formatter, поиск включает display title. | Согласованность всех представлений после изменения даты; реальные неизвестные/импортные даты, не только новый title unit test. |

## Ограничения авторских проверок, увиденные при чтении

Прочитан `tests/browser_call_player.py`, **не запущен**. Он имеет полезные сценарии playback/seek, metadata delay, 503/retry, play rejection, мобильный viewport и unmount. Но его текущий код открывает `/api/state` рабочего сервера и продолжает реальные GET аудио. Browser route guard блокирует не-GET/HEAD/OPTIONS, однако не предотвращает серверные side effects чтения и внешние media fetch; `context.request.get` — отдельный APIRequestContext. Поэтому этот скрипт не является нашим offline fixture harness и без отдельного задания здесь не запускается.

Скрипт моделирует `waiting` сразу с последующим `canplay`, что не покрывает F01. Mobile-проверка `document.documentElement.scrollWidth <= innerWidth` не доказывает доступность всех колонок в горизонтальном table wrapper; для U05 надо дополнительно проверить `scrollWidth > clientWidth` контейнера, изменить `scrollLeft`, увидеть правые колонки/действие и добраться к ним клавиатурой. Сама обрезка на старом screenshot остаётся наблюдением, а не подтверждённой поломкой таблицы.

## Снимок, проверки и изменённые файлы

Хеши прочитанного снимка (SHA-256; автор продолжает работу, номера строк могут измениться):

| Файл | SHA-256 |
|---|---|
| `src/components/AudioPlayer.tsx` | `203D4DDC4E04438E646E2C6B6E736E10EC71F3851643557A0F02158BA3834922` |
| `src/features/ConversationDetail.tsx` | `7B31BE9EBAB0909225CF10C9D6C9A3C01B07F989F740BD24E7A23E760C26DD60` |
| `src/features/conversationTitle.ts` | `F7F73DE1138F4D5F9618354FA86DEBC7C3A8B14AE5B3A0FFED322FC84D09B75E` |
| `src/features/Conversations.tsx` | `CD6421F8AB7D5138B5A7D606C28BF84E4EFC6C74F4018930446B0EA6B55FFFD0` |

Дополнительно прочитаны `src/components/AudioPlayer.css`, `src/main.tsx`, `src/features/conversationTitle.test.mjs`, допустимые формы дат backend и DTO. Внешние технические источники указаны рядом с F01. Подбор навыков и границы доказательств — в плане приёмки.

Выполнено: read-only code review; локальная Node-проверка title/date/time без сети и файловых записей; проверка 32 уникальных сценариев плана. Не выполнялось: app build, общий test suite, запуск/остановка сервера, запросы к рабочему API, реальные media/model вызовы и динамическая проверка auth/batch/eval.

Изменены этим QA-этапом только `docs/audit/commercial-review-findings.md` и дополнение к `docs/audit/commercial-acceptance-plan.md`. `.env.local`, SQLite, DEV, код, `dist` и Git не менялись. P0/P1 runtime-дефектов нового плеера этим статическим проходом не доказано; есть три конкретных P2-замечания и перечисленные пробелы проверки. Финальная приёмка ждёт задания PM.

## Дополнительный этап — серверная очередь (01:44 Asia/Qyzylorda)

По отдельному поручению PM прочитаны текущие `backend/batches.py`, `backend/evaluator.py`, `backend/app.py`, `src/features/batchPlan.ts` и авторский `backend/tests/test_batches.py`. Автор параллельно исправлял код; нижеследующие результаты относятся к более позднему снимку, чем исходный план.

### F04 — P1: транспортный timeout маскируется под обычную ошибку и допускает массовый повтор

Связанные места: `evaluator/openai_v2.py:except (TimeoutError, httpx.TransportError)`, `backend/batches.py:_settle/create`, `src/features/batchPlan.ts:isUncertainEvaluation`.

OpenAI adapter преобразует транспортный обрыв/таймаут в `AdapterFailure("PROVIDER_UNAVAILABLE", retryable=True)`. При потере ответа после отправки запроса это **не доказывает**, что провайдер не выполнил работу. Но batch признаёт uncertain только коды `BATCH_OUTCOME_UNKNOWN`, `PROVIDER_TIMEOUT`, `EVALUATOR_ERROR`; запрет нового batch и frontend-фильтр также не включают `PROVIDER_UNAVAILABLE`. В итоге транспортный unknown превращается в обычный `failed` и допускает «Повторить ошибки» с новым вызовом.

**Воспроизведено без сети** скриптом `tmp/qa-independent-batch/repro.py`: disposable SQLite, один синтетический диалог, stub adapter выдаёт тот же `AdapterFailure`, который выдаёт реальный транспортный обработчик. После первого batch создать новый batch с текущей revision и тем же диалогом. Итог `tmp/qa-independent-batch/repro-results.json`:

| Сценарий | Item первого batch | Job/error | Новый batch | Stub-вызовы |
|---|---|---|---|---:|
| `PROVIDER_UNAVAILABLE` | failed | failed / PROVIDER_UNAVAILABLE / retryable=true | принят | **2** |
| прямой `asyncio.TimeoutError` | uncertain | failed / PROVIDER_TIMEOUT | отклонён | 1 |
| повтор alias request ID с другим составом | исходный batch завершён | идентичность alias сохранена | отклонён | 1 |

Реальные API/расходы не вызывались. Отличие первой и второй строк показывает разрыв между adapter contract и классификацией очереди. Durable budget сохраняет reserve неизвестной попытки, но `BudgetLedger.reserve` блокирует неизвестную **историю** или превышение лимита, а не каждый новый `state=unknown`; сам по себе ledger не устраняет повтор при достаточном остатке.

Ожидание: различать гарантированный отказ до отправки/полученный отказ провайдера и неизвестный исход после I/O; latter блокируется одинаково в batch item, job code, frontend retry list и серверном create. Если код `PROVIDER_UNAVAILABLE` остаётся общим, безопасная классификация должна быть консервативной. Добавить test через `httpx.MockTransport` с `ReadTimeout`/`ReadError` после фиксации запроса, а не только прямой `asyncio.TimeoutError` в service.

### Что подтвердилось независимым запуском

`backend/tests/test_batches.py`: **25 passed in 14.46s**, exit 0. Запуск через `tmp/qa-independent-batch/run_suite.py`, уникальный `--basetemp`, cacheprovider отключён, реальные `httpx` transport/urllib HTTP запрещены. В тестах — synthetic adapters, отдельные SQLite и explicit пустой organizer URL/cache fixture. Лог: `tmp/qa-independent-batch/author-suite.txt`.

Проверенные авторскими кейсами области: coalescing двух клиентов и повтор POST; checkpoint claim/attempt до stub I/O; stop/resume; сохранённая версия чек-листа; restart до/после сохранения результата; terminal context-invalid при новом appeal/inactive/content hash; cancel remaining переводит jobs в terminal и освобождает одиночный запуск; запрет повторного batch для явных unknown codes; process worker lock. Эти 25 тестов не покрывают F04 и не доказывают готовность последующей auth-интеграции.

Дополнительные независимые probes подтвердили, что автор уже закрыл два ранее замеченных пути: явный `PROVIDER_TIMEOUT` больше нельзя включить в новый batch; coalesced request alias теперь привязан к исходному fingerprint и не допускает другого состава. Они не включены в открытые дефекты.

Первый запуск harness ошибочно заблокировал Windows asyncio socketpair и завершился 18 failures/3 passes; это **ошибка QA harness, не регрессия продукта**. После исправления запрета на уровне HTTP обычное ограниченное выполнение всё равно остановилось внутри Windows `socket.accept` при создании loop (подтверждено traceback). Зависшие собственные процессы остановлены; тесты успешно повторены с разрешённым локальным runtime. Разрешение автоматической проверкой получено, отказа не было. Внешние API остались запрещены.

Хеши позднего снимка: `backend/batches.py` — `F417E7CAAA7FEC8DF46B27DD2C242AD32D4B2FA71328AB75720902A998314A31`; `backend/evaluator.py` — `AC26623A9748B4338BCEC6A675B635BD638B1873E1A8FA5B12C5013573374B5F`; `evaluator/openai_v2.py` — `1645A0781926C63A0851D5A26EF964042D1987FEB512C6EDF67E6347B7E87381`; `src/features/batchPlan.ts` — `DE562080B83B11A5DCCBA1374A57D5F709B034539E1FD36D7EEB767DCA88DAEB`.

Дополнительные файлы этого этапа: только QA harness/log/results и disposable SQLite под `tmp/qa-independent-batch/` плюс этот отчёт. **Рабочая** SQLite и код продукта не менялись.

## Read-only ревью auth/eval перед интеграционной приёмкой

PM сообщил, что security ещё интегрирует `app.py`, `api.ts`, `App.tsx`; поэтому отсутствие готовых hooks в текущем снимке не выдаётся за дефект завершённой поставки. Финальный runtime-пакет ожидает готовности security.

### Auth — конкретные пункты для проверки итогового handoff

- `security.py` проверяет RS256, подпись/JWKS, точные issuer/audience/tenant/client, scope, срок и известные application roles. Алгоритм/адрес JWKS не берутся из токена; нет доверия `X-Role`. Проверки имеют offline tests, но независимый runtime auth suite здесь ещё **не запускался**.
- **Несогласованность новых маршрутов:** в прочитанном `security_access.py:RULES` есть batch stop/resume, но **нет cancel**, тогда как `app.py` и UI уже используют `/api/batches/{id}/cancel`. Без `extra_rules`/исправления policy корпоративный reviewer не сможет отменить остаток и освободить очередь. Это обязательный пункт повторной проверки после интеграции, не подтверждённый дефект ещё не завершённого app wiring.
- Operator global views и создание апелляции сейчас намеренно denied; per-record доступ требует trusted ownership resolver. Это безопасное ограничение, но **не готовый сценарий сотрудника**. Нельзя показывать матрицу ролей как полностью реализованный operator workflow без owner projection и безопасного ответа create appeal.
- `authoritative_actor_payload` сам по себе недостаточен: repository должен сохранять `createdBy`/`decidedBy`, брать principal из verified request state, не из body, и API DTO обязан их возвращать. Нужна проверка реальных обработчиков/SQLite, не только тест helper.
- Enterprise middleware выставляет `frame-ancestors 'none'`. Это не пропуск для iframe Dynamics: если handoff обещает HTML embedding, нужен явный согласованный trusted parent policy и тест; иначе честно обозначить, что adapter не встроен. Dataverse client в прочитанном файле сам сообщает, что пока не подключён к app.
- Реального tenant нет; успех mocked verifier/WhoAmI не разрешает утверждать live SSO/Dynamics readiness. Режим с неизвестной конфигурацией должен остаться закрытым.
- По уточнению PM обязательный P1 gate итоговой интеграции — Bearer media/download. Проверить защищённый `/audio`, Range/seek, экспорт и logout без token-in-query и без публичного bypass. `audio.src` не добавляет Bearer автоматически; если интеграция оставит такой путь без авторизованного transport, рабочий корпоративный звонок не принят. Local audio должен остаться доступным. Это ещё не выполненная runtime-проверка.

### Eval/knowledge/audio — что видно и что остаётся

- Новый `evaluator/human_labels.py` связывает label/prediction с case/rubric hash, различает independent/adjudicated, unassessable и N/A, отдельно показывает synthetic/real и calibration/holdout, coverage gaps, MAE и critical precision/recall. `accuracyClaim` не выдаёт синтетическую регрессию за точность Freedom. Это полезный механизм, **не проведённая экспертная оценка**; фактическая независимость людей остаётся внешним доказательством.
- `ScopedKnowledgeFact`/`KnowledgeContext` требуют entity/jurisdiction/product, подтверждённую дату разговора и ограниченный период действия. Selector отбрасывает неподтверждённый контекст/approval, несовпадающую область и будущие/просроченные факты. В текущем OpenAI adapter перед отправкой `snapshot.knowledgeFacts=[]`: это безопасное исключение старых непривязанных фактов, но также означает, что scoped knowledge **ещё не интегрирован как работающий product-fact input**. Не выдавать появление selector за live tariff checking.
- Selector может вернуть несколько одновременно применимых противоречащих текстов; в схеме нет ключа конкретного утверждения/правила приоритета для разрешения такого конфликта. Это ограничение требуемого экспертного review каталога. Пока adapter отправляет пустой knowledge, оно не становится активным ошибочным входом модели.
- `audio_metrics.py` измеряет локальный PCM signal и отдельно темп только по human-verified intervals/hash. В результатах `voiceQualityScore`/дикция/интонация не выдумываются, ограничения quiet energy vs речевые паузы подписаны. Это отдельный локальный инструмент, не доказательство прослушивания DEV-звонков моделью.
- Тестовые фикстуры human labels не являются независимым GT; PCM metadata/statistics не подтверждают акустическое качество/правильность консультации. Свежий тестовый прогон этих новых модулей и итоговой auth-интеграции остаётся следующим заданием.

Источники этого read-only этапа: `backend/security.py`, `backend/security_access.py`, `backend/integrations/dynamics.py`, `backend/tests/test_enterprise_access.py`, `backend/knowledge.py`, `evaluator/human_labels.py`, `tests/test_human_labels.py`, `evaluator/audio_metrics.py`, `evaluator/budget.py`, `evaluator/openai_v2.py`. Внешние identity/model сервисы не вызывались.
