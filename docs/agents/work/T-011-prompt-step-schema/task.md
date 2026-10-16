---
id: T-011
title: Промпт узла под режимы восприятия и схема ответа шага
stage: E2
status: draft
criticality: critical
branch: z/T-011-prompt-step-schema
created: 2026-10-16
---

# T-011. Промпт узла под режимы восприятия и схема ответа шага

## Цель
`prompt_template.md` и `persona.py` собирают промпт шага по NETWORK.md и KNOWLEDGE.md v2: правило
знаний зависит от `PERCEPTION_MODE`, есть блоки целей сети и сообщений других узлов, ответ шага —
JSON по схеме с валидацией.

## Контекст
- После T-007, T-008, T-009. Модель уровня `node` — `openrouter/qwen/qwen3.7-flash` (выбор пользователя по T-004, NETWORK.md §6).
- ADR-018, ADR-020, ADR-021, ADR-022; текущие `agent/last_order/prompt_template.md`,
  `runtime/lastorder/persona.py` (строгая подстановка, комментарий шаблона вырезается).

## Объём
Можно трогать:
- `agent/last_order/prompt_template.md`, `runtime/lastorder/persona.py`, новый `runtime/lastorder/step.py`
- `tests/test_persona.py`, `tests/test_step_schema.py`
- файлы задачи

## Критерии приёмки (предварительно)
- [ ] Шаблон: правило знаний для `plain` и `masked`; блоки «цели сети (совет)», «сообщения узлов»,
  «сеть просит оценить»; порядок блоков под кеш префикса из KNOWLEDGE.md v2.
- [ ] `step.py`: схема ответа (вариант, why, say, msg, note, votes, new_term, goal) и разбор с
  отказом на мусор; тесты на валидный, частичный и битый ответ.
- [ ] `masked`: тест на отсутствие сырых id в собранном промпте; `plain`: тест на отсутствие служебных
  строк uc.
- [ ] Черты числами в речь не выводятся; пресет имени не зашит; 4-я черта не добавлена.
- [ ] Снапшот трёх запросов task1 на модели `node` в обоих пресетах — в `docs/agents/snapshots/`.

## Заметки надзора
