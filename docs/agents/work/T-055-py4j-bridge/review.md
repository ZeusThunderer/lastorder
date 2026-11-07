# T-055. Ревью надзора

## Раунд 1 · 2026-11-07 · вердикт: changes · headless
Проверено: `git diff main...z/T-055-py4j-bridge` @ a23438b

### Проверки (прогнаны надзором)
- `bash dev/check.sh` — pytest ok · 930 passed, 3 skipped; check.py ok
- `bash dev/slot_restart.sh 1 --dry-run` — печатает stop mind-1 → up -d --force-recreate --no-deps body-1 →
  wait_for 420 --healthy → up -d --no-deps mind-1
- Реальный прогон на стенде надзор не повторял (отчёт: одноразовый стенд `lo-t055`, выдержка лога есть).

### Критерии приёмки
- [x] Разбор (c) — `test_teardown_survives_a_half_started_callback_server`, `test_factory_failure_lands_in_the_reconnect_path`.
- [ ] SIGTERM → `close()` → выход — порядок есть и протестирован, но см. R1-1: путь «сервер упал сам» сломал рестарт.
- [x] Готовность тела — healthcheck по 25333, обоснование «игрок в мире недоступен без py4j» с файл:строка; `service_healthy`.
- [x] `dev/slot_restart.sh` — dry-run прогнан, реальный прогон в отчёте.
- [x] Обновления uc — в отчёте, со ссылкой на источник.
- [x] `bash dev/check.sh` зелёный.

### Находки
- R1-1 · major · `runtime/mind/mcp_body.py:742-753` · uvicorn теперь работает в потоке. При сбое старта
  (порт 8700 занят, сбой lifespan) `Server.startup` вызывает `sys.exit(STARTUP_FAILURE)`
  (`uvicorn/server.py:118,192`). В потоке `SystemExit` проглатывается молча: `thread.is_alive()` становится
  False, срабатывает `teardown_mind`, `main()` возвращается, и процесс выходит с кодом 0. Раньше
  `uvicorn.run` в главном потоке выходил с 1, и `restart: on-failure` поднимал mind. Теперь слот тихо
  остаётся без ума. · Ожидается: если поток сервера завершился без `stop.is_set()`, после разбора выходить
  с ненулевым кодом (например, `sys.exit(1)` в `serve_mind`), и тест на это.
- R1-2 · minor · `runtime/game/py4j_body.py:153-157` · комментарий «ours is quiet and uc is untouched»
  неверен. При гонке на bind `JavaGateway.start_callback_server` сам вызывает `self.shutdown()`
  (`py4j/java_gateway.py:1955-1957`), а это SHUTDOWN шлюзу uc. Окно узкое, но в итоге возникает та самая
  смерть моста, от которой задача защищает. · Ожидается: стартовать сервер в обход обёртки
  (`gw._callback_server = gw._create_callback_server(gw.callback_server_parameters)`; `.start()` в
  try; при отказе — `server_socket.close()` и `gw.close()`) или хотя бы исправить комментарий и описать
  остаточный риск в `unionclef-bridge.md`.
- R1-3 · minor · `dev/slot_restart.sh:85` · dry-run печатает `$\(docker compose ps -q body-1\)`: внутри
  двойных кавычек `\(` не экранирование, обратные косые черты выводятся как есть. · Ожидается:
  `\$(docker compose ps -q body-$n)`.

### Чек-лист проекта
Без замечаний: сырых id нет, путь смерти не трогается (отказ скрипта при живом node-N учитывает
заметку T-054), база и уровни LLM не затронуты, `network_mode` не менялся.

## Раунд 2 · 2026-11-07 · вердикт: accept · headless
Проверено: `git diff main...z/T-055-py4j-bridge` @ 0186bee

### Проверки (прогнаны надзором)
- `bash dev/check.sh` — pytest ok · 931 passed, 3 skipped; check.py ok
- `bash dev/slot_restart.sh 1 --dry-run` — порядок прежний, строка ожидания без слэшей.

### Ответы на находки
- R1-1 · закрыта: `serve_mind` поднимает `SystemExit(1)`, если поток сервера закончился без сигнала;
  разбор идёт до выхода; тест `test_serve_mind_exits_nonzero_when_the_server_dies_unsignal`.
- R1-2 · закрыта: комментарий исправлен, риск принят явно. Строку об остаточном риске в
  `unionclef-bridge.md` дописал надзор (документ, не прямая правка кода).
- R1-3 · закрыта.

### Критерии приёмки
- [x] Все пункты; SIGTERM-путь и путь «сервер упал сам» теперь дают верный код выхода.

### Находки
- нет
