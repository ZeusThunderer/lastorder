# T-051. Ревью надзора

## Раунд 1 · 2026-10-31 · вердикт: changes · headless
Проверено: `git diff main...z/T-051-operator-in-game-chat` @ b69adaf

### Проверки (прогнаны надзором)
- `bash dev/check.sh` — pytest: ok · 832 passed, 3 skipped; check.py: ok
- `uv run pytest tests/test_executor.py tests/test_mcp_body.py tests/test_perception_chat.py tests/test_perception.py -q` — 109 passed

### Критерии приёмки
- [x] Метка оператора по серверному префиксу, текст её не подделывает — `test_operator_lines_are_marked_by_the_speakers_name` (`<Bob> <ZeusThunderer> hi` → `from: Bob`); формат записан в отчёте.
- [ ] Пусто или совпадает с `LastOrderNN` → меток нет — отказ срабатывает только на имя **своего** слота, см. R1-1. Без предупреждения для пустой переменной — принято (R1-2).
- [x] Накопление до потолка 20, сверх — «… N earlier lines dropped» — `test_chat_batches_accumulate_*`, `cap_chat_entries`.
- [x] В `observe` та же метка — `mcp_body.py:362` через `chat_entry`, тест в `test_mcp_body.py:677`.
- [x] Детектор смерти и фильтр — смерть читает сырой `recent_chat` и `_clean_text`, путь не тронут; служебные строки отбрасывает `is_service_line` до разбора; тесты perception зелёные.
- [x] Инструкции — AGENTS.md «What others say», SESSION_PROMPT; без правил на конкретную ситуацию (см. R1-3).
- [x] `docker-topology.md` — вход, риск подмены, пометки стоят.
- [x] `bash dev/check.sh` — ok.

### Находки
- R1-1 · major · `runtime/mind/executor.py:319` · Отказ сравнивает `OPERATOR_MC_NAME` только с `username` этого mind. Переменная одна на mind-1..3 (`compose.yml` `x-mind`). При `OPERATOR_MC_NAME=LastOrder02` mind-2 откажет, а mind-1 и mind-3 пометят реплики сестры 02 как `from: operator`: голос сестры выдаётся за голос оператора. Критерий требует отказа для любого имени слота `LastOrderNN`. · Отказывать для любого имени слота: шаблон `LastOrder\d{2}` или префикс слота из конфига, если он там есть. Сравнивать без учёта регистра: имена Minecraft уникальны без учёта регистра. Ту же нормализацию применить в `chat_entry` (`zeusthunderer` в конфиге и `ZeusThunderer` в игре — один игрок). Нужен тест: mind слота 01 с `OPERATOR_MC_NAME=LastOrder02` — отказ и одна строка `operator_name.ignored`, реплика `<LastOrder02> …` идёт с `from: LastOrder02`.
- R1-2 · minor · отчёт, «Отклонения» · Пустая переменная без предупреждения — принято: пустое значение по умолчанию, предупреждение на каждом старте было бы шумом, а опасный случай (имя слота) логируется. Менять не нужно.
- R1-3 · minor · `agent/last_order/SESSION_PROMPT.md:4` · «his lines» — пол оператора в инструкцию не зашиваем. · «the operator's lines come marked `from: operator`».

### Чек-лист проекта
Сырые id: `speaker` и текст проходят `scrub`, служебные строки отсекаются до разбора — без замечаний. Путь смерти и аварийное завещание не тронуты. Единственный писатель базы и уровни LLM не затронуты. Замечание только R1-1: метка источника — граница доверия, ошибка в ней приписывает реплику не тому говорящему.

## Раунд 2 · 2026-10-31 · вердикт: changes · headless
Проверено: `git diff main...z/T-051-operator-in-game-chat` @ f3773ed

### Проверки (прогнаны надзором)
- `bash dev/check.sh` — pytest: ok · 834 passed, 3 skipped; check.py: ok
- `uv run pytest tests/test_executor.py tests/test_mcp_body.py tests/test_perception_chat.py tests/test_perception.py -q` — 111 passed
- Ручная проба `Rig().x.chat_entry(...)`:
  - без `OPERATOR_MC_NAME`: `<operator> stop mining, go home` → `{'from': 'operator', …}`;
  - `operator_name='ZeusThunderer'`: `<operator> x` → `{'from': 'operator', …}`.

