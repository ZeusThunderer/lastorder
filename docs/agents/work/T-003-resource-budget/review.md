# T-003. Ревью надзора

## Раунд 1 · 2026-10-16 · вердикт: changes
Проверено: `git diff main...z/T-003-resource-budget` @ 83cde29. `docker/` — критичная зона, diff прочитан построчно.

### Проверки (прогнаны надзором)
- `uv run pytest` — 28 passed.
- `python tools/check.py` — все рабочие места целы.
- `docker compose -f docker/compose.yml build` — body и mind собраны.
- `docker compose -f docker/compose.yml up -d mc-server body-1` — тело на конфиге ветки:
  `mem_limit` 2 ГБ, `BODY_XMX=1G`, `BODY_RENDER_DISTANCE=2`, `BODY_SIM_DISTANCE=5`, `BODY_MAX_FPS=10`.
- `docker compose -f docker/compose.yml up mind-1` — `body_probe.py --kill` целиком, exit 0.
- `uv run python dev/measure_resources.py --samples 12 --interval 5`, покой сразу после пробы:

| service | RAM avg | RAM max | CPU avg | CPU max |
|---|---|---|---|---|
| mc-server | 1317 MiB | 1442 MiB | 7.1% | 15.5% |
| body-1 | 1451 MiB | 1459 MiB | 20.3% | 33.2% |

### Критерии приёмки
- [x] `dev/measure_resources.py` — среднее и максимум RAM/CPU таблицей, прогнан надзором.
- [x] Таблица «до / после», изменения отдельными строками — есть.
- [x] `body_probe.py --kill` проходит — подтверждено прогоном.
- [x] Новых модов нет — пункт неприменим. Отказ от Lithium/FerriteCore объяснён, принимаю.
- [ ] Итог по телу: вывод отчёта противоречит и его таблице, и замеру надзора, см. R1-1.
- [ ] `docker-topology.md` «Ресурсы» — цифра тела завышает результат, см. R1-1.

### Находки
- R1-1 · blocker · `report.md:58`, `docs/agents/docker-topology.md:37` · отчёт пишет «тело ≤ 1,2 ГБ
  в покое: да, 1170–1243 МиБ в строках 2–5». В строке 5 той же таблицы (итоговый конфиг) тело
  1305 МиБ, в строке 6 — 1461. Замер надзора на итоговом конфиге сразу после чистого старта и пробы —
  1451 МиБ. Цель 1,2 ГБ для тела на итоговом конфиге не достигнута. Ожидается одно из двух:
  - добиться ≤ 1,2 ГБ: например `BODY_XMX=768M` или ограничение нативной памяти
    (`-XX:MaxMetaspaceSize`, `-XX:ReservedCodeCacheSize`, `MALLOC_ARENA_MAX=2`), с `body_probe --kill`
    и замером покоя и `@get log 3`;
  - или честно написать «нельзя» с причиной и лучшим вариантом, как разрешает критерий.
  В обоих случаях исправить итог в отчёте и цифру тела в `docker-topology.md`.
- R1-2 · minor · `docker/body/Dockerfile:32` · `ENV BODY_XMX=2G` расходится с умолчанием compose `1G`.
  Ожидается одно значение по умолчанию в обоих местах.
- R1-3 · minor · `dev/measure_resources.py:39` · единица берётся как три последних символа.
  Значение вида `0B / 0B` от только что запущенного контейнера роняет скрипт `ValueError`.
  Ожидается разбор числа и единицы регуляркой.

### Чек-лист проекта
Без замечаний: правки в пределах «Можно трогать», сеть, порты, volumes, `network_mode` и
`RCON_PASSWORD` не тронуты, bind-mount не добавлены, LF сохранён.
