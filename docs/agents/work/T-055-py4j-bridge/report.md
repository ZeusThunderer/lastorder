# T-055. Отчёт исполнителя

## Раунд 1 · 2026-11-07
Коммиты: ad6ff9d, 8980634, f610ec7

### Сделано
- **Разбор (c)** (`runtime/game/py4j_body.py`): фабрика шлюза поднимает callback-сервер
  сама (`start_callback_server=False` + явный старт) и перед py4j пробует bind callback-порта
  (25334) — путь отказа py4j 0.10.9.9 зовёт `gateway.shutdown()` (гасит шлюз uc, ловушка E1) и
  падает на `thread=None` (NoneType-join, T-052 c). `_ensure` заводит не-сетевые ошибки
  фабрики в Py4JNetworkError → штатный реконнект с backoff. `_drop` после `close()` /
  `shutdown_callback_server()` дожимает сокет callback-сервера напрямую — тихо и идемпотентно.
- **Проба порта с SO_REUSEADDR** (`_callback_port_free`): остатки TIME_WAIT убитого шлюза
  (uc дозванивается на InitPythonCallback, python закрывает первым) без SO_REUSEADDR читались
  как живой скваттер — реконнект-цикл падал насмерть на минуту (найдено живым прогоном).
- **SIGTERM** (`runtime/mind/mcp_body.py`): uvicorn в потоке, свои обработчики SIGTERM/SIGINT
  (`stop_handler`), `teardown_mind` — сервер → C1 → `body.close()`; compose `stop_grace_period:
  20s` против ~11 с разбора. Живьём: `mind stopped: server down, C1 stopped, bridge closed`
  (docker stop -t 25).
- **Готовность тела**: healthcheck body-N — bash `/dev/tcp` на 25333 (шлюз uc слушает с
  титульного экрана: `AltoClef.java` `onInitializeLoad → initializePythonSender`); mind-N —
  `depends_on: service_healthy` (подключение во время загрузки ломало мост, T-052 b).
  «Игрок в мире» из контейнера без py4j не виден — состояние в `MinecraftClient`
  (`AltoClef.java:324-326`, `inGame()`), вход видит опрос самого ума. Живьём: compose напечатал
  `body-1 Healthy` перед стартом mind; тёплый recreate → healthy за 23 с.
- **`dev/slot_restart.sh N|all`**: mind стоп (SIGTERM) → body `up -d --force-recreate --no-deps`
  (restart изношенного тела не переподключает, T-053) → ожидание healthcheck через
  `compose ps -q` (имя контейнера с префиксом проекта) → mind старт с `--no-deps`. Живой node-N
  без `--force` — отказ (перезапуск ума среди жизни теряет life clock и створ T-054, заметка
  надзора). `--dry-run`, `--project` для параллельного стенда.
- **Upstream uc**: после v0.95.66 (26.10.2026) исправлений жизненного цикла шлюза нет —
  релизов новых нет, в main только `3ece419` (chunk-пакеты) и репорт [источник:
  github.com/3ndetz/unionclef, releases и main, проверено 2026-11-07]. Решения не принимал.

### Проверки
```
$ bash dev/check.sh
pytest: ok · 930 passed, 3 skipped, 2 warnings in 264.53s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ bash dev/slot_restart.sh 1 --dry-run
(печатает: stop mind-1 → up -d --force-recreate --no-deps body-1 →
 wait_for 420 --healthy $(docker compose ps -q body-1) → up -d --no-deps mind-1)
```
Реальный прогон — одноразовый стенд `lo-t055` (свой, порт 25599, после — `down -v`; стенды
`lastorder` и `t048` не тронуты): `wait_for: ok after 23s / healthy` → `slot_restart: done`,
после рестарта `/runner/status` → `{"alive":true,...}`, 0 переподключений за 20 с. Подъём
моста (docker logs mind-1): `13:23:15,143 Starting → ,144 Shutting Down (гонка listen)
→ ,645 Starting → 13:23:16,646 Starting / Socket listening on ('127.0.0.1', 25334)` →
тишина = мост стоит. Два бага скрипта нашлись живым прогоном и чинились (ps -q; --no-deps).
Отказ при живом node-N живьём не проверен (node на стенде не поднимался).

### Отклонения от задачи
- Оператор на вопрос о стенде не ответил: прогон сделан на своём одноразовом стенде, а не на
  поднятом чужом; сборка освежила общий тег `lastorder-mind:dev` (рабочие контейнеры main-стенда
  не пересоздавались).

### Вне задачи
- нет

### Вопросы
- нет

## Раунд 2 · 2026-11-07
Коммиты: 039d0f5 (merge main), 2efb8c2

### Ответ на ревью
- R1-1 · fixed 2efb8c2 — `serve_mind` поднимает `SystemExit(1)`, когда поток сервера закончился
  без стоп-сигнала; тест `test_serve_mind_exits_nonzero_when_the_server_dies_unsignal`
  (stub-сервер через `make_server`, обработчики сигналов восстанавливаются).
- R1-2 · fixed 2efb8c2 — комментарий переписан честно: в микросекундном окне между пробой и
  bind py4j сам зовёт `shutdown()` и может погасить шлюз uc; обход обёртки = приватное API
  py4j, остаточный риск принят явно.
- R1-3 · fixed 2efb8c2 — dry-run печатает `$(docker compose ps -q body-1)` без слэшей.

### Проверки
```
$ bash dev/check.sh
pytest: ok · 931 passed, 3 skipped, 2 warnings in 121.62s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ bash dev/slot_restart.sh 1 --dry-run | sed -n 6p
bash dev/wait_for.sh 420 --healthy $(docker compose ps -q body-1)
```

### Вопросы
- нет