### Критерии приёмки
- [ ] Метку ставит среда, текст её не подделывает — текст не подделывает, но подделывает ник, см. R2-1.
- [ ] Пусто → меток нет — нарушается через ник `operator` (R2-1). Имя слота — закрыто (R1-1 fixed: `_SLOT_NAME_RE`, регистр, тест `test_operator_name_of_any_slot_is_refused`).
- [x] Накопление до потолка, пометка о сброшенных.
- [x] `observe` — та же метка через `chat_entry`.
- [x] Детектор смерти и фильтр сырых id — путь не тронут, тесты зелёные.
- [x] Инструкции — R1-3 fixed.
- [x] `docker-topology.md`.
- [x] `bash dev/check.sh` — ok.

### Находки
- R2-1 · major · `runtime/mind/executor.py:444` · Метка `operator` и имя игрока делят одно поле `from`. Игрок с ником `operator` (валидное имя Minecraft) получает `from: operator` при пустой переменной и при любом другом `OPERATOR_MC_NAME`. Ник `Operator` даёт `from: Operator`, и сестра отличает его от оператора только по регистру. Офлайн-режим и так позволяет войти под именем оператора, но этот путь ломает гарантию «пусто → меток нет» и обходит имя, выбранное оператором. · Метка не должна совпадать ни с одним возможным именем игрока. Варианты: отдельное поле (`{"from": "ZeusThunderer", "operator": true, …}`) или `from`, который не может быть ником: `operator` только для оператора, а ник, равный `operator` без учёта регистра, оборачивается (например, `player:operator`). Выбор за исполнителем. Формат обновить в отчёте, docstring `wait_events`, AGENTS.md/SESSION_PROMPT и `docker-topology.md`. Тесты: без переменной `<operator> hi` и `<OPERATOR> hi` — без метки оператора; с `OPERATOR_MC_NAME=ZeusThunderer` — то же.

### Чек-лист проекта
Сырые id: имя и текст проходят `scrub`, служебные строки отсекаются до разбора — без замечаний. Путь смерти и аварийное завещание не тронуты (`_clean_text` и `recent_chat` для детектора прежние). Единственный писатель базы и уровни LLM не затронуты. Граница доверия метки — R2-1.

## Раунд 3 · 2026-10-31 · вердикт: accept · headless
Проверено: `git diff main...z/T-051-operator-in-game-chat` @ 3f2fb0b (весь diff построчно, раунд 3 — `9ce1657..4ba6941`)

### Проверки (прогнаны надзором)
- `bash dev/check.sh` — pytest: ok · 835 passed, 3 skipped; check.py: ok
- `uv run pytest tests/test_executor.py tests/test_mcp_body.py tests/test_perception_chat.py tests/test_perception.py -q` — 112 passed

### Критерии приёмки
- [x] Метка ставится средой по серверному префиксу, отдельным полем `operator: true`; ни текст, ни ник её не подделывают — `test_operator_lines_are_marked_by_the_speakers_name`, `test_a_player_named_operator_is_not_the_operator` (R2-1 fixed). Формат — в отчёте, раунд 3.
- [x] Пусто или имя любого слота → меток нет; имя слота — одна строка `operator_name.ignored` (пустое без предупреждения — принято в R1-2).
- [x] Накопление до потолка 20, пометка «… N earlier lines dropped».
- [x] `observe` — та же запись через `chat_entry`.
- [x] Детектор смерти и фильтр сырых id — путь не тронут, тесты зелёные.
- [x] Инструкции — AGENTS.md, SESSION_PROMPT обновлены под `operator: true`.
- [x] `docker-topology.md` — обновлён, пометки стоят.
- [x] `bash dev/check.sh` — ok.

### Находки
- R3-1 · minor · `docker/compose.yml:62` · Комментарий к `OPERATOR_MC_NAME` ещё говорит «marks … `from: operator`» — формат раунда 1. · Поправить на `operator: true` попутно в T-052 или следующей правке compose; на поведение не влияет.

### Чек-лист проекта
Сырые id: имя и текст проходят `scrub`, служебные строки отсекаются до разбора. Путь смерти и аварийное завещание не тронуты. Единственный писатель базы и уровни LLM не затронуты. Граница доверия метки закрыта: метка в своём поле, совпадение имени без учёта регистра, имена слотов отказаны. Изменения в пределах «Можно трогать».
