
## 2026-11-10 · сделано напрямую (ADR-047)
Коммиты: bac8364 (релей), 4466cc6 (мастерская), c316590 (инструкции).

### Сделано
- **Релей грантов** `runtime/hive/relay.py`: MCP `http_request` у Last Order и `POST /lo/fetch`
  для её расширений. `{{secret:NAME}}` подставляется в hive, запрос только https и только на
  домен с активным egress-грантом, наружу через `egress:3128`, значение секрета вычищается из
  ответа, ошибок и журнала. Секрет в песочницу не попадает.
- **Мастерская.** Pi 1.4.0 не стартует, если хоть одно расширение не грузится
  [проверено: lastorder-node:dev, ParseError → `pi --mode rpc` выходит]. Поэтому Pi грузит только
  `~/.pi/agent/workshop-active/`, а туда попадает лишь расширение, которое чисто загрузилось в
  отдельном `pi -ne -e <entry>`:
  - при каждом рендере сессии — `runtime/node/workshop.py` (раннер сестры, стол Last Order);
  - в живой сессии — расширение среды `runtime/pi/workshop/`: инструмент `workshop_reload`
    проверяет, пересобирает активный каталог и ставит в очередь `/workshop-reload`
    (`ctx.reload()` есть только у команд — паттерн Pi `examples/extensions/reload-runtime.ts`);
    после перезагрузки новое расширение шлёт агенту отчёт сообщением с `triggerTurn`.
  - Своё (`own-`) затеняет полку (`net-`) при совпадении имени.
- **Полка** `base/tools/<name>/` под git (`runtime/hive/tools.py`): Last Order — `publish_tool`
  (hive читает её `workshop/` через `hive-work:/lo-workplace:ro`) и `shelf`; сестра —
  `propose(kind=tool)`, решает Last Order. Раннер берёт `GET /tools/bundle` при каждом рендере.
- `workshop/` — запись для обеих сторон; обе инструкции получили по абзацу.
- `docker/node/up.sh` поднимает `HIVE_TOOL_KEY`: без него `egress` был в цикле перезапуска
  и ни один грант не работал.

### Проверки
```
pytest: 1005 passed, 3 skipped           # +14 test_workshop, +7 test_hive_relay
tools/check.py: Every workplace is intact
```
В образе `lastorder-node:dev` (без LLM): Python-проверка отбросила битое расширение с ParseError,
хорошее и «полочное» прошли; TS `rebuild()` через node подхватил добавленное на ходу;
`/workshop-reload` в RPC — после перезагрузки инструменты `tg_post, good_tool, late_tool`,
отчёт пришёл сообщением `customType: workshop` [проверено].

Стенд (`lastorder`, HAPI на 3010 — 3006 занят старым `t044-hapi-hub-1`):
`egress` жив; `/tools/bundle` → `{}`; `/lo/fetch` на api.telegram.org → 403 `no_egress_grant`;
в живой сессии Last Order `Available commands: workshop-reload` [проверено].

### Не проверено
- Вызов `workshop_reload` самой моделью посреди хода (followUp-команда → reload → новый ход)
  на живом GLM — паттерн из документации Pi, на стенде не гонялся (это расход квоты).
- `publish_tool` → рождение сестры с инструментом с полки — слоты на стенде не подняты.
