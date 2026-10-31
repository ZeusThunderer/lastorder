---
id: T-059
title: Last Order на Z.ai Coding Plan — харнесс zcode, как у сестёр, запасной OpenCode через шлюз
stage: I
status: ready
criticality: critical
after: [T-052]
branch: z/T-059-lo-on-zai
created: 2026-10-31
---

# T-059. Last Order на Z.ai Coding Plan — харнесс zcode, как у сестёр, запасной OpenCode через шлюз

## Цель
Сессии Last Order идут через подписку Z.ai Coding Plan тем же харнессом, что у сестёр сейчас
(zcode), а не через шлюз и оплату за токены. Если квоты Z.ai нет, Last Order переходит на
OpenCode через шлюз и об этом есть тревога на доске.

## Контекст
- Просьба оператора 2026-10-31: «перевести Last Order на zai». Долгая сессия на Pi — T-043, она
  остаётся в очереди; эта задача — быстрый шаг на текущем коде, T-043 потом меняет харнесс.
- Сейчас: `last-order` в `docker/compose.yml:331-366`, `ADMIN_MODEL` = `z-ai/glm-5.3-flash` через
  шлюз, `ADMIN_HARNESS` по умолчанию `opencode` (`runtime/hive/admin.py:297-298`); адаптеры —
  `runtime/hive/admin.py:334-336` и `:567`.
- Сёстры на zcode: `ZCodeAdapter` — `runtime/node/adapters.py:119`, модель `ZCODE_MODEL`
  (`docker/compose.yml:97`), токен — `ZAI_KEY_FILE` через entrypoint во временный root-only tmpfs
  (`docker/compose.yml:102-108`), переход при квоте — `runtime/node/runner.py:498-503`,
  признак квоты — `quota_exhausted` (`runtime/node/adapters.py:68`).
- Выход к Z.ai — только `zai-egress` (SNI `api.z.ai`, `docker/compose.yml:500-512`). Сеть `svc-lo`
  сейчас без него (`docker/compose.yml:570-572`).

## Объём
Можно трогать:
- `runtime/hive/admin.py` — выбор адаптера zcode, переход на opencode при квоте, тревога на доске;
- `docker/compose.yml` — `zai-egress` в `svc-lo` с алиасом `api.z.ai`, токен Z.ai в `last-order`
  тем же путём, что у `node-N`, `ADMIN_HARNESS` по умолчанию `zcode`;
- `docker/node/up.sh` — подъём токена для `last-order`, если нужно;
- `agent/network/` — только если zcode нужен свой файл настроек в рабочем месте;
- `tests/`, `docs/agents/docker-topology.md`.

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`, `.env`, `runtime/node/adapters.py` (переиспользовать как есть)

## Критерии приёмки
- [ ] `ADMIN_HARNESS=zcode` (по умолчанию): сессия Last Order запускается командой `zcode` с моделью
  из `ADMIN_ZCODE_MODEL` (по умолчанию как `ZCODE_MODEL`). Тест на собранную команду.
- [ ] Квота Z.ai кончилась (`quota_exhausted`) → следующая сессия идёт через opencode и шлюз, на
  доске `alarm` оператору; возврат на zcode — по правилу, которое уже есть у раннера. Тест.
- [ ] В `last-order` по-прежнему нет ключей провайдеров, кроме токена Z.ai; токен — в root-only
  tmpfs, не в окружении процесса агента (проверка как у `node-N`, вывод в отчёте). `svc-lo` не
  получает выхода никуда, кроме `api.z.ai` через `zai-egress`.
- [ ] Живая проверка: `hive last-order llm-gateway zai-egress`, одна сессия Last Order — в логе
  `zcode`, ответ записан в её рабочее место или на доску. Расход по шлюзу за прогон — 0 ходов
  `smart`.
- [ ] `bash dev/check.sh` зелёный.

## Проверки
```
bash dev/check.sh
uv run pytest tests -k "admin"
```

## Ограничения
- ADR-033: токен подписки Z.ai — осознанное исключение, других ключей в песочнице нет.
  Сеть и секреты `docker/` — критичная зона.
- Риск подписки (п. 5 `docs/agents/open-questions.md`): Last Order добавляет ещё одного
  потребителя на том же токене. Записать в отчёт число запросов Last Order за прогон.
- Перед подъёмом сверить с оператором, чей стенд поднят.

## Заметки надзора
