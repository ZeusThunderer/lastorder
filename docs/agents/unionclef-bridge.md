# unionclef (uc): тело в Minecraft

Fabric-мод: altoclef (`src/main/java/adris/altoclef`) + tungsten (pathfinder, `tungsten/…`).
`baritone/` и `shredder/` не компилируются, лежат как справочник. LLM внутри мода нет:
«агент» — внешний процесс, который ходит через Py4J или MCP.

## Версии и сборка
- Мульти-версия через preprocessor (`versions/1.16.5 … 1.21.11`). В `gradle.properties` указано 1.21.1,
  но релизная линия — **1.21.11**. JDK 21.
- `gradlew runClient`; релиз только `gradlew :1.21.11:githubRelease`.
  Перед релизом удалить `versions/*/bin`, иначе в jar попадёт старый код.
- Для нас: брать release-jar `unionclef-1.21.11-0.95.66.jar` с GitHub, фиксировать sha256.
  Gradle-сборку не делаем.

## Управление
- **Py4J** `:25333`, callback-порт +1. Если порт занят, мод сдвигает его на +2 и пишет в
  `altoclef_settings.json`. Gateway создаётся без адреса и слушает только localhost →
  мозг кладётся в сетевое пространство тела (`network_mode: service:body-N`).
- Точка входа — `Py4jEntryPoint.java`: ~6000 строк, 100+ методов. **Целиком не читать**, искать по имени.
- **MCP** `:25350`: JSON-RPC `POST /mcp`, `Authorization: Bearer <mcpAuthToken>`, только опрос, ~60 инструментов.
  Документация: `docs/features/MCP_SERVER.md`, `docs/features/AGENT_PY4J_LEVERS.md`.
- `ChatMessage(msg)`:
  - `@` → altoclef: `@goto`, `@get`, `@food`, `@stop`…;
  - `;` → tungsten;
  - `/` → сервер;
  - остальное → чат;
  - `#` не работает.
- `ExecuteCommand(cmd)` выполняет команду altoclef в обход чата.
- Примитивы:
  - движение: `gotoXYZ` + `pathStatus`, `gotoFar`, `stopPathing`;
  - блоки: `mineBlock(s)`, `placeBlockAt`, `bridgeTo`, `pillarTo`;
  - бой: `punk` / `punkStop`, `runAwayPlayer`;
  - управление: `lookAt`, `selectHotbar`, `tapKey`, `holdKey`;
  - мир и подключение: `respawnPlayer`, `ConnectToServer`, `disconnectFromServer`.
- Наблюдения:
  - `getGameState()` — self: hp, food, pos, held, lookingAt; players; beds;
  - `getInventoryFull()`, `getEntitiesInfo(limit)` — `limit` это ЛИМИТ ЧИСЛА, не радиус
    (дефект T-006): фильтр по расстоянию делает Python-сторона по полю `distance`
    (строка `"%.1f"`); `position` — строка `"x, y, z"`, у сущностей координаты округлены
    до целых (`%.0f`);
  - `getBlocksAround(r≤5)`, `getSurfaceMap(r≤6)`, `getBlockAt`;
  - `getRecentChat(n)` — кольцевой буфер;
  - `hasActiveTask`, `inGame`, `getScreenshot()`.

### Рычаги выживания (проверены на стенде, T-020)

Формы ответов — как вернул живой uc 1.21.11 (образ mind-1 от 2026-10-17).

- `mineBlocks(positions)` — очередь tungsten break queue (не altoclef-задача:
  `hasActiveTask` её не видит). `positions` — список `[x,y,z]`, py4j передаёт
  Python-int как Java-Long, uc читает через `Number.intValue()`. Ответ
  `{'ok': True, 'queued': 1}`; блок под ногами добыт за 2,3 с. Блоки вне reach
  остаются в очереди — подойти `gotoXYZ` и поллить `mineStatus()` → `{mining,
  remaining}` или сам мир (`getBlocksAround`). Очередь изредка молча теряет
  клетку (стенд: ok/queued, блок цел 90 с) — проверять мир и на таймауте
  перекидывать очередь, лучше со свежего места.
- `punkAny(allow, avoid)` → `{ok, allow, avoid}` — бой **только по игрокам**:
  `PunkPlayerTask.isAcceptable` принимает `PlayerEntity` и ничего больше.
  Проверено коровой: `allow=["cow"]`, 10 с — hp 10.0 → 10.0, урона нет.
  `interactEntity`/`attackPlayer`/`@punk` — тоже игроки (`getEntity` →
  `getPlayerEntity`, `PlayerArg`). Бой с мобом — `lookAt` + `mouseClick("left")`.
