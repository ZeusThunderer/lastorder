---
id: T-034
title: Прогон узла 15 мин в Hermes на стенде — сравнение с T-024
stage: E2
status: ready
criticality: normal
branch: z/T-034-hermes-run
created: 2026-10-22
after: [T-033]
---

# T-034. Прогон узла 15 мин в Hermes на стенде — сравнение с T-024

## Цель
Цифры для выбора харнесса узла (ADR-032): живёт ли сестра 15 минут в Hermes на GLM Coding Plan
через MCP тела и hive, сколько это стоит квотой, какие задержки — в тех же метриках, что OpenCode
и ZCode в T-024.

## Контекст
- После: T-033 (адаптер `hermes`, сухой прогон).
- Протокол и метрики T-024: `docs/agents/work/T-024-harness-spike/task.md`,
  итог — `docs/agents/harness-spike.md`; сводка — `dev/harness_spike/summarize.py`.
- Запуск — штатный `node-1` с `NODE_HARNESS=hermes NODE_HARNESS_FAILOVER=` (compose не правится).
- Ожидание условий на стенде — `dev/wait_for.sh`, не циклы `sleep`.

## Объём
Можно трогать:
- `dev/harness_spike/summarize.py` — разбор stream-json Hermes и логов раннера
- `tests/test_harness_spike_summary.py` (новый, если разбору нужен тест)
- `docs/agents/harness-spike.md` — раздел «Прогон Hermes (15 мин)»
- файлы задачи

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`, `.env`
- `runtime/`, `docker/`, `agent/` — дефекты идут в отчёт как предложения задач

## Критерии приёмки
- [ ] Один прогон 15 мин на стенде (`mc-server`, `body-1`, `mind-1`, `hive`, `node-1` с
  `hermes`). Узел живёт, пишет память, говорит. Смерть — сама или RCON `/kill` на 10-й минуте;
  завещание пишет агент (аварийное — отдельной строкой, с причиной).
- [ ] Сводка в `harness-spike.md` в тех же строках, что у OpenCode и ZCode в T-024:
  ходы в минуту, токены вх/вых на ход, доля кеша, квота Z.ai (кредиты по формуле из шага 0),
  p50/p95 задержки хода и `act` → действие, память контейнера, выборы `wait_events` и таймауты,
  3–5 цитат, фрагмент `state.md`, отказы и странности.
- [ ] Отдельно — вспомогательные вызовы LLM Hermes (сжатие, саммари): сколько и какая доля квоты.
- [ ] Отдельно — следы личности Hermes в речи и памяти сестры (если были), с цитатами.
- [ ] Вывод: место Hermes рядом с OpenCode и ZCode для узла и для Last Order; стоит ли включать
  встроенную память и скилы Hermes в рабочем месте (предложение, решает надзор).

## Проверки
```
uv run pytest
uv run python tools/check.py
NODE_HARNESS=hermes NODE_HARNESS_FAILOVER= docker compose -f docker/compose.yml up -d mc-server body-1 mind-1 hive node-1
```

## Ограничения
- Квота Z.ai общая с ZCode-воркером: один прогон; потраченное — в сводке.
- Мир прогона не коммитится, ключей в артефактах нет (grep перед сдачей).
- Не править ядро личности и шаблон сестры; плохое поведение — предложение в отчёт (ADR-032).

## Заметки надзора
- 2026-10-22 · поставлена по просьбе пользователя вместе с T-033.
