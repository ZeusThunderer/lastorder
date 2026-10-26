
## Раунд 1 · 2026-10-26
Коммиты: 1cf6d54, 3435051, 5e04574 (restart-политика)

### Сделано
- Образ: `docker/node/Dockerfile` — слой Pi `@earendil-works/pi-coding-agent@1.4.0`
  (npm-пин, `pi --version` гейт; Node в образе 22.23.4 ≥ 22.19).
- Рендер: `runtime/node/workplace.py` — `render_pi_configs` (models.json: провайдер
  zai, `apiKey: "$ZAI_API_KEY"` — интерполяция из env процесса, ключ в файлы не
  попадает; compat `supportsDeveloperRole: false`, `thinkingFormat: "zai"`;
  mcp.json: body+hive с X-Node-Key; settings.json: zai-дефолты, телеметрия выкл) и
  `drop_pi_agent_dir` (конфиги root 0444, каталог сестры 0700). MCP в Pi встроен
  (pi-mcp — зависимость 1.4.0), отдельный адаптер не нужен.
- Стенд: `dev/console_spike/up_spike.sh` (стенд задачи, раннер спит — квоту жгут
  только сессии спайка) и `pi_rpc.py` (рендер конфига, одна RPC-сессия `pi --mode rpc
  --no-session` от uid sister; ключ через 0600 env-файл в доме сестры, не argv).
- Сессии Pi на стенде (4 LLM-хода из ≤6; логи в `dev/console_spike/logs/`):
  - Критерий 2: get_state → `provider: zai, model: glm-5.3-flash, baseUrl
    https://api.z.ai/api/coding/paas/v4`; ответ модели получен (сессия 1: disposition
    started → agent_settled; текст ответа — в сессии 3). Grep по префиксу ключа:
    /tmp/pi-spike — 0, хостовые логи — 0; единственный файл с ключом —
    `/home/sister/.pi/spike-env` (0600 сестры, паттерн hermes .env, ADR-033).
  - Критерий 3: инструмент виден как `mcp__body__observe`, вызван моделью, ответ:
    «Я … мёртв — hp 0, вокруг пусто: ни сущностей, ни блоков…» (тело стояло мёртвым
    с прошлых прогонов). Способ подключения MCP: встроенный, `~/.pi/agent/mcp.json`.
  - Критерий 4: steer в середине инструментальной серии — `disposition: queued`,
    `queue_update` показал доставку после текущего вызова (модель сделала 1 observe
    вместо 5: thinking «One observe done, per the steering rule I stop now»);
    follow_up — доставлен после полной остановки (финальная реплика «руль дошёл»).
  - Критерий 5: «мысль» работает: расширение (TS, `~/.pi/agent/extensions/thought.ts`)
    командой `/thought <текст>` вызывает `pi.sendMessage({role: "assistant", content:
    [{type:"text",…}]})` — следующему вызову LLM текст виден как реплику самого
    агента: модель процитировала «Мысль оператора: тестовый снег падает вверх»
    дословно. API-поверхность снята зондом (`appendEntry` —持久но, но НЕ в контекст
    модели; события `before_agent_start` (return message), `agent_before_settle` +
    `continue: true`; RPC-диалоги расширений — `extension_ui_request/response`).
- Консоли: `docker/compose.yml` профиль `console-spike` (2 сервиса на образе node,
  порты только 127.0.0.1); `docs/agents/console-choice.md` — таблица 4 кандидатов,
  2 прогнаны на стенде: pi-web-ui поднялся (нужны make/g++ для node-pty; UI 200,
  /api/health 200, pi SDK 1.4.0 внутри), pi-web jmfederico — нет: `pi-web install`
  требует systemd-run, `start` — `systemctl --user` (в контейнере нет systemd).
  Главный вывод: готовые консоли сами спавнят локального pi, attach к сессии нашего
  раннера в node-N не заявлен ни у одной — для ADR-041 нужен мост. Рекомендация:
  pi-web-ui, выбор за надзором/оператором.
- Рецепт оператора — в конце console-choice.md, проверен самим (стенд + профиль +
  pi-web-ui 200 + RPC-сессия).

### Проверки
```
$ bash dev/check.sh
pytest: ok · 738 passed, 3 skipped in 58.47s
check.py: ok · Every workplace is intact, files present, links resolve, boxes parse.
$ docker compose -f docker/compose.yml build node-1
Image lastorder-node:dev Built
$ docker run --rm --entrypoint sh lastorder-node:dev -c "pi --version && node --version"
1.4.0
v22.23.4
```
Один промежуточный прогон check.sh падал (test_infrastructure_restarts_on_failure:
restart-политика консольных сервисов) — исправлено (on-failure), повтор зелёный.

### Отклонения от задачи
- LLM-сессий потрачено 4 из 6 (лимит соблюдался); консольный кандидат pi-web-ui
  поднимался без LLM-хода — управление агентом из UI не проверялось (квота), клетка
  помечена в console-choice.md.

### Вне задачи
- unionclef в корне грязный (.settings/org.eclipse.buildship.core.prefs, шум IDE,
  существовал до чата) — не чинил.
- Orphan-контейнеры lastorder-harness-1/lastorder-harness-zcode-1 от старых спайков
  замечены compose-предупреждением — не трогал (вне задачи).
- Стенд после сдачи оставлен поднятым (hive/body-1/mind-1/zai-egress/node1-pi спит;
  квоту не жжёт), консольные контейнеры сняты: `bash dev/console_spike/up_spike.sh down`.

### Вопросы
- нет
