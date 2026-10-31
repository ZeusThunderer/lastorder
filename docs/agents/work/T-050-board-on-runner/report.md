
## Передача · 2026-10-31
- Сделано: ничего кроме взятия задачи (этот чат закрыл T-049 и T-046; T-050 взят из
  очереди раньше T-046 по ошибке порядка и ждал).
- Дальше: работать по task.md с нуля; `main` уже содержит T-049 (страница доски) и
  T-046 (гранты) — сделать `git merge main` перед стартом.
- Проверить: задачу не трогал, замечаний надзора о конфликтации нет.

## Раунд 1 · 2026-10-31
Коммиты: c5361b1 (merge main), 4aad203

### Сделано
- `runtime/node/boardlink.py` (новый): мост одной жизни — опрос `GET /board/feed`
  ключом узла раз в 10 с (≤ 15), конверт §11 (префикс из структурированных полей,
  текст автора схлопывается в одну строку — подделка конверта остаётся текстом, имя
  узла по пресету), пачка ≤ 10 строк + маркер «… N more, `board`», `POST /runner/event`
  в mind, ack только после 200, дедуп последних 200 `#id:seq`, hive недоступен — пауза
  2→60 с, после 5 неудач подряд тревога `delivery_failing` при первом ответе hive
  (ключом узла — единственным автором этого типа).
- `runtime/hive/client.py`: `BoardFeedClient` — feed/ack/alarm по X-Node-Key, одна
  попытка на вызов, таксономия ошибок как у остальных клиентов.
- `runtime/mind/mcp_body.py`: маршрут `POST /runner/event` под X-Runner-Key; тело
  строго `{"type": "board", "lines": [str]}`, иное — 400.
- `runtime/mind/executor.py` (только EventQueue): тип `board` копит строки с тем же
  потолком 10 + маркер, остальные типы сворачиваются как раньше.
- `runtime/node/runner.py`: мост стартует с началом `_life`, останавливается в
  `finally` — до пути смерти; нет моста в волевой сессии и между жизнями. Фабрика
  инъектируется, дефолт строит настоящий мост (нет node_key — выкл с логом
  `boardlink.off`). config.py не тронут: константы §11 в boardlink.py.
- Инструкции: AGENTS.md — инструменты доски + правила §13 одним абзацем, в «Rhythm»
  событие board (обещание «a node message» убрано, уточнён inbox), «What arrives
  once» про board-пачки; SESSION_PROMPT.md — правда об операторе вместо «there is no
  operator watching», в первом заходе один `board()`.
- TASKBOARD.md §11: пометка о временном мосте до misaka-link.

### Проверки
```
$ bash dev/check.sh
pytest: ok · 820 passed, 3 skipped in 87.17s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ uv run pytest tests/test_node_boardlink.py tests/test_mcp_body.py tests/test_executor.py tests/test_node_runner.py -q
92 passed, 3 skipped; 41 passed (mcp_body отдельно)
```
Стенд не поднимал: задача прямо запрещает (живой прогон — T-052).

### Отклонения от задачи
- Конверты reply/transition не несут текст перехода: feed-событие transition хранит
  только статус (текст лежит в треде, §11 пример 3 недостижим из ленты дословно).
  Форматы: `[board #N kind · reply by X] text`, `[board #N kind · status by X]`.

### Вне задачи
- В корне репозитория сабмодуль unionclef показывает изменённый IDE-файл
  (.settings/org.eclipse.buildship.core.prefs) — шум, корень чистый, не трогал.

### Вопросы
- нет

## Раунд 2 · 2026-10-31
Коммиты: <sha фикс-коммита ниже>

### Ответ на ревью
- R1-1 · fixed: пустой снимок подтверждается — `poll_once` при `snapshot: true` и
  пустых событиях шлёт ack по `response["cursor"]` и двигает свой курсор. Тест
  `test_an_empty_snapshot_is_acked_and_delivery_moves_on`: пустой снимок → ack(9) →
  следующий опрос с `since=9` → сообщение узлу доставлено.
- R1-2 · fixed: `_run` обёрнут catch-all — исключение пишет `boardlink.cycle_failed`
  и мост продолжает опрос. Тест `test_an_unexpected_error_does_not_end_the_thread`
  (первый feed() кидает ValueError, второй цикл проходит). Заодно здоровый темп
  вынесен в `poll_s` (шов для тестов, дефолт 10 с).

### Проверки
```
$ bash dev/check.sh
pytest: ok · 822 passed, 3 skipped in 80.19s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
```
