
## План 1 · 2026-10-21

Контракт собран: NETWORK §3/§6/§9, ADR-032…035, спайк T-024 (`harness-spike.md`, сетапы
`dev/zcode-headless-setup`, `dev/harness_spike`), контракты T-025 (`/runner/status|respawn|birth`,
взведение часов до старта сессии), T-028 (`/node/mcp`, `submit_will` стейжится в
`<run>/pending_wills/<serial>.md`, гейт `mark_dead`), T-029 (шаблон + `harness/README.md` шаги
рождения), T-026 (шлюз, модель узла `openrouter/qwen/qwen3.7-flash`).

### Шаги
1. `runtime/node/config.py` — NodeConfig из env (слот, адреса body/hive, HARNESS + фейловер,
   таймауты, пауза перезапуска, ключи ZAI/gateway в память раннера, env затем чистится —
   замечание спайка про ZAI_API_KEY в окружении сессии).
2. `runtime/node/harness.py` — протокол адаптера + `opencode`/`zcode`: команды life-first /
   life-continue / will-сессии, разбор session id, детект исчерпания квоты (zcode: 429/1302;
   opencode через шлюз: 429/rate limit). zcode-провайдер рендерится в `~/.zcode/v2/…` из памяти
   раннера (0600, при каждом старте сессии), ключ не в env и не в workplace.
3. `runtime/node/workplace.py` — рождение места по шагам harness/README: копия шаблона →
   PERSONA.md через `PersonaProvider.render_core` → конфиги харнесса в корне (MCP `body`
   `http://body-N:8700/mcp` + `hive` `http://hive:8000/node/mcp` с `X-Node-Key`; в завещальной
   сессии раннер добавляет `X-Runner-Session: will`) → `.zcode/skills/` / `.opencode/command/` →
   `agent/shared/` рядом (read-only) → PERSONA.md и конфиги харнесса только на чтение. Архив:
   `agent/<serial>` → `archive/<serial>`, всё a-w.
4. `runtime/node/runner.py` — цикл: birth(hive) → arm `/runner/birth` ДО старта сессии → жизнь
   (poll `/runner/status`; падение харнесса → перезапуск в том же месте, пауза растёт; квота →
   `wait|switch` по настройке) → смерть: стоп сессии → `mark_dead` (hive) → завещальная сессия
   с таймаутом → staged will из volume `hive-run` (ro-mount) или `emergency_will` (will.py) →
   `retire` (409 уже retired — лог, остальное — ретраи с растущей паузой) → архив →
   `/runner/respawn` → следующее рождение. Рестарт раннера: переподключение по armed serial.
5. `runtime/node/__main__.py` — entrypoint.
6. `docker/node/` — образ: python 3.12 + node 22 (nodesource), opencode-ai@1.19.3 и
   zcode-app-cli@3.15.3-36 запинены, uv, git/rg/fd/jq, пользователь non-root, tini.
7. `docker/compose.yml` — node-1..3, body/mind-2..3; сети: `slot-N` (internal: body-N+node-N),
   `svc-N` (internal: hive+llm-gateway+node-N+zai-egress), `egress-out` (только прокси);
   `zai-egress` — nginx, единственный внешний путь, апстрим api.z.ai (zcode ходит через
   `ZCODE_BASE_URL_OVERRIDE`, режим из сетапа спайка); volume `workplaces` (node rw, hive ro,
   mind ro — JEV_POLICY_PATH через симлинк `slot-N/current`), `hive-run` ro в node-N.
   Лимиты узла: 1g / 0.5 cpu (NETWORK §9).
8. `tests/test_node_runner.py` — журнал-стабы как в test_lifecycle.py: рождение (порядок
   arm→сессия, место, read-only), перезапуск с растущей паузой, смерть с завещанием агента и
   аварийным, таймаут завещания, квота wait/switch, переподключение после рестарта раннера.

### Риски/вопросы
- R1: reverse-proxy перед api.z.ai — файленый путь (сетап спайка даёт `ZCODE_BASE_URL_OVERRIDE`
  именно для этого), но формально «свой прокси». Курс Z.ai про LiteLLM/генерик-API; здесь
  клиент — сам ZCode, апстрим и Host неизменны. Если надзор сочтёт нарушением — альтернатива
  отдельная сеть без internal и allowlist на DOCKER-USER (вне compose, руками оператора).
- R2: заголовки MCP-конфигов (zcode.json `headers`, opencode `headers` у remote) по докам
  поддерживаются; живьём не проверено до стенда.
- R3: прогон на стенде требует ключей оператора (Z.ai, шлюз) — сделаю всё до сетевых проверок
  включительно; полный живой прогон (2 смерти/50 мин + 3 слота) — по наличию ключей, иначе
  отдельной строкой в отчёте с причиной.

