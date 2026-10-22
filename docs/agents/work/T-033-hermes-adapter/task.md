---
id: T-033
title: Адаптер Hermes Agent в раннере узла — Z.ai, сухой прогон
stage: E2
status: in_progress
criticality: critical
branch: z/T-033-hermes-adapter
created: 2026-10-22
---

# T-033. Адаптер Hermes Agent в раннере узла — Z.ai, сухой прогон

## Цель
Третий харнесс узла рядом с `opencode` и `zcode` (ADR-032): Hermes Agent (NousResearch,
MIT) на GLM Coding Plan. После задачи `NODE_HARNESS=hermes` поднимает узел без правок compose,
Hermes видит только MCP тела и hive и инструменты файлов/bash, а одна короткая сессия на ключе
Z.ai проходит. Прогон 15 минут на стенде — T-034.

## Контекст
- Решение пользователя 2026-10-22: протестировать Hermes с ключом Z.ai.
- ADR-032 (сменный харнесс), ADR-033 (песочница, токен Z.ai — исключение), ADR-030 (Telegram —
  только хроника), ADR-037 (`plain`, без `@gamer`).
- Адаптеры: `runtime/node/adapters.py` (`OpenCodeAdapter`, `ZCodeAdapter`, `quota_exhausted`),
  выбор — `runtime/node/config.py:18` (`HARNESSES`), `:79–91`; регистрация —
  `runtime/node/__main__.py:37–39`; конфиги харнесса — `runtime/node/workplace.py`
  (`render_zcode_config`, `render_opencode_config`, `_drop_harness_configs`).
- Образ и секреты: `docker/node/Dockerfile`, `docker/node/entrypoint.sh` (ключ Z.ai → файл в
  домашнем каталоге сестры, env очищается до `exec`).
- compose уже передаёт `HARNESS` из `NODE_HARNESS` и `ZAI_API_KEY` в `node-1..3`
  (`docker/compose.yml:84`, `:178`) — compose не трогать.
- Условия плана: только официальные инструменты (Hermes Agent в списке, best-effort), base URL
  `https://api.z.ai/api/coding/paas/v4`, модели только GLM-5.3 / GLM-5.3-Flash, без прокси
  [источник: docs/agents/harness-spike.md, шаг 0].
- Hermes [источник: репозиторий NousResearch/hermes-agent, website/docs, 2026-10-22; не проверено
  прогоном]:
  - релиз v0.22.4 (2026-10-21); на PyPI отстаёт (0.19.0) — ставить с git-тега; апстрим
    поддерживает Python 3.14;
  - провайдер `zai`, ключ `GLM_API_KEY`, `GLM_BASE_URL`; без него Hermes сам перебирает
    endpoint'ы — нам нужен явный coding-endpoint;
  - headless: `hermes chat --query-file <f> --oneshot --format stream-json --resume <id>`,
    `--toolsets <csv>`, `--yolo`; минимальный раннер `hermes-agent --max-turns …`;
  - MCP — `mcp_servers.<name>.url` в `config.yaml` (HTTP и stdio);
  - домашний каталог — `HERMES_HOME` (config.yaml, .env, SOUL.md, memories, skills, state.db);
  - встроенная память `memory.memory_enabled`, `user_profile_enabled`; вспомогательные вызовы
    LLM (сжатие контекста, саммари сессий, напоминания о памяти) — отдельные настройки;
  - файлы контекста: грузится только один тип, `.hermes.md`/`HERMES.md` перекрывают `AGENTS.md`.

## Объём
Можно трогать:
- `runtime/node/adapters.py` — `HermesAdapter`
- `runtime/node/config.py` — `hermes` в `HARNESSES`, `HERMES_MODEL` (по умолчанию значение
  `ZCODE_MODEL`)
