# T-042. Отчёт исполнителя

## Раунд 1 · 2026-10-26
Коммиты: e38a98f, 8068ea8

### Сделано
- `runtime/hive/board.py` (новый): модель Entry/Reply, SQLite (`board.sqlite` в RUN_DIR, WAL,
  каждая мутация — `BEGIN IMMEDIATE`), переходы 3.2 c проверкой прав и обязательного текста,
  матрица прав 3.3, лимиты 6 (5/жизнь оператору, 20/ч LO, 30 записей, 800/400 символов),
  тревоги 7 со свёрткой повторов `(type, from)` за 10 минут, журнал `board_log` (before/after) +
  события `board.<op>` в `events.jsonl` без текста (только `len`), лента 9 с курсорами по
  serial/last-order, снапшот новой жизни (открытые `net` задачи разово), смерть 4.3 одной
  транзакцией (взятые → `open` с системной репликой, личные → `net` c `readdressed_from`,
  запросы → `canceled` `poster_dead`), отказ сырых `minecraft:` id как в базе знаний.
- `runtime/hive/app.py`: маршруты раздела 9 — `/board/entries[/{id}][/transition|/reply]`,
  `/board/alarms`, `/board/feed`, `/board/stream` (SSE), `/board/ack`; гейты: `X-Operator-Key`
  (оператор), `X-Node-Key` (агент ленты: слот → живой serial / ключ LO), `X-Runner-Key` /
  ключ LO как `admin` / ключ узла (только `delivery_failing`) для тревог; `from` из тела — 422.
- `runtime/hive/mcp.py`: инструменты `board`/`post`/`claim`/`update`/`reply` на обоих серверах;
  `filter=all` — только у Last Order; строки `#id kind from→to status: text[:120]`, ≤ 15.
- `runtime/hive/lifecycle.py`: хук `set_death_hook` (вызовы в `mark_dead`, подтверждённом
  RCON-убийстве, `retire`, `_close_lost`) — застрявший/перезапущенный ум не оставляет задач
  висеть; сбой хука не ломает путь смерти (событие `life.death_hook_failed`).
- `runtime/hive/client.py`: `board_alarm` (X-Runner-Key) и `board_alarm_admin` (ключ LO);
  вызовы из раннера/admin — T-043/T-045, здесь только методы.
- `dev/stand_keys.py`: `HIVE_OPERATOR_KEY`; `docker/compose.yml`: env hive + publish
  `127.0.0.1:${HIVE_BOARD_PORT:-18080}:8000`; `docs/architecture/NETWORK.md`: раздел «Доска».

### Проверки
```
$ bash dev/check.sh
pytest: ok · 778 passed, 3 skipped in 64.64s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ uv run pytest tests/test_hive_board.py tests/test_hive_board_mcp.py -q
40 passed in 9.10s   # все критерии приёмки: личность, матрица, переходы, 10 потоков claim
                     # (1 taken + 9 already_taken), too_many_taken, смерть, запросы, лимиты,
                     # тревоги, журнал, рестарт, лента/ack/снапшот, SSE, MCP, сырые id
```
Стенд (docker, проект `t042-check` из worktree, hive + mc-server, без LLM; стенд оператора
не трогал, образ собран под тегом `t042-mind:check`, после проверки свёрнут):
```
$ curl POST /board/entries  -H "X-Operator-Key: …"  → HTTP 200, {"id":1,…,"from":"operator"}
$ curl POST /board/entries  (без ключа)             → HTTP 401 operator_key
$ docker restart hive; curl GET /board/entries      → HTTP 200, задача #1 на месте
```

### Отклонения от задачи
- `tests/test_hive_mcp.py` (вне «Можно трогать»): вынужденная минимальная правка — в
  `SISTER_TOOLS`/`LO_TOOLS` добавлены 5 досковых инструментов и ослаблено утверждение о
  нулевом пересечении наборов (доска по спецификации 10 общая для обоих серверов, admins
  по-прежнему структурно разделены). Без правки пин-тест падает о новый набор инструментов.
