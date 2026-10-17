---
id: T-020
title: Тело — позиции в наблюдении и рычаги добычи, боя, еды
stage: E2
status: review
criticality: normal
branch: z/T-020-body-actions
created: 2026-10-17
after: [T-006]
---

# T-020. Тело — позиции в наблюдении и рычаги добычи, боя, еды

## Цель
У `Py4jBody` есть всё, из чего T-021 построит варианты шага: позиции сущностей и блоков в
наблюдении и рычаги «добыть», «атаковать», «съесть», «взять в руку», «остановить бой». Каждый рычаг
проверен на живом стенде.

## Контекст
- `runtime/game/py4j_body.py` (T-006): `LEVERS` (`goto`, `goto_far`, `look_at`, `select_hotbar`,
  `place_block`, `bridge_to`, `pillar_to`, `tap_key`, `hold_key`, `chat`, `command`), геттеры
  `entities()`, `blocks_around()`, `inventory()`.
- `runtime/ports.py`: `EntityInfo(kind, name, dist_m)`, `BlockInfo(name)` — позиций нет.
- **Дефект T-006:** `entities(radius)` передаёт радиус в `getEntitiesInfo(int limit)`
  (`unionclef/src/main/java/adris/altoclef/Py4jEntryPoint.java:1346`): параметр — лимит числа, а не радиус.
- Рычаги uc (`unionclef/docs/features/AGENT_PY4J_LEVERS.md`, `Py4jEntryPoint.java`):
  `mineBlocks(positions)` :5190, `punkAny(allow, avoid)` :5010 и `punkStop()`, `useHeldItem()` :2167,
  `selectHotbar`; команды altoclef `@get <item> <n>`, `@equip <item>`, `@food <n>`, `@stop`
  (`commands/`). В uc `position` приходит строкой `"x, y, z"`.
- `dev/body_smoke.py` — образец проверки на стенде.

## Объём
Можно трогать:
- `runtime/game/py4j_body.py`
- `runtime/ports.py` — только добавить поле `pos: tuple[int, int, int] | None = None` в `EntityInfo` и
  `BlockInfo` (с умолчанием: остальной код не ломается)
- `tests/test_py4j_body.py`, `tests/test_ports.py`
- `dev/body_smoke.py` — новый режим `--actions`
- `docs/agents/unionclef-bridge.md` — раздел «Управление»: проверенные рычаги и их ответы
- файлы задачи

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`
- `runtime/perception/` — имена не переводятся здесь

## Критерии приёмки
- [ ] `entities(radius, limit=32)`: `getEntitiesInfo(limit)` и фильтр по `dist_m <= radius` на стороне
  Python. Тест на подставном ответе: дальние отброшены, лимит уходит в uc.
- [ ] `EntityInfo.pos` и `BlockInfo.pos` заполняются из `position`; битая строка → `None`, без исключения.
  Тест.
- [ ] Новые рычаги в `LEVERS`, у каждого своя функция и тест с подставным `mc`:
  - `mine` (`{"pos": [x,y,z]}` или список `positions`) → `mineBlocks`;
  - `attack` (`{"allow": [...], "avoid": [...]}`) → `punkAny`; `attack_stop` → `punkStop`;
  - `use_held` → `useHeldItem`;
  - `eat` (`{"slot": n, "ms": 1700}`) → `selectHotbar(n)` + `holdKey("use", ms)` (review R2-4:
    одна `useHeldItem` не съедает еду);
  - `get` (`{"item": id, "count": n}`) → команда `@get <id> <n>`;
  - `equip` (`{"item": id}`) → `@equip <id>`.
- [ ] `busy()` учитывает и активную задачу altoclef, и бой tungsten (`punkStatus`). `stop()` останавливает
  оба. Тест.
- [ ] `dev/body_smoke.py --actions` на живом стенде: добыть блок под ногами по позиции из
  `blocks_around`, `@get log 2`, взять в руку, `attack` по ближайшему животному и `attack_stop`,
  `eat` при наличии еды. Каждый шаг — до/после (инвентарь, hp, `busy`). Вывод в отчёте.
- [ ] Вызов `punkAny` с типом моба (`cow`, `zombie`) проверен на стенде. Если tungsten бьёт только
  игроков — записать в отчёт и `unionclef-bridge.md` и сделать `attack` через другой рычаг
  (`interactEntity`/`attackPlayer` или `@punk`); выбор описать.
- [ ] `unionclef-bridge.md` «Управление» дополнен проверенными рычагами и формой ответов.

## Проверки
```
uv run pytest
uv run python tools/check.py
docker compose -f docker/compose.yml up -d mc-server body-1
docker compose -f docker/compose.yml run --rm mind-1 python dev/body_smoke.py --actions
```

## Ограничения
- py4j: команды uc только с префиксом `@`; закрывать `gateway.close()`, не `shutdown()`.
- `@gamer` не добавлять в `LEVERS`.
- Никаких вызовов LLM.

## Заметки надзора
Разблокировано 2026-10-17 (ADR-028): headless-надзору открыт стенд (`docker compose`), задача снова на ревью — прогнать `body_smoke.py --actions` и вынести вердикт.
2026-10-17, раунд 2: критерий `eat` изменён на `holdKey("use", ms)` по данным стенда (R2-4).
