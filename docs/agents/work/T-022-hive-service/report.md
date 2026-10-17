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
