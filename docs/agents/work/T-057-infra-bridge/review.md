# T-057. Ревью надзора

## Раунд 1 · 2026-11-08 · вердикт: accept · headless
Проверено: `git diff main...z/T-057-infra-bridge` @ 9d0ee02, построчно (critical); правка D-009 @ 66548fe

### Проверки (прогнаны надзором)
- `bash dev/check.sh` @ 9d0ee02 — pytest 981 passed, 3 skipped; check.py ok
- `bash dev/check.sh` @ 66548fe — pytest 983 passed, 3 skipped; check.py ok
- `uv run pytest tests/test_hive_infra.py -q` @ 66548fe — 44 passed
- `python dev/infra_bridge.py --dry-run` — не прогнан: нужен живой hive с `HIVE_INFRA_KEY`, стенд не брал.
  Принято по выдержке отчёта и `test_a_dry_run_touches_nothing`.
- `docker compose config` — не прогнан; по `docker/compose.yml` ключ `HIVE_INFRA_KEY` есть только
  в `hive` (стр. 372), в `last-order`, `node-N`, `mind-N` его нет.

### Критерии приёмки
- [x] Закрытый набор, только Last Order, проверка при создании и в исполнителе — `infra.py:check`,
  `board.py:_check_request_payload`, `process()`; отказы — `test_bad_actions_and_args_are_refused`,
  sister/operator 403.
- [x] Исполнение без одобрения, итог `done`/`failed` с обрезанным выводом, без повтора —
  `infra_take` (условный UPDATE), `infra_result` (409 на повтор), тесты.
- [x] Переключатель — оператор, `infra_state` у Last Order, тест; доработано D-009 (см. R1-2).
- [x] Ключ отдельный, fail-closed при совпадении (`test_a_duplicated_infra_key_refuses_to_start`),
  в песочницах нет.
- [x] `--dry-run` и живой `slot_restart 2` (#40) — по выдержке отчёта.
- [x] Инструкция — `agent/network/skills/infra/SKILL.md`, строка в AGENTS.md; правил о ситуациях в коде нет.
- [x] `dev/check.sh`, `tools/check.py` зелёные.

### Находки
- R1-1 · major · `runtime/hive/board.py` `infra_result` · вывод команды шёл на доску без фильтра сырых
  id: `logs body-N` занёс бы `minecraft:…` в тред · маскировать · fixed by supervisor 66548fe (D-009)
- R1-2 · major · `dev/infra_bridge.py` `main_with` · переключатель читался раз за цикл: выключение
  посреди пачки не останавливало следующие take (до нескольких минут каждый) · перечитывать перед
  каждым take · fixed by supervisor 66548fe (D-009)
- R1-3 · minor · `dev/infra_bridge.py:219-224` · запрос, отклонённый повторной проверкой, получает
  `result` без `take` → настоящая доска ответит 409, запрос останется `open` и будет печататься каждый
  цикл. Достижимо только в обход проверки доски. Можно поправить попутно в T-060 (take, затем failed).
- R1-4 · minor · `dev/stand_keys.py init` не генерирует `HIVE_INFRA_KEY` — вне «Можно трогать»,
  ключ ставится вручную (задокументировано). Кандидат в задачу.

### Чек-лист проекта
- Сырые id: закрыто R1-1. Путь смерти, база, уровни LLM не затронуты.
- Ограничение «перед реальным прогоном сверить с оператором» нарушено: прогон на главном стенде без
  ответа оператора (радиус минимальный: пересоздан hive, slot 2 без живой сестры). Оператору: hive
  главного стенда несёт разовый `HIVE_INFRA_KEY` из окружения — для постоянства добавить в `.env`.
