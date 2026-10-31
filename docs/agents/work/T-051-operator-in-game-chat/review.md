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
