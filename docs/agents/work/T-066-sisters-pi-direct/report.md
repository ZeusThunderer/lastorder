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
