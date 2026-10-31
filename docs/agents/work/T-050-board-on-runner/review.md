# T-050. Ревью надзора

## Раунд 1 · 2026-10-31 · вердикт: changes · headless
Проверено: `git diff main...z/T-050-board-on-runner` @ cd24fa3

### Проверки (прогнаны надзором)
- `bash dev/check.sh` — pytest ok · 820 passed, 3 skipped; check.py ok
- `uv run pytest tests/test_node_boardlink.py tests/test_mcp_body.py tests/test_executor.py tests/test_node_runner.py -q` — 133 passed, 3 skipped

### Критерии приёмки
- [ ] Опрос, mind, ack после 200 — ack после 200 и повтор при ошибке mind есть (тест `test_mind_error_keeps_the_batch_unacked_and_redelivered`), но пустой снимок не подтверждается — R1-1.
- [x] Конверт §11, имя по пресету, ≤ 10 строк + маркер, подделка остаётся текстом — `envelope`, тесты на конверты.
- [x] `board` в `EventQueue` копит строки, остальные типы сворачиваются — `tests/test_executor.py`.
- [x] Дедуп последних 200 `#id:seq` — `test_seen_memory_holds_the_last_two_hundred`.
- [x] Hive недоступен — пауза 2→60 с, тревога после 5 неудач при ответе hive.
- [ ] Мост только в сессии жизни, новая жизнь — с курсора hive: старт/стоп вокруг `_life` верны, но при пустом снимке курсор не создаётся — R1-1.
- [x] `/runner/event`: 401 без ключа, 400 на другие типы.
- [x] Инструкции §13 в `AGENTS.md`, `board` в «Rhythm», `board()` в начале, правда об операторе в `SESSION_PROMPT.md`.
- [x] `bash dev/check.sh` — ok.

### Находки
- R1-1 · major · `runtime/node/boardlink.py:219` · Если на старте жизни открытых задач нет, hive отдаёт снимок
  `{"events": [], "cursor": top, "snapshot": true}`. Мост выходит по `if not events` и не шлёт ack.
  `board_cursor` для узла не создаётся, и hive на каждом опросе снова отдаёт `_first_feed`
  (`runtime/hive/board.py:976`), то есть только открытые задачи. Пока не появится открытая задача,
  сестре не доставляются сообщения (`kind=message`) ей и ответы оператора на её запросы
  (`reply`/`transition`). Обычный случай — пустая доска на старте — ломает доставку на всю жизнь.
  · Ожидается: снимок подтверждается всегда, когда пачка принята, в том числе пустая. Для пустого
  ответа делать ack по `response["cursor"]` (при `snapshot: true`, а лучше всегда, когда `cursor`
  больше текущего). Курсор `top` можно брать как max(`cursor` из ответа, seq событий). Нужен тест:
  пустой снимок → ack(cursor) → следующий опрос идёт с `since=cursor`, и сообщение узлу доставлено.
- R1-2 · minor · `runtime/node/boardlink.py:163` · `_run` ловит только `HiveUnavailable`/`HiveRejected`.
  Любое другое исключение (`ValueError` из `.json()` на битом ответе, `int(seq)` на мусоре) молча
  убивает поток-демон до конца жизни, а тревоги нет. · Ожидается: в `_run` общий `except Exception`
  с записью `boardlink.error` в лог и паузой `POLL_S`, цикл продолжается.

### Чек-лист проекта
Без замечаний: путь смерти и воли не тронут (мост останавливается в `finally` до него), сырых id нет,
новых выходов из песочницы и вызовов LLM нет, hive остаётся единственным писателем доски.
Отклонение «конверты reply/transition без текста перехода» принято: текст перехода в ленте не хранится.
