---
id: T-003
title: Замер и снижение ресурсов тела и сервера
stage: E2
status: changes
criticality: normal
branch: z/T-003-resource-budget
created: 2026-10-16
---

# T-003. Замер и снижение ресурсов тела и сервера

## Цель
Есть воспроизводимый замер RAM и CPU для `mc-server` и `body-N` и настройки, которые их снижают без
поломки моста uc. Три слота плюс сервер укладываются в бюджет из раздела «Критерии», запас под hive и
`jev` остаётся. Требование R5.

## Контекст
- Замер E1 (2026-10-14, 1 тело в покое): сервер 2,2 ГБ, тело 1,5 ГБ, тело ~30% CPU.
  `docs/agents/docker-topology.md` «Ресурсы».
- `docker/compose.yml`: сервер `MEMORY=2G`, `VIEW_DISTANCE=8`; тело `mem_limit: 3g`.
- `docker/body/entrypoint.sh`: `options.txt` пишется только при первом старте (`renderDistance:4`,
  `simulationDistance:5`, `maxFps:20`), `-Xmx${BODY_XMX}`.
- Проверка, что тело живо: `tools/body_probe.py` из `mind-1` (`docker compose -f docker/compose.yml up mind-1`).
- Хост: Docker Desktop WSL2, 24 ГБ / 18 CPU.

Направления, которые стоит проверить (не обязательно все):
- сервер: `VIEW_DISTANCE`, `SIMULATION_DISTANCE`, `pause-when-empty-seconds`, `MEMORY`, флаги GC;
  Fabric-сервер с модами производительности (Lithium, FerriteCore и подобные) через itzg `TYPE=FABRIC`;
- тело: `options.txt` переписывать на каждом старте (как остальные настройки в entrypoint),
  `maxFps` ниже, `renderDistance` 2, `-Xmx` ниже, моды производительности клиента — только если
  `body_probe.py` проходит целиком;
- лишние загрузки при первом старте (звуки и ассеты) — можно ли их не качать.

## Объём
Можно трогать:
- `docker/body/Dockerfile`, `docker/body/entrypoint.sh`
- `docker/compose.yml` — переменные `mc-server` и `x-body`, лимиты; сеть, порты, volumes и секреты не трогать
- `dev/measure_resources.py` или `dev/measure_resources.sh` (новый)
- `docs/agents/docker-topology.md` — раздел «Ресурсы»
- файлы задачи в `docs/agents/work/T-003-resource-budget/`

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`
- `tools/` (upstream aw), `runtime/`
- `RCON_PASSWORD`, `network_mode`, порты

## Критерии приёмки
- [ ] `dev/measure_resources.*` снимает `docker stats` по сервисам стенда N раз с интервалом и печатает
  среднее и максимум RAM и CPU в таблице.
- [ ] В отчёте таблица «до / после» для сервера и одного тела в двух состояниях: покой после входа и
  `@get log 3` в процессе. Каждое изменение настроек — отдельной строкой с эффектом.
- [ ] После изменений `tools/body_probe.py --kill` проходит целиком (вывод в отчёте).
- [ ] Каждый новый мод — с версией и sha256 в Dockerfile, как остальные jar.
- [ ] Итог: тело ≤ 1,2 ГБ RAM в покое, сервер ≤ 1,8 ГБ с одним телом, или отчёт объясняет, почему
  нельзя, и что даёт лучший вариант.
- [ ] `docker-topology.md` «Ресурсы» обновлён цифрами.

## Проверки
```
uv run pytest
python tools/check.py
docker compose -f docker/compose.yml build
docker compose -f docker/compose.yml up -d mc-server body-1
docker compose -f docker/compose.yml up mind-1
```

## Ограничения
- Пути bind-mount в compose разрешает демон, а не шелл; новых bind-mount не добавлять.
- `.gitattributes` требует LF для `*.sh`, `*.py`, Dockerfile и yml.
- py4j: команды uc только с префиксом `@`; закрывать `gateway.close()`, не `shutdown()`.

## Заметки надзора
