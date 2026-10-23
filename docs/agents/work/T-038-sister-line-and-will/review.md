# T-038. Ревью надзора

## Раунд 1 · 2026-10-23 · вердикт: accept · headless
Проверено: `git diff main...z/T-038-sister-line-and-will` @ 8cdbc7a

### Проверки (прогнаны надзором)
- `uv run pytest` — 717 passed, 3 skipped
- `uv run python tools/check.py` — Every workplace is intact

### Критерии приёмки
- [x] Первая реплика — линия: `AGENTS.md` (bullet `inheritance()`) и шаг 3 `SESSION_PROMPT.md`;
  пустой/недоступный `inheritance()` покрыт; текста реплики нет, манера не зашита.
- [x] Нет инструмента — находка: `AGENTS.md` «Stuck…», запись в `memory/state.md`, вслух, только MCP
  `body`/`hive`, без обходных клиентов.
- [x] `write-will`: `submit_will` первым вызовом после чтения файлов, две неудачи — стоп, не исследовать.
- [x] Прирост +99 слов (по отчёту; по diff +9/−4 строк в AGENTS.md и +3/−1 в SESSION_PROMPT.md — согласуется).
- [x] Тест `test_line_finding_and_will_order_rules_present`, check.py зелёный.

### Находки
- R1-1 · minor · `agent/last_order/AGENTS.md:45` · из раздела «Your body» ушло «…write it down and
  propose it»; новое правило в «Stuck…» говорит записать и сказать, но не предложить. Не блокирует:
  `propose` описан в разделе сети. При следующей правке файла можно добавить «and propose it».

### Чек-лист проекта
Без замечаний. Изменения в пределах «Можно трогать».
