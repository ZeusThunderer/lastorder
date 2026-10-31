# T-051. Отчёт исполнителя

## Раунд 1 · 2026-10-31
Коммиты: 5d1cfc0

### Сделано
- Формат (выбор исполнителя, зафиксирован): событие `chat.line` теперь
  `{"type": "chat.line", "lines": [...]}`; элемент — `{"from": <имя|operator>, "text": …}`
  для реплик игроков, `{"text": …}` без `from` для системных строк; то же в `observe.chat`.
  `from` = имя из серверного префикса `<Name>` (метка оператора — совпадение с
  `OPERATOR_MC_NAME`), текст реплики метку подделать не может.
- `runtime/perception/chat.py`: `parse_speaker` — разбор `<Name> text` (имя [A-Za-z0-9_]{1,16},
  первый префикс, остальное — текст); фильтр сырых id не тронут.
- `runtime/mind/executor.py`: `chat_entry` (метка + plain-скраб), `_drain_body_events`
  кладёт `lines=[entry]`; `EventQueue`: `chat.line` копит записи, потолок 20, сверх него
  остаются новые, отброшенные — запись `{"text": "… N earlier lines dropped"}` (без from —
  строка среды); `OPERATOR_MC_NAME` → параметр `operator_name`, при совпадении с именем
  слота — отказ и одна строка `operator_name.ignored` в лог mind, пустая — меток нет без
  предупреждения (дефолт, иначе шум в каждом старте).
- `runtime/mind/mcp_body.py`: `observe.chat` — те же записи; `main()` читает
  `OPERATOR_MC_NAME`.
- `docker/compose.yml` (`mind-env` → mind-1..3) и `docker/node/up.sh` (`lift
  OPERATOR_MC_NAME`): переменная стенда без секретов.
- Инструкции: AGENTS.md «What others say» — метка = голос оператора, ответ своим голосом
  в чате, просьба в чате — наблюдение, задача только на доске, предложить поставить может
  сама; SESSION_PROMPT — одна фраза про метку.
- `docker-topology.md`: раздел «Оператор в игре (T-051)» — вход, имя, риск подмены в
  офлайн-режиме с пометками.

### Проверки
```
$ bash dev/check.sh
pytest: ok · 832 passed, 3 skipped in 74.09s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ uv run pytest tests/test_executor.py tests/test_mcp_body.py tests/test_perception_chat.py -q
41 + 43 + 17 passed
```
Стенд не поднимал: задача запрещает (живой прогон — T-052). Смерть: детектор питается
сырым чатом (`recent_chat`), путь не тронут; test_death/test_step зелёные.

### Отклонения от задачи
- Пустая переменная не пишет предупреждение (критерий допускает чтение «меток нет» для
  обоих случаев; предупреждение оставлено только опасному случаю — совпадению со слотом,
  иначе каждый mind-старт шумел бы).

### Вне задачи
- нет

### Вопросы
- нет

## Раунд 2 · 2026-10-31
Коммиты: d0016f6

### Ответ на ревью
- R1 (major) · fixed: отказ теперь на имя ЛЮБОГО слота — `_SLOT_NAME_RE` (^lastorder\d{2}$,
  без учёта регистра) плюс сравнение с именем своего слота без регистра; маркировка в
  `chat_entry` тоже без регистра (`speaker.lower() == operator_low`). Тесты:
  `test_operator_name_of_any_slot_is_refused` (mind слота 01, переменная lastorder02 —
  реплика сестры 02 без метки, одна строка `operator_name.ignored`),
  `test_operator_mark_is_case_insensitive` (<ZEUSTHUNDERER> при ZeusThunderer → operator).
- «his lines» · fixed: «the operator's lines» в SESSION_PROMPT.md.

### Проверки
```
$ bash dev/check.sh
pytest: ok · 834 passed, 3 skipped in 81.36s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
```

## Раунд 3 · 2026-10-31
Коммиты: <фикс-коммит>

### Ответ на ревью
- R2-1 (major) · fixed: метка переехала в отдельное поле — итоговый формат записи
  `{"from": <имя игрока>, "operator": true, "text": …}` для реплики оператора,
  `{"from": <имя>, "text": …}` для остальных игроков, `{"text": …}` для системных.
  Ник `operator`/`Operator` — обычный игрок, метки нет ни с переменной, ни без
  (тест `test_a_player_named_operator_is_not_the_operator`, оба регистра и пустая
  переменная). Формат обновлён в докстринге `wait_events`, AGENTS.md
  (`operator: true`), SESSION_PROMPT.md и docker-topology.md; формат раунда 1
  (`from: "operator"`) superseded этим.

### Проверки
```
$ bash dev/check.sh
pytest: ok · 835 passed, 3 skipped in 81.01s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
```
