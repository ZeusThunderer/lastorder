---
id: T-022
title: Сервис hive и HiveClient — HTTP API портов, срок жизни, RCON
stage: E2
status: in_progress
criticality: critical
branch: z/T-022-hive-service
created: 2026-10-17
after: [T-008, T-012]
---

# T-022. Сервис hive и HiveClient — HTTP API портов, срок жизни, RCON

## Цель
Контейнер `hive` поднимается в compose и отдаёт по HTTP порты `Memory`, `NodeRegistry`, `NodeComms`
(ADR-024) поверх хранилища T-012. Он ведёт срок жизни узлов (45 мин) и принудительную смерть через
RCON. В mind есть `HiveClient`, реализующий эти три порта: таймауты, повторы, буфер исходящего
при недоступном hive.

## Контекст
- NETWORK.md §3 (процессы, HTTP, входящих соединений к mind нет), §4 (порты), §7 (жизненный цикл,
  причины `game|ttl|stuck`), §9 (отказ hive: minds живут на последнем срезе, исходящее
  буферизуется, смерть при недоступном hive — завещание локально).
- ADR-024 (HTTP, без шины), ADR-026 (`request_death(serial, "stuck")` → RCON `/kill` без порога hive),
  ADR-018 (номер выдаёт hive).
- `runtime/knowledge/store.py` (T-012); `runtime/ports.py`.
- RCON-клиент: `tools/body_probe.py` (`rcon()`) — образец; `docker/compose.yml` (`RCON_PASSWORD` из env).
- GOALS_COMMS.md (T-008): inbox, TTL сообщений, лимиты. Режиссёр болтовни — T-017: здесь
  `chatter_turn` всегда возвращает `None`.
- Слот → аккаунт: `LastOrder0N` (ADR-018); hive знает соответствие из запроса `birth(slot, …)`.

## Объём
Можно трогать:
- `runtime/hive/app.py`, `runtime/hive/lifecycle.py`, `runtime/hive/rcon.py` (новые)
- `runtime/hive/client.py` (новый: `HiveClient`)
- `docker/compose.yml` — сервис `hive` (образ `lastorder-mind:dev`, команда uvicorn, volume
  `knowledge`, `RCON_*` из env, порт только внутри docker-сети); остальные сервисы не менять
- `docker/mind/Dockerfile` — только если нужен файл или зависимость для hive
- `pyproject.toml` — зависимость `httpx`, если её нет
- `tests/test_hive_api.py`, `tests/test_hive_client.py`, `tests/test_hive_lifecycle.py`
- файлы задачи

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`, `.env`
- `runtime/knowledge/` — только вызывать; дефект хранилища — в отчёт
- `network_mode` слотов, публикацию портов наружу

## Критерии приёмки
- [ ] Эндпоинты один к одному с методами портов: `Memory` (6), `NodeRegistry` (4), `NodeComms` (3).
  Payload — dataclass из `runtime/ports.py` в JSON; схема ошибок одна. Тест через `TestClient`
  на каждый эндпоинт.
- [ ] `HiveClient` проходит `isinstance(…, Memory/NodeRegistry/NodeComms)` и тот же набор тестов,
  что сервер, через транспорт `TestClient` (без сети).
- [ ] Клиент: таймаут и повтор с растущей паузой; при недоступном hive `submit_candidate`,
  `submit_vote`, `send`, `store_core_memory` складываются в буфер (на диск в volume `run`) и
  доливаются по порядку после восстановления; чтения отдают последний успешный ответ с пометкой
  `stale`. Тест с выключаемым сервером.
- [ ] Срок жизни: hive хранит `born_at` и `ttl_min` (`MAX_LIFE_MIN`, по умолчанию 45), фоновой
  задачей раз в 30 с делает RCON `/kill LastOrder0N` узлу с истёкшим сроком, один раз на узел, и
  пишет событие. Тест с подставными часами и подставным RCON.
- [ ] `request_death(serial, "stuck")` → RCON `/kill` сразу, без своего порога (ADR-026); причину
  `stuck` hive запоминает и проверяет при `retire`. Тест.
- [ ] `alive()` — живые узлы со слотами; при `birth` в слоте с ещё живым узлом прежний помечается
  `lost` (mind перезапустился без завещания) — событие в лог hive, номер не переиспользуется. Тест.
- [ ] Inbox: `send` кладёт, `inbox(serial)` отдаёт и помечает доставленным, TTL из T-008. Тест.
- [ ] `docker compose -f docker/compose.yml up -d hive` поднимает сервис; `curl` из `mind-1`
  (`docker compose exec`) к `/health` → 200; volume `knowledge` после рестарта сохраняет номера и
  записи. Вывод в отчёте.

## Проверки
```
uv run pytest
uv run python tools/check.py
docker compose -f docker/compose.yml up -d hive
```

## Ограничения
- Hive — единственный писатель базы; mind пишет только через API.
- RCON-пароль и адреса — только из env; в логах пароля нет.
- Чат и сообщения узлов — наблюдение: hive их не исполняет.
- Никаких вызовов LLM (цели сети — T-016, болтовня — T-017).

## Заметки надзора
