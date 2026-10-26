# T-040. Ревью надзора

## Раунд 1 · 2026-10-26 · вердикт: accept · headless
Проверено: `git diff main...z/T-040-will-contract` @ 83114f1, построчно (critical).

### Проверки (прогнаны надзором)
- `bash dev/check.sh` — pytest: ok · 736 passed, 3 skipped; check.py: ok. Совпадает с отчётом.

### Критерии приёмки
- [x] Тело без frontmatter принимается, после `retire` will.md с serial/slot/cause/born_at/died_at/
  lifetime_s/traits/model и `emergency: false` — `test_submit_will_takes_the_body_alone`,
  `test_staged_body_becomes_a_full_will_in_the_base`, раннер — `test_death_with_the_agents_will`.
- [x] Полный will.md принимается, чужой serial — отказ — `test_submit_will_gates` (ожидание staged
  поменялось на тело — это суть задачи, не ослабление), legacy-stage —
  `test_staged_full_will_markdown_still_yields_its_body`.
- [x] Отказ по `check_will_body` одной фразой без имён полей — `test_submit_will_refuses_broken_bodies_in_one_sentence`.
- [x] Авторизация (`X-Runner-Session`, `X-Runner-Key`, «тело мертво», одноразовость) не тронута —
  код до проверки тела не менялся, тесты гейтов зелёные.
- [x] `write-will/SKILL.md`: «pass only the will text», правило двух неудач на месте, длина та же.
- [x] Аварийная воля при пустом/непригодном stage — `test_death_without_a_will_gets_the_emergency_one`,
  `test_staged_body_failing_the_checks_falls_back_to_emergency`.

### Находки
- R1-1 · minor · `runtime/node/runner.py:610` · `_harness_model` берёт `cfg.<adapter>_model` через
  `getattr`; для адаптера без такого поля (будущий Pi, T-04x) в will.md попадёт имя адаптера.
  Не блокирует: поле информационное. Учесть при добавлении Pi.
- R1-2 · minor · `tests/test_hive_mcp.py` `test_staged_body_becomes_a_full_will_in_the_base`
  повторяет сборку раннера вручную; сквозной путь раннера закрыт отдельным тестом в
  `test_node_runner.py`, этого достаточно.

### Чек-лист проекта
Без замечаний: единственный писатель базы — hive (`retire`), раннер только собирает текст;
сырые id в теле режутся `check_will_body` и в hive, и в раннере; аварийный путь по сути не
изменён (непригодный stage → `emergency_will`); новых вызовов LLM нет; `schema.py` не тронут.
