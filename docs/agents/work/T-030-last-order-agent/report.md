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
