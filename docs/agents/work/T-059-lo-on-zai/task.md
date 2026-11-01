---
id: T-059
title: Last Order на Z.ai Coding Plan — харнесс Pi, модель GLM-5.3, запасной OpenCode через шлюз
stage: I
status: done
criticality: critical
after: [T-052, T-066]
branch: z/T-059-lo-on-zai
created: 2026-10-31
---

# T-059. Last Order на Z.ai Coding Plan — харнесс Pi, модель GLM-5.3, запасной OpenCode через шлюз

## Цель
Сессии Last Order идут через подписку Z.ai Coding Plan в харнессе Pi на модели GLM-5.3, а не через
шлюз и оплату за токены. Если квоты Z.ai нет, Last Order переходит на OpenCode через шлюз и об этом
есть тревога на доске. Сессии пока короткие, по поводу, как сейчас; долгая сессия — T-043.

## Контекст
- Просьба оператора 2026-10-31: «перевести Last Order на zai». Харнесс и модель пересмотрены
  тем же днём (см. «Заметки надзора»): Pi вместо zcode, GLM-5.3 вместо Flash.
- Замер надзора 2026-10-31, постоянная часть запроса (без истории):
  zcode ≈22k токенов своих, ≈31k первый запрос [источник: model_usage БД zcode хоста, с
  CLAUDE.md проекта — оценка]; opencode ≈5–7k своих, ≈10k первый запрос Last Order
  [источник: LiteLLM_SpendLogs, 123 запроса 2026-10-24, медиана 13k, макс. 21.6k];
  Pi ≈1k своих, ≈3k с AGENTS.md и навыками [проверено: сухой прогон T-066].
  Рост контекста: zcode — окно 1M, порог сжатия не настраивается (у сестёр 200–240k за жизнь);
  opencode — сжатие у предела модели; Pi — порог задаётся (`compaction.*`, T-066).
- Сестёр на Pi переводит T-066: `PiAdapter` и `render_pi_configs`/`drop_pi_agent_dir`
  (`runtime/node/adapters.py`, `runtime/node/workplace.py`) — переиспользовать.
- Сейчас: `last-order` в `docker/compose.yml:331-366`, `ADMIN_MODEL` = `z-ai/glm-5.3-flash` через
  шлюз, `ADMIN_HARNESS` по умолчанию `opencode` (`runtime/hive/admin.py:297-298`); адаптеры —
  `runtime/hive/admin.py:334-336` и `:567`.
- Выход к Z.ai — только `zai-egress` (SNI `api.z.ai`, `docker/compose.yml:500-512`). Сеть `svc-lo`
  сейчас без него (`docker/compose.yml:570-572`).
- Незакоммиченная работа ZCode в worktree (compose, `admin.py`, тесты) под zcode: часть про
  `zai-egress`, `svc-lo` и токен годится как есть, выбор адаптера — переделать на Pi.

## Объём
Можно трогать:
- `runtime/hive/admin.py` — выбор адаптера Pi, переход на opencode при квоте, тревога на доске;
- `docker/compose.yml` — `zai-egress` в `svc-lo` с алиасом `api.z.ai`, токен Z.ai в `last-order`
  тем же путём, что у `node-N`, `ADMIN_HARNESS` по умолчанию `pi`;
- `docker/node/up.sh` — подъём токена для `last-order`, если нужно;
- `agent/network/` — только если Pi нужен свой файл в рабочем месте;
- `tests/`, `docs/agents/docker-topology.md`.

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`, `.env`
- `runtime/node/adapters.py`, `runtime/node/workplace.py` — переиспользовать как есть; нужна
  правка — вопрос надзору

## Критерии приёмки
- [ ] `ADMIN_HARNESS=pi` (по умолчанию): сессия Last Order — `pi -p --mode json` с моделью
  `ADMIN_PI_MODEL` (по умолчанию `GLM-5.3`), MCP hive — `direct`, сжатие по порогу
  (`ADMIN_PI_CONTEXT_WINDOW`, значение с обоснованием в отчёте). Тест на команду и конфиги.
- [ ] Квота Z.ai кончилась (`quota_exhausted`) → следующая сессия идёт через opencode и шлюз, на
  доске `alarm` оператору; возврат на Pi — по правилу, которое уже есть у раннера. Тест.
- [ ] В `last-order` по-прежнему нет ключей провайдеров, кроме токена Z.ai; токен — в root-only
  tmpfs, не в окружении процесса агента (проверка как у `node-N`, вывод в отчёте). `svc-lo` не
  получает выхода никуда, кроме `api.z.ai` через `zai-egress`.
- [ ] Живая проверка: `hive last-order llm-gateway zai-egress`, одна сессия Last Order — в логе
  `pi`, ответ записан в её рабочее место или на доску. Расход по шлюзу за прогон — 0 ходов
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
  потребителя на том же токене. Записать в отчёт число запросов Last Order за прогон и размер контекста по файлу сессии Pi.
- Сёстры на стенде делят ту же квоту: 2026-10-31 они съели ≈125M из ≈141M токенов окна 5 ч.
  Живую проверку — при остановленных `node-N`, если оператор не сказал иначе.
- Перед подъёмом сверить с оператором, чей стенд поднят.

## Заметки надзора
- 2026-10-31, решение оператора («Approved by me»): харнесс Last Order — Pi, не zcode (zcode —
  самый дорогой по постоянной части и без порога сжатия, см. «Контекст»); модель — GLM-5.3
  вместо Flash: способности Last Order даёт модель, а не харнесс. Запасной — OpenCode через шлюз
  с тревогой (ADR-041 без изменений). Расход квоты GLM-5.3 против Flash не замерен
  [предположение: выше] — в отчёт.
- 2026-10-31: `after` + T-066 (`PiAdapter`). ZCode: перечитать задачу; код под zcode в worktree
  не коммитить как есть. Статус остаётся `in_progress`.
