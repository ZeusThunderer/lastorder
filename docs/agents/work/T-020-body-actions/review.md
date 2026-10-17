# T-020. Ревью надзора

## Раунд 1 · 2026-10-17 · вердикт: blocked · headless
Проверено: `git diff main...z/T-020-body-actions` @ 7064b19

### Проверки (прогнаны надзором)
- `uv run pytest` — 95 passed
- `python tools/check.py` — Every workplace is intact
- `docker compose ... up -d mc-server body-1` — стенд уже поднят (mc-server, body-1 Up)
- `docker compose ... run -T --rm mind-1 python - --actions < dev/body_smoke.py` — **не прогнан**:
  headless-режим не дал прав на запуск docker. По правилу Headless проверка считается непройденной.

### Критерии приёмки
- [x] `entities(radius, limit=32)` — лимит в uc, фильтр по `dist_m` в Python; тест есть.
- [x] `pos` у `EntityInfo`/`BlockInfo`, битая строка → None; тесты есть.
- [x] Рычаги `mine`, `attack`, `attack_stop`, `use_held`, `eat`, `get`, `equip` + тесты на подставном `mc`.
- [x] `busy()` учитывает `punkStatus().active`, `stop()` зовёт `punkStop`; тесты есть.
- [ ] `body_smoke.py --actions` на стенде — по отчёту пройден, надзором не подтверждён (нет прав).
- [x] `punkAny` по мобу проверен (по отчёту): только игроки; добавлен `strike` (lookAt + клик),
  выбор описан в отчёте и bridge. Довод по коду uc (`PunkPlayerTask.isAcceptable`) принимаю.
- [x] `unionclef-bridge.md` дополнен формами ответов.

### Находки
- R1-1 · minor · `dev/body_smoke.py:13` · в docstring команда `python - dev/body_smoke.py --actions`
  без `<`: в таком виде скрипт не попадёт в stdin · `python - --actions < dev/body_smoke.py`.
- R1-2 · minor · `runtime/game/py4j_body.py` `_lever_eat` · по стенду `selectHotbar` + `useHeldItem`
  не съедает еду; рычаг сделан по букве критерия. Решение (перевести `eat` на `holdKey("use", ~1700)`
  или оставить до T-021) — за оператором/надзором, исполнитель поступил верно, не меняя сам.

### Чек-лист проекта
Без замечаний. Изменения в пределах «Можно трогать»; `strike` — новый рычаг сверх списка, обоснован
отклонением «punkAny только по игрокам», которое задача прямо разрешала.

## Раунд 2 · 2026-10-17 · вердикт: changes · headless
Проверено: `git diff main...z/T-020-body-actions` @ 7854974 (код не менялся с раунда 1)

### Проверки (прогнаны надзором)
- `uv run pytest` — 95 passed
- `python tools/check.py` — Every workplace is intact
- `docker compose ... ps` — mc-server (healthy), body-1 Up
- `docker compose -f docker/compose.yml run -T --rm mind-1 python - --actions < dev/body_smoke.py` —
  **exit 1, `1 step(s) failed: cow visible in entities`**. Образ mind-1 не пересобирался (сборку
  headless не дали), прогон на образе, который исполнитель собрал из ветки.
  Пройдено: mine под ногами (`{'queued': 1, 'ok': True}`, блок исчез за 2,4 с, +1 dirt);
  `get log 2` (73 с, 2 × birch_log); `select_hotbar` → `held='minecraft:birch_log'`;
  `attack allow=cow` → `{'allow': ['cow'], 'avoid': [], 'ok': True}`, `attack_stop` → `{'ok': True}`;
  `eat slot=2` (beef) — food 18 → 18, beef 3 → 3; `hold_key use 1700` — food 18 → 20, beef 3 → 2.
  Не пройдено: корова не увидена, strike прошёл вхолостую (`cow down, 0 entity hit(s) landed`).

### Критерии приёмки
- [x] `entities(radius, limit=32)`, `pos`, рычаги с тестами, `busy()`/`stop()` — как в раунде 1.
- [ ] `body_smoke.py --actions` на стенде — красный (R2-1), бой по животному на этом прогоне
  не проверен (R2-2).
- [x] `punkAny` по мобу: только игроки. Принято по коду uc (`PunkPlayerTask.isAcceptable`) и прогону
  исполнителя; мой прогон этого не подтверждает и не опровергает (R2-3).
- [ ] `eat` не ест (R2-4).
- [x] `unionclef-bridge.md` дополнен.

### Находки
- R2-1 · major · `dev/body_smoke.py:194` · корова призывается у `feet`, снятых до `@get log 2`, а
  `@get` увёл тело с (-8,64,-10) на (58,63,52), за ~90 м. В `entities(24)` её нет, smoke падает.
  · брать позицию заново (`body.state()`) прямо перед `summon`.
- R2-2 · major · `dev/body_smoke.py:219-234` · `strike_the_cow` и «cow left entities» проходят, если
  коровы в `entities` не было с самого начала: «cow down, 0 entity hit(s)» засчитано как успех.
  · требовать, чтобы корова была видна до удара и чтобы был хотя бы один `hit == ENTITY`
  (или падение hp по RCON). Иначе — FAIL.
- R2-3 · minor · `dev/body_smoke.py:196,204` · `@e[type=cow,limit=1]` берёт произвольную корову мира
  (в мире есть коровы прошлых сессий), cleanup `kill @e[type=cow]` убивает их все. · призывать с тегом
  (`{Tags:["t020"]}`), здоровье и cleanup — по `@e[tag=t020]`.
- R2-4 · major · `runtime/game/py4j_body.py:212` `_lever_eat` · решение по R1-2: рычаг `eat` должен
  съедать. Оба прогона показали, что `useHeldItem` не ест, а `holdKey("use", ~1700)` ест.
  · `eat` = `selectHotbar(slot)` + `holdKey("use", ms)`, `ms` из аргументов, по умолчанию 1700;
  тест на подставном `mc`; поправить строку в `unionclef-bridge.md`. `use_held` остаётся как есть.
  Критерий в `task.md` поправлен.
- R2-5 · minor · `dev/body_smoke.py:13` · R1-1 не закрыта: в docstring `python - dev/body_smoke.py --actions`
  · `python - --actions < dev/body_smoke.py`.

### Чек-лист проекта
Без замечаний. LLM не вызывается, `@gamer` нет в `LEVERS`, `unionclef/` и `runtime/perception/` не тронуты.
