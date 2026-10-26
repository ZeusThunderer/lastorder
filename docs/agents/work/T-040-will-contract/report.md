
## Раунд 1 · 2026-10-26
Коммиты: cf925aa

### Сделано
- `runtime/hive/mcp.py` `submit_will`: принимается тело воли (2–4 строки) без frontmatter;
  валидация `check_will_body`, отказ — одна фраза без имён полей схемы; stage-файл теперь
  хранит только тело. Полный will.md по-прежнему принимается (берётся тело), чужой serial
  в его frontmatter — отказ, как раньше. Проверки авторизации (сессия, runner key, «тело
  мертво», одноразовость) не менялись.
- `runtime/node/runner.py`: `_staged_will` возвращает тело (legacy-stage с полным will.md
  читается через парсер; непригодный stage оставляет путь аварийной); `_will_of` собирает
  полный will.md через `assemble_will` с `emergency: false`, модель харнесса —
  `_harness_model(adapter_name)` из тех же полей cfg, что раздаёт `__main__` (у адаптеров
  модель приватная, adapters.py вне зоны).
- `runtime/lastorder/will.py`: `_assemble` → публичная `assemble_will` (без смены поведения),
  используется сценарием и раннером.
- `agent/last_order/skills/write-will/SKILL.md`: «pass only the will text (the 2-4 lines);
  authorisation and service fields are not your business» — та же длина, правило «после двух
  неудачных вызовов — стоп» уже было (T-039 R1-1).
- Тесты: `test_hive_mcp` — тело без frontmatter принимается; отказы (числа черт, >4 строк,
  пустая строка) одной фразой и ничего не staged; сквозной критерий 1 (тело → сборка как в
  раннере → retire → will.md в базе с serial/slot/cause/born_at/died_at/lifetime_s/traits/
  model, emergency: false). `test_node_runner` — смерть со stage-телом (emergency: false,
  model=GLM-5.3-Flash, тело дословно); legacy-stage с полным will.md (frontmatter берётся у
  среды, не у stage); stage, не проходящий проверки, → аварийная воля.

### Проверки
```
$ uv run pytest tests/test_node_runner.py tests/test_hive_mcp.py -q
60 passed, 3 skipped in 22.19s
$ uv run pytest tests/test_will.py -q
21 passed in 0.20s
$ bash dev/check.sh
pytest: ok · 736 passed, 3 skipped in 57.34s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
```
До правки скила один прогон check.sh падал: тест-якорь «authorisation» (lowercase) исчез
из-за заглавной буквы — формулировка ужата, якорь вернулся, повторный прогон зелёный.

### Отклонения от задачи
- Нет. Схема — предложенная в задаче: hive кладёт тело, раннер собирает will.md из полей
  аварийной воли с emergency: false.

### Вне задачи
- В корне репозитория сабмодуль `unionclef` грязный (`.settings/org.eclipse.buildship.core.prefs`,
  шум IDE, существовал до чата) — не чинил, `unionclef/` только чтение.

### Вопросы
- нет
