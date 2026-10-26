---
id: T-040
title: Контракт воли — submit_will принимает текст, служебные поля заполняет среда
stage: I
status: ready
criticality: critical
after: []
branch: z/T-040-will-contract
created: 2026-10-26
---

# T-040. Контракт воли — submit_will принимает текст, служебные поля заполняет среда

## Цель
Сестра отдаёт в `submit_will(text)` только свои 2–4 строки, а frontmatter will.md (serial, slot,
cause, born_at, died_at, lifetime_s, emergency, traits, model) собирает среда. Воля агента
доходит с первого вызова, доля аварийных падает с 92% (T-039).

## Контекст
- Прогон T-039, инцидент 1: `docs/agents/runs/2026-10-24-mvp-2.md` — 134 вызова `submit_will`,
  ошибки «expected --- frontmatter --- then the body», «missing field: cause»; ревью T-039, R1-1.
- Противоречие: навык `agent/last_order/skills/write-will/SKILL.md:20,31` — «plain text»,
  `submit_will(text)`; инструмент `runtime/hive/mcp.py:280` — `Will.from_markdown(text)` с полным
  frontmatter (`runtime/knowledge/schema.py:528` WillMeta, `:611` from_markdown).
- Все служебные поля уже знает раннер: он же собирает аварийную волю
  (`runtime/node/runner.py:570` — staged или `emergency_will(...)`, `:595` `_staged_will`).
  Проверка тела — `runtime/lastorder/will.py:316` `check_will_body`.
- Предлагаемая схема (можно иначе, если обоснуешь в отчёте): hive проверяет и кладёт в stage
  только тело; раннер собирает will.md из тех же полей, что у аварийной, с `emergency: false` и
  телом агента. Полный will.md с frontmatter по-прежнему принимается (берётся тело).
- ADR-034, ADR-041 (поведение — в инструкциях, среда — в коде).

## Объём
Можно трогать:
- `runtime/hive/mcp.py` (только `submit_will` и его docstring)
- `runtime/node/runner.py` (сборка воли из stage), `runtime/lastorder/will.py` — при нужде
- `agent/last_order/skills/write-will/SKILL.md`
- тесты, где сейчас проверяются `submit_will` и сборка воли (`tests/test_hive_mcp*.py`,
  `tests/test_node_runner.py`, `tests/test_will*.py`)
- файлы задачи

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`
- `runtime/knowledge/schema.py` — формат will.md в базе не меняется
- `agent/last_order/prompt_template.md`, `runtime/lastorder/persona.py`

## Критерии приёмки
- [ ] `submit_will` с телом из 2–4 строк без frontmatter в волевой сессии мёртвой сестры
  принимается; в базе после `retire` лежит will.md с правильными serial, slot, cause, born_at,
  died_at, lifetime_s, traits, model и `emergency: false` — тест.
- [ ] Полный will.md с frontmatter тоже принимается; serial чужого узла — отказ, как сейчас — тест.
- [ ] Тело, нарушающее `check_will_body` (числа черт, markdown, больше 4 строк), — отказ с
  понятной сестре причиной одной фразой, без имён полей схемы — тест.
- [ ] Проверки авторизации (`X-Runner-Session`, `X-Runner-Key`, «тело мертво») не ослаблены —
  существующие тесты проходят без правок их ожиданий.
- [ ] `write-will/SKILL.md`: «передай только текст воли»; после двух неудачных вызовов —
  остановиться (R1-1 T-039); навык не длиннее, чем сейчас.
- [ ] Аварийная воля при таймауте сессии по-прежнему пишется — тест.

## Проверки
```
bash dev/check.sh
```
Стенд не нужен.

## Ограничения
- Смерти без завещания не бывает: аварийный путь раннера по сути не трогать.
- Общую базу пишет только hive; формат will.md в базе прежний.
- Инструкции — по-английски, воля — по-русски.

## Заметки надзора