- `runtime/node/__main__.py` — регистрация адаптера
- `runtime/node/workplace.py` — `render_hermes_config` и раскладка `HERMES_HOME`
- `docker/node/Dockerfile` — установка Hermes с закреплённым тегом в отдельный venv
- `docker/node/entrypoint.sh` — ключ Z.ai в `.env` Hermes сестры (как для zcode)
- `tests/test_node_runner.py` — тесты адаптера и рендера конфига
- `docs/agents/harness-spike.md` — раздел «Hermes: установка и сухой прогон»
- файлы задачи

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`, `.env`
- `docker/compose.yml` (его правит T-030), `runtime/hive/`, `runtime/mind/`, `agent/`
- поведение `opencode` и `zcode` адаптеров — только общий код, если без этого никак, с причиной
  в отчёте

## Критерии приёмки
- [ ] Hermes в образе `node`: версия закреплена (тег или sha), установлен в отдельный venv/uv tool
  с нужным ему Python и не ломает `/app/.venv` раннера; `hermes --version` в `docker build`.
  Обновления Hermes в рантайме выключены (если есть настройка — указать её).
- [ ] `HermesAdapter` (`name = "hermes"`): `life_turn`, `will_session`, `session_id` по контракту
  двух других. Промпт передаётся файлом (`--query-file`), не аргументом. Возобновление — по id из
  stream-json. `quota_exhausted` срабатывает на ошибки Hermes 429/1302 — тест на образце вывода,
  снятом в сухом прогоне (образец без ключей — в `tests/`).
- [ ] `render_hermes_config` даёт `config.yaml` с:
  - провайдером `zai`, моделью из `HERMES_MODEL`, `GLM_BASE_URL` =
    `https://api.z.ai/api/coding/paas/v4`; вспомогательные вызовы — на тот же провайдер и модель
    (никаких OpenRouter/Nous Portal по умолчанию);
  - MCP тела слота и hive (те же URL и ключ узла, что у `render_zcode_config`);
  - набором инструментов: файлы, bash и MCP; веб, браузер, генерация картинок, TTS, отправка
    в мессенджеры, cron, делегирование — выключены;
  - встроенной памятью и профилем пользователя — выключены; авто-создание скилов — выключено
    (память и скилы живут в рабочем месте aw, сравнение с T-024 на равных);
  - пустым или нейтральным `SOUL.md`: личность Hermes по умолчанию не должна ложиться поверх
    сестры. Что Hermes всё равно добавляет в системный промпт («You are Hermes…» и т. п.) —
    выписать в отчёт дословно.
  Тест на рендер: нет ключей в файле, нет запрещённых наборов, URL MCP верные.
- [ ] `HERMES_HOME` — в домашнем каталоге сестры (tmpfs), не в рабочем месте; `GLM_API_KEY`
  пишет `entrypoint.sh` в `.env` этого каталога с правами 0600 и владельцем `sister`; в env раннера
  и детей ключа нет (как для zcode). Файлы контекста: Hermes читает `AGENTS.md` рабочего места;
  проверить, что в рабочем месте нет `.hermes.md` и других файлов, которые его перекроют.
- [ ] Сухой прогон в контейнере `node` без стенда, итог — в отчёт и раздел `harness-spike.md`:
  - `hermes --version`, `hermes doctor` (или аналог) — вывод без ключей;
  - список инструментов, которые видит агент (`hermes-agent --list-tools` или аналог) с
    отрендеренным конфигом — только разрешённые;
  - одна oneshot-сессия на ключе Z.ai («ответь одним словом») через coding-endpoint: прошла или
    нет, сколько вызовов LLM ушло на неё всего (включая вспомогательные), сколько квоты;
  - память контейнера в простое и во время сессии.
- [ ] `uv run pytest` и `uv run python tools/check.py` проходят.

## Проверки
```
uv run pytest
uv run python tools/check.py
docker build -f docker/node/Dockerfile -t ai-char-node:hermes .
```

## Ограничения
- Ключей в коде, логах, тестах и отчёте нет; после сухого прогона — grep артефактов на ключ.
- Токен Z.ai — только в `node-N` (ADR-033), не в шлюз и не через прокси (условия плана).
- Квота Z.ai общая с ZCode-воркером: на сухой прогон — не больше нескольких коротких сессий;
  потраченное — в отчёт.
- Telegram, gateway и cron Hermes не включать (ADR-030). Сырых команд uc и `@gamer` в
  инструментах нет (ADR-037) — их и так нет в MCP тела, Hermes не должен добавлять своих обходов.
- Поведение сестры не чинится кодом адаптера (ADR-032): адаптер только запускает харнесс.
- Hermes ставится из сети апстрима: закреплённая версия, без `curl | bash` установщика.

## Заметки надзора
- 2026-10-22 · поставлена по просьбе пользователя (протестировать Hermes с ключами Z.ai).
  Решение надзора: встроенная память и скилы Hermes в спайке выключены, чтобы сравнивать с
  T-024 на равных. Стоит ли их включать в рабочем месте — вывод T-034.
