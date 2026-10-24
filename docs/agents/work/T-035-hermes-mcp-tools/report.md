# T-035. Отчёт исполнителя

## Раунд 1 · 2026-10-24
Коммиты: 1109631, 78b24d2

### Сделано
- `runtime/node/adapters.py`: `HERMES_TOOLSETS` = `("file", "terminal", "body", "hive")`.
  В Hermes v0.22.4 `-t/--toolsets` — это и allowlist запуска MCP-серверов
  (`hermes_cli/mcp_startup.set_mcp_server_filter`, сравнение по голому имени сервера), и
  поверхность `enabled_toolsets` агента (политика gateway-chat); голые `body`/`hive` — легальные
  алиасы toolset-ов `mcp-<имя>` (`tools/mcp_tool_registration.py:160`).
- `runtime/node/workplace.py`: `render_hermes_config` — `platform_toolsets.cli` тем же списком,
  комментарий и docstring согласованы с флагом.
- `tests/test_node_runner.py`: argv-тест (точное равенство хвоста argv обеих команд), тест
  согласованности флаг ↔ config (`test_hermes_toolsets_allow_exactly_the_two_mcp_servers`).

### Проверки
```
$ uv run pytest tests/test_node_runner.py -q
35 passed, 3 skipped
$ bash dev/check.sh
pytest: ok · 717 passed, 3 skipped · check.py: ok
```
Стенд (dev-open, ключей hive в `.env` нет): `NODE_DEV_OPEN=1 NODE_HARNESS=hermes
NODE_HARNESS_FAILOVER= NODE_QUOTA_ACTION=wait bash docker/node/up.sh mc-server body-1 mind-1
node-1 hive zai-egress`. Oneshot-сессия в node-1 (sister, HERMES_HOME уже отрисован входной
точкой, конфиг перерисован на настоящий hive):
```
docker exec -u 1001:1001 -e HERMES_HOME=/home/sister/.hermes lastorder-node-1-1 \
  hermes chat --query-file /tmp/t035-prompt.txt --oneshot --format stream-json --yolo \
  --toolsets file,terminal,body,hive
```
Исход: exit 0, 258 событий, но инструментов MCP в сессии нет. Модель видит инструменты через
`tool_search` (отложенный каталог), не сразу списком; сама перечислила: patch, read_file,
search_files, terminal, write_file, tool_search, tool_describe, tool_call, process_manage.
`tool_search {"queries":["body observe","hive"]}` → 0 совпадений, `available_sources:
[{"name":"terminal","tool_count":1}]` — источники body/hive не подключены. Прямой пробой в
контейнере: `discover_mcp_tools()` → `registered: []`, `not connected: ['body','hive']`, в логе
`mcp package not installed -- MCP tool support disabled`. Досягаемость самих эндпоинтов есть
(curl body-1:8700/mcp и hive:8000/node/mcp из node-1 отвечает 200 и держит SSE-поток).
Критерий про вызов observe() не выполнен — причина ниже, не во флаге.

### Отклонения от задачи
- Критерий 3 не выполнен полностью: инструменты hive «видны» только отрицательным результатом
  (источников нет). Причина — вне «Можно трогать», см. «Вопросы».

### Вне задачи
- `docker/node/Dockerfile` (слой hermes, `uv sync --frozen --no-dev --python 3.14`): пакет `mcp`
  не попадает в /opt/hermes — upstream держит его в extra `mcp` (`mcp==2.3.0`+`httpx2`+`starlette`,
  `/opt/hermes-src/pyproject.toml` [project.optional-dependencies]). Образ 2026-10-22 актуален
  текущему Dockerfile (проверено по истории git). egress на PyPI из песочницы закрыт (ADR-033),
  т.е. SDK доставляется только пересборкой образа.

### Вопросы
- Критерий 3 (вызов observe() и инструменты hive на стенде) невыполним на текущем образе:
  в hermes-venv нет пакета `mcp`, MCP отключён на уровне SDK. Чинится одной строкой в
  `docker/node/Dockerfile` (`--extra mcp` в `uv sync` hermes-слоя) + пересборкой образа, но
  `docker/` в «Нельзя трогать» (строка задачи «Объём»). Варианты: 1) расширить «Можно трогать»
  на `docker/node/Dockerfile` — доделаю в этой задаче и закрою критерий 3 той же сессией; 2) отдельная
  задача на образ, T-035 сдавать по критериям 1–2 с ограничением; 3) надзор правит сам. Я бы выбрал 1.

### Расход на стенде
- 2 сессии (лимит «одна-две»). (1) Воля-сессия раннера со старым флагом (T-032-состояние: сестра
  00094 мертва), убита на ~4-й минуте для экономии: 9 вызовов API, input 16129, output 3538,
  cache_read 86464 [проверено: state.db `session_model_usage`]. (2) Проверочная oneshot выше:
  2 вызова API, total 11694 токенов (input 6370 + cache_read 4160, output 1164, reasoning 888).
  estimated_cost_usd в state.db 0.0/unknown — план Z.ai, оплаты по балансу нет; всего 11 запросов.
