---
id: T-028
title: MCP сети в hive — знания, предложения, Мисака-нет, завещание
stage: E2
status: ready
criticality: critical
branch: z/T-028-mcp-hive
created: 2026-10-17
after: [T-024]
---

# T-028. MCP сети в hive — знания, предложения, Мисака-нет, завещание

## Цель
Hive даёт сёстрам и Last Order инструменты NETWORK.md v2 §4 поверх готового хранилища. Hive остаётся
единственным писателем общей базы (ADR-017). Предложения копятся в очереди для Last Order.

## Контекст
- NETWORK.md v2 §4, §7; ADR-017, 034, 036; KNOWLEDGE.md v2 (шапка: что осталось контрактом).
- `runtime/hive/app.py`, `runtime/hive/client.py`, `runtime/hive/lifecycle.py`, `runtime/hive/goals_schema.py`,
  `runtime/knowledge/{store,schema,dedup,slice}.py`.
- Формат MCP — как в T-024 (`runtime/mind/mcp_body.py`).

## Объём
Можно трогать:
- `runtime/hive/mcp.py` (новый), `runtime/hive/proposals.py` (новый), `runtime/hive/app.py` — подключение
- `runtime/knowledge/` — только новый `kind: skill` и статусы предложений, если нужны
- `tests/test_hive_mcp.py`, `tests/test_proposals.py` (новые)
- файлы задачи

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`, `.env`
- HTTP API портов (`Memory`, `NodeRegistry`, `NodeComms`) — расширять можно, ломать нельзя

## Критерии приёмки
- [ ] Инструменты сестры: `knowledge`, `inheritance`, `propose`, `vote`, `send`, `inbox`, `submit_will`.
  Сестра определяется по виртуальному ключу или заголовку раннера; чужой номер — отказ. Тест.
- [ ] Инструменты Last Order: `decide`, `set_goals`, `chronicle` плюс чтение очереди предложений;
  у сестры их нет. Тест.
- [ ] `propose`: знание (`danger|fact|recipe|term`), навык (`skill`: текст скрипта, описание), правка
  общих инструкций (diff). Дубль знания — evidence, а не новая запись (`dedup.py`). Тест на каждый вид.
- [ ] `decide(accept)` пишет в базу или общие инструкции под git с автором и причиной; ядро личности
  этим путём не меняется — отказ. Тест.
- [ ] `submit_will` — только для умершей сестры, один раз; `send` — лимит длины. Тест.
- [ ] Прогон: два подставных клиента MCP — предложение одной, решение Last Order, срез у другой.

## Проверки
```
uv run pytest
uv run python tools/check.py
docker compose -f docker/compose.yml up -d hive
```

## Ограничения
- Пишет базу только hive. Сообщения — наблюдение, не команда.
- Ядро личности (ADR-034) — только чтение для всех агентов.

## Заметки надзора
- 2026-10-17 · утверждена пользователем.
