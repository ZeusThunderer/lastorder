
## 2026-11-11 · сделано напрямую (ADR-047)

### Сделано
- Ярусы compose (ADR-050): ядро без профиля, `unless-stopped` — hive, last-order, hapi-hub,
  llm-gateway, gateway-db, zai-egress, egress, media, infra; `slots` (node-N, mind-N), `bodies`
  (body-N), `world` (mc-server) — `restart: "no"`, поднимает запрос на доске. Логи json-file
  10 МБ × 3 у всех; healthcheck у ума и hive.
- Разум и тело — соседи по `link-N` (internal, 10.250.N.0/29: тело .2, разум .3). socat в теле:
  `10.250.N.2:25433` → uc `127.0.0.1:25333` (только адрес разума), `127.0.0.1:25334` →
  `10.250.N.3:25334`. Адреса py4j из env; `eager_load=False` у callback-сервера.
- Тело — забота разума: `runtime/mind/bodyctl.py` (body_up после 20 с без ответа, body_restart
  после 90 с тишины, ждёт загрузку 420 с, 429 → одна тревога `stuck`). Раннер просит
  `slot_restart`, если разум молчит 90 с.
- Доска: `POST /board/infra/slot` (ключ раннера, только свой слот, `body_*` и `slot_restart`,
  3 перезапуска за 10 мин); оператор пишет infra-запросы; кнопки ▲ ↻ ▼ слотов на странице.
- Исполнитель — контейнер `infra` (docker 29.9.2 CLI + compose v5.6.1, sha256), сокет, репо на
  чтение по пути демона, `.stand/` на запись, read_only, cap_drop ALL, `infra-net`; мир по
  требованию и остановка без тел; пробуждение `start` у Last Order.
- `up.sh`: `STAND_DAEMON_ROOT`, `.stand/servers.env`; без аргументов — ядро `up -d` и `create`
  остального.

### Найдено на стенде и исправлено
- Git Bash переписывал `STAND_DAEMON_ROOT=/run/desktop/...` в `C:/Program Files/Git/run/...`
  при передаче в docker.exe (`mkdir C:\Program Files\Git\run: Access is denied`) →
  `MSYS2_ENV_CONV_EXCL=STAND_DAEMON_ROOT` в `up.sh`.
- `status` отдавал `{.Service} {.State} {.Status}` на каждый контейнер: `run_steps` гнал аргументы
  через `str.format` → подстановка только `{name}` захватов. Last Order из-за этого приняла
  снятые слоты за петлю рождений и сделала `mc_restart`.
- socat: `range=…/32` у `TCP-LISTEN` — «unspecified address family» → `TCP4-LISTEN`.
- Имя `body-2` разрешалось в адрес тела на default-сети, relay отказывал (`refusing connection
  from 172.25.0.5`); обратный вызов шёл бы так же мимо → фиксированные адреса link-N с обеих
  сторон, relay слушает только адрес на link.

### Проверки
```
pytest: 1108 passed, 3 skipped; tools/check.py — intact
compose --profile '*' config — ок
стенд: down → up.sh: работают только 9 сервисов ядра (unless-stopped), infra healthy;
  слоты/тела/мир created, restart no
  пробуждение start → Last Order: status (#122), logs (#123), mc_restart (#124)
  оператор slot_up 2 (#125): mind-2 healthy → node-2; mind-2 сам body_up (#126, runner:LastOrder02)
  после фиксов: body_restart 2 (#129), slot_restart 2 (#130) → мост по link-2: callbacks uc,
  MCP сестры; смерть 00225 по сроку → воля origin agent → retire → архив → respawn → рождение 00226
  из node-2: 10.250.2.2:25433 — Network is unreachable, body-2 не резолвится, сокета нет
```

### Не проверено
- Автостарт ядра после перезагрузки хоста (Docker не перезапускали: обучение в WSL).
- `slot_down` и остановка мира без тел на живом стенде — только тесты (снимать живую сестру
  без воли не стали).
- Убийство моста uc внутри тела → `body_restart` разумом и потолок 3/10 мин — только тесты.
- Смена тела на тело другой игры — адаптеров, кроме `minecraft`, нет.
