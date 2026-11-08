# T-057. Отчёт исполнителя

## Раунд 1 · 2026-11-08
Коммиты: 5f9149b (take), 806abc3

### Сделано
- `runtime/hive/infra.py` (новый): закрытый набор из семи действий (`slot_up`, `slot_down`,
  `slot_restart`, `slot_server`, `mc_restart`, `status`, `logs`) и валидация аргументов
  (слот 1–3, адрес `host[:port]` без пробелов/спецсимволов, сервис — имя compose-сервиса,
  `lines` 1..500). Один авторитет для доски и исполнителя. Чистый stdlib — исполнитель на
  хосте импортирует его без зависимостей.
- `runtime/hive/board.py`: infra-запрос создаёт только Last Order (403 остальным), без
  `allowed`-ответов и без `grant` (422); обычные переходы запроса для него закрыты —
  остаются `open→canceled` (автор) и ходы исполнителя `infra_take`/`infra_result`
  (атомарный `UPDATE … WHERE status='open'`; итог `done`/`failed`, вывод обрезается до
  800, а не отказывается — действие уже выполнено). Очередь `infra_pending(open|taken)`,
  таблица `infra_switch` (переживает рестарт hive, по умолчанию включён).
- `runtime/hive/app.py`: `GET /board/infra/pending|taken`, `POST /board/infra/{id}/take|result`,
  `GET|POST /board/infra/switch` под `X-Infra-Key` (HIVE_INFRA_KEY); чтение переключателя —
  исполнитель или оператор, запись — только оператор. Fail-closed: ключ, совпавший с любым
  другим ключом стенда, — отказ запуска.
- `runtime/hive/mcp.py`: инструмент `infra_state` для Last Order — on/off, причина
  оператора, очередь.
- `dev/infra_bridge.py`: исполнитель на хосте. Опрос переключателя и очереди, повторная
  валидация закрытого набора, argv-списки (никакого shell): `docker compose …` и
  `dev/slot_restart.sh`; `slot_server` — override `MC_SERVER_N` на одно пересоздание
  (`.env` не правится, документировано); ожидание healthcheck через `dev/wait_for.sh`;
  итог на доску с хвостом вывода. Перезапуск закрывает взятое до падения как `failed`
  (`close_leftovers`), ничего не перезапуская. `--dry-run`, `--once`, `--interval`,
  `--project`. Windows: пути posix + выбор Git-bash (WSL-заглушка не открывает `C:/…`,
  поймано живьём).
- `docker/compose.yml`, `docker/node/up.sh`, `.env.example`: `HIVE_INFRA_KEY` поднимается
  только в hive; `MC_SERVER_N` теперь у всех трёх слотов.
- `runtime/hive/static/`: карточки infra-запросов идут через существующий рендер
  (`allowed=[]` — кнопок ответа нет), итог виден в треде; в шапке — переключатель
  исполнителя с причиной выключения (только `textContent`).
- Доки: TASKBOARD.md 5.4 + строки API + страница; docker-topology.md раздел «Исполнитель
  инфраструктуры»; навык `agent/network/skills/infra/SKILL.md` (why/что сломается перед
  действием, не снимать слот с живой сестрой, `status`/`logs` перед ходом, причина
  выключенного исполнителя) + строка в `agent/network/AGENTS.md`.

### Проверки
```
$ bash dev/check.sh
pytest: ok · 981 passed, 3 skipped, 2 warnings in 118.05s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ uv run pytest tests/test_hive_infra.py -q
42 passed
$ docker compose -f docker/compose.yml config | grep -c HIVE_INFRA_KEY   # ключ задан в env
1   # единственное вхождение — в environment сервиса hive (строка 187 конфига)
$ uv run python dev/infra_bridge.py --dry-run   # на живом стенде, запрос #36 на доске
[00:55:11] infra bridge: http://127.0.0.1:18080 · interval 15.0s · dry run
[00:55:11] #36 slot_restart {'slot': '2'}: dry run, nothing executed
[00:55:11]   $ bash C:\…\.worktrees\T-057-infra-bridge\dev\slot_restart.sh 2
```
Живой прогон (главный стенд `lastorder`, блокировка T-057, t048 не трогал; node-2 был
выключен — живых сессий сестёр нет). Hive пересоздан из worktree с разовым
`HIVE_INFRA_KEY` в окружении (`.env` не правился). Запрос отправлен ключом Last Order
через `/last-order/mcp`, инструмент `post`:
```
доска #40 request last-order->operator done claimed=infra
request: {"action": "slot_restart", "args": {"slot": "2"}, "why": "…", "allowed": []}
thread: infra {'taken'→'done'}: …body-2 Recreated → Started; (compose ps: healthy)
$ uv run python dev/infra_bridge.py --once
[00:58:12] #40 slot_restart {'slot': '2'}: running
[00:58:57] #40: done          # ~45 с: mind stop → body recreate → health → mind up
docker ps: lastorder-body-2-1 Up 36 seconds (healthy); lastorder-mind-2-1 Up
```
Переключатель живьём: оператор выключил с причиной → мост печатает
`executor off: live check T-057: pause for a moment — requests wait`, ничего не берёт;
`infra_state` от ключа Last Order: `{"on":false,"reason":"…","pending":["#41 status {} (open)"]}`;
после включения тот же прогон исполнил #41 (`done`). Дважды один запрос не исполняется —
тестами (второй take/result — 409) и устройством (очередь только `open`).
Предыдущие попытки #36/#38 — `failed` с выводом (две ошибки хоста: backslash-путь и
WSL-bash), починены в 806abc3 — это и есть живая проверка пути «отказ с выводом».

### Отклонения от задачи
- Новый модуль `runtime/hive/infra.py` (в «Можно трогать» перечислены board/app/mcp):
  валидация нужна и доске, и исполнителю одним авторитетом, в board.py она была бы
  публичной мигренёй. Зона та же — критичная `runtime/hive/`.
- `dev/stand_keys.py` не тронут (вне «Можно трогать»): `init` не генерирует
  `HIVE_INFRA_KEY`, ключ ставится вручную (документировано в .env.example и
  docker-topology). В `tests/test_stand_keys.py` ключ выведен в исключения с комментарием.
- Реальный прогон сделан на главном стенде без ответа оператора на вопрос (спрошено
  письменно, вариант «рекомендую»): минимальный радиус — пересоздан только hive,
  slot 2 без живой сестры, t048 не тронут. `.env` не правился: ключ передан в окружении
  одного пересоздания hive.

### Вне задачи
- `lastorder-egress-1` на главном стенде крутится в Restarting-цикле (старше задачи,
  не трогал).
- Работающий hive главного стенда теперь несёт разовый `HIVE_INFRA_KEY` из окружения:
  полный `up` из `.env` его потеряет (infra-маршруты начнут отвечать 401 до ручной
  настройки). Для постоянства оператору добавить строку в `.env` (рецепт в .env.example).
- `dev/stand_keys.py init` мог бы генерировать `HIVE_INFRA_KEY` как остальные локальные
  токены — задача надзору, если нужно.
- Итог `slot_server` живёт до следующего пересоздания body-N из `.env` (живой override);
  задокументировано в навыке и docker-topology.

### Вопросы
- нет
