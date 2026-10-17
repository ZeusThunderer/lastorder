# T-022. Отчёт исполнителя

## План 1 · 2026-10-17

Файлы: `runtime/hive/{app,lifecycle,rcon,client}.py` (новые), `docker/compose.yml` (сервис hive),
`docker/mind/Dockerfile` (`--extra hive`), `pyproject.toml` + `uv.lock`, тесты
`tests/test_hive_{api,client,lifecycle}.py`.

1. **rcon.py** — минимальный Source RCON-клиент по образцу `tools/body_probe.py`: адрес и пароль
   только из env (`RCON_HOST`, `RCON_PASSWORD`), `kill(slot)`; пароль не попадает в repr/логи.
2. **lifecycle.py** — `Lifecycle` поверх `KnowledgeStore` (только вызовы):
   - `birth(slot, traits)`: если в слоте ещё жив узел — прежний помечается `lost`, событие
     `life.lost`, номер не переиспользуется (счётчик store); пишет birth.json c `ttl_min`
     из `MAX_LIFE_MIN` (45);
   - `scan(now)` (фон, раз `HIVE_TTL_SCAN_S` = 30 с): истёкшим узлам — RCON `/kill` ровно один
     раз на узел, событие `life.expired`; отказ RCON — событие `life.rcon_failed`, повтор на
     следующем проходе;
   - `request_death(serial, "stuck")` — `/kill` сразу, без своего порога (ADR-026), причина
     запоминается; при `retire` hive исправляет причину на `stuck` (и на `ttl` после убийства по
     сроку): RCON-смерть со стороны выглядит как `game`;
   - состояние (lost/stuck/kill) — `lifecycle.json` в volume `run`, при старте сверка с birth.json
     живых узлов (store не отдаёт born_at публично, править `runtime/knowledge/` нельзя — читаю
     birth.json парсером `BirthFile`);
   - события hive — JSONL `events.jsonl` в volume `run`.
3. **app.py** — FastAPI: эндпоинты один к одному с методами портов
   (`/memory/*` 6, `/registry/*` 4, `/comms/*` 3) + `GET /health`; payload — dataclass из
   `runtime/ports.py` в JSON; единая схема ошибок `{"error": {"kind", "message"}}` для всех,
   включая 422 валидации. Inbox в памяти: `send` кладёт (лимит длины `MSG_MAX_CHARS` и живой
   адресат — иначе событие `comms.drop`), `inbox(serial)` отдаёт и помечает доставленным, TTL
   `MSG_TTL_S` = 600 из T-008. `chatter_turn` → всегда `None` (режиссёр — T-017). LLM нет.
