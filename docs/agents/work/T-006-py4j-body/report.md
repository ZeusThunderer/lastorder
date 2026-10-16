# T-006. Отчёт исполнителя

## Раунд 1 · 2026-10-16
Коммиты: 89b2be0, b930e7d

### Сделано
- `runtime/game/py4j_body.py` — `Py4jBody`, порт `GameBody` из `runtime/ports.py` поверх
  моста uc (T-002 смержена, сигнатуры сверены с `runtime/ports.py`):
  - `command()` добавляет `@`, если его нет; `act()` — таблица рычагов `LEVERS`
    (goto, goto_far, look_at, select_hotbar, place_block, bridge_to, pillar_to, tap_key,
    hold_key, chat, command); неизвестный рычаг — `ValueError`, а не молчаливая потеря;
  - `close()` и реконнект рвут соединение только `close()` + `shutdown_callback_server()`,
    `shutdown()` не вызывается никогда;
  - реконнект при `Py4JNetworkError`: задержка 0.5→1→2…сек с потолком, после `retries`
    неудач — `BodyUnavailable`; бесконного цикла без пауз нет (часы/сон внедряются);
  - `state()` → `BodyState` (подкласс `ports.GameState`): поля порта + `max_hp`, `armor`,
    `saturation`, `on_ground`, `dimension`, `yaw`, `pitch`, `vel`; `dead` = `in_game and
    hp ≤ 0` (вне игры hp неизвестен и равен 0.0 — это не труп, иначе путь смерти T-013
    срабатывал бы на отключившемся теле);
  - `respawn()` — `respawnPlayer()` и ожидание hp > 0 с таймаутом (`BodyUnavailable`);
  - колбэки `BodyCallback` (`adris.altoclef.PythonCallback`) → очередь `ports.Event`
  (id, ts_mono, ts_wall, source=body) с флагом `payload["service"]` для строк
    `[Alto Clef]`/`[Tungsten]` (после снятия `§`-кодов); строки не удаляются — фильтрует
    perception; `agentCommandRequest` всегда возвращает `""` (ADR-012);
  - наблюдения: `entities`, `blocks_around`, `inventory` (пустые слоты пропускаются),
    `recent_chat`, `server()`.
- `runtime/game/__init__.py` — экспорт публичного API.
- `tests/test_py4j_body.py` — 30 тестов на подставном gateway/часах, без Minecraft.
- `dev/body_smoke.py` — смоук на живом стенде через `Py4jBody` целиком.

### Проверки
```
$ uv run pytest
58 passed in 0.79s        # 30 новых test_py4j_body.py + 28 прежних
$ uv run python tools/check.py
CHARACTERS: last_order, vesper
Every workplace is intact: files present, links resolve, boxes parse.
```

