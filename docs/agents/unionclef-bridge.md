# unionclef (uc): тело в Minecraft

Fabric-мод: altoclef (`src/main/java/adris/altoclef`) + tungsten (pathfinder, `tungsten/…`).
`baritone/` и `shredder/` не компилируются, лежат как справочник. LLM внутри мода нет:
«агент» — внешний процесс, который ходит через Py4J или MCP.

## Версии и сборка
- Мульти-версия через preprocessor (`versions/1.16.5 … 1.21.11`). В `gradle.properties` указано 1.21.1,
  но релизная линия — **1.21.11**. JDK 21.
- `gradlew runClient`; релиз только `gradlew :1.21.11:githubRelease`.
  Перед релизом удалить `versions/*/bin`, иначе в jar попадёт старый код.
- Для нас: брать release-jar `unionclef-1.21.11-0.95.59-all.jar` с GitHub, фиксировать sha256.
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
  - `getInventoryFull()`, `getEntitiesInfo(limit)`;
  - `getBlocksAround(r≤5)`, `getSurfaceMap(r≤6)`, `getBlockAt`;
  - `getRecentChat(n)` — кольцевой буфер;
  - `hasActiveTask`, `inGame`, `getScreenshot()`.
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
- Как ловим смерть: маркер через `deathCommand` в чат + `getRecentChat`, плюс hp ≤ 0 в `getGameState`.
  Принудительная смерть — RCON `/kill LastOrder0N`.

## Сырые имена — всё нужно маскировать

| Метод | Поле | Что содержит |
|---|---|---|
| `getBlockAt` | `block`, `id` | display name, `minecraft:…` |
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
- Без рендера llvmpipe тянет 7–8 FPS на настоящем мире. В HeadlessMC рендер не нужен. Если uc без GL
  не взлетает, запасной путь — Xvfb + llvmpipe, 854×480.
  GPU в контейнере через D3D12 на WSL не работает: см. `deploy/gpu-image/Dockerfile`.
- `@gamer` по статусу 2026-09 не ест, не убегает при низком hp, не спит ночью. Узлу его всё равно не даём.

## Работа в этом репозитории
- Свой цикл (`docs/CHECKLIST.md`, 1400 строк, видеоотчёты), английский язык, коммиты без Co-Authored-By.
- `.claude/settings.json`: bypassPermissions, Stop-хук `autonomy_stop.py` не даёт завершить ход,
  пока есть `.claude/autonomy_active.flag`. Агента из `unionclef/` не запускать.