- `punkStatus()` → `{ok, active, target}` (target null вне цели), `punkStop()` →
  `{ok: True}`. `active` поднимается асинхронно (клиентский поток): сразу после
  `punkAny` читать рано.
- `mouseClick("left")` → `{ok, button, hit: BLOCK|ENTITY|MISS}` — физический клик
  по прицелу. Атака моба: подойти в reach 3 блока (животное уходит — догонять
  `gotoXYZ`), прицел в центр хитбокса (клетка +0.5, y+0.7 — точка ног попадает
  под хитбокс, `hit: BLOCK`), клик. Корова 10 hp — 3 удара железным мечом.
- `useHeldItem()` → `{ok, result, held}` — одна интеракция с предметом в руке.
  Для еды этого НЕДОСТАТОЧНО: один вызов не съедает хлеб (food и count не
  меняются). Рычаг `eat` = `selectHotbar(n)` + `holdKey("use", ms)`, `ms` из
  аргументов, по умолчанию 1700 — food 17→20, saturation → 6, count −1 (MC
  держит use 32 тика). `use_held` остаётся одной интеракцией.
- `@get <item> <n>` — «обеспечить ≥ n»: с уже имеющимся предметом завершается
  сразу (3 с), с нуля у дерева ~13 с. `@equip <item>` — только броня/щит
  (`EquipCommand.EquipmentItemArg`), обычные предметы — `selectHotbar`.

### События и Python-образцы

- События: Python реализует `PythonCallback.java` и поднимает callback-сервер, затем `InitPythonCallback()`.
  Колбэки: `onChatMessage`, `onDeath`, `onKill`, `onDamage`, `onAutoclefEvent`, `onUpdateServerInfo`,
  `onVoiceFeed`, `agentCommandRequest`. Без callback-сервера события молча теряются.
- Готовые Python-образцы:
  - `deploy/runner/uctest/actors.py`: `cmd`, `chat`, `recent_chat`, `pos`, `health`, `ensure_alive`, `fresh_reset`;
  - `scripts/example_goto.py`;
  - `deploy/runner/agent_loop_test.py`.
  Реализация callback на Python — в `NetTyan/src/data_collectors/minebridge.py`.

## Смерть
- `onDeath(name, killer)` не вызывается, если killer = `undefined`. Это смерть от моба, падения,
  лавы или голода — то есть почти все смерти узла.
- `chains/DeathMenuChain.java`:
  - авторесп, если `autoRespawn` = true (по умолчанию);
  - выполняет `deathCommand` с `{deathmessage}`;
  - счётчик смертей приватный.
- **Авторесп срабатывает только во время задачи.** Без задачи `TaskRunner.tick()` не крутит цепочки,
  и тело лежит трупом сколько угодно (проверено в E1). Мозг сам вызывает `respawnPlayer()` после
  завещания. Это нам и нужно: респаун — шаг цикла смерти, а не событие мода.
- Как ловим смерть (проверено в E1): hp = 0 в `getGameState` плюс ванильная строка
  `LastOrder01 was killed` / `… was slain by …` в `getRecentChat`. `deathCommand` пустой.
  Принудительная смерть — RCON `/kill LastOrder0N`.

## Py4J: проверено в E1
- `ExecuteCommand` и `RunInnerCommand` требуют префикс: `@get log 1`. Без `@` команда молча
  отбрасывается (`CommandExecutor.isClientCommand`).
- Python-сторона закрывается через `gateway.close()` и `shutdown_callback_server()`. **Не
  `gateway.shutdown()`**: он гасит GatewayServer в uc, и порт возвращается только после перезапуска клиента.
- Перезапуск мозга безопасен: новый процесс подключается, `InitPythonCallback()` перепривязывает callback.
- `getRecentChat` и `onChatMessage` отдают и служебные строки самого uc: `§2§l§o[Alto Clef] …`,
  `[Tungsten] …`, с сырыми id внутри. Мозг отбрасывает их до perception.
- Задержки: геттеры 1–130 мс, `getBlocksAround(2)` около 100 мс. `@get log 1` рядом с деревом — 5–10 с.
- `gotoXYZ(x, y, z)` → `{goal, ok}`. Дальше опрашивать `pathStatus()` → `{arrived, busy, distance, pos}`.

