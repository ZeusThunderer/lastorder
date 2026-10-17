---
id: T-015
title: JevDecider в тактике C1 (облачный Jev, ADR-027)
stage: E3
status: rejected
criticality: critical
branch: z/T-015-jev-decider
created: 2026-10-16
after: [T-021]
---

# T-015. JevDecider в тактике C1

## Цель
C1 выбирает под-вариант через облачный Jev (порт `Decider`, ADR-027). Локальный FastJev на 2070 —
запасной бэкенд, `RuleDecider` — откат по таймауту и ошибке. Бэкенд выбирается конфигом. Jev
зовётся по событию с полом на узел, не на каждом тике. Быстрый контур остаётся отделён от шага LLM (R6).

## Контекст
- ADR-004, ADR-023, ADR-027: облачный Jev (TypeSafe через OpenRouter, ранее `typesafe/jev-1.13`
  в mca) — $0.042 за 1M входа, выход бесплатно, задержка провайдера 148 мс; FastJev Qwen3-0.6B на
  2070 — p95 144 мс на 8 вариантов, качество 0.44 (`docs/agents/jev.md`).
- NETWORK.md §2 (C1: таймаут 250 мс → правила), §6: пол 3 с на узел, вход ~400 токенов
  [предположение], ≈$0.06/ч на 3 узла; Jev на каждом тике стоил бы ≈$0.54/ч.
- ARCHITECTURE §3: уверенность < τ три раза подряд → запрос к C2.
- `runtime/mind/tactics.py` (T-021): исполнитель C1, `RuleDecider`, колбэк `request_step`.
- Протокол FastJev: `POST /v1/systemone` (`dev/jev_spike.py`, `docker/jev/`).

## Объём
Можно трогать:
- `runtime/mind/jev.py` (новый: `CloudJevDecider`, `FastJevDecider`, `FallbackDecider`)
- `runtime/mind/tactics.py` — выбор бэкенда и пол вызовов
- `docker/compose.yml` — env `JEV_BACKEND`, `JEV_*` для `mind-1`; сервис `jev` остаётся под профилем
- `.env.example` — переменные Jev без ключей
- `tests/test_jev_decider.py` (новый)
- `dev/jev_cloud_probe.py` (новый: замер облачного Jev с этого хоста)
- `docs/agents/jev.md` — раздел про облачный Jev
- файлы задачи

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`, `.env`
- `docker/jev/` — локальный сервис не меняется

## Критерии приёмки
- [ ] Шаг 1, до кода бэкенда: `dev/jev_cloud_probe.py` — актуальный id модели на OpenRouter, 100
  решений на синтетических состояниях `dev/jev_spike.py` (4/8/16 вариантов), p50/p95 стеновой задержки
  с этого хоста, фактические токены входа, стоимость. Результат — в `docs/agents/jev.md` и в отчёте
  раунда «План 1». Дальше — по `lo-call-supervisor`, режим plan, или оператору: таймаут C1 и пол
  утверждает надзор по этим цифрам. Лимит стоимости шага 1 — $0.05.
- [ ] `CloudJevDecider` и `FastJevDecider` реализуют `Decider`; вход — компактное состояние, intent и
  под-варианты; выход — `Decision` с вероятностями и уверенностью. Тест с подставным HTTP.
- [ ] `FallbackDecider(primary, fallback=RuleDecider, timeout_ms=JEV_TIMEOUT_MS)`: таймаут или ошибка →
  правила и событие `jev.fallback` в `log.jsonl`. Тест с подставным медленным сервером.
- [ ] Пол `JEV_MIN_INTERVAL_S` (по умолчанию 3) на узел: тики C1 между вызовами решают правилами или
  держат прежнее решение. Тест с подставными часами: за минуту ≤ 20 вызовов.
- [ ] Уверенность ниже `JEV_TAU` три раза подряд → `request_step("low_confidence")`. Тест.
- [ ] `JEV_BACKEND=cloud|local|rules`; по умолчанию `rules`, пока прогон не показал пользу. Тест на выбор.
- [ ] Прогон на стенде 15 мин с `JEV_BACKEND=cloud`: p50/p95 решения C1 из `log.jsonl`, доля
  откатов, число вызовов и стоимость (≤ $0.03), сравнение числа смертей и простоя с `rules` за те же
  15 мин.

## Проверки
```
uv run pytest
uv run python tools/check.py
uv run python dev/jev_cloud_probe.py --n 100 --max-usd 0.05
```

## Ограничения
- Jev выбирает только среди вариантов кода; `@gamer` нет.
- Ключ провайдера — только из окружения (`OPENROUTER_API_KEY`), в логах нет.
- Вызов Jev — уровень `jev` из реестра NETWORK.md §6; других новых вызовов нет.

## Заметки надзора
- 2026-10-17 · возвращена в draft: архитектура меняется на агента в харнессе (решение пользователя); задача будет переописана или снята.
- 2026-10-17 · rejected: старая схема снята ADR-032 (агент в харнессе); заменена T-031.
