# T-024. Отчёт исполнителя

## План 1 · 2026-10-17

Шаг 0 выполнен до остального, фактами (пруны — в логах сессии и harness-spike.md):

- **ZCode без GUI в Linux — работает, но не через Electron.** ZCode 3.15.3 Linux x64 — AppImage
  (бета). Electron-часть без X-сервера падает: «Missing X server or $DISPLAY», platform failed to
  initialize; под `xvfb-run` живёт (окно 1, процесс до SIGTERM). Внутри бандла есть headless CLI
  `resources/glm/zcode.cjs` (zcode 0.16.9, требует Node >= 22 из-за `node:sqlite`): `zcode -p
  "<prompt>" --json --mode yolo [--resume sess_...]`, плюс `app-server` (ACP stdio). Проверено в
  ubuntu:24.04 без GUI: `--help`, `version`, `doctor` — ок. Итог: харнесс ZCode в контейнере
  возможен через CLI-бандл, Electron не нужен.
- **Квота ×1.5 — действующее условие, не акция.** Карточка «ZCode (1.5× Usage)» на официальной
  странице интеграций (docs.z.ai/devpack/tool/others; то же для AutoClaw). Квоты теперь в кредитах:
  Lite 2000/5 ч + 10000/нед, Pro 12000/60000, Max 28000/140000; формула — токены × множители
  (GLM-5.3: 6.9/1.7 кеш/24; Flash: 2.3/0.56/8), кредиты = сумма/10000.
- **Свой прокси (LiteLLM) планом не разрешён.** Usage Policy: «may only be used within officially
  supported tools and products»; список — ZCode, Claude Code, Codex, OpenCode, Pi, Cursor, Cline,
  TRAE, Qoder, Droid, Kilo, Roo, Crush, Goose, Eigent (+best-effort AutoClaw/OpenClaw/Hermes),
  LiteLLM отсутствует; нарушения — риск-контроль (лимиты, заморозка, бан после 3 нарушений).
  Обязательны base URL `api.z.ai/api/coding/paas/v4` (или `/api/anthropic`) и только модели
  GLM-5.3/GLM-5.3-Flash. Следствие для ADR-033: токен остаётся в `node-N`, в шлюз не уходит.

Шаги:

1. **Зависимость MCP SDK.** `uv add mcp` (modelcontextprotocol python-sdk: FastMCP + streamable
   HTTP). Файлы: `pyproject.toml`, `uv.lock`. Проверка: `uv run pytest`.
2. **`runtime/mind/__init__.py`, `runtime/mind/mcp_body.py` (новые).** Минимальный MCP тела поверх
   `Py4jBody` + `Perception` (`plain`) + `DeathDetector`: инструменты `observe`, `act` (рычаги
   `LEVERS` минус `command` и `chat`, id намерения), `stop`, `say` (без `/` и `@`, лимит длины 256
   и частоты 1/4 с, через рычаг `chat`), `wait_events(timeout_s)` (урон, смерть, чат после
   `clean_chat`, завершение намерения busy True→False; фоновый поллер ~1 Гц по `body.events()` и
   `state()`). Транспорт — streamable HTTP `0.0.0.0:${MCP_BODY_PORT:-8700}/mcp`; вход —
   `python -m runtime.mind.mcp_body`.
3. **`tests/test_mcp_body.py` (новый).** Подставное тело (интерфейс Py4jBody без py4j), клиент MCP
   streamable HTTP против uvicorn на порту 0. Тесты на каждый пункт критерия: набор инструментов;
   отказ `command` и `chat` в `act`; `say` с `/`, с `@`, длинное, частое; фильтр служебных строк
   (`[Alto Clef]`, `[Tungsten]`) и plain-имена в `observe`; `wait_events` по таймауту и по событию
   (чат, смерть hp=0 + ванильная строка).
