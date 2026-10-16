---
id: T-009
title: Режимы восприятия plain/masked и фильтр служебных строк uc
stage: E2
status: ready
criticality: critical
branch: z/T-009-perception-modes
created: 2026-10-16
---

# T-009. Режимы восприятия plain/masked и фильтр служебных строк uc

## Цель
`runtime/perception` работает в двух режимах из ADR-020: `plain` (по умолчанию: нормализованные
имена игры + термин узла, если он есть) и `masked` (как сейчас: handle + описание/термин). В обоих
режимах служебные строки uc отсекаются до промпта. Требование R3.

## Контекст
- ADR-020; CLAUDE.md «Правила персонажа» (восприятие — режим) и «Ловушки» (служебные строки uc).
- Текущий код: `runtime/perception/translate.py` — `normalize`, `Perception.handle/see/raw/name_term/
  scrub/leaks`; тесты `tests/test_perception.py` (8 тестов, все про masked).
- Образцы сырых данных uc из прогона E1 (`docs/agents/unionclef-bridge.md`, раздел про сырые имена):
  - `getBlocksAround` отдаёт display name (`"birch leaves"`), не `minecraft:…`;
  - `getEntitiesInfo` — `type: "chicken"`, `name: "Chicken"`, `item: "minecraft:birch_log"`;
  - чат: `"§2§l§o[Alto Clef] §rPICKUPDEC …"`, `"§2§l§o[Tungsten] §rWalker: BFS 20 wp"`,
    ванильное `"LastOrder01 was killed"`, игроки `"<LastOrder01> probe: body online"`.
- Схема наблюдения целиком — задача T-002/T-011; здесь только перевод имён и чистка строк.

## Объём
Можно трогать:
- `runtime/perception/translate.py`, `runtime/perception/__init__.py`, новый `runtime/perception/chat.py`
- `tests/test_perception.py` — дополнять; существующие проверки masked не ослаблять и не удалять
- `tests/test_perception_plain.py`, `tests/test_perception_chat.py` (новые)
- `runtime/config.py` — только чтение `PERCEPTION_MODE`
- файлы задачи

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`
- `agent/last_order/prompt_template.md`, `runtime/lastorder/persona.py` — правило знаний в промпте
  меняется в T-011

## Критерии приёмки
- [ ] `PERCEPTION_MODE=plain|masked` (по умолчанию `plain`); неизвестное значение — ошибка при старте.
- [ ] Ключ вещи стабилен в обоих режимах и годится как `about` в базе: `masked` — handle как сейчас;
  `plain` — `<kind>:<нормализованный id>` (например `blk:birch_log`). Display name (`"Birch Leaves"`,
  `"birch leaves"`) и `minecraft:birch_leaves` дают один ключ. Тест.
- [ ] `plain`: `see()` даёт ключ, человекочитаемое имя (`birch log`) и термин узла в «», если он есть;
  `raw()` работает для ключей обоих режимов. Тест.
- [ ] `masked`: все 8 существующих тестов зелёные без изменения их проверок; `leaks()` и `scrub()`
  работают как раньше.
- [ ] `runtime/perception/chat.py`: `strip_formatting()` убирает коды `§x`; `is_service_line()` узнаёт
  `[Alto Clef]`, `[Tungsten]` и другие префиксы uc из исходников мода (список с источником в
  комментарии); `clean_chat(lines)` отбрасывает служебные, оставляет речь игроков и ванильные
  системные строки. Тест на образцах выше.
- [ ] Тест: в обоих режимах ни одна служебная строка из образцов не проходит `clean_chat`.

## Проверки
```
uv run pytest
python tools/check.py
```

## Ограничения
- В режиме `masked` сырые id не попадают в промпт никогда; тест на утечки не ослаблять.
- Вики и выгрузки в perception не класть.
- Никаких вызовов LLM.

## Заметки надзора
