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

## Раунд 2 · 2026-10-17
Коммиты: 8be8e80 (merge main: T-007/T-008/T-012/T-020), 30695a3

### Сделано
- Влил `main` в ветку (конфликтов нет). Правку T-020 в `runtime/game/py4j_body.py`
  принял как факт: тело уже на импорте `is_service_line` из `runtime/perception/chat.py`,
  дифф ветки по этому файлу — ровно замена локальной копии на импорт.
- R1-1: у `Perception` режим по умолчанию `None` → `runtime.config.perception_mode()`.
  Теперь `PERCEPTION_MODE` решает, пока тест не передал явный `mode`; неизвестное значение
  из env валится при создании `Perception` (fail-closed в критичной зоне). Тесты:
  `test_default_mode_comes_from_the_environment` (env=masked → masked, `see().render()`
  без «birch», `leaks == []`; env=plain → plain), `test_bad_env_mode_fails_at_perception_creation`.
- R1-2: локальный кортеж `MODES` в translate.py убран, список режимов один —
  `PERCEPTION_MODES` из `runtime/config.py` (тот же импорт, что закрывает R1-1; циклов нет,
  config зависит только от `os`/`pathlib`).
- R1-4: `clean_chat` приводит каждую строку к `str` один раз в начале итерации;
  тест `test_clean_chat_stringifies_each_line_once`.
- R1-3: не исправлял — ревью раунда 1 прямо: «в этой задаче не исправлять». Ограничение
  display→id (Block of Iron, Jack o'Lantern) задокументировано в докстринге `normalize`
  со ссылкой на T-002/T-011.

### Ответ на ревью (раунд 1)
- R1-1 · fixed 30695a3
- R1-2 · fixed 30695a3
- R1-3 · оставлено: по прямому указанию ревью раунда 1 («в этой задаче не исправлять»);
  ограничение документировано в докстринге `normalize` для T-002/T-011
- R1-4 · fixed 30695a3

### Проверки
```
$ uv run pytest
293 passed
$ uv run python tools/check.py
Every workplace is intact: files present, links resolve, boxes parse.
```
(`tools/check.py` под `uv run`: в Git Bash этого стенда голый `python` — заглушка Store.)

### Отклонения от задачи
- нет

### Вне задачи
- `tools/snapshot_replies.py:76` и `dev/llm_bench.py:264` строят `Perception(…)` без явного
  режима и после R1-1 честно следуют env-режиму (`PERCEPTION_MODE=masked` больше не
  проигрывается молча). Файлы вне «Можно трогать», не правил.

### Вопросы
- нет

### Самопроверка
- Раундов `lo-checker`: 1. Итог: CHECK: clean (прогон диспетчера после 30695a3).
- Находок в раунде самопроверки не было.

### Вызовы надзора
- нет (вердикт `changes` раунда 1 поставлен прошлой сессией до конвейера; строк для этого
  раунда диспетчер не передавал)
