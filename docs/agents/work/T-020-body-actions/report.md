# T-020. Отчёт исполнителя

## Раунд 1 · 2026-10-17
Коммиты: cd598cc (код и тесты), 9d8bd7d (smoke --actions), f89d911 (smoke: догон и прицел), 93a3ff3 (bridge, отчёт)

### Сделано
- `runtime/ports.py`: `EntityInfo.pos` и `BlockInfo.pos` — `tuple[int, int, int] | None = None`;
  умолчание None, остальной код не тронут (`tests/test_ports.py`).
- `runtime/game/py4j_body.py`:
  - `entities(radius=16, limit=32)`: дефект T-006 закрыт — `getEntitiesInfo(limit)` получает
    лимит числа, фильтр `dist_m <= radius` на стороне Python;
  - `pos` у сущностей и блоков из строки `"x, y, z"` (`_parse_cell_pos`): битая строка → None,
    без исключения;
  - рычаги `mine` (`pos`/`positions` → `mineBlocks`), `attack` (→ `punkAny`), `attack_stop`
    (→ `punkStop`), `use_held`, `eat` (`selectHotbar(n)` + `useHeldItem()`), `get`
    (`@get <id> <n>`, count по умолчанию 1), `equip` (`@equip <id>`);
  - `busy()`: `hasActiveTask()` **или** `punkStatus().active`; `stop()`: `stopPathing` + `@stop`
    + `punkStop`.
- `tests/test_py4j_body.py`: тесты на каждый критерий (радиус-фильтр, лимит в uc, pos/битая
  позиция, каждый рычаг, busy от боя, stop останавливает бой). Фикстура E1_ENTITIES дополнена
  дальней коровой (40 м) для фильтра.
- `dev/body_smoke.py --actions`: новый режим (см. «Проверки»); каждый шаг печатает до/после
  (hp, food, pos, busy, инвентарь).
- `docs/agents/unionclef-bridge.md`: раздел «Управление» дополнен подразделом «Рычаги
  выживания (проверены на стенде, T-020)» с формами ответов.

### Проверки
```
$ uv run pytest
95 passed in 3.99s
$ uv run python tools/check.py
CHARACTERS: last_order, vesper
Every workplace is intact: files present, links resolve, boxes parse.

$ docker compose -f docker/compose.yml up -d mc-server body-1   # стенд уже стоял
$ docker compose -f docker/compose.yml run -T --rm mind-1 python - < dev/body_smoke.py --actions
actions smoke passed
```
Ветка релевантных шагов стенда (полный вывод — в git-истории прогона, выжимка здесь):
- mine блока под ногами по позиции из `blocks_around`: `{'ok': True, 'queued': 1}` → блок
  исчез из мира за 2,3 с; инвентарь +1 dirt;
- `act get log 2` → 3 с, инвентарь: 2 × birch_log; `select_hotbar` → `held='minecraft:birch_log'`;
- summon cow (RCON) → `EntityInfo(kind='cow', dist_m=2.6, pos=(-4, 64, -4))`;
- `attack allow=cow` → `{'ok': True, 'allow': ['cow']}`, 10 с наблюдения: hp коровы 10.0 → 10.0;
- `attack_stop` → `{'ok': True}`, busy False;
- strike-путь: goto к корове + `lookAt` (центр хитбокса) + `mouseClick("left")` →
  «cow down, 3 entity hit(s) landed» (железный меч), корова исчезла из entities;
- `eat slot=2` (bread): `{ok: True, result: …}` — food 17 → 17, count 3 → 3: одна интеракция
  не ест хлеб. Доследование: `hold_key use 1700` → food 17 → 20, saturation 6.0, bread 3 → 2.
  Вывод в unionclef-bridge.md: полный приём пищи = selectHotbar + holdKey("use", ~1700).

### Отклонения от задачи
- **punkAny бьёт только игроков — проверено на стенде** (корова, 10 с, hp без изменений).
  Альтернативы из задачи тоже игроки по коду uc: `interactEntity`/`attackPlayer` идут через
  `getEntity` → `EntityTracker.getPlayerEntity` (Py4jEntryPoint.java:477), `@punk` —
  `PlayerArg` + `getValidPlayer` (PunkCommand.java); сам tungsten фильтрует
  `isAcceptable: instanceof PlayerEntity` (PunkPlayerTask.java:175). Поэтому `attack` оставлен
  как punkAny (для игроков, по критерию), а для мобов добавлен рычаг `strike`
  (`{"pos": [x,y,z]}` → `lookAt` + `mouseClick("left")`) — проверен: корова убита. Выбор
  описан здесь и в unionclef-bridge.md. Прицел — центр хитбокса (клетка +0.5, y+0.7): точка
  ног даёт `hit: BLOCK`; моб должен быть в reach (~3 блока) — животное уходит, нужен догон
  goto (первый прогон это поймал, smoke исправлен).
- `busy()` в момент сразу после `punkAny` читает active=False: punkAny поднимает active в
  клиентском потоке (асинхронно). Факт в bridge, поведение busy() не менял (критерий про
  учёт punkStatus, тест на подставном mc есть).
- eat по критерию (selectHotbar + useHeldItem) оставлен как есть; ограничение (одна
  интеракция ≠ приём пищи) задокументировано — менять рычаг на holdKey без ведома надзора
  не стал, это решение для T-021/C1.

### Вне задачи
- Образ `lastorder-mind:dev` на стенде был собран до T-006 (в runtime/ нет game/) — пересобран
  из ветки задачи для прогона smoke. Сам стенд (mc-server, body-1) поднимался до меня.
- В первом прогоне RCON `kill @e[type=cow]` убил 8 сущностей: в мире оставались коровы прошлых
  сессий. Не чинил (мир стенда — вне задачи).

### Вопросы
- нет

### Вызовы надзора
- нет
