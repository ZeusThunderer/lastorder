---
id: T-024
title: Спайк харнесса — узел 15 мин в OpenCode и ZCode через MCP тела
stage: E2
status: done
criticality: critical
branch: z/T-024-harness-spike
created: 2026-10-17
---

# T-024. Спайк харнесса — узел 15 мин в OpenCode и ZCode через MCP тела

## Цель
Получить цифры, по которым выбирается харнесс узла (ADR-032): живёт ли агент-сестра 15 минут через
минимальный MCP тела, сколько это стоит деньгами и квотой, какие задержки. Проверить, работает ли ZCode
без GUI в Linux-контейнере.

## Контекст
- ADR-032…035, NETWORK.md v2 §2–§5, §8 (цифры там — предположения, эта задача их заменяет).
- Тело: `runtime/game/py4j_body.py` (`Py4jBody`, `LEVERS`), восприятие `runtime/perception/`
  (`translate.py`, `chat.py`), смерть `runtime/lastorder/death.py`.
- Личность: `agent/last_order/prompt_template.md`, `runtime/lastorder/persona.py`, `traits.py`, `naming.py`.
- Модели: node `openrouter/qwen/qwen3.7-flash`; GLM-flash по подписке Z.ai.
- Сведения о ZCode пока только из сторонних обзоров: Linux-сборка в бете, встроенный headless CLI
  упоминается без инструкции, ×1.5 квоты описан как акция запуска (июль 2026). GLM Coding Plan
  работает и в сторонних клиентах, но без ×1.5.
- OpenCode: `opencode serve` / `opencode run`, MCP `type: remote` (HTTP) в `opencode.json`.

## Объём
Можно трогать:
- `runtime/mind/__init__.py`, `runtime/mind/mcp_body.py` (новые: минимальный MCP тела)
- `dev/harness_spike/` (новый: Dockerfile харнесса, `opencode.json`, конфиг ZCode, промпт сестры
  для спайка, скрипт сводки)
- `dev/compose.harness-spike.yml` (новый оверрайд compose)
- `pyproject.toml`, `uv.lock` — зависимость MCP SDK
- `tests/test_mcp_body.py` (новый)
- `docs/agents/harness-spike.md` (новый: итог)
- `.env.example` — имена переменных без значений
- файлы задачи

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`, `.env`
- `runtime/game/`, `runtime/perception/`, `runtime/lastorder/` — только использовать; дефект — в отчёт
- `docker/compose.yml` — только оверрайд в `dev/`

## Критерии приёмки
- [ ] Шаг 0, до остального, итог — в отчёт и `docs/agents/harness-spike.md`:
  - запускается ли ZCode без GUI в Linux-контейнере: команда, версия, что получилось;
  - действующие условия квоты в ZCode (×1.5 или нет) по официальной странице Z.ai, со ссылкой;
  - разрешают ли условия GLM Coding Plan вести запросы через свой прокси (LiteLLM).
  Если ZCode без GUI не работает, вторым харнессом идёт OpenCode с GLM по подписке.
- [ ] `runtime/mind/mcp_body.py` — MCP-сервер (streamable HTTP) с инструментами `observe`, `act`
  (рычаги `LEVERS` без `command` и `chat`), `stop`, `say` (без `/` и `@`, лимит длины и частоты),
  `wait_events(timeout_s)` (урон, смерть, чат после фильтра, завершение намерения). Имена — `plain`,
  служебные строки uc отфильтрованы. Тесты на подставном теле: набор инструментов, отказ `command`,
  `say` с `/`, фильтр служебных строк, `wait_events` по таймауту и по событию.
- [ ] Промпт сестры для спайка — из `prompt_template.md` с пресетом `lab` и чертами. Инструкция:
  играть, говорить, вести `memory/state.md`, при смерти — написать `will.md`. Рабочее место —
  временная копия `agent/last_order/`.
- [ ] Прогон на стенде (`mc-server`, `body-1`, `mind-1` с MCP, харнесс в контейнере из оверрайда):
  15 мин в каждом из двух харнессов. Узел живёт, пишет память, говорит. При смерти пишется
  `will.md` (смерть можно вызвать RCON `/kill` на 10-й минуте, если узел не умер сам).
- [ ] Сводка по каждому харнессу в `docs/agents/harness-spike.md`:
  - ходы агента в минуту, токены вх/вых на ход, доля кеша;
  - $/ч (по OpenRouter) или квота (по Z.ai);
  - p50/p95 задержки хода и `act` → действие в игре;
  - память контейнера харнесса;
  - сколько раз агент сам выбирал ожидание и какой таймаут;
  - 3–5 цитат реплик и фрагмент `state.md`;
  - отказы и странности (сырые команды, попытки обойти инструменты).
- [ ] Вывод: рекомендация харнесса для узла и для Last Order, пересчёт NETWORK.md §8 на 3 узла
  (предложение в отчёт, документ правит надзор). Лимит денег на спайк — $1.

## Проверки
```
uv run pytest
uv run python tools/check.py
docker compose -f docker/compose.yml -f dev/compose.harness-spike.yml up -d mc-server body-1 mind-1
```

## Ограничения
- Спайк: ключ OpenRouter — из окружения прямо в контейнер харнесса (шлюз делает T-026); в логах
  и отчёте ключей нет. Токен Z.ai — так же.
- Сырых команд uc, `@gamer` и команд сервера в инструментах нет (ADR-037).
- Не править ядро личности (`prompt_template.md`) — промпт спайка собирается поверх него.
- Квота Z.ai общая с ZCode-воркером: отметить в сводке, сколько квоты ушло на прогон.

## Заметки надзора
- 2026-10-17 · утверждена пользователем.
