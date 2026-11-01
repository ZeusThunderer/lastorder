# T-059. Отчёт исполнителя

## Раунд 1 · 2026-11-01
Коммиты: 4257168 (wip до перескоупа), 35151f6 (merge main c T-066), d09b53b (переделка на Pi)

### Сделано
- `runtime/hive/admin.py`: `LastOrderPiAdapter(PiAdapter)` — argv/env раннера без правок
  (`pi -p --mode json --session-dir .pi-sessions --no-approve --offline --no-skills
  --skill skills -- <промпт>`), свой рендер agent-dir: mcp.json только hive, `direct`,
  `/last-order/mcp` с ключом Last Order; models.json/settings.json — из
  `render_pi_configs` (T-066), ключ — файл `zai-key` 0400 сестры через
  `apiKey: "!cat …"`. Рендер перед каждой сессией (лечит подмену). zcode-ветка WIP
  удалена, zcode.json рабочего места больше не пишется.
- Выбор и квота: `ADMIN_HARNESS=pi` по умолчанию, `ADMIN_PI_MODEL=GLM-5.3` (выбор
  оператора 2026-10-31), failover opencode через шлюз (`llm-gateway/<smart>`),
  `quota_exhausted` → следующая сессия на failover + `alarm` на доску (`harness_failover`,
  X-Node-Key, автор `admin`); без готового failover — `quota.waits`; рестарт процесса —
  снова pi (правило раннера). Валидация пары харнессов на боте.
- `docker/compose.yml`: `ADMIN_HARNESS:-pi`, `ADMIN_PI_MODEL:-GLM-5.3`,
  `ADMIN_PI_CONTEXT_WINDOW:-96000`, `ADMIN_HARNESS_FAILOVER:-opencode`; zai-egress в
  `svc-lo` с алиасом `api.z.ai` (сеть internal, единственный выход); токен — тем же
  лифтом, что node-N (`ZAI_API_KEY` → entrypoint → root-only tmpfs `ZAI_KEY_FILE`).
  `ADMIN_ZCODE_MODEL`/`ZCODE_MODEL` из окружения службы убраны.
- `docs/agents/docker-topology.md`: раздел «Last Order и выход к Z.ai (T-059)».
- Тесты: `tests/test_admin_harness.py` (команда и env pi без ключа, agent-dir: hive-only
  direct, contextWindow/compaction из конфига, zai-key 0400 и `!cat`, лечение подмены,
  квота → opencode + тревога, waits без готового failover, рестарт → pi, валидация
  пары, from_env), `test_admin_wake.py` (промпт после `--`, свежий env с
  PI_CODING_AGENT_DIR), `test_stand_compose.py` (pi-строки службы, поверхность ключей
  = lo/node-gateway/zai, алиас egress в svc-lo).

### Обоснование `ADMIN_PI_CONTEXT_WINDOW`=96000
Значения сестёр из T-066: порог 96k − 16k = 80k; после сжатия ≈ базовый контекст
(~11.5k символов ≈ 3k токенов у сестры; у стола SESSION_PROMPT больше, ~10–15k) +
сводка + keepRecent 20k ≈ 40k; зазор до порога ~40k — без сжатий подряд. Сессии стола
короткие (пробуждение по поводу), потолок — страховка от разбега одной сессии; долгая
сессия T-043 настроит своё.

### Проверки
```
$ bash dev/check.sh
pytest: ok · 855 passed, 3 skipped, 2 warnings in 98.32s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ uv run pytest tests -k "admin"
(test_admin_harness.py 10, test_admin_wake.py 20, test_stand_compose 5 — в полном прогоне)
```
Сухой прогон в образе узла (Linux, Pi 1.4.0), веточный `runtime/` bind-mount'ом,
деск как root → `su sister` [проверено: 2026-11-01 ~00:15 UTC]:
- `session()`: argv `pi -p --mode json --session-dir .pi-sessions … -- <промпт>`,
  env ровно `{"PI_CODING_AGENT_DIR": "/home/sister/.pi/agent"}` — ключа нет нигде;
- рендер: mcp.json/models.json/settings.json root:root 0444, `zai-key` 1001:1001 0400;
  mcp.json — только hive `direct` `/last-order/mcp`;
- `pi auth print-api-key --provider zai` напечатал содержимое zai-key — разрешение
  `!command` работает и для рендера стола.

### Живая проверка — не прогонял: сеть оператора
- Из контейнеров наружу TLS по-прежнему рвётся: `zai-egress` → api.z.ai —
  `SSL routines::unexpected eof` [проверено: 2026-11-01]; с хоста api.z.ai отвечает
  (301). Сессия до модели не дойдёт при любом стенде — переносится, как в T-066.
- Пересборка образа `node`/`last-order` тоже блокируется: `uv sync` в билде не может
  скачать hatchling с pypi (та же сеть). Стенд оператора (проект `lastorder`,
  поднят рецептом T-052) не трогал; тело и ум слота 1 оставлены как были.
- Когда сеть починят: пересборка образа, `up zai-egress last-order`, одна сессия
  (ответ модели), расход по шлюзу — 0 ходов `smart`. Квота Z.ai не тратилась: живых
  запросов к модели из моих проверок 0.

### Ключи (критерий 3)
Поверхность как у node-N: провайдерских ключей в `last-order` нет, кроме токена Z.ai;
токен — root-only tmpfs (entrypoint materialize 0400 + unset), в env процесса агента
отсутствует (проверено сухим прогоном выше); сестра-агент имеет свой zai-key 0400 —
тот же класс экспозиции, что провайдер-конфиг zcode в её home (ADR-033).

### Отклонения от задачи
- Нет. zcode-часть незакоммиченного WIP прошлого чата переработана на Pi согласно
  перескоупу; egress/tокен-части WIP сохранены.

### Вне задачи
- `docker/node/up.sh` не поднимает `GATEWAY_KEY_LO`/`HIVE_MCP_LO_KEY` из `.env` (токен
  Z.ai — поднимает). Полный подъём стола сейчас требует экспорта руками; лечить —
  отдельной правкой up.sh (не токен, «если нужно» из задачи не покрывает).
- `docker-topology.md`: таблица «Сервисы» не содержит node-N/last-order/zai-egress —
  устарела шире задачи, не трогал.

### Вопросы
- нет
