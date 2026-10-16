# T-009. Отчёт исполнителя

## Раунд 1 · 2026-10-17
Коммиты: 063b5d4, 60f0ea2

### Сделано
- `runtime/perception/translate.py`: у `Perception` появился `mode=plain|masked`
  (по умолчанию `plain`, неизвестное значение — `ValueError`). В `plain` ключ вещи —
  `<kind>:<id>` (`blk:birch_log`), без соли, стабилен между эпохами и годится как `about`.
  `normalize` теперь сводит display-имена (`Birch Leaves`, `birch leaves`) и реестровые id
  (`minecraft:birch_leaves`) к одному ключу; `readable()` даёт человекочитаемое имя (`birch log`).
  `see()` в plain рендерит `blk:birch_log birch log`, с термином узла — `… birch log «термин»`;
  `raw()` работает через ту же карту в обоих режимах. `scrub`/`leaks` в plain запрещают только
  форму с `minecraft:` (namespace вырезается), в `masked` поведение прежнее.
- `runtime/config.py`: `perception_mode()` читает `PERCEPTION_MODE` — по умолчанию `plain`,
  неизвестное значение — `ValueError` при старте; регистр и внешние пробелы прощаются
  (`.env` правят руками).
- `runtime/perception/chat.py` (новый): `strip_formatting()` срезает `§x`; `is_service_line()`
  знает префиксы из исходников мода — `[Alto Clef]` (Settings.java:94, Debug.java:48,73,94),
  `[Tungsten]` (tungsten/…/Debug.java:16,48) и `WARNING:` без скобок у tungsten logWarning
  (там же, строка 71); `clean_chat()` отбрасывает служебные и пустые строки, речь игроков
  и ванильные системные оставляет.
- `runtime/game/py4j_body.py`: локальная копия `is_service_line` заменена на импорт из
  `runtime/perception/chat.py` (правка надзора учтена); `runtime.game.is_service_line`
  и его тесты продолжают работать, других правок в `runtime/game/` нет.
- Тесты: `tests/test_perception_plain.py` (ключи, see/raw, scrub/leaks, конфиг), 
  `tests/test_perception_chat.py` (образцы E1, оба режима), фикстура `tests/test_perception.py`
  теперь явно `mode="masked"` — все 8 проверок без изменений.

### Проверки
```
$ uv run pytest
95 passed
$ uv run python tools/check.py
Every workplace is intact: files present, links resolve, boxes parse.
```
Каждый из двух коммитов отдельно зелёный (проверено checkout'ом: 82 и 95 passed).
В Git Bash этого стенда `python` — заглушка Microsoft Store, `tools/check.py` запускался
как `uv run python tools/check.py`.

### Отклонения от задачи
- нет

### Вне задачи
- `tools/snapshot_replies.py` строит `Perception(SAMPLE, salt=…)` без режима и теперь
  по умолчанию получает plain: вид наблюдения в снапшотах станет «настоящими» именами
  (это и есть смысл ADR-020), `leaks`-проверка инструмента остаётся зелёной. Файл вне
  «Можно трогать», не правил.
- В tungsten logError стек-трейс multiline, строки продолжения не начинаются с маркера;
  в образцах E1 таких строк не было, оставил как есть.

### Вопросы
- нет

### Вызовы надзора
- нет