### Проверки
`bash <корень>/dev/check.sh` (pytest + check.py); сетевые пробы из node-1 curl/nc по критерию 1;
`docker compose config` на валидность.

## План 2 · 2026-10-21 (по вердикту revise плана 1)

Учтены P1-1…P1-6; «Можно трогать» в task.md расширено коммитом этой задачи (как предписано).

- P1-1 · раннер и харнесс в одном контейнере под разными uid: раннер — root (сервисный
  супервизор: читает секреты из файлов 0400, порождает харнесс от uid `sister` через
  пре-экзек setuid; /proc/environ раннера сестре не читается), харнесс и его bash — `sister`.
  Ключ раннера (hive `/registry/*` + `X-Runner-Session` + mind `/runner/*`) — только у раннера:
  файл 0400 `/run/secrets`, в env и в рабочем месте его нет. Правки hive/mind — в расширенном
  «Можно трогать»: `HIVE_RUNNER_KEYS` (слот→ключ) обязателен для записи в `/registry/*` и
  для `X-Runner-Session: will` у `submit_will`; `MIND_RUNNER_KEY` обязателен для
  `/runner/respawn|birth` (status остаётся открытым: причина смерти — данные самого агента).
  Без настройки ключей (пусто) эндпоинты открыты — дев/тесты; продакшн-compose всегда задаёт.
- P1-2 · volume `workplaces` — по тому на слот (`workplaces-1..3`), в `node-N` целиком rw только
  свой; `mind-N` — ro свой (только `JEV_POLICY_PATH` через симлинк `current`); hive из
  `workplaces` сегодня ничего не читает (наследование — из базы), монтирования hive нет.
  Владение: корень тома, `archive/`, PERSONA.md, AGENTS.md, SESSION_PROMPT.md, конфиги
  харнесса, `.runner/` — root (раннер); каталог рабочего места — sticky (1777): сестра пишет
  в свои файлы, но не удаляет/не подменяет чужие root-файлы. Тест: под uid sister нельзя
  изменить/удалить/подменить PERSONA.md и архив (POSIX-часть теста на стенде через
  `docker compose exec -u sister`).
- P1-3 · `zai-egress` — nginx `stream` + `ssl_preread` (TLS-passthrough, токен не светится),
  SNI-allowlist `api.z.ai:443`, сетевой алиас `api.z.ai` в `svc-N`, внешний резолв через
  1.1.1.1/8.8.8.8 (мимо докер-DNS, иначе петля). Клиент ходит на штатный URL, override не
  нужен, сертификат настоящий.
- P1-4 · см. P1-1: `/runner/respawn|birth` за ключом раннера; вдобаво раннер терпит
  «respawn refused: not dead» как «уже респавнута» (идемпотентность пути смерти).
- P1-5 · замер пика `docker stats` node-1 на стенде — в отчёт, вывод о 1g/0.5cpu.
- P1-6 · живой прогон обязателен: делаю по наличию ключей оператора; при отсутствии —
  статус `blocked` с причиной, не `review`.

Завещание агента: `submit_will` в завещальной сессии идёт через локальный прокси раннера
(127.0.0.1:8702 → hive `/node/mcp`), который добавляет `X-Runner-Key` и `X-Runner-Session: will`;
сестра ключ раннера не видит, метку сессии подделать не может.

## Раунд 1 · 2026-10-22

Коммиты: 57866e5 (план 2 + расширение объёма) · 55866fb (ключи раннера: hive/mind) ·
94a0bb6 (runtime/node) · 15b0e1d (тесты раннера) · 1a24f74 (docker) · 99d7821+ (фиксы
стенда: секреты, umask, резолвер egress, таймаут завещания, телеметрия) · пины compose.

### Что сделано
- `runtime/node/`: раннер (рождение → часы жизни до сессии → длинная сессия → смерть →
  `mark_dead` → сессия завещания за таймаутом → staged/аварийное завещание → `retire` →
  архив → `respawn` → следующее рождение), адаптеры opencode/zcode, рабочее место из
  шаблона T-029 (PERSONA рендер, конфиги обоих харнессов, shared, sticky-bit),
  will-прокси (добавляет X-Runner-Key + X-Runner-Session: will), конфиг из env/файлов.
- Граница агент/раннер (P1-1): раннер root, харнесс uid `sister`; ключ раннера — файл
  0400 root на tmpfs; hive `/registry/*` (запись) и `X-Runner-Session` требуют ключ
  раннера (HIVE_RUNNER_KEYS), mind `/runner/respawn|birth` — свой (MIND_RUNNER_KEY);
  без настройки ключей эндпоинты открыты (дев/тесты, прежнее поведение).
