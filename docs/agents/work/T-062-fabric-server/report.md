# T-062. Отчёт исполнителя

## Раунд 1 · 2026-11-11

### Сделано
- `docker/compose.yml` — `mc-server`: `TYPE: FABRIC`, `FABRIC_LOADER_VERSION` (0.20.1) и
  `FABRIC_LAUNCHER_VERSION` (1.1.2) закреплены (стабильные версии meta.fabricmc.net для
  1.21.11, проверено 2026-11-11), единственный серверный мод — `MODRINTH_PROJECTS:
  fabric-api:${FABRIC_API:-0.140.0+1.21.11}`. Та же `FABRIC_API` — build arg `x-body`
  (якорь `x-fabric-api`), `FABRIC_LOADER` — env тел: версии сервера и тел из одного места.
- `docker/body/entrypoint.sh` — пин загрузчика тел: `FABRIC_LOADER` → цель HeadlessMC
  `fabric:<mc>:<loader>`. Форму проверил на живом контейнере: `fabric/1.21.11/0.20.1` и
  `fabric-loader-0.20.1-1.21.11` HMC 2.10.0 не резолвит («Couldn't find object»),
  колоночная — резолвит: `Installing 1.21.11 with fabric 0.20.1`,
  `Launching version fabric-loader-0.20.1-1.21.11`.
- `dev/backup_world.sh` — снимок/восстановление volume `world` потоком через alpine:3.20
  (без bind-mount, работает из любого checkout), `--dry-run`, guard: живой volume —
  только с `--force`.
- `docker-topology.md` — раздел «Сервер Fabric»: версии, добавление мода одной строкой,
  бэкап, новый выход наружу (modrinth.com при старте; у тел — piston-data.mojang.com и
  maven.fabricmc.net как было). `.env.example` — необязательные `FABRIC_*`.
- `tests/test_fabric_server.py` — пины сервера, равенство fabric-api у сервера/тел/ARG
  Dockerfile, dry-run бэкапа и guard восстановления.

### Проверки
```
$ bash dev/check.sh
pytest: ok · 995 passed, 3 skipped, 2 warnings in 133.82s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ bash dev/backup_world.sh --dry-run   # печатает команды, ok
```
Стенд (изолированный проект `t062`, копия живого `lastorder_world` из архива, не сам volume):
```
$ docker logs t062-mc-server-1 | grep -E "fabric|Done"
Downloaded ./fabric-server-mc.1.21.11-loader.0.20.1-launcher.1.1.2.jar
Downloaded /data/mods/fabric-api-0.140.0+1.21.11.jar
Loading Minecraft 1.21.11 with Fabric Loader 0.20.1
[23:41:11] Done (0.270s)! For help, type "help"
$ docker exec t062-mc-server-1 rcon-cli seed
Seed: [-4419881977858743592]      # vanilla на той же копии: тот же сид
$ docker exec t062-mc-server-1 rcon-cli list
There are 1 of a max of 20 players online: LastOrder01
```
Бэкап: архив 121M/291 записей; восстановлен в копию `t062-van` (vanilla, снят сид) и
`t062-fab` (fabric) — листинги совпали. Тело: холодный старт в t062, healthy за 118 c,
вход в мир `[23:59:08] LastOrder01 logged in … at (75.58, 64.0, 66.10)`.
Не прогонял: переключение самого стенда `lastorder` на Fabric — общий с очередью a,
по согласованию с оператором; снимок мира для этого лежит в
`.worktrees/T-062-fabric-server/backups/lastorder_world-20261110-232750.tar.gz`.

### Отклонения от задачи
- Тест версий у сервера и тел смотрит compose + Dockerfile ARG (как в задаче), но равенство
  loader у тел держит compose-переменная в env тел, а не Dockerfile: у тел один образ,
  версия загрузчика — runtime, не build.

### Вне задачи
- В корне репозитория лежат мусорные `.mcp-body-test-9912659*.jsonl` (4 шт.) — не трогал.
- `lastorder-egress-1` на главном стенде в рестарт-лупе (было до меня) — не трогал.

### Вопросы
- нет