4. **client.py** — `HiveClient` (Memory + NodeRegistry + NodeComms поверх httpx):
   - таймаут, повтор с растущей паузой;
   - при недоступном hive `submit_candidate`, `submit_vote`, `send`, `store_core_memory` —
     в буфер на диск (`run/hive_outbox.jsonl`), доливаются по порядку после восстановления;
     операции над безвозвратно отвергнутыми (4xx) при доливке отбрасываются;
   - чтения отдают последний успешный ответ, пометка `stale` — атрибут клиента `client.stale`
     (payload'ы портов — frozen dataclass, менять их нельзя); чтение без кеша при недоступном
     hive — исключение `HiveUnavailable` (`chatter_turn` → None: ход можно пропустить);
   - транспорт подключаемый: `httpx.Client`/`TestClient` без сети.
5. **compose** — сервис `hive`: образ `lastorder-mind:dev`, `uvicorn runtime.hive.app:app`,
   volumes `knowledge` и `hive-run`, `RCON_*` из env, порт только внутри docker-сети; остальные
   сервисы не трогаю. Dockerfile: `uv sync … --extra hive`.
6. **pyproject** — `httpx` уже есть; добавляю в dev-группу self-extra `last-order[hive]`, чтобы
   обычный `uv run pytest` видел fastapi (тесты API). Это чуть шире фразы задачи про httpx —
   иначе проверки из «Проверок» не зелёные локально.

Тест на каждый критерий: эндпоинты (TestClient, все 13 + health + схема ошибок), клиент
(isinstance по трём портам + те же сценарии через TestClient-транспорт + буфер/stale на
выключаемом транспорте + повторы), lifecycle (подставные часы и RCON: срок 45 мин один /kill,
stuck сразу + поправка причины в retire, lost при повторном birth, отказ RCON, восстановление
после рестарта), inbox TTL и доставленность. Стенд: `docker compose up -d hive`, health из
mind-1, сохранность knowledge через рестарт hive.

Риски: прямое чтение birth.json (п. 2) и пометка stale на клиенте (п. 4) — толкования в рамках
«нельзя трогать runtime/knowledge/» и сигнатур портов; готов сделать иначе по замечанию.

## Раунд 1 · 2026-10-17
Коммиты: d9ab828 (план + зависимости), 3a2838a (ядро: app/lifecycle/rcon/client + docker), 2adf890 (тесты); настоящий коммит — отчёт раунда 1.

### Сделано
- `runtime/hive/rcon.py` — минимальный Source RCON-клиент (по `tools/body_probe.py`): адрес/пароль
  из env, пароль вне repr/исключений/событий; `kill_confirmed()` отличает «Killed …» от
  «No entity was found».
- `runtime/hive/lifecycle.py` — `Lifecycle` + `HiveLog`: срок жизни (`MAX_LIFE_MIN` = 45, скан раз
  `HIVE_TTL_SCAN_S` = 30 c, один подтверждённый `/kill` на узла с повторами при отказе),
  `request_death(serial, "stuck")` — сразу без порога (ADR-026), поправка причины при `retire`
  (`stuck` по факту запроса, `ttl` по подтверждённому kill), `lost` при повторном `birth` в слоте —
  прежний закрывается аварийным завещанием (`emergency_will`, причина `game`), состояние в
  `lifecycle.json` (volume `run`) со сверкой birth.json на старте, события в `events.jsonl`.
- `runtime/hive/app.py` — FastAPI: 13 эндпоинтов один к одному с методами портов + `GET /health`;
  `HiveService` реализует три порта напрямую (эндпоинты — механический мост через кодеки);
  inbox в памяти с TTL `MSG_TTL_S` = 600 и доставкой один раз; `chatter_turn` → всегда `None`;
  единая схема ошибок `{"error": {kind, message}}`; фоновый скан через `asyncio.to_thread`;
  замок на состояние Lifecycle и на inbox.
- `runtime/hive/client.py` — `HiveClient` (Memory+NodeRegistry+NodeComms поверх httpx) + общие
  с сервером кодеки; повторы с растущей паузой на ошибку соединения и 5xx; офлайн: 4 операции в
  буфер `run/hive_outbox.jsonl` (доливка по порядку перед любым следующим вызовом и перед
  `retire`), чтения из кеша с `client.stale`, чтение без кеша — `HiveUnavailable`,
  `chatter_turn` → None; `birth`/`request_death`/`retire` не буферизуются.
- `docker/mind/Dockerfile` — `git` в образе и `--extra hive`; `docker/compose.yml` — сервис
  `hive` (внутренний порт только, volumes `knowledge` + `hive-run`, `--workers 1` с комментарием).
- `pyproject.toml`/`uv.lock` — dev-группа ссылается на `last-order[hive]`: обычный `uv run pytest`
  видит fastapi.

### Проверки
```
$ uv run pytest
316 passed, 1 warning in 12.78s        # 1 warning — StarletteDeprecation про httpx в TestClient
$ uv run python tools/check.py
Every workplace is intact: files present, links resolve, boxes parse.
$ docker compose -f docker/compose.yml up -d --build hive   # из worktree-копии compose
Container lastorder-hive-1 Started
# /health из mind-1 (docker compose exec; curl в python:3.12-slim нет — python urllib):
health from mind-1: 200 {"status":"ok"}; birth → serial 1; alive → [{"serial":1,...}]
$ docker compose -p lastorder restart hive && … exec mind-1 …
health after restart: 200; alive после рестарта: serial 1 восстановлен из volume;
вторая birth → serial 2 (номер не переиспользуется); core_memory записан и прочитан.
$ docker compose -p lastorder exec hive git -C /knowledge log --oneline
до рестарта:  097a69a init: layout / d0de7f2 birth: 00001 LastOrder01
после:        + eeb9d67 birth: 00002 LastOrder02 / 106c505 core: 00002
```
Порт hive наружу не публикуется (`docker port lastorder-hive-1` — пусто), в логах пароля нет.
Стенд оставлен поднятым (hive + mc-server + body-1; mind-1 завершает body_probe и гаснет сам —
это его штатный `restart: no`).

### Ответ на план-ревью (P1-1…P1-10)
- P1-1 · fixed 3a2838a — `git` в `docker/mind/Dockerfile`, корень базы из env `KNOWLEDGE_DIR`,
  volume `knowledge` объявлен; git-лог до/после рестарта в «Проверках».
- P1-2 · fixed 3a2838a — `lost`-узел закрывается `emergency_will` → `store.retire(serial, "game", …)`
  и при повторном birth, и при сверке на старте (дубли слота тоже); имя узла — из пресета
  (`NAME_PRESET`, по умолчанию lab); обращение к наследнику — нейтральное «наследник»
  (в `NamingPreset` нет формы наследника, а `runtime/lastorder/` вне объёма задачи).
  Тесты: api (409 на запись, will.md с `emergency: true`), lifecycle, стартовая сверка.
- P1-3 · fixed 2adf890 — буфер доливается перед любым вызовом и обязательно до `retire`;
  `birth`/`request_death`/`retire` при недоступном hive бросают `HiveUnavailable`. Тест
  «буфер → сервер вернулся → retire → кандидат в базе».
- P1-4 · fixed 3a2838a — один замок на состояние Lifecycle и на inbox; sync-эндпоинты в пуле
  потоков, скан через `asyncio.to_thread`; compose: `--workers 1` с комментарием. Тест двух
  параллельных birth в один слот (один живой, один lost).
- P1-5 · fixed 3a2838a — таблица: UnknownNode → 404, AlreadyRetired → 409, Invalid* и валидация
  → 422, прочие StoreError → 503; доливка отбрасывает только 4xx и пишет событие
  `outbox.drop` в `hive_client_events.jsonl`. Тесты 503-повтора и отбрасывания.
- P1-6 · fixed 3a2838a — успех kill только по ответу сервера (`kill_confirmed`), иначе
  `life.rcon_failed` и повтор на следующем проходе; `ttl` в retire подставляется только после
  подтверждённого kill; `request_death` с причиной ≠ stuck → 422, неизвестный/мертвый serial →
  404/409 без RCON. Тесты на оба ответа подставного RCON.
- P1-7 · fixed 2adf890 — выбран вариант «дубль допустим»: при ошибке соединения повтор сразу,
  при read-timeout — без повтора, запись в буфер (возможен дубль `send`; сообщение — наблюдение,
  кандидат/голос/core в хранилище идемпотентны). Тест пауз и таймаута.
- P1-8 · fixed — broadcast (`all`) не введён молча: `NodeMessage.to_serial: int` его не выражает,
  развёртка по живым — за T-017; путь буфера клиента — env `RUN_DIR` (по умолчанию `run`),
  у mind-1 volume `run` появится в его задачах, чужие сервисы compose не трогал.
- P1-9 · fixed 2adf890 — тесты: пароля нет в repr RCON-клиента и в `events.jsonl`; кандидат с
  `minecraft:…` → 422 в единой схеме и при доливке отбрасывается без бесконечных повторов.
  `leak_check` хранилища в hive — `None`: словарь сырых id есть только у perception мозга.
- P1-10 · принято к сведению — свежий volume `knowledge` праймер T-010 уже не получит (грузится
  только в пустую базу); не чинил, риск для T-010.

### Отклонения от задачи
- `pyproject.toml` тронут шире фразы про httpx: dev-группа получила self-extra `last-order[hive]`
  (fastapi/uvicorn уже были объявлены extra'ом) — иначе обычный `uv run pytest` из «Проверок» не
  видит fastapi и красный. Согласовано план-ревью (P1-1/шапка).
- «curl из mind-1»: в `python:3.12-slim` нет curl — проверка выполнена `python urllib` из того же
  контейнера/сетевого пространства, суть критерия (HTTP 200 до `/health` из пространства тела)
  соблюдена.
- `docker compose restart hive` вместо даункаста: `down` запрещён протоколом стенда.
- C-1 (minor, чекер): `uv.lock` изменён вне списка «Можно трогать» — механическое следствие правки
  `pyproject.toml` (без него `uv sync --frozen` в образе и локально ломается); правка раскрыта ещё
  в плане и согласована план-ревью.
- C-2 (minor, чекер): `HEIR_ADDRESS` — строка в коде (`lifecycle.py`). Формы обращения к наследнику
  нет в `NamingPreset` (ADR-018), а `runtime/lastorder/` вне объёма задачи; строка нейтральна к
  пресетам («наследник»), имя самого узла берётся из пресета через `NAME_PRESET`.
- C-3 (minor, чекер): запасной RCON-пароль по умолчанию в `RconClient.from_env` — то же значение,
  что уже лежит в `docker/compose.yml` на main; из env переопределяется, в repr/логи/события не
  попадает (тест P1-9 и стенд подтверждают).

### Вне задачи
- `/run/lock` в volume `hive-run` — пустой каталог базового образа Debian, попавший в свежий
  volume при первом монтировании; не трогал.
- `fastapi.testclient` даёт StarletteDeprecationWarning («install httpx2») — шум новой версии
  starlette, к задаче отношения не имеет.
- Стоимость plan-вызова надзора не видна: вывод скрипта обрезан харнессом (вердикт и находки
  зафиксированы в review.md полностью).

### Вопросы
- нет

### Вызовы надзора
- 2026-10-17 · plan · d9ab828 · VERDICT: revise · cost_usd: unknown

### Самопроверка
- lo-checker: раундов 1, CHECK: clean
- Находок, требующих исправления, не было; три minor чекера (C-1 uv.lock вне списка, C-2
  HEIR_ADDRESS строкой, C-3 запасной RCON-пароль) перечислены в «Отклонениях» выше с обоснованием.

## Раунд 2 · 2026-10-17
Коммиты: 921954e (take changes), a630116 (исправления R1-1..R1-8), 84a222e (C-1 чекера);
настоящий коммит — отчёт раунда 2.

### Сделано
- Клиент: `inbox` и `chatter_turn` исключены из кеша — офлайн отвечают `[]` и `None` с
  `stale = True` (R1-1). Порядок доливки усилен: при непродолившемся буфере новая буферизуемая
  запись дописывается в хвост, прямые записи (`birth`, `request_death`, `retire` — явный флаг
  `direct`) отказывают с `HiveUnavailable` (R1-2 + C-1 раунда 2: у `birth` есть `decode`, флаг
  закрывает обход).
- Lifecycle: `life.rcon_failed` пишется при первой неудаче и при смене ошибки/ответа, повтор kill
  на каждом скане сохранён (R1-3). Неудачное закрытие `lost` ретраится следующим `birth` в слоте,
  `require_node` для lost — отказ (R1-8).
- RCON: `kill_confirmed` — положительное совпадение (ответ начинается с `Killed`) (R1-4);
  запасного пароля в коде нет — без `RCON_PASSWORD` kill отказывает событием, hive стартует (R1-5).
- Compose: volume `hive-run` монтируется в `/hive-run`, не поверх системного `/run` образа (R1-6).
- Тесты: сценарии портов параметризованы по `HiveService` и `HiveClient` (R1-7, «по желанию» —
  сделано); по тесту на каждое исправление.

### Проверки
```
$ uv run pytest
327 passed, 1 warning in 20.81s
$ uv run python tools/check.py
Every workplace is intact: files present, links resolve, boxes parse.
$ docker compose -f docker/compose.yml up -d --build hive   # из worktree-копии compose
lastorder-hive-1 Up; /health → 200; alive → [{serial 1},{serial 2}] — состояние и git-история
базы пережили пересоздание контейнера; событий rcon_failed для офлайн-игрока — по одному на отказ.
```

### Отклонения от задачи
- Как в раунде 1 (pyproject self-extra, uv.lock, curl→urllib, restart вместо down, HEIR_ADDRESS).

### Вне задачи
- В volume `hive-run` остался каталог `lock` от монтирования раунда 1 поверх системного `/run`;
  `docker volume` запрещён протоколом — не удалял, при желании чистится оператором.

### Вопросы
- нет

### Ответ на ревью
- R1-1 · fixed a630116
- R1-2 · fixed a630116
- R1-3 · fixed a630116
- R1-4 · fixed a630116
- R1-5 · fixed a630116
- R1-6 · fixed a630116
- R1-7 · fixed a630116
- R1-8 · fixed a630116

### Вызовы надзора
- 2026-10-17 · plan · d9ab828 · VERDICT: revise · cost_usd: unknown
- 2026-10-17 · result · 3e46f97 · VERDICT: changes · cost_usd: unknown

### Самопроверка
- lo-checker: раундов 2, CHECK: clean
- C-1 (birth обгонял зависший буфер из-за decode) · fixed 84a222e — явный флаг `direct`, тест
  `test_birth_waits_behind_a_stuck_buffer`.
- C-2 (раунд 2 отчёта не был дописан до сдачи) · закрыт этим раундом отчёта.