- Окно повторов тревог считается от последнего срабатывания (скользящее), а не от первого —
  в спецификации «в течение 10 минут» не уточнено.
- `request.why` ограничен 400 символами (в спецификации лимита нет) — защита от безразмерной
  строки; все прочие лимиты по разделу 6.
- Порт страницы: у оператора на хосте `0.0.0.0:8000` занят системным процессом (bind
  `127.0.0.1:8000` даёт WinError 10013, проверено), поэтому дефолт `127.0.0.1:18080` с
  переопределением `HIVE_BOARD_PORT`; только loopback — по спецификации.
- Лента «своё» реализована дословно по разделу 9: реплики/смены статуса приходят автору и
  взявшему; адресат без взятия смены статуса не получает (свои новые записи — получает).
- `admin` как автор тревог аутентифицируется ключом Last Order по HTTP (`X-Node-Key`):
  отдельного ключа admin в проекте нет; сам агент Last Order тревог не ставит (нет инструмента
  в MCP), разделение структурное.

### Вне задачи
- `unionclef` подмодуль грязный (VS Code redhat.java переписал `.settings/…buildship…`),
  оператор разрешил игнорировать в этом чате.
- Хост-порт 8000 занят svchost — может мешать и другим задачам стенда.

### Вопросы
- нет

## Раунд 2 · 2026-10-26
Коммиты: 3091539

### Сделано
- R1-1: права разделены — `open→taken` («может взять»: адресат или `net`) и `taken→done|
  failed|needs_input|open` («взявший»: только `claimed_by`). Тест: вторая сестра и Last
  Order двигают чужую взятую задачу → 403.
- R1-2: `needs_input→taken` не меняет `claimed_by` (исполнитель сохраняет задачу); право —
  автор, а если автор мёртв/закрыт — оператор или Last Order (4.3). Тесты: живой автор
  (оператору 403, автор отвечает, `claimed_by` у исполнителя), мёртвый автор (оператор и
  Last Order отвечают за него).
- R1-3: лимит записей Last Order — скользящее окно 30/час (`LO_MUTATIONS_PER_HOUR`,
  `[предположение]` в коде; сестрам по-прежнему 30 на жизнь). Тест: 31-я запись через час
  проходит.
- R1-4: `filter` в MCP-инструменте `board` нормализуется `strip`-ом до проверки прав.
  Тест на сервере MCP с `" all"`.
- R1-5: у `update` Last Order появился необязательный `args` (edit+approve, 5.2); у сестры
  аргумента нет. Тест: запрос сестры → `last-order`, LO апрувит с исправленными `args`.
  NB: запросы `→ operator` LO по-прежнему не отвечает (адресат — оператор, 3.2), тест
  использует адресата `last-order`.
- R1-6: курсор новой жизни создаёт только `ack`; снапшот повторяется в каждом чтении до
  ack («хотя бы раз», раздел 9/11). Тесты: повтор снапшота до ack, пусто после.
- R1-7: `request.why` и значения `args` проверяются на сырые id как `text`. Тесты.

### Ответ на ревью
- R1-1 · fixed 3091539
- R1-2 · fixed 3091539
- R1-3 · fixed 3091539 (скользящий час, [предположение] — число к T-045)
- R1-4 · fixed 3091539
- R1-5 · fixed 3091539
- R1-6 · fixed 3091539 (курсор двигает только ack)
- R1-7 · fixed 3091539
- R1-8 · не чинил (указание надзора): тревоги admin ключом Last Order — вопрос отдельного
  ключа admin передан в T-043.

### Проверки
```
$ bash dev/check.sh
pytest: ok · 783 passed, 3 skipped in 67.25s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
```
Стенд повторён (проект `t042-check`, hive + mc-server, без LLM; стенд оператора не тронут,
после проверки свёрнут):
```
curl POST /board/entries с X-Operator-Key → 200 (задача #1)
curl POST /board/entries без ключа       → 401 operator_key
docker restart hive; GET /board/entries  → 200, задача #1 на месте
```
