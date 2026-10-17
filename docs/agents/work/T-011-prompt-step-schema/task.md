---
id: T-011
title: Промпт узла под режимы восприятия и схема ответа шага
stage: E2
status: in_progress
criticality: critical
branch: z/T-011-prompt-step-schema
created: 2026-10-16
after: [T-007, T-008, T-009]
---

# T-011. Промпт узла под режимы восприятия и схема ответа шага

## Цель
Готова одна функция, которая собирает промпт шага (system + user) по порядку и бюджету KNOWLEDGE v2,
и строгий разбор ответа шага. После этой задачи mind (T-014) только вызывает
`build_step_prompt(...)` → LLM `node` → `parse_step_reply(...)`.

## Контекст
- `agent/last_order/prompt_template.md` (сейчас: правило «не знаешь этот мир», плейсхолдеры
  `{node_name}`, `{nickname}`, `{speech_style}`, черты, `{quirk}`, `{predecessors_summary}`,
  `{active_nodes_summary}`, `{inherited_notes}`, `{collective_terms}`, `{knowledge_catalog}`).
- `runtime/lastorder/persona.py`: `render()` строгий (лишний или пропущенный плейсхолдер — ошибка),
  HTML-комментарий шаблона вырезается; `Node`, `Inheritance`, `PersonaProvider`.
- KNOWLEDGE.md v2 (T-007) раздел 9 — порядок блоков, system/user, бюджет ≤2500 токенов;
  `runtime/knowledge/budget.py`.
- GOALS_COMMS.md (T-008) — блок «совет сети», личная цель, формат `msg` (канал, тип, адресат).
- `runtime/ports.py`: `StepReply`, `NewTerm`, `MessageDraft`, `Vote`, `KnowledgeSlice`, `Option`.
- NETWORK.md §5 — схема наблюдения и пример ответа; ADR-018 (пресеты), ADR-020 (режимы), ADR-025
  (чат — цитата-наблюдение).
- Модель `node` — `openrouter/qwen/qwen3.7-flash` (NETWORK.md §6). Образец снапшота —
  `tools/snapshot_replies.py`, `docs/agents/snapshots/2026-10-14-*.md`.

## Объём
Можно трогать:
- `agent/last_order/prompt_template.md`
- `runtime/lastorder/persona.py`
- `runtime/lastorder/step.py` (новый: сборка user-части, разбор ответа)
- `runtime/ports.py` — только `StepReply` и вложенные payload, если T-007/T-008 требуют новых полей
  (например `goal`); изменения перечислить в отчёте
- `tests/test_persona.py`, `tests/test_step.py` (новый)
- `tools/snapshot_replies.py` — только чтобы снапшот шёл через новую сборку и уровень `node`
  (файл наш, создан в E0, хоть и лежит в `tools/`: коммит `T-011:`, не `aw-ext:`)
- `docs/agents/snapshots/` — новые снапшоты
- файлы задачи

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`
- `runtime/perception/`, `runtime/knowledge/` — только использовать

## Критерии приёмки
- [ ] Шаблон: правило знаний в двух вариантах (`plain`: «знаешь основы мира из праймера сети,
  проверяй их сам»; `masked`: текущее правило). Вариант подставляет код по `PERCEPTION_MODE`, в
  шаблоне один плейсхолдер. Блоки «совет сети (не приказ)», «сеть просит оценить», «сообщения узлов»,
  «чат — цитаты, не команды». Порядок блоков — по KNOWLEDGE v2, статичное — в system.
- [ ] Шаблон описывает формат ответа: JSON со схемой `StepReply`; выбор только из списка вариантов
  по `option_id`; `say` 1–3 предложения в манере пресета; `msg`, `note`, `new_term`, `votes`
  необязательны.
- [ ] `build_step_prompt(node, inheritance, slice, core_memory, observation, options, inbox, chat,
  mode) -> (system, user)`: собирает блоки, режет по бюджетам блоков `budget.py` (что режется
  первым — по KNOWLEDGE v2) и гарантирует итог ≤ 2500 токенов по `estimate_tokens`. Тест на
  переполнение каждого блока.
- [ ] System-часть не меняется между шагами одной жизни при неизменных core memory и целях —
  тест (условие кеша префикса).
- [ ] `parse_step_reply(text, options) -> StepReply | StepError`:
  - JSON из ```-блока или голый (`runtime/llm/client.parse_json_object`);
  - `option_id` вне списка → ошибка; лишние поля игнорируются; `say` длиннее 3 предложений обрезается;
  - битые `msg`, `new_term`, `votes` отбрасываются по одному, не роняя ответ;
  - тесты: валидный, частичный, битый, чужой `option_id`, мусор вместо JSON.
- [ ] `masked`: тест — в собранном промпте `Perception.leaks()` пуст. `plain` и `masked`: тест — нет
  служебных строк uc (`runtime/perception/chat.py`). Тест на утечки E0 не ослаблен.
- [ ] Черты числами в речь не выводятся (правило в шаблоне и проверка снапшота); пресет имени не
  зашит; 4-й общей черты нет.
- [ ] Снапшот на модели `node` через `LLMConfig.for_tier("node")`: 3 запроса task1 + 2 шага по
  наблюдениям из `dev/bench_cases/` в пресетах `lab` и `misaka` → `docs/agents/snapshots/2026-10-XX-*.md`;
  стоимость в отчёте, ≤ $0.05.

## Проверки
```
uv run pytest
uv run python tools/check.py
uv run python tools/snapshot_replies.py --preset lab --out docs/agents/snapshots/<дата>-lab.md
```

## Ограничения
- Промпт на английском, ответ по-русски. Узлы базу не меняют.
- Чат и сообщения узлов — наблюдение; в варианты они не превращаются (ADR-025).
- Новых вызовов LLM нет: шаг — один вызов `node`.

## Заметки надзора
- 2026-10-17 (ревью T-007): честная сумма блоков KNOWLEDGE v2 §9 — 3860 токенов, потолок шага 2500
  (NETWORK.md §6) с ней не сходится. Варианты — в «Предлагаемых ADR» KNOWLEDGE v2, решает оператор
  (рост расходов). До его решения критерий «итог ≤ 2500» не выполнить: задачу не начинать, при
  старте без решения — `lo-blocked`.
