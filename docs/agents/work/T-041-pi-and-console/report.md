
## Раунд 1 · 2026-10-26
Коммиты: 1cf6d54, 3435051, a8201e2 (restart-политика)

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

## Раунд 2 · 2026-10-26
Коммиты: f32fb0b

### Ответ на ревью
- R1-1 · fixed f32fb0b — расширение положено в `dev/console_spike/thought.ts` (обе
  команды `/thought` и `/thought2`). Дополнительно прогнан путь
  `before_agent_start` → возврат `{message:{role:"assistant",…}}` (session6,
  LLM-сессия 5 из 6): в сессию снова ложится `role: custom`, модель читает строку
  как внешнюю («they provide "Operator's thought…"»). Итог честно записан в
  console-choice.md: в Pi 1.4.0 НИ ОДИН из двух документированных путей не даёт
  «реплику самого агента»; вместо — событие `before_provider_request` (правка
  запроса к модели перед отправкой) [источник: docs/extensions.md, не прогонял:
  лимит сессий]; для «голоса извне» custom-строки достаточно, для «мысли» — нет.
- R1-2 · fixed f32fb0b (в пределах автоматизации) — сервис `console-pi-web-ui`
  теперь несёт доказательный путь: рендерит тот же agent-dir узла
  (`drop_pi_agent_dir` в `/root/.pi/agent` — [проверено: models.json/mcp.json/
  settings.json на месте, get_state → zai/glm-5.3-flash]), сидит в сетях
  slot-1 (тело) + default (hive), поднимает ключ Z.ai из материализации entrypoint
  в env процесса (без argv и файлов). До границы консоли проверено; HTTP API
  списков/отправки у pi-web-ui нет (очевидные пути 404), клик в браузере не
  автоматизировался — операторский прогон по рецепту (`up_spike.sh console`,
  http://127.0.0.1:8787), один оставшийся LLM-ход не тратил (лимит 5/6).
  Сессия, запущенная не консолью: RPC-процесс в контейнере консоли пишет в её
  session-dir, но валидный файл без LLM-хода не оставился — клетка помечена,
  проверка оператором по рецепту. Отклонение раунда 1 («цель из консоли не
  достигнута») снято документированно: готовые консоли спавнят СВОЕГО pi;
  для «оператор пишет сестре в node-1» нужен мост — формулировка в
  console-choice.md («Рекомендация») остаётся.
- R1-3 · fixed f32fb0b — `pi-web-ui@0.111.0`, `@jmfederico/pi-web@1.202609.1`;
  сеть `console-spike` (своя, egress для установки) + slot-1 (тело) + default
  (только hive: увод hive из default — правка сервиса hive, вне зоны задачи;
  комментарий в compose).
- R1-4 · fixed f32fb0b — верный sha коммита (a8201e2) в раунде 1 исправлен в
  списке ниже по факту; `pi_rpc.sh`→`pi_rpc.py` в комментарии up_spike.sh;
  piui2/piweb2 убраны из console-choice.md; лицензия Epsilondelta-ai сверена —
  MIT [проверено: LICENSE].

### Проверки
```
$ bash dev/check.sh
pytest: ok · 738 passed, 3 skipped in 68.87s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ bash dev/console_spike/up_spike.sh console   # стенд + консоль
wait_for: ok after 56s  (GET http://127.0.0.1:8787 -> 200)
```

### Отклонения от задачи
- LLM-сессий суммарно 5 из 6 (раунд 1: 4, раунд 2: 1); UI-клик оператора не
  автоматизирован (нет HTTP API) — последние клетки чек-листа ждут оператора.

### Вне задачи
- (как в раунде 1) unionclef грязный — шум IDE, не чинил. Docker Desktop за чат
  падал дважды (сбой первого вызова надзора 1073807364 и упавший к моменту ревью
  демон) — поднимал заново, стенд воспроизводился.

### Вопросы
- нет
