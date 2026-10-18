---
id: T-026
title: Шлюз LLM — LiteLLM proxy, виртуальные ключи узлов, учёт и потолки
stage: E2
status: in_progress
criticality: normal
branch: z/T-026-llm-gateway
created: 2026-10-17
after: [T-024]
---

# T-026. Шлюз LLM — LiteLLM proxy, виртуальные ключи узлов, учёт и потолки

## Цель
Сервис `llm-gateway` в compose — единственный путь к моделям OpenRouter (ADR-033). У каждого узла,
Last Order и `mind-N` свой виртуальный ключ, расход считается по ключу, потолок ходов в минуту
и $/ч режет запросы (ADR-035).

## Контекст
- ADR-023, ADR-033, ADR-035; NETWORK.md v2 §3, §8, §9.
- Модели уровней: `runtime/llm/client.py` (`for_tier`), `.env.example`.
- Выводы T-024 о проксировании GLM Coding Plan (`docs/agents/harness-spike.md`).

## Объём
Можно трогать:
- `docker/gateway/` (новый: конфиг LiteLLM proxy)
- `docker/compose.yml` — сервис `llm-gateway`, его volume; env уровней для `mind-1` и `hive`
- `runtime/llm/client.py` — адрес шлюза и виртуальный ключ из env
- `dev/gateway_keys.py` (новый: выдать или отозвать ключ узла), `dev/gateway_report.py` (расход по ключам)
- `.env.example`, `tests/test_gateway_config.py` (новый)
- файлы задачи

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`, `.env`

## Критерии приёмки
- [ ] `llm-gateway` поднимается в compose; ключи провайдеров только у него (env), наружу — порт
  во внутренней сети. Тест конфига: модели уровней заданы, ключей в файлах нет.
- [ ] Виртуальные ключи: на слот, на Last Order, на `mind-N` (Jev). Скрипт выдаёт и отзывает.
- [ ] Потолки на ключ: ходов в минуту и $/ч; превышение → ошибка с понятным текстом. Проверка
  на стенде коротким циклом запросов.
- [ ] `dev/gateway_report.py`: расход по ключам и моделям за период, $/ч.
- [ ] Если T-024 показал, что GLM Coding Plan можно вести через прокси, — маршрут GLM в шлюзе;
  иначе в отчёте отметка «не проводим, причина».
- [ ] `runtime/llm/client.py` ходит через шлюз; прежние тесты зелёные.

## Проверки
```
uv run pytest
uv run python tools/check.py
docker compose -f docker/compose.yml up -d llm-gateway
uv run python dev/gateway_report.py --since 1h
```

## Ограничения
- Ключи — только из окружения; в логах, отчётах и git их нет.

## Заметки надзора
- 2026-10-17 · утверждена пользователем.