Живой стенд (mc-server + body-1 up, прогон `dev/body_smoke.py` в mind-контейнере;
образ mind не пересобран — registry недоступен (TLS timeout), свежий `runtime/`
смонтирован в `/app/runtime`, разрешение пакета в образе идёт через `/app`:
```
[ok] wait_in_game: BodyState(in_game=True, hp=20.0, food=20.0, pos=(-28.3,64.0,35.3), held='minecraft:birch_log', …, on_ground=True, dimension='OVERWORLD')
[ok] bind_callbacks: true
[ok] act goto +5 x: {'goal': '-23,64,35', 'ok': True}
[ok] goto finished → state after goto: pos=(-22.2, 65.0, 35.6)   # тело реально дошло
[ok] command get log 1 → get finished → inventory: [ItemStack(name='minecraft:birch_log', count=1, slot=0)]
     recent_chat: строки [Alto Clef]/[Tungsten] все с service=True, ванильные — False
[ok] rcon kill: "Killed LastOrder01"
[ok] wait dead (hp = 0): dead=True hp=0.0
[ok] respawn: hp=20.0, dead=False, pos=(7.5,68.0,9.5)   # точка спавна
EVENTS: chat.line [service]: §2§l§o[Tungsten] …, chat.line: LastOrder01 was killed
smoke passed
```

### Отклонения от задачи
- Сырого дампа прогона E1 в репозитории нет (искал в `docs/`); фикстуры тестов —
  формы из `Py4jEntryPoint.java` (getGameState/getEntitiesInfo/getBlocksAround/
  getInventoryFull), выверенные по E1-документации `docs/agents/unionclef-bridge.md`.
- `dead` ограничен `in_game`: см. выше, иначе тело без соединения считалось бы мёртвым.
- Смоук запускался не `compose run mind-1` из «Проверок», а эквивалентным
  `docker run -i --network container:lastorder-body-1-1 … python - < dev/body_smoke.py`:
  mind-образ не пересобирается без registry (TLS timeout на manifest базового образа),
  а `dev/` в образ не входит. После восстановления доступа к registry команда из
  задачи заработает как есть (образ пересоберётся с `runtime/game`).

### Вне задачи
- `docker compose build` падает на TLS handshake timeout к registry-1.docker.io —
  базовые образы (`python:3.12-slim`, `eclipse-temurin:21-jre-noble`) локально
  отсутствуют. Сказывается и на T-003 (пересборка body под новые моды). Сеть хоста,
  не репозиторий.
- В `getRecentChat` строка смерти приходит дважды («LastOrder01 was killed» ×2) —
  дубль на стороне сервера/uc; для T-013 это надо знать (детектор не должен считать
  это двумя смертями).

### Вопросы
- нет

### Вызовы надзора
- 2026-10-16 · result · 50ebbe5 · VERDICT: blocked (нужен оператор: живой смоук командой
  из «Проверок» или подтверждение эквивалента из отчёта) · cost_usd: ? (вывод CLI не
  распарсен; раунд ревью и статус blocked закоммичены надзором: 77191ad)

## Раунд 2 · 2026-10-16
Коммиты: (этот раунд)

Статус остаётся `blocked`: решение о зачёте живого смоука — за оператором.
Закрыл minor-находки раунда 1, чтобы ветка была готова к accept сразу после решения.

### Ответ на ревью
- R1-1 · fixed · `dev/body_smoke.py`: `wait_busy_clears`/`wait_dead` при таймауте
  бросают `TimeoutError` → `step` печатает `[FAIL]`, «smoke passed» недостижим.
- R1-2 · fixed · `runtime/game/py4j_body.py`: docstring `_call` и `act` предупреждают
  о повторе принятой uc команды после реконнекта (важно для C1/T-014).
- R1-3 · действие надзора (перенос заметки о дубле строки смерти в T-013) — не для
  исполнителя.

### Проверки
```
$ uv run pytest
58 passed
```

## Раунд 3 · 2026-10-16
Коммиты: (этот раунд)

Оператор разблокировал задачу: доступ к registry починен через прокси (Docker Desktop
переведён на `socks5://host.docker.internal:25344`, pull/build работают), пересобран
mind-образ со свежим `runtime/game` внутри, и дано добро на headless-ревью.

### Сделано
- `wait_in_game` переживает ещё не поднявшийся мост: `BodyUnavailable` от невозможности
  подключения (клиент после старта открывает py4j-gateway минутами) больше не съедает
  5 реконнектов за 16 с — внешний цикл опрашивает до общего таймаута, как `body_probe`
  в E1. Нашлось на живом стенде: смоук сразу после пересоздания body-1 падал на входе.
  +2 теста (`test_wait_in_game_survives_a_bridge_that_is_not_up_yet`,
  `test_wait_in_game_returns_state_once_in_game`).
- mind-образ пересобран (внутри настоящий `runtime/game`), смоук прогнан через
  `docker compose -f docker/compose.yml run -T --rm mind-1 python - < dev/body_smoke.py`
  из каталога задачи — отличие от команды из «Проверок» только в способе доставки
  скрипта (stdin вместо файла: `dev/` не входит в образ, Dockerfile вне «Можно трогать»):
  все шаги [ok] — goto −5.5→0.3 по x, `@get log 1` собрал birch_log, kill → dead=True
  hp=0, respawn → hp=20; «smoke passed», 0 FAIL.

### Проверки
```
$ uv run pytest
60 passed
$ docker compose -f docker/compose.yml run -T --rm mind-1 python - < dev/body_smoke.py
… все шаги [ok], smoke passed
```

### Вызовы надзора
- 2026-10-16 · result · 1db9b24 · VERDICT: blocked (ревью «T-006: review round 2», f6a4d7b:
  код принят, эквивалент смоука признан, blocker/major нет; headless-профиль сам не может
  прогнать docker и `python tools/check.py` — ограничение прав, не код; при зелёных прогонах
  надзор обещает accept) · cost_usd: ? (вывод CLI не распарсен).
- Замечено: `dev/call_supervisor.sh` парсит вывод через голый `python`, который на этом
  хосте — заглушка Windows Store (exit 49), поэтому VERDICT/cost в stdout не попадают;
  вердикт живёт в review.md. Правка скрипта вне «Можно трогать» обеих задач.

## Раунд 4 · 2026-10-16
Коммиты: (этот раунд)

### Ответ на ревью
- R3-1 · fixed · `dev/body_smoke.py`:
  - мёртвый старт: `wait_in_game` → при `dead` отдельный шаг «respawn (dead at start)»
    и позиция берётся заново;
  - goto проверяется по факту: `abs(pos.x − (x+5)) ≤ 2`, иначе `[FAIL]`;
  - `@get log 1` проверяется приростом брёвен в инвентаре (`+≥1`, иначе `[FAIL]`);
  - новое: если брёвна уже лежат в инвентаре, `@get log 1` («обеспечить ≥1») честно
    завершается за 0 с без работы — это поймала новая проверка на живом прогоне
    (`[FAIL] @get added a log: log count did not grow (before 1, after 1)`), поэтому
    добавлен шаг очистки инвентаря (rcon kill → wait dead → respawn) перед `@get`.
  - Прогоны (вывод ключевых шагов; полный — в логе команды из «Проверок»):
    1) живой старт, инвентарь пуст: `goto moved the body: x moved +5.5`, `@get added a
       log: +1 log(s)`, kill → dead=True, respawn → hp 20 — «smoke passed»;
    2) **мёртвый старт** (hp=0.0 с прошлой сессии): `respawn (dead at start)` → hp 20,
       `goto moved the body: x moved +6.2`, `@get added a log: +1 log(s)` (задача
       12.3 с), kill → dead, respawn → hp 20 — «smoke passed».
- R3-2 · fixed · строка в docstring `act`: на мёртвом теле uc отвечает `ok: True`,
  задача не встаёт — вызывающий сначала смотрит `state().dead` (заметка для C1/T-014).

### Проверки
```
$ uv run pytest
60 passed
$ uv run python tools/check.py
Every workplace is intact: files present, links resolve, boxes parse.
$ docker compose -f docker/compose.yml run -T --rm mind-1 python - < dev/body_smoke.py
… см. прогоны 1–2 выше, оба «smoke passed», 0 FAIL
```
