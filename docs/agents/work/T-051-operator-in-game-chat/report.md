# T-051. Отчёт исполнителя

## Раунд 1 · 2026-10-31
Коммиты: <рабочий коммит>

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