4. **`dev/harness_spike/` (новый).** `Dockerfile` (node:22-slim + opencode + CLI-бандл ZCode из
   AppImage 3.15.3 + git/curl), `opencode.json` (модель `openrouter/qwen/qwen3.7-flash`, MCP
   `type: remote` → `http://localhost:8700/mcp`), конфиг ZCode (формат проверю на probe-контейнере:
   `~/.zcode` / проектный конфиг), `sister_prompt.py` (промпт сестры из `prompt_template.md` через
   `PersonaProvider`, пресет `lab`, черты `roll(seed)`, `Inheritance` пустая, `plain`, без праймера —
   его файлов ещё нет; + инструкция спайка: играть, говорить, вести `memory/state.md`, при смерти —
   `will.md`), `run_opencode.sh` / `run_zcode.sh` (рабочее место — копия `agent/last_order/`, сессия
   15 мин с `timeout`), `summarize.py` (ходы/мин, токены вх/вых и кеш, p50/p95 хода, память
   контейнера из `docker stats`, wait_events-статистика, цитаты реплик из логов mc-server, фрагмент
   `state.md`, отказы и странности).
5. **`dev/compose.harness-spike.yml` (новый оверлей).** `mind-1` command → MCP-сервер; сервис
   `harness` (profiles `opencode`/`zcode`) в `network_mode: service:body-1`, ключи из окружения.
   `.env.example`: имена новых переменных без значений (`ZAI_*` для токена, `SPIKE_MODEL`).
6. **Прогоны на стенде.** OpenCode 15 мин (ключ OpenRouter из окружения уже есть; лимит $1).
   ZCode 15 мин — упирается в токен Z.ai: в корневом `.env` и окружении его нет (проверил имена),
   OAuth-логин `zcode login zai` требует браузера оператора. Вопрос Q1 ниже.
7. **`docs/agents/harness-spike.md` (новый).** Итог шага 0 + сводки по харнессам + вывод
   (рекомендация для узла и Last Order, пересчёт NETWORK.md §8 на 3 узла — предложением, документ
   правит надзор).

Тесты на критерии приёмки: п.2 (MCP-сервер с инструментами) — шаг 3 по каждому ограничению; п.1
(шаг 0) — факты выше с исходными ссылками; пп.3–5 (промпт, прогоны, сводка) — артефакты прогона и
сводка; проверка `docker compose ... up -d mc-server body-1 mind-1` — шаг 6.

Риски: формат MCP-конфига headless-CLI ZCode не документирован — выясняю экспериментально на
probe-контейнере до прогона; если CLI не принимает конфиг без OAuth-логина — ZCode-прогон
невозможен без оператора, зафиксирую в отчёте.

### Вопросы

- **Q1 (токен Z.ai).** Для ZCode-прогона нужна аутентификация: в окружении и `.env` токена нет,
  `zcode login zai` — браузерный OAuth (оператор). Варианты: (а) оператор кладёт токен в `.env`
  (имя?); (б) оператор проходит OAuth внутри контейнера спайка по напечатанному URL; (в) прогон
  ZCode не делаем, ограничиваемся шагом 0 + OpenCode. Мой выбор: (б) — токен подписки и так «лежит
  в node-N» (ADR-033), OAuth в контейнере не оставляет копию в `.env` репозитория. До ответа делаю
  шаги 1–5 и OpenCode-прогон.

### Вызовы надзора

- 2026-10-17 · plan · 46f83f2 · VERDICT: revise (P1-1…P1-8; вывод до меня не дошёл из-за сломанного
  `python`-алиаса Windows в `call_supervisor.sh`, вердикт взят из `review.md` @ 368c165; повторный
  вызов не создал раунда) · cost_usd: 0.37 · effort: high
- 2026-10-17 · plan · 939c5c7 · VERDICT: revise (P2-1 блокер: отдельная сеть harness-spike без
  mc-server/hive; P2-2: /kill и аварийное завещание — на хосте; P2-3: после смерти — новый узел,
  если до 15:00 > 3 мин; P2-4: после act поллер 5 Гц 5 с; Q1: токен Z.ai — оператор) · cost_usd:
  0.59 · effort: high

План 2 дорабатывать новым планом не буду (лимит 2 планов исчерпан): находки P2-1…P2-4 учтены
непосредственно в работе, см. «Сделано» раунда 1.

## План 2 · 2026-10-17

Учтены P1-1…P1-8 ревью плана 1. Шаг 0 не меняется; его факты в `harness-spike.md` получат пометки
`[источник: URL]` / `[проверено: команда, версия]`.

