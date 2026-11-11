---
id: T-062
title: mc-server на Fabric — закреплённые версии загрузчика и модов, мир без потерь
stage: I
status: done
criticality: normal
lane: b
after: []
branch: z/T-062-fabric-server
created: 2026-10-31
---

# T-062. mc-server на Fabric — закреплённые версии загрузчика и модов, мир без потерь

## Цель
`mc-server` работает на Fabric 1.21.11 вместо vanilla. Версии загрузчика и модов закреплены в одном
месте и одинаковы у сервера и тел. Серверный мод добавляется одной строкой конфига. Мир `world` тот же.

## Контекст
- Решение оператора 2026-10-31: сервер — Fabric (ADR-045). Тела уже на Fabric: HeadlessMC запускает
  `fabric:${MC_VERSION}` (docker/body/entrypoint.sh:63), Fabric API `0.140.0+1.21.11`
  (docker/body/Dockerfile:9). Версия Fabric Loader у тел не закреплена: HeadlessMC берёт свою
  [предположение: последнюю].
- Сейчас `mc-server`: `itzg/minecraft-server:java21`, `TYPE: "VANILLA"`, `VERSION: "1.21.11"`
  (docker/compose.yml:126-147), мир на volume `world`.
- itzg [проверить по документации образа]: `TYPE: FABRIC`, `FABRIC_LOADER_VERSION`,
  `FABRIC_LAUNCHER_VERSION`, моды — `MODRINTH_PROJECTS` с версией (`project:version`).
- Серверных модов, кроме Fabric API, в этой задаче нет. Скин — T-061; повторы и остальное решает
  Last Order (ADR-045).

## Объём
Можно трогать:
- `docker/compose.yml` — только сервис `mc-server`: тип, версии, список модов;
- `docker/body/Dockerfile`, `docker/body/entrypoint.sh` — закрепить версию Fabric Loader у тел;
- `.env.example`, если есть, — переменные версий с комментарием;
- `docs/agents/docker-topology.md` — раздел «Сервер Fabric»: версии, как добавить мод, бэкап мира;
- `dev/backup_world.sh` — новый: снимок volume `world` в архив на хосте и восстановление;
- `tests/` — тест, что версии у сервера и тел совпадают (по compose и Dockerfile).

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`, `.env*` с секретами
- сети, секреты и bind-mount в `docker/compose.yml`
- `runtime/`

## Критерии приёмки
- [ ] `mc-server` поднимается с `TYPE: FABRIC`; версии загрузчика и Fabric API закреплены явно,
  не `latest`. Выдержка лога с версией загрузчика — в отчёте.
- [ ] Тела запускаются с той же версией Fabric Loader. Если HeadlessMC её не закрепляет, в отчёте
  написано почему и что проверено.
- [ ] Версии записаны в одном месте (переменные compose или `.env`), тест сверяет сервер и тела.
- [ ] До смены типа снят бэкап `world` (`dev/backup_world.sh`), восстановление проверено на копии.
- [ ] Мир открывается тот же: выдержка лога `mc-server` (загрузка уровня, `Done`); сид или спавн
  совпадают с vanilla — в отчёте.
- [ ] RCON работает, тело входит в мир (`dev/body_smoke.py` или один слот) — вывод в отчёте.
- [ ] В `docker-topology.md` описано, как добавить серверный мод одной строкой с версией.
- [ ] `bash dev/check.sh` зелёный.

## Проверки
```
bash dev/check.sh
bash dev/backup_world.sh --dry-run
docker compose -f docker/compose.yml up -d mc-server
docker compose -f docker/compose.yml logs mc-server | tail -40
```

## Ограничения
- Мир не пересоздавать. Перед подъёмом сверить с оператором, чей стенд поднят (идёт T-052).
- Новый выход наружу из `mc-server` — только загрузка модов образом при старте; записать в
  `docker-topology.md`.
- Модов «на будущее» не добавлять: какие нужны, решает Last Order или отдельная задача.

## Заметки надзора
