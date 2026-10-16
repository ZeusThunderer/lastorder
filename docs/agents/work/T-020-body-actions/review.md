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
