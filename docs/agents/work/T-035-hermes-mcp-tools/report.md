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