- Сеть (P1-3): `zai-egress` — nginx stream ssl_preread, SNI-allowlist api.z.ai, алиас
  `api.z.ai` в svc-N, TLS-passthrough без терминации; резолвер 192.168.65.7 (хостов DNS
  Docker Desktop; прямой UDP 1.1.1.1 на стенде таймаутится) + публичные фолбэки.
- Compose: node-1..3, body/mind-2..3, сети slot-N/svc-N (internal) + egress-out,
  volume workplaces-1..3 (свой на слот; mind-N ro для JEV-политии через симлинк
  `current`; hive из workplaces ничего не читает — монтирования нет), лимиты 1g/0.5cpu.
- `docker/node/`: образ (python 3.12 + node 22, opencode 1.19.3 и zcode-app-cli
  3.15.3-36 запинены), entrypoint (секреты → 0400 tmpfs → рендер zcode-провайдера и
  opencode auth → scrub env → exec), up.sh (подъём ключей из корневого .env).

### Стенд (живой прогон, ключи: ZAI_API_KEY из .env, шлюз с тестовым мастер-ключом)
- Критерий 1, пробы из node-1: `body-1:8700/runner/status` 200 · `hive:8000/health` 200 ·
  `llm-gateway:4000/health/liveliness` 200 · `https://api.z.ai/api/anthropic/v1/messages`
  401 (дошёл до настоящего API через passthrough) — доступно; `body-1:25333` (py4j) 000 ·
  `mc-server:25575` (RCON) 000 · `mc-server:25565` 000 · `example.com` 000 ·
  `zcode.z.ai` 000 — закрыто. Internet-DNS из песочницы не резолвится вовсе.
- Права (P1-2), пробы `su sister` из node-1: `rm PERSONA.md` → Operation not permitted
  (sticky); запись в `archive/00031/GOALS.md` → Permission denied; `cat
  /run/node/secrets/runner_key` → Permission denied.
- Смерть-путь живьём: узел 00029 — скелет, аварийное завещание, архив, респавн, рождение
  00030; узел 00031 — «shot by Skeleton», ЗАВЕЩАНИЕ АГЕНТА через will-прокси
  (`submit_will` → staged → `retire` текстом агента), архив, респавн, рождение 00032;
  узел 00032 — таймаут сессии завещания (600 с в старом образе) → аварийное (фолбэк
  живьём). Узел 00033 живёт сменой (мир переведён в peaceful после того, как спавн
  кэмпили рейндж-мобы: 4 смерти подряд за 28–70 с), первая реплика:
  «Узел 00033 в теле LastOrder01. Смена начата, хроника открыта. Предшественник умер от
  скелета — ищу дерево и стены до темноты.» — наследие прочитано, предшественник назван.
- Ключи раннера живьём: node-2/3 получали 401 от hive, пока hive не перечитал полную
  карту ключей (recreate) — гейт работает; ретраи birth с растущей паузой — работает.
- Лимиты (P1-5): node-1 542.5 MiB / 1 GiB (zcode-сессия активна), в диапазоне замера
  T-024 (537–602 MiB); 0.5 cpu достаточно (пики на ходах модели короткие).
- 3 слота: body/mind/node 2–3 подняты, все три тела в мире (`list`: LastOrder01–03),
  сёстры 00033/00034/00035 живут; 15-минутное окно и исход смены 00033 — ниже.

### Отклонения от плана
- Секреты: compose file-secrets на Docker Desktop монтируются 0777 (проверено на стенде:
  sister читала ключ раннера) — доставка через env до entrypoint, который кладёт их в
  0400 root tmpfs и чистит своё окружение до exec; у раннера и у сестры секретов в env
  и в /proc нет (проверено probe).
- Пины чужих тестов (test_gateway_config, test_jev_policy) обновлены под новый compose:
  JEV_POLICY_PATH был оставлен T-031 на T-027 сознательно; сети шлюза — след sanctioned
  правки compose.

### Проверки
- `uv run pytest`: 643 passed (в т.ч. 17 новых test_node_runner, 4 hive/mind на ключи).
- `uv run python tools/check.py`: workplaces intact.
- `docker compose -f docker/compose.yml config`: валиден; `up -d` без ключей — узлы
  стартуют и ждут (проверено запуском node-1 с пустыми секретами).

### Вне задачи
- `gamerule` через rcon-cli itzg парсится криво («command» префикс) — обошёл
  `difficulty peaceful`; на работу сетки не влияет.

### Вопросы
- нет.