## Жизненный цикл моста (T-055)
- Подключаться только к поднятым телом: uc поднимает GatewayServer при выходе на титульный
  экран (`AltoClef.java: onInitializeLoad → initializePythonSender`, шлюз слушает 25333),
  подключение клиента py4j во время загрузки ломает шлюз (T-052 b). compose держит healthcheck
  тела по порту 25333 и `depends_on: service_healthy` для mind-N. «Игрок в мире» из контейнера
  без py4j не видна: состояние живёт в `MinecraftClient` (`AltoClef.java:324-326`, `inGame()`),
  вход в мир ум узнаёт своим опросом.
- Python-партнёра нельзя убивать SIGKILL: шлюз Java в uc умирает навсегда (T-052 a). mind-N по
  SIGTERM останавливает uvicorn, C1 и закрывает мост `close()` (порядок — `teardown_mind`,
  `runtime/mind/mcp_body.py`); compose даёт `stop_grace_period: 20s` против ~11 с разбора.
- Путь отказа py4j 0.10.9.9 сам зовёт `gateway.shutdown()`: гасит шлюз uc и падает на
  `thread = None` (`AttributeError: 'NoneType' object has no attribute 'join'`, T-052 c). Наша
  фабрика сначала пробует bind callback-порта (25334) и отказывается до py4j; `_drop` дожимает
  сокет callback-сервера напрямую. Остаточный риск принят: если порт заняли между пробой и
  bind, py4j всё равно шлёт uc SHUTDOWN, и шлюз мёртв до перезапуска тела (`slot_restart.sh`).
  Обновления uc: после 0.95.66 (26.10.2026) исправлений
  жизненного цикла шлюза в upstream нет [источник: github.com/3ndetz/unionclef, releases и
  main, проверено 2026-11-07].
- `docker compose restart` изношенного тела не гарантирует переподключения к серверу; свежий
  контейнер (`up -d --force-recreate`) подключается сразу (T-053). Перезапуск слота —
  `bash dev/slot_restart.sh N` (mind стоп → body recreate → healthcheck → mind старт).

## Сырые имена — всё нужно маскировать

| Метод | Поле | Что содержит |
|---|---|---|
| `getBlockAt` | `block`, `id` | display name, `minecraft:…` |
| `getBlocksAround` | `block` | display name строчными: `birch leaves`, `dirt` |
| `getInventoryFull` | `item`, `name` | `minecraft:birch_log`, `Birch Log` |
| `getGameState` | `held`, `lookingAt` | registry id, `block@x,y,z` |
| `getEntitiesInfo` | `type`, `name`, `item` | `zombie`, display name, item |
| `getPlayersInfo` | `hand_item`, `ground_block` | сырые имена |
| `getOpenScreen` | items | id и display name |
| `getRecentChat` | строки | ванильные сообщения, в том числе причина смерти |

Команды (`@get birch_log`) тоже принимают настоящие id. Обратный перевод термин/handle → id
делает `runtime/perception`.

## Настройки (`altoclef_settings.json`)
`autoConnectServer`, `autoReconnect`, `autoRespawn`, `deathCommand`, `pythonGatewayPort`, `mcpEnabled`,
`mcpPort`, `mcpAuthToken`. Ник меняется через `changePlayerName()` и только до подключения.
Для нас ник постоянный — `LastOrder0N`.

## Контейнеры
- Тестовый стенд `deploy/compose.test.yml`: itzg 1.21.11, offline, RCON + клиенты `mineswarm-mc:amd64`.
  Образа клиента у нас нет, мы собираем свой на HeadlessMC.
- uc 0.95.59 под HeadlessMC с `-lwjgl` (без GL) работает: E1, 2026-10-14. Xvfb не понадобился,
  но остаётся в образе (`BODY_RENDER=xvfb`) для показа и стрима.
  GPU в контейнере через D3D12 на WSL не работает: см. `deploy/gpu-image/Dockerfile`.
- `@gamer` по статусу 2026-09 не ест, не убегает при низком hp, не спит ночью. Узлу его всё равно не даём.

## Работа в этом репозитории
- Свой цикл (`docs/CHECKLIST.md`, 1400 строк, видеоотчёты), английский язык, коммиты без Co-Authored-By.
- `.claude/settings.json`: bypassPermissions, Stop-хук `autonomy_stop.py` не даёт завершить ход,
  пока есть `.claude/autonomy_active.flag`. Агента из `unionclef/` не запускать.
