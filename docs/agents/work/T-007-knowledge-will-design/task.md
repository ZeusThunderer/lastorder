---
id: T-007
title: Проект знаний, праймера и завещаний (KNOWLEDGE.md v2)
stage: E2
status: review
criticality: critical
branch: z/T-007-knowledge-will-design
created: 2026-10-16
after: [T-002]
---

# T-007. Проект знаний, праймера и завещаний (KNOWLEDGE.md v2)

## Цель
KNOWLEDGE.md переписан в v2: по нему и по `runtime/knowledge/schema.py` пишутся хранилище базы
(T-012), праймер (T-010), промпт шага (T-011), путь смерти (T-013) и рефлексия (T-023), и
решать заново ничего не нужно. Схемы — код с тестом. Требование R2.

Критичность: документ задаёт формат базы, завещания и состав промпта.

## Контекст
- `docs/architecture/KNOWLEDGE.md` (v1, 2026-10-14) — слои, запись §2, триггеры §3, согласование §4.
- `docs/architecture/NETWORK.md` §4 (порты и payload), §5 (наблюдение, ответ шага), §6 (реестр
  вызовов, токены шага 2500/200), §7 (жизненный цикл), §8 (знания и завещания).
- `runtime/ports.py`: `KnowledgeEntry`, `KnowledgeSlice`, `KnowledgeCandidate`, `Vote`,
  `Inheritance` (из `runtime/lastorder/persona.py`).
- ADR-017 (hive — единственный писатель), ADR-020 (`plain`/`masked`, праймер), ADR-022 (вызовы),
  ADR-026 (причина смерти `stuck`). Бриф §3–4: завещание 2–4 строки, один урок, одна шутка.
- Ключ вещи в базе (`about`) — формат T-009: `plain` → `<kind>:<id>` (`blk:birch_log`,
  `ent:zombie`, `itm:bread`), `masked` → handle (`blk:7f3a`). Пример в NETWORK.md §5 (`birch_log`
  без префикса) устарел — поправить в этой задаче.
- Решение надзора о файлах узла: в volume `knowledge` (под git) лежат только `birth.json`,
  `core.md`, `will.md` узла. `log.jsonl` шагов и отладочный лог промптов пишет mind в свой
  volume `run` (вне git): иначе git базы растёт на каждом шаге.

## Объём
Можно трогать:
- `docs/architecture/KNOWLEDGE.md` (переписать в v2)
- `docs/architecture/NETWORK.md` — только §5 (пример ключа) и §8 (ссылки на KNOWLEDGE v2)
- `runtime/knowledge/__init__.py`, `runtime/knowledge/schema.py` (новые: dataclass и валидаторы,
  без ввода-вывода)
- `runtime/knowledge/budget.py` (новый: оценка токенов)
- `tests/test_knowledge_schema.py` (новый)
- файлы задачи в `docs/agents/work/T-007-knowledge-will-design/`

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`
- `runtime/ports.py` — если payload портов надо менять, предложить в отчёте списком, правит надзор
- `docs/architecture/DECISIONS.md` — несогласие с ADR в разделе «Предлагаемые ADR» KNOWLEDGE v2

## Критерии приёмки
- [ ] KNOWLEDGE.md v2, разделы:
  1. слои памяти (обновлённая таблица §1 v1) и раскладка файлов: volume `knowledge` (что, кто пишет,
     под git) и volume `run` (что, кто пишет, вне git);
  2. запись знания: поля, типы, обязательность; `source: primer|node`; `about` в формате T-009;
     `claim` — по-русски, в `masked` без сырых id;
  3. события-триггеры (KNOWLEDGE v1 §3.1): таблица тип → важность 1–10 → условие, порог суммы
     важности для внеочередной рефлексии;
  4. согласование: дедуп без эмбеддингов (алгоритм похожести `claim` словами: нормализация, мера,
     порог — числами), переходы статусов как таблица (откуда, событие, куда), вес evidence против
     голоса, `retired`, что видит узел при `disputed`;
  5. завещание: `will.md` — frontmatter (serial, slot, cause `game|ttl|stuck`, born_at, died_at,
     lifetime_s, emergency, traits, model) и тело 2–4 строки; аварийное завещание — шаблон и из каких
     полей оно собирается без LLM (черты, причина, последнее действие, последние события, обращение
     к наследнику);
  6. `birth.json` и `core.md` (≤1.5 KB, что внутри: личная цель, 3–5 фактов, отношение к целям сети);
  7. `Inheritance`: какие поля, сколько завещаний (K), сводка предшественников и живых;
  8. праймер: формат файлов в `agent/last_order/primer/`, бюджет — сколько записей и токенов идёт в
     промпт, как помечен в промпте (`confirmed`, `source: primer`), что идёт в «расширенный праймер»
     (в промпт не попадает);
  9. сборка контекста шага: порядок блоков под кеш префикса, бюджет токенов на блок, что в
     system-сообщении, что в user; итог ≤ 2500 токенов входа (NETWORK.md §6); различия `plain` и
     `masked`;
  10. вход и выход рефлексии и завещания (вызов `smart`): поля входа, JSON выхода, бюджет токенов
      (≤ 4000/600 по NETWORK.md §6);
  11. что остаётся на потом (KNOWLEDGE v1 §6, обновить).
- [ ] Порядок блоков под кеш предложен и обоснован. Отправная точка надзора: личность и правила →
  каталог и праймер → наследие (статично на жизнь) → core memory → цели сети → знания о видимом →
  кандидаты на голос → сообщения и чат → наблюдение и варианты.
- [ ] `runtime/knowledge/schema.py`: `Record`, `Evidence`, `RecordVote`, `WillMeta`, `Will`,
  `BirthFile`, `TriggerEvent`, `ReflectionInput`, `ReflectionOutput`; у каждого `from_dict`/`to_dict`
  и проверка, которая отклоняет неверные поля. Перевод `Record` → `ports.KnowledgeEntry`.
- [ ] `runtime/knowledge/budget.py`: `estimate_tokens(text) -> int` — консервативная оценка без сети
  (правило и запас указаны в docstring), бюджеты блоков из п.9 — константы здесь же.
- [ ] `tests/test_knowledge_schema.py`: каждый JSON- и markdown-пример из KNOWLEDGE v2 проходит
  разбор (тест вытаскивает примеры из документа, а не копирует их); неверные примеры отклоняются;
  аварийное завещание собирается из подставных полей без LLM.
- [ ] Новых видов вызовов LLM нет (ADR-022); у рефлексии и завещания уровень `smart`.

## Проверки
```
uv run pytest
uv run python tools/check.py
```

## Ограничения
- Пишет базу только hive; узел шлёт кандидатов, голоса, core memory и завещание через порты.
- Черты в `will.md` — служебные поля frontmatter; в тексте завещания чисел черт нет.
- Пресет имени не зашивать; 4-ю общую черту не додумывать.
- Документ по-русски, сухо, ≤ 350 строк.

## Заметки надзора