- Стенд после проверки остановлен (`docker stop` на поднятые контейнеры); раннер node-1 перед
  этим был заморожен SIGSTOP, чтобы прервать непрерывный цикл жизней из T-032-состояния.

### Вызовы надзора
- нет

## Раунд 2 · 2026-10-24
Коммиты: 1109631, 78b24d2, ffdcef9; верификация и этот отчёт — коммит раунда.

### Ответ на ревью
- C-1 · fixed ffdcef9 + раунд 2: пересборка, `import mcp`, повторная oneshot-сессия и расход
  второй партии записаны ниже (в раунде 1 они существовали только в сообщении диспетчеру).
- C-2 · fixed: список остановленных контейнеров — раздел ниже.
- C-3 · fixed: верификация правки образа (пересборка + `import mcp`) записана здесь; артефакт
  сессии приложен путём `C:/lastorder-t035-run/t035-check-session2.jsonl`.

### Сделано (ответ 1)
- `docker/node/Dockerfile` (ffdcef9): `uv sync --frozen --no-dev --extra mcp --python 3.14`
  в слое hermes + 2 строки комментария. `--frozen` не снимался; uv sync с extra прошёл без
  отказа, других extras нет.
- Пересборка: `docker compose -f docker/compose.yml build node-1` → `Image lastorder-node:dev
  Built`, образ `d0529a17f42c` (created 2026-10-24 01:52 +0200, позже коммита ffdcef9).
- Обязательная проверка SDK: `docker run --rm --entrypoint /opt/hermes/bin/python
  lastorder-node:dev -I -c "import mcp; …"` → `mcp ok 2.3.0
  /opt/hermes/lib/python3.14/site-packages/mcp/__init__.py`.
- Стенд (dev-open, тот же набор сервисов, раннер заморожен SIGSTOP на birth.arm_retry —
  сессий раннера не было). Входная точка нового образа отрисовала `platform_toolsets.cli =
  [file, terminal, body, hive]`. Oneshot-сессия с тем же промптом, что в раунде 1:
  ```
  docker exec -u 1001:1001 -e HERMES_HOME=/home/sister/.hermes lastorder-node-1-1 \
    hermes chat --query-file /tmp/t035-prompt.txt --oneshot --format stream-json --yolo \
    --toolsets file,terminal,body,hive
  ```
  exit 0, 779 событий, session `local-1019c1f0ab82c5bfe73fb21da45dd107aea49a89dcdf351f874a9fd137ad0e3e`.
- Критерий 3. Вызов тела: в потоке `tool_use: tool_describe {"names": ["mcp__body__observe"]}`
  и `tool_use: mcp__body__observe {}`; в логе шлюза `MCP server 'body' (HTTP): registered
  10 tool(s)`, `MCP: registered 21 tool(s) from 2 server(s)`,
  `tool mcp__body__observe completed (1.26s, 52865 chars)` (POST http://body-1:8700/mcp → 200).
  Ответ observe начинается `"self": {"in_game": true, "hp": 0.0, "dead": true, …}` — T-032
  состояние аватара. Инструменты hive видны: модель перечислила 11 (mcp__hive__inbox, send,
  propose, vote, submit_will, inheritance, knowledge, get_prompt, list_prompts,
  list_resources, read_resource); body 10 + hive 11 = 21. Вызова hive не было — промпт его не
  требовал; в dev-open вызов ответил бы «unknown node key» (ожидаемо, ответ 1).
- Как инструменты видны модели: через отложенный каталог — имена доступны сразу через
  `tool_search`, схема подгружается `tool_describe` (модель сделала describe → call), готового
  списка в init-событии нет.

### Проверки
```
$ bash dev/check.sh
pytest: ok · 717 passed, 3 skipped · check.py: ok
$ uv run pytest tests/test_node_runner.py -q
35 passed, 3 skipped
```

### Расход на стенде
- Вторая партия (после пересборки): 1 сессия из разрешённых двух — 3 вызова API, total 19876
  токенов (input 18550, из них cache_read 11456; output 1326, reasoning 497) [проверено:
  result-событие артефакта и state.db `session_model_usage`].
- Всего за задачу: 3 сессии, 14 запросов API; оплата — план Z.ai, estimated_cost_usd 0.0.

### Остановленные контейнеры после проверки
`lastorder-node-1-1`, `lastorder-mind-1-1`, `lastorder-body-1-1`, `lastorder-hive-1`,
`lastorder-mc-server-1`, `lastorder-zai-egress-1` — `docker ps` по этим именам пуст.

### Отклонения от задачи
- нет

### Вне задачи
- gateway.log до регистрации MCP пишет «8 tool schema(s) materialized» (схемы базовых
  toolset-ов) — косметика upstream, не чинил.

### Вопросы
- нет

### Вызовы надзора
- 2026-10-24 · answer · 9cf4dc2 · VERDICT: answered · cost_usd: 0.39687800000000006 · effort: medium

### Самопроверка
- lo-checker: раундов 1 · CHECK: C-1 blocker, C-2/C-3 minor — все закрыты этим раундом
- C-1 · fixed ffdcef9 (+ этот коммит: верификация и отчёт) · C-2 · fixed (этот коммит) ·
  C-3 · fixed (этот коммит)
