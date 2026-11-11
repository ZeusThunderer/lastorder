# T-061. Отчёт исполнителя

## Раунд 1 · 2026-11-11
Коммиты: 0d67ed6 (реализация), 3b8711e (тесты), 20e5557 (docker-topology), + фиксы ниже.

### Сделано
- Мод: `skinrestorer` `2.11.1+1.21.11-fabric` (Modrinth, единственный живой серверный Fabric-мод
  скинов с точной сборкой 1.21.11; FabricTailor сборки нет) — в `MODRINTH_PROJECTS` рядом с
  fabric-api, пин `${SKINRESTORER:-…}` (docker/compose.yml).
- Скин оператора монтируется в mc-server read-only (`../agent/last_order/skin/sister.png:/skins/sister.png:ro`),
  конфиг мода `docker/mc-server/skinrestorer/config.json` — по схеме, которую мод сам
  сгенерировал на стенде (не по догадке): `join.autoFetch.enabled=false`, провайдер
  `collection` включён, источник `sister` = `/skins/sister.png`, variant slim. Ни один другой
  сервис этих mount’ов не видит.
- `dev/apply_skin.sh`: rcon `skin set collection sister LastOrder0N` на все три слота,
  `--dry-run` / `--project` / `-f`, онлайн-статус из одного rcon `list`.
- Внешний сервер: `SessionLogin(skin_command=…)` (runtime/mind/login.py) шлёт одну команду
  сразу после каждого `/login` при входе в мир, тем же путём и под той же маской; env
  `MC_SKIN_COMMAND_N` → compose `MC_SKIN_COMMAND` в mind-N, как пароль T-053. Команду внешнего сервера
  не пробовал (аккаунт оператора); публичные доки команду не называют, кандидат в рецепте —
  `/skin <ник>` [источник: skinsrestorer.net; не проверено].
- `runtime/node/workplace.py`: `copytree(…, ignore=shutil.ignore_patterns("skin"))` — `skin/`
  шаблона не попадает в рабочее место сестры.
- `docs/agents/docker-topology.md`: раздел «Скин сестёр (T-061)» — мод, mount’ы, рецепт
  применения, egress (modrinth.com на старте; api.mineskin.org — подпись собственных скинов,
  без подписи ванильный клиент текстуры отбрасывает; api.mojang.com/sessionserver — только при
  autoFetch, он выключен), финальная проверка клиентом оператора.
- Тесты: `tests/test_skin_stand.py` (пин мода, mount’ы только у mc-server, MC_SKIN_COMMAND
  только в mind-N, dry-run скрипта), `tests/test_workplace_skin.py`, сценарий skin_command в
  `tests/test_mcp_body.py`; `tests/test_fabric_server.py` (T-062) переписан с «единственный
  мод — fabric-api» на точный список «fabric-api + skinrestorer, оба с пинами».

### Проверки
```
$ bash dev/check.sh
pytest: ok · 1017 passed, 3 skipped, 2 warnings in 130.15s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ bash dev/apply_skin.sh --dry-run
# docker compose -f docker/compose.yml -p lastorder exec -T mc-server rcon-cli skin set collection sister LastOrder01 … (×3 слота)
```
Стенд (изолированный проект `t061`, свой volume `t061-world`, свой образ тела `t061-body:dev`;
живой стенд lastorder/t048 не тронут):
- мод скачан и загружен: `- skinrestorer 2.11.1+1.21.11-fabric`; дефолтный конфиг прочитан из
  volume (schema v3), мой конфиг применён (команда collection работает, autoFetch-запросов нет).
- офлайн-применение всем трём слотам: `apply_skin: LastOrder01 (offline — the saved skin shows
  at the next join)` ×3; лог сервера `[Rcon: Saved skin for LastOrder01, applies on next join]` ×3;
  хранилище `/data/world/skinrestorer/<offline-uuid>.json` (3 файла, provider collection,
  argument sister, есть `signature`; декодированные текстуры: `"model" : "slim"`).
- тело онлайн: `LastOrder01 joined the game` (00:26:50), rcon `list` = 1/20; повторный прогон
  скрипта: `apply_skin: LastOrder01 (online)`.
- перезапуск mc-server и тела: скины в volume пережили рестарт, тело само зашло
  (`LastOrder01 joined the game`, 00:28:35), новых `Saved skin` нет — повторное применение
  не требовалось. Финальный взгляд клиентом оператора — рецепт в docker-topology.md, сам не проверял.
- Внешний сервер живьём не проверял: probing mc.example.net запрещён (аккаунт оператора).
  Механизм покрыт фейковым рычагом в tests/test_mcp_body.py.

### Отклонения от задачи
- Порт стенда 25577 вместо 25576: порт занят живым `t048-mc-server-1` (трогать нельзя).
- Тело поднималось `docker run` в сети `t061_default`, без slot-1: пул адресов docker исчерпан
  (см. «Вне задачи»), mind-1/node-1 для проверки скина не нужны. Compose-путь тела не менялся.
- `tests/test_fabric_server.py` (зона T-062, файл в «Можно трогать» через tests/): инвариант
  «единственный мод» устарел после добавления второго мода — переписан на точный список.
- `FileSystemException … config.json … Device or resource busy` на старте mc-server: мод пишет
  конфиг обратно поверх read-only bind. Безвредно (конфиг уже прочитан, мусорных tmp нет, скин
  ставится), задокументировано в docker-topology.md.

### Вне задачи
- На хосте исчерпаны пулы адресов docker («all predefined address pools have been fully
  subnetted»): новый compose-проект с сетями не поднимается. Не связано с T-061; оператору
  стоит почистить простаивающие сети/проекты или расширить default-address-pools.
- `.mcp-body-test-*.jsonl` в worktree — мусор от прогонов pytest, не коммитился.

### Вопросы
- нет

### Ревью-фикс (ZCode, 2026-11-11)
- `/skin` добавлен в DEFAULT_DENY чат-рычага сестры (review-находка: permissionLevel 0 у
  мода позволял сестре сменить скин самой; выбор оператора — не сестры, задача T-061).
  Среда шлёт свою команду мимо рычага (login.py → body.act), запрет её не задевает.
- Замечание по внешнему серверу: skin_command шлётся сразу за /login, без ожидания
  подтверждения входа — authme-сервера могут отбросить команду до авторизации; путь всё
  равно не проверен на живом сервере (см. выше), при пробе с оператором уточнить тайминг.