1. **MCP SDK.** `uv add mcp`. Файлы: `pyproject.toml`, `uv.lock`.
2. **`runtime/mind/mcp_body.py`.** Как в плане 1, плюс:
   - **P1-2:** в `act` имена предметов (`get`, `equip`) принимаются в plain-форме, переводятся
     `normalize()`, затем raw-имя обязано матчиться `^[a-z0-9_]+$`; `count` — целое 1..64; числовые
     аргументы приводятся к числу до вызова тела; всё остальное — отказ. Тесты: `oak log` →
     `oak_log`; `dirt; @gamer`, `dirt\n@goto 0 0 0`, `minecraft:dirt 1 x` — отказ.
   - **P1-3:** `say` проверяет текст после `strip()`; отказ, если: начинается с `/`, содержит `@`
     в любом месте, содержит `\n`/`\r`/управляющие символы или `§`, длиннее 256, чаще 1/4 с.
     По `unionclef/` (корень, только чтение) проверю, ловит ли uc/Baritone чат с ведущим `#`;
     если да — `#` в начале тоже отказ. Тесты: `" /kill"`, `"привет\n/op"`, `"§kx"`, 257 символов,
     два вызова подряд.
   - **P1-4:** путь смерти: `wait_events` отдаёт смерть, затем сервер через настраиваемую паузу
     (`MCP_RESPAWN_DELAY_S`, 30–60 с) сам вызывает `body.respawn()` (uc сам не возрождает).
     `run_*.sh` после смерти ждёт `will.md` с таймаутом; нет файла — пишет аварийное завещание
     с пометкой `emergency` (по мотивам `runtime/lastorder/will.py`). Тест: hp=0 + ванильная строка
     → событие смерти → `respawn` вызван ровно один раз.
   - **P1-5:** JSONL-лог вызовов (`MCP_LOG`, по умолчанию stderr и файл): инструмент, аргументы,
     t_start/t_end, отказ и причина; `wait_events` — таймаут и что разбудило; `act` — первое
     изменение busy/позиции поллером (задержка «act → действие»). Тест: записи на вызов и на отказ.
   - **P1-8:** `tap_key`/`hold_key` — allowlist клавиш (движение w/a/s/d/space/shift, sneak,
     sprint, attack/use через мышь отдельными рычагами уже есть; без chat/console/inventory-экрана),
     `ms` 1..5000.
3. **`tests/test_mcp_body.py`.** Тесты плана 1 + новые из P1-2/P1-3/P1-4/P1-5.
4. **`dev/harness_spike/`.** Как в плане 1, плюс:
   - `run_*.sh` на 10:00 делает RCON `/kill LastOrder01`, если смерти в логе MCP нет (P1-5);
   - **P1-7:** версии фиксируются: opencode — точная версия npm, ZCode — URL AppImage + sha256;
   - **P1-6:** в контейнер харнесса передаются только нужные переменные (`OPENROUTER_API_KEY`,
     `ZAI_API_KEY`), без `env_file: .env`; рабочее место и сырые логи прогона не коммитятся,
     перед коммитами — grep на отсутствие ключей;
   - **P1-7:** прогон: через ~3 мин сверка фактической стоимости (usage из событий opencode);
     прогноз 15 мин > $0.8 — стоп и запись.
5. **`dev/compose.harness-spike.yml`.** **P1-1:** харнесс-сервис в общей сети проекта (своего
   сетевого пространства body-1 он НЕ разделяет), MCP тела — `http://body-1:8700/mcp`;
   py4j-порт uc биндится на localhost внутри ns body-1 и снаружи недостижим. В прогоне —
   socket-проверка из харнесса: `body-1:25333` отказ, `body-1:8700` ок; итог — в сводку.
6. **Прогоны.** OpenCode 15 мин (шаг 6 плана 1). ZCode: до прогона — probe-тест: принимает ли
   `zcode` CLI ключ API (переменная/конфиг) без браузерного OAuth. Затем `lo-blocked`:
   `Blocked: нужен оператор — токен Z.ai для прогона ZCode`, оператору выбор (а) ключ в `.env`
   (имя из `.env.example`) или (б) OAuth в контейнере по URL, `~/.zcode` — в именованном томе.
7. **`docs/agents/harness-spike.md`.** Как в плане 1: шаг 0 с пометками источников, сводки
   (включая доступность портов из P1-1, смерти/завещания/аварийные из P1-4, задержки из P1-5),
   вывод и пересчёт §8.
