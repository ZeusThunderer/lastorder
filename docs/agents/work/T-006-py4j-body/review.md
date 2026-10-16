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

## Раунд 2 · 2026-10-16 · вердикт: blocked · headless
Проверено: `git diff 77191ad..1db9b24` (раунды 2–3 отчёта) и полный `git diff main...z/T-006-py4j-body` @ 1db9b24

### Проверки (прогнаны надзором)
- `uv run pytest` — 60 passed.
- `python tools/check.py` — **не прогнано**: `python` в headless-шелле — заглушка Windows Store (exit 49),
  `uv run python tools/check.py` отклонён правами headless.
- `docker compose … up -d mc-server body-1` / `run --rm mind-1 python dev/body_smoke.py` — **не прогнано**:
  `docker` отклонён правами headless. Вывод смоука в раунде 3 отчёта надзором не подтверждён.

### Ответ на находки раунда 1
- R1-1 · закрыта: таймауты ожидания бросают `TimeoutError` → `[FAIL]`.
- R1-2 · закрыта: docstring `_call`/`act` о повторе после реконнекта.
- R1-3 · за надзором: перенести дубль строки смерти в заметки T-013 при её постановке.

### Новые изменения
- `wait_in_game` глотает `BodyUnavailable` до внешнего таймаута — верно для старта клиента; два теста
  (`clock.sleeps.count(10.0) >= 3`, переход inGame False→True). Новых находок нет.
- Доставка смоука через stdin (`python - < dev/body_smoke.py`) вместо файла: `dev/` не в образе,
  Dockerfile вне «Можно трогать». Принимаю как эквивалент команды из «Проверок».

### Критерии приёмки
- [x] первые шесть — без изменений с раунда 1.
- [ ] `dev/body_smoke.py` на живом стенде — по отчёту зелёный, надзором не прогнан.

Blocked: нужен оператор — headless-надзор не может прогнать `tools/check.py` и живой смоук
(`docker compose -f docker/compose.yml run -T --rm mind-1 python - < dev/body_smoke.py`). При зелёных
прогонах — `accept` и `dev/merge_task.sh T-006`: blocker/major в коде нет.

## Раунд 3 · 2026-10-16 · вердикт: changes
Проверено: `git diff main...z/T-006-py4j-body` @ 894f3a6. Оператор-сторона: снимает blocked раундов 1–2.

### Проверки (прогнаны надзором)
- `uv run pytest` — 60 passed.
- `python tools/check.py` — все рабочие места целы.
- `docker compose -f docker/compose.yml up -d mc-server body-1` — стенд поднят.
- `docker compose -f docker/compose.yml run --build -T --rm mind-1 python - < dev/body_smoke.py` —
  «smoke passed», 0 FAIL. Но прогон ложноположительный, см. R3-1:
  - `wait_in_game` вернул `hp=0.0` — тело было мёртвым с прошлой сессии;
  - `act goto +5 x` → `{'goal': '16,68,3', 'ok': True}`, «goto finished» через 3 с;
  - `state after goto` → `pos=(11.3, 68.0, 3.7)`, то же, что до goto: тело не сдвинулось;
  - дальше uc сам ожил на задаче `@get`, kill/respawn отработали честно.

### Критерии приёмки
- [x] первые шесть — без изменений с раунда 1, код `runtime/game/py4j_body.py` перечитан, замечаний нет.
- [ ] `dev/body_smoke.py` на живом стенде: «gotoXYZ на +5 по x» смоук не проверяет. Он сообщил
  успех, когда тело стояло на месте.

### Находки
- R3-1 · major · `dev/body_smoke.py:90-99` · смоук не проверяет исход шагов, которые проверяет
  критерий. На мёртвом теле `goto` возвращает `ok`, `busy()` сразу False, и шаг печатает `[ok]`.
  Ожидается:
  - после `wait_in_game`, если `st.dead`, вызвать `respawn()` и взять позицию заново (отдельный шаг);
  - после goto проверить смещение по x: `abs(pos_after.x - (x + 5)) <= 2`, иначе `[FAIL]`;
  - для `@get log 1` сравнить число брёвен в инвентаре до и после: прирост ≥ 1, иначе `[FAIL]`.
  Сейчас брёвна могли лежать в инвентаре заранее, и шаг прошёл бы без работы.
  Вывод нового прогона, где тело стартует мёртвым или живым, — в отчёт.
- R3-2 · minor · `runtime/game/py4j_body.py` · факт для T-014: `act("goto")` на мёртвом теле
  отвечает `ok: True`, задача не встаёт. Можно одной строкой в docstring `act`. Не блокирует.

### Чек-лист проекта
Без замечаний.
