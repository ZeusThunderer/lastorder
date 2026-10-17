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

- 2026-10-17 · plan · pending
