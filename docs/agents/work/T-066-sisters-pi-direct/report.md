# T-066. Отчёт исполнителя

Исполнитель — надзор (Claude), исключение по решению оператора 2026-10-31; ревью — ZCode.

## Раунд 1 · 2026-10-31
Коммиты: efcce7b

### Сделано
- `runtime/node/adapters.py`: `PiAdapter` — `pi -p --mode json --session-dir .pi-sessions
  --no-approve --offline --no-skills --skill skills -- <prompt>`; продолжение — `--session <id>`
  с `CONTINUE_MESSAGE`; id — из записи `{"type":"session"}`. Ключ — `ZAI_API_KEY` в env процесса
  (`models.json` его подставляет), в argv ключа нет. Метка квоты 1308.
- `runtime/node/workplace.py`: `render_pi_configs` — body и hive `"exposure": "direct"`,
  `autoEnableCodemode: false`, `contextWindow` модели и `compaction` в `settings.json`;
  `drop_pi_agent_dir` запоминает параметры, `set_will_mode(…, "pi", …)` — свежий рендер с hive
  на прокси воли (правило Hermes R2-2). Конфиги root 0444, запись link-safe.
- `runtime/node/config.py`: `pi` в `HARNESSES` и по умолчанию; `PI_MODEL` (по умолчанию
  `ZCODE_MODEL`), `PI_CONTEXT_WINDOW` (96000), `PI_KEEP_RECENT_TOKENS` (20000).
- `runtime/node/runner.py`: `pi` готов, когда есть ключ Z.ai. `__main__.py` — проводка.
- `docker/compose.yml`: `HARNESS: ${NODE_HARNESS:-pi}`, параметры сжатия.

Потолок контекста: сжатие при `> 96000 − 16384 ≈ 80k`, после сжатия ≈ база + сводка +
20k [предположение: ≈45k]. Зазор ≈35k — без сжатия через ход. На zcode жизнь росла до 200–240k.

### Проверки
```
$ bash dev/check.sh
pytest: ok · 844 passed, 3 skipped, 2 warnings in 105.35s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ python tools/check.py
Every workplace is intact: files present, links resolve, boxes parse.
```
Новые тесты (`tests/test_node_runner.py`): argv и id сессии Pi; MCP direct и потолок;
воля Pi поверх подменённого `mcp.json` и возврат hive на следующем ходе; воля без рендера
ничего не пишет; 1308; `pi` по умолчанию, остальные выбираемы.

Сухой прогон в образе узла (Pi 1.4.0), рабочее место — копия `agent/last_order`, тот же argv
[проверено: 2026-10-31 21:30 UTC]:
- флаги приняты, stderr пуст, первая запись — `{"type":"session",…}`, файл сессии в
  `.pi-sessions/`;
- системный промпт Pi: preamble 169, tools 339, rules 839, docs 1301, project_context
  (AGENTS.md) 7555, skills 1254 (4 навыка рабочего места) — ≈11.5k символов, ≈3k токенов.
  У zcode первый запрос — ≈31k токенов [источник: model_usage zcode хоста];
- запросы к модели не прошли: `Connection error.` / `Request timed out.`. Причина — сеть
  контейнеров хоста: TLS к api.z.ai и www.google.com из контейнера рвётся
  (`SSL routines::unexpected eof`), с хоста api.z.ai отвечает. Pi при этом выходит с кодом 0.

### Не прогнано
- Короткий прогон на стенде (1 слот, 15 мин): квота Z.ai до 23:04 UTC и нет выхода из
  контейнеров. MCP тела и hive в Pi вживую не проверены, сжатие вживую не видели.

### Отклонения от задачи
- Нет. Прогон перенесён по условию критерия.

### Вне задачи
- Pi выходит с кодом 0 при ошибках провайдера: раннер видит обычное завершение и
  перезапускает с паузой — как у zcode. Признак квоты ищется по тексту, 1308 теперь есть.
- Секция `docs` (1.3k символов) в системном промпте Pi сестре не нужна — для T-056.
- Образ `node` несёт `runtime/` копией (`docker/node/Dockerfile:86`): после merge нужна
  пересборка `node-N`.

### Вопросы
- нет

## Раунд 2 · 2026-11-01 — ревью ZCode (решение оператора) и фикс
Коммиты: efcce7b (надзор, D-004) + фикс этого раунда.

### Ревью кода
- Диф efcce7b прочитан построчно (задача критичная). `PiAdapter`, перерендер перед каждой
  сессией, воля через `_PI_RENDER`-параметры (контракт Hermes R1-1/R2-2), `session_id`,
  `direct`-MCP, сжатие 96k/16k/20k с обоснованием, 1308, `pi` по умолчанию — соответствуют
  критериям; находок против реализации нет.
- Открытая находка надзора «ключ Z.ai в env процесса Pi» — подтверждена и закрыта фиксом
  (ниже). Проверка `PI_CODING_AGENT_DIR` по бандлу Pi 1.4.0: переменная существует
  (`ENV_AGENT_DIR = APP_NAME.toUpperCase() + "_CODING_AGENT_DIR"` → `getAgentDir()`),
  путь по умолчанию тот же `/home/sister/.pi/agent`.

### Фикс: ключ в файле, не в env
- Pi 1.4.0 поддерживает `!command` в значениях конфига: `parseConfigValueReference` —
  строка с `!` → `execSync(rest, {timeout: 10s}).trim()` [проверено: бандл
  @earendil-works/pi-coding-agent 1.4.0 в образе lastorder-node:dev].
- Живая проверка без запроса к модели: в контейнере как sister отрендерен `models.json` с
  `"apiKey": "!cat /home/sister/.pi/agent/zai-key"` → `pi auth print-api-key --provider zai`
  напечатал содержимое файла [проверено: 2026-11-01, вывод `fake-zai-key-12345`, exit 0].
- Правки: `drop_pi_agent_dir` пишет `zai-key` (0400, владелец сестра, link-safe
  `_write_file_no_links`) и включает его в параметры `_PI_RENDER` — рендер воли ключ
  переписывает; `render_pi_configs`: `apiKey: "!cat <agent_dir>/zai-key"` вместо
  `$ZAI_API_KEY`; `PiAdapter._command`: из env убран `ZAI_API_KEY` (остался только
  `PI_CODING_AGENT_DIR`). Экспозиция — как у zcode-провайдера в home сестры: прочитать
  может, в вывод `env` и в архив сама не попадает (ADR-033).

### Проверки
```
$ uv run pytest tests/test_node_runner.py -q
47 passed, 3 skipped in 6.20s
$ bash dev/check.sh
pytest: ok · 844 passed, 3 skipped, 2 warnings in 86.26s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
```
Тесты: обновлены `test_pi_adapter_commands_and_session_id` (env без ключа, файл 0400,
`!cat` в models.json, ключа нет в конфигах), `test_pi_configs_expose_mcp_directly…`,
`test_pi_will_mode_re_renders…` (ключ переписывается рендером воли),
`test_pi_configs_render_the_zai_plan…` (T-041, под новый контракт).

### Сеть стенда (вне кода)
- Выход контейнеров наружу по-прежнему сломан у оператора: из `zai-egress` к api.z.ai —
  `SSL routines::unexpected eof` [проверено: 2026-11-01]; с хоста api.z.ai отвечает (301).
  Живой прогон сестры на Pi невозможен до починки сети Docker/VPN — переносится, как
  в раунде 1 (условие критерия). Квота Z.ai здесь ни при чём.

### Вопросы
- нет
