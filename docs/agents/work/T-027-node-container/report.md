
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
