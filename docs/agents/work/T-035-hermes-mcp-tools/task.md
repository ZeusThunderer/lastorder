---
id: T-035
title: Hermes — инструменты MCP тела и hive в сессиях узла
stage: E3
status: ready
criticality: critical
branch: z/T-035-hermes-mcp-tools
created: 2026-10-23
---

# T-035. Hermes — инструменты MCP тела и hive в сессиях узла

## Цель
В сессиях Hermes (жизнь и завещание) у сестры есть инструменты MCP-серверов `body` и `hive`, и она
ими пользуется. Сейчас их нет ни в одной сессии: тело и сеть сёстры дёргают самодельными
HTTP-клиентами через `terminal`.

## Контекст
- Прогон T-032 (`docs/agents/runs/2026-10-23-mvp.md`), артефакты `C:/lastorder-t032-run/run/hermes-messages.jsonl`:
  24 сессии, вызовы инструментов — `terminal` 803, `read_file` 220, `write_file` 77, `tool_search` 38,
  `patch` 34, `search_files` 24; вызовов MCP — 0 [проверено надзором: подсчёт `tool_name` по файлу].
  Память сестёр: 00085 «Hermes session had no MCP tools loaded», 00086 «MCP-инструментов в сессии
  нет», 00093 «Инструменты профиля не подключены» (`workplaces-*/archive/*/memory/state.md`).
- Причина: `HERMES_TOOLSETS = ("file", "terminal")` и `--toolsets` в `runtime/node/adapters.py:170,195`.
  В Hermes v0.22.4 значение `-t/--toolsets` — ещё и список разрешённых к запуску MCP-серверов:
  `hermes_cli/mcp_startup.py` `set_mcp_server_filter` — сервер не из списка в oneshot не стартует
  [проверено надзором: исходник в образе `lastorder-node:dev`, `/opt/hermes-src`]. Серверы имеют
  toolset `mcp-<имя>`, голое имя — алиас (`tools/mcp_tool_registration.py:160`).
- `render_hermes_config` (`runtime/node/workplace.py:282`): `platform_toolsets.cli: [file, terminal]`,
  комментарий «without any --toolsets flag: files, terminal, MCP» — неверен при флаге.
- Режим воли: `set_will_mode` переводит hive MCP на will-прокси, `will_session` идёт с теми же
  `_base()` — после правки прокси тоже должен загрузиться.
- dev-open: `X-Node-Key` пустой, hive MCP отвечает «unknown node key» (00089). Прогоны идут на стенде
  с ключами (T-036), открывать hive в dev-open не нужно — fail-closed R2-6 остаётся.
- T-034 видела симптом («свой `mcp.py`, REST-fallback»), но не причину.

## Объём
Можно трогать:
- `runtime/node/adapters.py` — `HERMES_TOOLSETS`, `HermesAdapter`
- `runtime/node/workplace.py` — `render_hermes_config`, комментарии рядом
- `tests/test_node_runner.py` — тесты Hermes-адаптера и рендера
- файлы задачи

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`, `.env`
- `runtime/hive/`, `runtime/mind/`, `docker/`, `agent/`
- сетевую политику песочницы (ADR-033): набор серверов — те же два, `body` и `hive`

## Критерии приёмки
- [ ] Команды `life_turn` и `will_session` разрешают MCP-серверы `body` и `hive` при сохранённых `file`
  и `terminal`; других toolset-ов не добавлено. Тест на argv.
- [ ] `render_hermes_config` согласован с флагом (`platform_toolsets` и комментарий); тест.
- [ ] Стенд, одна oneshot-сессия Hermes в `node-1` с коротким запросом (перечислить свои инструменты,
  один раз вызвать `observe()`, остановиться): в выводе или `state.db` есть вызов инструмента
  MCP тела и видны инструменты hive. Команда и выдержка — в report.md. В отчёте же — как инструменты
  видны модели: сразу в списке или через `tool_search`.
- [ ] Расход проверки на стенде — одна-две сессии; число кредитов в report.md.

## Проверки
```
uv run pytest
uv run python tools/check.py
```
Стенд: `NODE_HARNESS=hermes` и ключи как в T-032 (`docker/node/up.sh`), ждать готовности через
`dev/wait_for.sh`. Если ключей hive в `.env` нет — стенд dev-open, критерий про hive ограничить
списком инструментов (вызов отвечает «unknown node key» — это ожидаемо, записать).

## Ограничения
- Сырых команд uc, `@gamer` и команд сервера в инструментах нет (CLAUDE.md, ADR-033).
- Квота Z.ai общая с воркером: на стенде не больше двух сессий.

## Заметки надзора
- 2026-10-23 · поставлена по разбору артефактов T-032.
