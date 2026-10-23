# T-036. Отчёт исполнителя

## Раунд 1 · 2026-10-24
Коммиты: c1de4c0, b2d7929, 7e220fc, bcbd0e9 + данный (report + status: review)

### Сделано
- `dev/stand_keys.py` (новый): `init [--env PATH]` генерирует недостающие/пустые локальные
  токены (`secrets.token_urlsafe(32)`, `GATEWAY_MASTER_KEY` — с префиксом `sk-`, как рецепт
  из `docker/gateway/up.sh`; `GATEWAY_DB_PASSWORD` — алфавит без `@:/#?`, он интерполируется
  в DATABASE_URL). Присутствующие непустые значения не трогаются, пустые заполняются на месте,
  отсутствующие дописываются в конец; остальные строки, комментарии и порядок — байт-в-байт.
  `issue [--env PATH]` выпускает недостающие `GATEWAY_KEY_*` через `gateway_keys.issue_key`
  (потолки ADR-035 по умолчанию), пишет их в файл после успешного выпуска всех; шлюз не поднят
  — понятная ошибка от gateway_keys («is the gateway up? … docker/gateway/up.sh»), rc 1, файл
  не меняется. Провайдерные ключи (`OPENROUTER_API_KEY`, `ZAI_API_KEY`) не создаются и не
  трогаются; недостающие называются по имени.
- Вывод — только имена ключей и added/kept/issued; значений нет ни в stdout, ни в stderr.
- `dev/gateway_keys.py`: выпуск вынесен в `issue_key()` + `token_of()` для переиспользования,
  CLI-вывод команд не менялся (тесты test_gateway_config.py зелёные без правок).
- `tests/test_stand_keys.py` (новый, 8 тестов): критерии 1–4 на временных файлах
  (сохранение файла, идемпотентность, форма токенов, подмена выпуска, шлюз лежит, утечка
  значений в вывод); критерий 5 — настоящий `docker/node/up.sh` на временном `.env` после
  `init` (каталог с `git init` как главный worktree, docker заменён записывающим стабом):
  без `NODE_DEV_OPEN`, exit 0, ни одного «refusing», доходит до `docker compose up -d`.
  Плюс пин списков LOCAL/VIRTUAL/PROVIDER против `docker/compose.yml` и обоих up.sh —
  переименование ключа в стенде падает в тесте первым.
- `docs/agents/docker-topology.md`: раздел «Ключи стенда» с порядком init → шлюз → issue →
  `docker/node/up.sh`.

### Проверки
```
$ bash dev/check.sh          # = uv run pytest + uv run python tools/check.py
pytest: ok · 724 passed, 3 skipped in 72.85s (0:01:12)
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ uv run pytest tests/test_stand_keys.py -q
8 passed in 9.78s
```

### Отклонения от задачи
- `GATEWAY_KEY_LO` выпускается с алиасом `last-order` (имя сервиса в compose); имена алиасов
  остальных — node-N/mind-N/hive, как в примерах `dev/gateway_keys.py`.

### Вне задачи
- `.env.example` не перечисляет `HIVE_MCP_LO_KEY`, `HIVE_NODE_KEY_1..3`, `HIVE_RUNNER_KEY_1..3`,
  `RCON_PASSWORD`, `GATEWAY_KEY_LO`, `GATEWAY_KEY_NODE_2/3`, `GATEWAY_KEY_MIND_2/3` — эти имена
  есть только в compose/up.sh; не трогал, вне «Можно трогать».

### Вопросы
- нет

### Вызовы надзора
- нет

### Самопроверка
- lo-checker: раундов 1, CHECK
- C-1 · fixed: данный коммит (report.md раунд 1 + status: review)
- C-2 · fixed bcbd0e9
