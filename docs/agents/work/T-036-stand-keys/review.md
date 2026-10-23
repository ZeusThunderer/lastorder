# T-036. Ревью надзора

## Раунд 1 · 2026-10-23 · вердикт: accept · headless
Проверено: `git diff main...z/T-036-stand-keys` @ 8819bbb

### Проверки (прогнаны надзором)
- `uv run pytest` — 724 passed, 3 skipped, 0 failed
- `uv run python tools/check.py` — Every workplace is intact

### Критерии приёмки
- [x] `init` дописывает только отсутствующие/пустые токены, остальное сохраняет —
  `test_init_fills_only_missing_and_preserves_the_rest`, `test_init_is_idempotent`.
- [x] `issue` через `gateway_keys.issue_key`, шлюз лежит → понятная ошибка, rc 1 —
  `test_issue_uses_gateway_keys_and_writes_tokens`, `test_issue_gateway_down_is_a_clear_error`.
- [x] Значения не печатаются — `test_no_secret_value_ever_printed`; в коде печатаются только имена.
- [x] Провайдерные ключи не создаются, недостающие названы — проверки в тесте init.
- [x] После `init` настоящий `docker/node/up.sh` без `NODE_DEV_OPEN` проходит — `test_node_up_sh_passes_after_init`.
- [x] Раздел «Ключи стенда» с порядком init → шлюз → issue → node/up.sh.

Списки ключей сверены надзором с `docker/compose.yml`, `docker/node/up.sh`, `docker/gateway/up.sh`
(grep): расхождений нет, плюс их закрепляет `test_key_lists_cover_the_stand`.

### Находки
- R1-1 · minor · `dev/stand_keys.py` `cmd_issue` · если выпуск падает на k-м ключе, уже выпущенные
  1..k-1 в `.env` не пишутся, а в шлюзе остаются сиротами; при повторном запуске их выпустят заново.
  Лучше писать в файл всё, что успели выпустить (так `.env` остаётся согласован со шлюзом).
  Не блокирует: на живом стенде шлюз обычно либо лежит целиком (падает первый ключ), либо работает.
- R1-2 · minor · вне задачи · в `.env.example` нет `HIVE_*`, `RCON_PASSWORD`, `GATEWAY_KEY_LO/NODE_2..3/MIND_2..3`
  (из отчёта). Кандидат на отдельную задачу.

### Чек-лист проекта
Без замечаний. Изменения в пределах «Можно трогать»; `gateway_keys.py` — только вынос функций,
CLI-вывод тот же.
