## План 1 · 2026-10-22

Контекст изучен: ADR-021/030/034/036, NETWORK §2/§3/§7/§8, GOALS_COMMS §1, KNOWLEDGE §4,
T-028 (LO MCP: proposals/decide/set_goals/chronicle), T-027 (адаптеры харнесса, раннер),
T-029 (шаблон сестры agent/last_order/).

### Архитектурное решение (вопрос P1)
Сессии Last Order требуют харнесс в контейнере, но образ hive (docker/mind) — только python+git,
а docker/mind/Dockerfile вне «Можно трогать». Предложение: отдельный сервис `last-order`
в compose на образе node (docker/node: opencode+zcode+runtime+agent), сети [default]
(hive:8000, llm-gateway:4000), volume `hive-work` (рабочее место) + `knowledge:ro`
(сигналы будильника: p-*.json, nodes/*/will.md). Это больше, чем «env и volume для hive»
из объёма задачи, поэтому выношу на план-ревью. Альтернатива — админ внутри сервиса hive —
требует менять Dockerfile и ставит bash агента в контейнер с RCON-клиентом.

### Шаги
1. `agent/network/` — шаблон рабочего места LO (стиль T-029, англ.): PERSONA.md (ядро, ro),
   AGENTS.md (manual: события пробуждения, инструменты MCP, права — общие инструкции правит
   сам, ядро нет), GOALS.md (цели оператора, формат), SESSION_PROMPT.md (короткая сессия),
   INBOX.md (сообщения оператора), memory/ (state.md), skills/ (decide — критерии принятия
   знаний/навыков/инструкций из KNOWLEDGE §4; goals — лестница и правила слияния из
   GOALS_COMMS §1 как инструкции, цели оператора с пометкой автора; chronicle — что и как).
2. `runtime/hive/admin.py`: AdminConfig (env ADMIN_*), WakeSources — чистые проверки по файлам
   и часам (новые will.md, очередь pending >= N, изменение INBOX.md/GOALS.md, таймер N мин),
   AdminAlarm — цикл poll → сессия; одновременно одна сессия (однопоточно), события во время
   сессии копятся (базлайн берётся на старте сессии для очереди/смертей, на конце — для файлов
   оператора, чтобы собственные записи агента не будили его); ensure_workplace — идемпотентный
   bootstrap из шаблона + рендер opencode.json (MCP /last-order/mcp + ключ LO, модель smart
   через шлюз); сессия — OpenCodeAdapter из runtime/node/adapters (переиспользование, не правка),
   таймаут, лог событий (HiveLog в workplace/.admin/), drop uid для ребёнка, чистый env ребёнка.
   Вход `python -m runtime.hive.admin`.
3. `docker/compose.yml`: сервис `last-order` (см. P1), env ADMIN_* + GATEWAY_NODE_KEY
   (=GATEWAY_KEY_HIVE, entrypoint node рендерит auth.json для opencode) + HIVE_MCP_LO_KEY
   (для opencode.json), volume hive-work.
4. `tests/test_admin_wake.py` (подставные часы): пробуждение по каждому из 4 источников;
   нет пробуждения на стартовом базлайне; события во время сессии копятся (fake spawn меняет
   состояние); одна сессия за раз; минимальная пауза между сессиями; промпт несёт сводку
   (смерти, очередь, цели, inbox); bootstrap идемпотентен; в конфиге — URL LO MCP и модель;
   таймаут сессии убивает процесс; в env ребёнка не попадают ключи.
5. Стенд: `bash docker/gateway/up.sh`, `bash docker/node/up.sh`, прогон ~30 мин, 1–3 слота;
   критерий 5: ≥1 решение, цели обновлены, запись летописи; счёт сессий и стоимость
   (dev/gateway_report.py). Отчёт.

### Риски / наблюдения (вне объёма, не чиню)
- В MCP LO (T-028) нет инструмента «ответить сестре в Мисака-нет» (ADR-036 «отвечает сёстрам»)
  и нет записи общих инструкций собственной рукой (ADR-034 «правит сам») — mcp.py вне объёма;
  инструкции задают тон и правила, канал — будущая задача. Отмечу в отчёте.
- Правила целей из GOALS_COMMS §1 (лестница, сигналы) становятся инструкциями LO (ADR-036);
  сигнал «сводка живых» (LivingStatus P3) ещё не пишется сетью — правила применяются по
  доступным данным (база, смерти), помечу в AGENTS.md.

### Проверки
uv run pytest; uv run python tools/check.py; docker compose up -d; 30-мин прогон.

## Результат · 2026-10-22 · раунд 1 (закрытие C-1: стенд на HEAD после падения хоста)

Утренний прогон (до падения хоста, ~09:00–11:00 UTC) дал 19 сессий, 4 записи летописи и цели сети,
но оборвался на двух дефектах: 401 «No api key passed in» (префикс провайдера в `--model`, фикс
9834699) и зависший в D-state ребёнок (фикс ec01fe8). Ниже — прогон на HEAD (образ пересобран
`stand_up.sh` → `docker compose up -d --build`), 14:59–15:11 UTC, все выводы — с контейнеров.

### Критерий 5: прогон с T-027
Три подачи через MCP узла (`docker exec -i lastorder-node-1-1 python - < stand_proposals.py`,
ключ узла из 0400-файла раннера): `p-0018` knowledge, `p-0019` knowledge, `p-0020` skill — все
`"status": "pending"`. Будильник проснулся на пачке, агент решил все три (`decision.by: last_order`,
15:10:03–04):
- p-0018 accept=true → k-0014, why: «Конкретный проверяемый предел — три удара вплотную —
  и условие, при котором он работает; сеть теперь знает, что ночью к нему не подходить.»;
- p-0019 accept=true → k-0015, why: «Первый рецепт еды в базе: сырая свинина и уголь дают
  жареную — конкретно, повторимо, ничего не опровергает.»;
- p-0020 accept=false, why: «Идея верна и давно стоит целью, но скрипт — заглушка: прогони
  проверку глубины на берегу и пришли то, что реально сработало.» (критерий decide: заглушка
  не принимается).
Летопись, дословно (`tail -1 base/chronicle.md`): «2026-10-22T15:10:35.878032+00:00 —
2026-10-22 (четвёртое пробуждение): умер узел 00069 — завещание подано, причина до стола не
дошла (восьмая смерть за день). Очередь: три подачи 00067 — k-0014 „ночной стон бьёт насмерть
с трёх ударов, если стоять вплотную“ принята как первая подтверждённая опасность существ;
k-0015 „жареную свинину можно готовить из сырой свинины и угля“ принята как первый рецепт еды
в базе; навык проверки глубины отклонён — скрипт оказался заглушкой, 00067 просила прогнать
его на деле. Цели без изменений, счёт причин в третьей обновлён (6 из 8 смертей без причины).»
Цели: обновлены в первой сессии дня (10:00:38, «Цели обновлены: разведка
восточного берега (оператор), осторожность у воды»); текущий `base/goals.md`:
`- text: провести разведку берега к востоку от спавна / why: 'оператор: карта почти пуста'` и
цель про воду; сессии продолжения цели не меняли (в летописи указана причина).
Сессии за прогон на HEAD: 4, все `session.done code 0` — 14:59:42 (timer+death 00063),
15:02:10 (deaths 00064-66), 15:06:11 (operator), 15:07:47 (death 00069; в ней же решены
предложения). Итого в `events.jsonl` за день 23 `session.start` (включая утро); все 4 источника
будильника живьём: death/operator/proposals/timer.
Стоимость (P1-4, отдельный ключ): `dev/gateway_report.py --since 8h` →
`last-order-10221646 $0.02060`; ключ выпущен в 14:46 UTC (`gateway_keys.py issue --alias
last-order-$(date +%m%d%H%M) --rpm 30 --hourly-usd 0.15`), т.е. вся сумма — 4 сессии HEAD-прогона.
По моделям за окно: `openrouter/z-ai/glm-5.3-flash $0.02846` (включая утренние сессии), всего
`$0.03981`. Модель — smart-уровень через шлюз (`session.done`… `z-ai/glm-5.3-flash`).

### P1-1: изоляция сети svc-lo
`docker exec lastorder-last-order-1 python -c …create_connection…`:
`mc-server:25575 gaierror: [Errno -3] Temporary failure in name resolution`;
`mc-server:25565 gaierror …`; контроль: `hive:8000 CONNECTED`, `llm-gateway:4000 CONNECTED`.
RCON и слоты недостижимы.

### P1-2: файлы оператора и uid
Владение (`ls -ln /workplace/`): `GOALS.md`, `INBOX.md`, `PERSONA.md`, `AGENTS.md`,
`SESSION_PROMPT.md`, `opencode.json` — uid 0, `-r--r--r--`; `memory/` — 1001:1001, 0777.
Записи от uid агента (`docker exec -u 1001:1001 … sh -c 'echo x >> …'`):
`cannot create /workplace/GOALS.md: Permission denied` (то же INBOX.md, PERSONA.md);
`memory-write-ok`. Правка оператора будит: `printf … >> /workplace/INBOX.md` от root (путь
оператора — `docker compose exec last-order` как root; bind-mount не заводился) →
`15:06:11 session.start reasons: [{"kind": "operator", "detail": "INBOX.md"}]` →
`15:07:17 session.done code 0`. Собственные записи агента не будят: дайджест берётся только
с операторских файлов (тест `test_admin_wake.py`).

### P1-4: ключ шлюза LO
`stand_up.sh` выпускает `GATEWAY_KEY_LO` отдельным ключом: `gateway_keys.py list` →
`last-order-10221646 0.0049 30 0.15` (rpm 30, потолок $0.15/ч). Расход отделим от hive:
в отчёте по ключам строка LO своя (выше), у hive — своя.

### Ключей провайдера в контейнере LO нет
Имена env контейнера: только `ADMIN_*, GATEWAY_NODE_KEY, GATEWAY_URL, HIVE_MCP_LO_KEY, …` —
нет `OPENROUTER_API_KEY`/`ZAI_API_KEY`. Внимание: pid 1 — это tini, он хранит исходный env
контейнера (включая `GATEWAY_NODE_KEY`, виртуальный ключ шлюза, не провайдерский); сам процесс
администратора (`pid 7 python -m runtime.hive.admin`) после scrub энтрипоинта содержит 0
переменных `GATEWAY_NODE_KEY|ZAI_API_KEY|OPENROUTER_API_KEY` (проверено по `/proc/7/environ`).
Ключ лежит: `/home/sister/.local/share/opencode/auth.json` 1001:1001 600 (своё кредо агента,
разрешено ADR-033) и `/run/node/secrets/gateway_node_key` root 0400 — uid 1001 туда
`agent-blocked-from-root-secret`. `knowledge` у LO смонтирован `:ro` (compose), запись —
только через MCP.

### Прочее из плана
P1-3: ядро root-owned (см. ls выше), переписывается из образа на каждом старте; откат подмены и
нетронутость `memory/` — тесты `test_admin_wake.py`. P1-5: раскладка — `HiveLayout` (константы
`base/proposals/p-*.json`, `nodes/*/will.md`, статус из содержимого) — тесты там же. P1-6: новых
видов вызовов LLM нет, кроме сессии smart; пауза 30 с между сессиями живьём (15:07:17→15:07:47).
P1-7: `agent/network/AGENTS.md` перечисляет доступное (MCP propose/decide/set_goals/chronicle/
send) и прямо запрещает писать базу руками.

### Проверки
`bash <root>/dev/check.sh` (в worktree): `pytest: ok · 672 passed, 2 skipped in 53.66s`;
`check.py: ok · Every workplace is intact…`. `docker compose -f docker/compose.yml up -d` —
стенд поднят, все сервисы healthy (вывод выше по ходу раунда). Стенд после проб остановлен
(`docker compose stop`, тома сохранены: knowledge, hive-work с events.jsonl и state.json).

## Раунд 2 · 2026-10-22 · сдача

### Сделано (по критериям)
1. Будильник: death / пачка N предложений / оператор / таймер, одна сессия за раз, копление
   событий на время сессии — `tests/test_admin_wake.py` (подставные часы); живьём все четыре
   вида `session.start` в `events.jsonl` за день (см. раунд 1).
2. Короткая сессия: сводка (смерти, очередь, INBOX/GOALS оператора, цели, летопись) в промпте,
   память в workplace, модель smart через шлюз — тесты + 4 сессии на HEAD, все `code 0`.
3. Инструкции: `agent/network/` — PERSONA/AGENTS (критерии принятия из KNOWLEDGE §4, лестница
   целей из GOALS_COMMS §1, права: общие инструкции — сам, ядро — нет), скилы decide/goals/chronicle.
4. Цели оператора: `GOALS.md` (root-owned) → `base/goals.md` с пометкой автора — стенд:
   `why: 'оператор: карта почти пуста'`.
5. Прогон с T-027: 3 решения (p-0018/p-0019 accept, p-0020 reject — заглушка), летопись
   15:10:35, цели сети; 4 сессии за прогон, $0.02060 на отдельном ключе (раунд 1).

### Проверки
- `bash <корень>/dev/check.sh` (повторно при сдаче): `pytest: ok · 672 passed, 2 skipped in
  45.33s`; `check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.`
- `docker compose -f docker/compose.yml up -d` — поднимался в раунде 1 (все сервисы up/healthy,
  работа описана); при сдаче повторно не поднимал: квота стенда, после проб стек остановлен
  (`docker compose stop`, тома knowledge/hive-work сохранены).
- Diff-аудит: файлов вне «Можно трогать» нет; секретов в diff нет (stand_up.sh читает корневой
  `.env` в переменные окружения по конвенции up.sh, значения не печатаются и не пишутся).

### Самопроверка
- Раундов `lo-checker`: 1. Итог: `CHECK: clean` (единственный минор — статус-коммит
  in_progress→review, закрывается этим же коммитом сдачи).

### Ответ на ревью (чекер, раунд 1)
- C-1 · fixed ecd07f7 (+e055e7e — дословные цитаты решений и летописи): в отчёте появился
  раунд результата с выводами команд по критерию 5, P1-1, P1-2, P1-4 и проверке отсутствия
  ключей провайдера в контейнере LO.
- C-2 · fixed этим же коммитом: `status: review` в `task.md` коммитится вместе с раундом
  (шаги 6–7 lo-submit).
- C-3 · fixed b4bfff6: `tests/test_gateway_config.py` внесён в «Можно трогать» `task.md`.

### Отклонения
- Повторный `docker compose up -d` при сдаче не прогонян — см. Проверки (квота).

### Вне задачи
- Не отозван осиротевший ключ шлюза LO утреннего (до падения хоста) прогона: живые алиасы
  `last-order-10221059` ($0.0002, выдан stand_up.sh) и ручные отладочные `last-order`,
  `last-order-fix-1156`, `last-order-run2-1237` (по $0.0; все с потолками rpm 30 / $0.15/ч).
  Автоотзыва нет — stand_up.sh штампует алиасы временем, чтобы не коллидировали; отзыв руками:
  `uv run python dev/gateway_keys.py revoke <token>`.
- Пробелы MCP LO из плана (нет ответа сёстрам через Мисака-нет, нет правки общих инструкций
  своей рукой) — mcp.py вне объёма задачи, канал будущей задачи.
