# T-006. Ревью надзора

## Раунд 1 · 2026-10-16 · вердикт: blocked · headless
Проверено: `git diff main...z/T-006-py4j-body` @ 50ebbe5

### Проверки (прогнаны надзором)
- `uv run pytest` — 58 passed
- `python tools/check.py` — все рабочие места целы
- `docker compose -f docker/compose.yml up -d mc-server body-1` / `run --rm mind-1 python dev/body_smoke.py` —
  **не прогнано**: в headless-режиме Bash с `docker compose` запрещён. `docker ps` показывает
  `lastorder-mc-server-1` (healthy) и `lastorder-body-1-1` (Up), но сам смоук надзор не запускал.
  Вывод в report.md раунд 1 получен не командой из «Проверок», а `docker run --network container:…`
  со смонтированным `runtime/` (образ mind не пересобирается: registry недоступен).

### Критерии приёмки
- [x] `command("get log 1")` и `command("@get log 1")` → `@get log 1` — `test_command_adds_the_at_prefix`,
  `test_command_keeps_the_existing_prefix`; рычаг `command` тоже нормализует.
- [x] `close()` не вызывает `shutdown()` — `test_close_never_calls_shutdown`; `_drop` только
  `close` + `shutdown_callback_server`, проверено и в тесте реконнекта.
- [x] Реконнект с растущей задержкой и `BodyUnavailable` — `test_reconnect_backoff_grows_and_ends_in_body_unavailable`
  (паузы 0.5/1/2 с потолком), `test_reconnect_recovers_after_a_transient_network_error`.
- [x] `state()` → `BodyState(GameState)` с `in_game`, `dead` — тесты на разбор образца. Образец взят из
  `Py4jEntryPoint.java` и bridge-doc, сырого дампа E1 нет — отклонение объяснено, принимаю.
  `dead` ограничен `in_game` — разумно, принимаю.
- [x] `respawn()` → `respawnPlayer()` + ожидание hp > 0 с таймаутом — два теста.
- [x] Чат и колбэки с отметкой времени, `service=True` без удаления — тесты есть.
- [ ] `dev/body_smoke.py` на живом стенде — вывод в отчёте есть, но надзором не прогнан (см. выше).

### Находки
- R1-1 · minor · `dev/body_smoke.py:56-77,104,113` · `wait_busy_clears` и `wait_dead` при таймауте
  возвращают False / живое состояние, а `step` печатает `[ok]` и смоук заканчивается «smoke passed».
  Ожидается: таймаут ожидания — `[FAIL]` (бросить исключение или проверить результат).
- R1-2 · minor · `runtime/game/py4j_body.py:301-313,404-409` · `_call` повторяет после реконнекта и
  неидемпотентные вызовы (`act`, `command`, `respawnPlayer`): если `Py4JNetworkError` случился после
  того, как uc принял команду, она уйдёт второй раз. Ожидается: хотя бы строка в docstring `act`/`_call`
  о повторе; для T-014 это важно (повторный `goto`/`chat`).
- R1-3 · minor · `report.md` «Вне задачи» · дубль строки смерти в `getRecentChat` — перенести в
  заметки T-013 при её постановке (действие надзора, не исполнителя).

### Чек-лист проекта
Без замечаний: изменения в пределах «Можно трогать», `unionclef/`, `NetTyan/`, `runtime/perception/`,
`tools/body_probe.py` не тронуты; RCON-пароль из env; вызовов LLM нет; сырые имена не переводятся.

Blocked: нужен оператор — прогнать живой смоук командой из «Проверок» (или подтвердить, что эквивалентный
`docker run --network container:lastorder-body-1-1 …` из отчёта засчитывается, пока registry недоступен).
При зелёном смоуке и без новых находок — `accept`; R1-1…R1-2 minor, merge не блокируют.
