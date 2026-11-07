# T-056. Отчёт исполнителя (надзор, задача `owner: supervisor`, D-006)

## Раунд 1 · 2026-11-07
Коммиты: adbfbae

### Сделано
- `dev/context_report.py` — разбор сессии Pi по частям (системный промпт по секциям, схемы
  инструментов, thinking, текст и вызовы сестры, вывод по инструменту), медианный и максимальный
  запрос, сумма, пробуждения. Отчёт с таблицами — `docs/agents/runs/2026-11-07-context.md`.
- `observe` (`runtime/mind/mcp_body.py`): компактный JSON; только блоки с открытой стороной внутри
  куба, число закопанных — `blocks_hidden`; группы по имени (`count`, 8 ближних `pos`); 12 ближних
  сущностей и `entities_more`; предел ответа 6000 символов (`fit_observe`). Описание инструмента обновлено.
- Все ответы MCP тела и hive — компактный JSON в тексте, тот же dict в `structuredContent`
  (`CallToolResult`). В hive тронута только сериализация в `_run`, запись базы не тронута.
- Тесты: `test_observe_is_compact_and_shows_only_blocks_in_view`,
  `test_observe_answer_has_a_ceiling_on_the_worst_cube`, `test_visible_blocks_without_a_center_drops_nothing`,
  `test_answers_are_compact_json` (hive).

Было/стало — в отчёте прогона: `observe` на равнине 37 981 → 623 символа, hive −15 %, оценка медианы
запроса 29.2k → ≈17.7k токенов, суммы входа −35 % [предположение, живой замер — T-058].

### Проверки
```
$ bash dev/check.sh          # из worktree
pytest: ok · 925 passed, 3 skipped, 2 warnings in 321.39s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ python dev/context_report.py C:/tmp/t056
Сессий: 15, запросов: 202, токенов входа всего: 6157316. Запрос: медиана 29192, максимум 69202 токенов.
```
Не прогонял: живой стенд (задача T-058).

### Отклонения от задачи
- Сессий прогона T-066 в томах нет (ранняя — 00150 от 2026-11-01): разобраны 15 сессий 2026-11-01 и 2026-11-06.
- Третья крупная часть под влиянием проекта — `project_context` (AGENTS.md, 8 %) — не сжата: без
  проверки поведения на стенде риск потерять правило. Самая крупная после `observe` — `thinking`
  (19 %) — настройка Pi, решение оператора. Сокращены: ответ `observe`, ответы hive, ответы тела.
- Склейка и таймаут `wait_events` не менялись: пустых пробуждений 3 из 49, тест на них не нужен.

### Вне задачи
- `tests/test_mcp_body.py` оставляет в корне worktree файлы `.mcp-body-test-*.jsonl` (4 за прогон).
- `lastorder-egress-1` на стенде в цикле перезапуска (`docker ps` 2026-11-07 13:2x UTC).
- Три инструмента ресурсов MCP Pi (≈1.8k символов схем на каждый запрос) не используются.

### Вопросы
- Нет.
