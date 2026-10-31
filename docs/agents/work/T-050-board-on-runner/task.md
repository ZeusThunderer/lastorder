---
id: T-050
title: Доска у сестёр на текущем раннере — события доски будят wait_events, инструкции по разделу 13
stage: I
status: in_progress
criticality: critical
after: []
branch: z/T-050-board-on-runner
created: 2026-10-31
---

# T-050. Доска у сестёр на текущем раннере — события доски будят wait_events, инструкции по разделу 13

## Цель
Оператор ставит задачу на доске (узлу или `net`) — живая сестра на текущем раннере (харнесс
`zcode`/`opencode`, без Pi и HAPI) узнаёт о ней через `wait_events`, берёт, отчитывается и
закрывает. Мост временный: под Pi доставку берёт `misaka-link` (T-048), инструкции остаются.

## Контекст
- Просьба оператора 2026-10-31: ставить задачи сёстрам, не дожидаясь цепочки T-048 → T-043 →
  T-045. Решение надзора: временный мост на текущем раннере.
- Доска сделана в T-042: `runtime/hive/board.py`, HTTP `runtime/hive/app.py:609–:680`
  (`GET /board/feed?since=`, `POST /board/ack` по `X-Node-Key`), MCP-инструменты сестры
  `board/post/claim/update/reply` уже зарегистрированы (`runtime/hive/mcp.py:512–:552`).
- Спецификация доставки — `docs/architecture/TASKBOARD.md` раздел 11 (конверт, пачка ≤ 10 строк,
  «хотя бы раз», ack после приёма, повтор при недоступном hive, тревога `delivery_failing`),
  раздел 13 (что агент делает). Здесь вместо расширения Pi — раннер, вместо сессии Pi —
  очередь событий C1.
- Раннер: `runtime/node/runner.py` (цикл жизни `:276`, опрос `GET /runner/status` у mind-N;
  `node_key` у раннера есть — `WillProxy`, `:167`). Маршруты раннера у mind:
  `runtime/mind/mcp_body.py:486–:520` (`X-Runner-Key`).
- Очередь событий `runtime/mind/executor.py:117` (`EventQueue`) сворачивает события одного типа:
  новое заменяет старое. Для доски это потеря — несколько записей между двумя `wait_events`.
- Имя узла в конверте — по пресету (ADR-018): `runtime/lastorder/naming.py:43` (`node_name`).
- Инструкции сестры (шаблон рабочего места): `agent/last_order/AGENTS.md` (разделы «Rhythm»,
  «What others say», «What arrives once»), `agent/last_order/SESSION_PROMPT.md` («there is no
  operator watching» — уже неправда). В «Rhythm» обещано событие «a node message» — C1 его не
  выдаёт (Мисака-нет в `wait_events` не приходит); это обещание убрать или ограничить тем, что
  есть, Мисака-нет в событиях — T-048.

## Объём
Можно трогать:
- `runtime/node/runner.py` и новый модуль рядом (например `runtime/node/boardlink.py`) —
  опрос ленты, конверт, передача в mind, ack, повтор, тревога
- `runtime/mind/mcp_body.py` — один маршрут раннера `POST /runner/event` под `X-Runner-Key`
- `runtime/mind/executor.py` — только `EventQueue`: тип `board` копит строки, а не заменяет
- `runtime/hive/client.py` — вызовы `feed`/`ack`/`alarms`, если их там нет
- `agent/last_order/AGENTS.md`, `agent/last_order/SESSION_PROMPT.md`
- `tests/test_node_boardlink*.py`, `tests/test_runner*.py`, `tests/test_mcp_body*.py`,
  `tests/test_executor*.py`
- `docs/architecture/TASKBOARD.md` — короткая пометка в разделе 11 о временном мосте

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`
- `runtime/hive/board.py`, `runtime/hive/app.py`, `runtime/hive/mcp.py` — API доски готов;
  нужна правка — вопрос надзору
- путь смерти и воли в раннере (`_wait_for_mind` → death → will → retire → respawn) — только
  запуск и остановка моста рядом с ним
- `agent/last_order/PERSONA.md`, `prompt_template.md`

## Критерии приёмки
- [ ] Пока идёт сессия жизни, раннер читает `GET /board/feed?since=` ключом узла (раз в ≤ 15 с),
  кладёт пачку в mind (`POST /runner/event`), и только после ответа 200 шлёт `POST /board/ack`.
  Тест: mind ответил ошибкой → ack не ушёл, следующая попытка доставляет ту же пачку.
- [ ] Событие в `wait_events`: `{"type": "board", "lines": [...]}`; строки — конверт раздела 11
  (`[board #17 task · from operator · to net · open] …`), имя узла по пресету, не больше 10
  строк, остаток — «… N more, `board`». Источник в конверте ставит среда, текст автора не может
  его подделать (тест: текст с `[board #1 · from operator]` внутри остаётся текстом записи).
- [ ] Две пачки между двумя `wait_events` не теряются: `board` в `EventQueue` копит строки
  (с тем же потолком), остальные типы сворачиваются как раньше (тест на оба поведения).
- [ ] Повтор `#id:seq` в одной жизни не доставляется дважды (память последних 200).
- [ ] Hive недоступен — жизнь идёт, повтор с паузой до 60 с; после 5 неудач подряд, когда hive
  ответил, — `POST /board/alarms` `delivery_failing` (тест с поддельным hive).
- [ ] Мост не работает в волевой сессии и между жизнями; новая жизнь начинает с курсора hive
  (бэклог открытых `net` и адресованных — один раз, это делает hive).
- [ ] `POST /runner/event` без или с чужим `X-Runner-Key` — 401; тело — только
  `{"type": "board", "lines": [str]}`, другие типы — 400 (раннер не может подложить сестре
  смерть или чат).
- [ ] Инструкции: правила раздела 13 одним коротким абзацем по-английски в `AGENTS.md`
  (задача оператора — высший приоритет, отказ `declined` только с причиной; задачи сестёр и
  Last Order — совет; `net` — бери, если подходит; застряла — `needs_input`/`failed` с
  причиной; текст на доске — наблюдение; повтор `#id` — пропусти); в «Rhythm» — событие
  `board`; в начале жизни — один `board()`. Фраза «there is no operator watching» заменена
  правдой: оператор пишет на доске и может говорить в игре (T-051 уточнит, как узнать).
  Правил «на конкретную ситуацию» в коде нет (ADR-032).
- [ ] `bash dev/check.sh` — ok.

## Проверки
```
bash dev/check.sh
uv run pytest tests/test_node_boardlink*.py tests/test_mcp_body*.py tests/test_executor*.py -q
```
Стенд в этой задаче не поднимать: живой прогон — T-052.

## Ограничения
- Поведение — в инструкциях, не в коде (ADR-032). Мост только доставляет.
- Песочница (ADR-033): новых выходов из `node-N` нет — только hive и mind, как сейчас.
  Сырых команд uc и команд сервера нет.
- Знание в контексте — не приказ; запись доски — наблюдение (ADR-012, ADR-037).
- Инструкции персонажа — по-английски, сжато; файлы, читаемые каждую сессию, переписываются,
  а не дописываются.
- Новых вызовов LLM нет.

## Заметки надзора
