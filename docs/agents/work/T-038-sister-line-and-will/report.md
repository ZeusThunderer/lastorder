# T-038. Отчёт исполнителя

## Раунд 1 · 2026-10-24
Коммиты: 82fda5b

### Сделано
- Первая реплика смены — линия предшественниц: правило в `agent/last_order/AGENTS.md`
  (bullet `inheritance()`, раздел The network) и шаг 3 `SESSION_PROMPT.md`: назвать
  предшественницу по номеру и что взяла из её воли или с чем спорит; если `inheritance()`
  недоступен или пуст — сказать это. Без готового текста («Your own manner, no ready-made
  text») — ADR-035 (тема, не текст) и ADR-018 (манера не зашита).
- Нет инструмента или отказ — две фразы в `AGENTS.md`, раздел «Stuck, context, end of a
  session»: записать в `memory/state.md`, сказать вслух, продолжать с тем, что есть; тело и
  сеть только через MCP `body` и `hive`, обходных клиентов к их портам и маршрутам не строить.
  Дублирующую фразу про находку в разделе «Your body» сжал, чтобы файл не рос.
- `skills/write-will/SKILL.md`: `submit_will` — первый вызов после чтения своих файлов;
  авторизация и сеть — не дело этой сессии; две неудачи — стоп, аварийное завещание пишет
  раннер (безлимитный retry из T-032, инцидент 1, убран).
- Тест `test_line_finding_and_will_order_rules_present` в `tests/test_sister_template.py`:
  иглы на все три правила; иглы сверяются с текстом, нормализованным по пробелам, чтобы ручной
  перенос строки не прятал правило (на этом первый прогон упал — исправлено тестом, не правилом).

### Проверки
```
$ bash dev/check.sh
pytest: ok · 717 passed, 3 skipped in 55.54s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ uv run pytest tests/test_sister_template.py -q
12 passed in 0.07s
```
Прирост слов (критерий 4; `len(text.split())`, `git show main:<файл>` против файла ветки):
- `AGENTS.md`: 924 → 994 (+70)
- `SESSION_PROMPT.md`: 141 → 170 (+29)
- итого +99 ≤ ~120

### Критерии приёмки
1. Первая реплика — линия: `AGENTS.md` + `SESSION_PROMPT.md` (выше), тест-иглы «first `say` of
   a shift», «predecessor by number», «unavailable or empty», «no ready-made text». Готово.
2. Нет инструмента — находка: `AGENTS.md` «Stuck…» (выше), иглы «missing or refusing tool is a
   finding», «no side clients». Готово.
3. `write-will`: иглы «first call of the session», «authorisation», «two failed calls»,
   «emergency will». Готово.
4. Прирост +99 слов (арифметика выше). Готово.
5. Тесты шаблона (12 passed) и `check.py` зелёные. Готово.

### Отклонения от задачи
- нет

### Вне задачи
- ничего; причина отсутствия инструментов — T-035, правило написано независимо от неё.

### Вопросы
- нет

### Вызовы надзора
- нет

### Самопроверка
- lo-checker: раундов 1, CHECK: changes
- C-1 · fixed: этим коммитом — report.md с раундом и подсчётом слов + `status: review`
  (суть находки была «задача не сдана», сдача и есть исправление)
