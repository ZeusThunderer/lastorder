---
id: T-041
title: Pi в образе узла на Z.ai Coding Plan и выбор готовой веб-консоли
stage: I
status: changes
criticality: critical
after: []
branch: z/T-041-pi-and-console
created: 2026-10-26
---

# T-041. Pi в образе узла на Z.ai Coding Plan и выбор готовой веб-консоли

## Цель
В образе `lastorder-node` есть Pi; в `node-1` агент Pi отвечает через Z.ai Coding Plan и видит
инструменты MCP тела и hive. Готовые веб-консоли для Pi проверены по одному чек-листу на стенде,
выбрана одна, и оператор может написать в ней тестовому агенту в `node-1`.

## Контекст
- ADR-041: харнесс Pi, консоль — готовое решение, два режима ввода (голос, мысль).
- Pi: `@earendil-works/pi-coding-agent` (раньше `@mariozechner/pi-coding-agent`); RPC
  `pi --mode rpc` (JSONL по stdio): `prompt`, `steer`, `follow_up`; расширения — TypeScript,
  `pi.registerTool`, события расширений [источник: pi.dev, docs/rpc.md, docs/extensions.md].
- Z.ai Coding Plan в Pi — через `~/.pi/agent/models.json`: провайдер `zai`,
  `api: openai-completions`, `compat: {supportsDeveloperRole: false, thinkingFormat: "zai"}`,
  base URL `https://api.z.ai/api/coding/paas/v4` [предположение: путь проверить], ключ —
  `"$ZAI_API_KEY"` (с `$`, иначе строка берётся буквально) [источник: aiengineerguide.com,
  docs/models.md]. Путь к api.z.ai в песочнице уже есть: `zai-egress`
  (`docker/node/zai-egress.conf`); ключ рендерит `docker/node/entrypoint.sh:33`.
- MCP в Pi: по одним источникам встроен с 1.0 (`pi mcp add`), по другим — расширение
  `pi-mcp-adapter` (`~/.pi/agent/mcp.json`). Проверить на собранной версии.
- Образ: `docker/node/Dockerfile` (Node 22 уже есть для opencode/zcode; версии пинятся `ARG`).
- Кандидаты консоли (готовые проекты, своё не писать):
  - HAPI — `github.com/tiann/hapi`: `hapi pi` гоняет Pi по RPC, hub (HTTP + Socket.IO, SQLite) +
    PWA/веб; у Pi «только удалённый ввод», права — «нет (всё одобрено)»;
  - `github.com/jmfederico/pi-web` — сессии живут без браузера, удалённые машины («fleet»),
    плагины, MIT, своей авторизации нет;
  - `github.com/Epsilondelta-ai/pi-web` — Go + Astro одним бинарником, steer/cancel на ходу;
  - `pi-web-ui` — `pi.dev/packages/pi-web-ui`.

## Объём
Можно трогать:
- `docker/node/Dockerfile` — слой Pi (пин версии, без curl|bash)
- `runtime/node/workplace.py` — отрисовка конфига Pi (`models.json`, MCP) по образцу
  `render_hermes_config`; `runtime/node/config.py` — при нужде
- `docker/compose.yml` — только сервис-кандидат консоли под профилем `console-spike`
- `dev/console_spike/` — скрипты прогона кандидатов
- `docs/agents/console-choice.md` — новый, итог выбора
- тесты отрисовки конфига харнесса (где сейчас тесты `render_hermes_config`)
- файлы задачи

Нельзя трогать:
- `unionclef/`, `NetTyan/`, `task1.md`
- `runtime/node/adapters.py`, `runtime/node/runner.py` — адаптер Pi делает T-045
- `runtime/hive/` — T-042, T-043
- `.env*` (только чтение имён ключей)

## Критерии приёмки
- [ ] `docker compose build node-1` проходит; `pi --version` в контейнере печатает пин.
- [ ] Сессия Pi в `node-1` от uid `sister` отвечает через Z.ai Coding Plan: команда, вывод и
  модель — в отчёте. Ключ Z.ai не попадает ни в файлы рабочего места, ни в вывод (grep по
  префиксу ключа — 0).
- [ ] В той же сессии Pi видит инструменты `body` и `hive` и один раз вызывает `observe` — вывод
  в отчёте. Способ подключения MCP (встроенный или адаптер) записан.
- [ ] На стенде проверено, работают ли `steer` и `follow_up` в RPC во время вызова инструмента —
  вывод в отчёте.
- [ ] Проверено, может ли расширение Pi вставить текст в историю как реплику самого агента перед
  следующим вызовом LLM (основа режима «мысль», ADR-041): какое событие или API, пример — в
  отчёте. Если нельзя — что можно вместо.
- [ ] `docs/agents/console-choice.md`: таблица 4 кандидатов по чек-листу — несколько агентов из
  разных контейнеров на одной странице; сессия, запущенная не консолью (нашим процессом), видна
  и принимает ввод; ввод во время работы агента; команды расширений Pi доступны (для
  `/thought`); диалоги подтверждения расширения (для грантов); работа в закрытой docker-сети без
  интернета; авторизация; лицензия; активность проекта. Каждая клетка — `[проверено: …]` или
  `[источник: …]`. Минимум два кандидата прогнаны на стенде, остальные — по документации с
  пометкой. Рекомендация одной строкой.
- [ ] В отчёте — рецепт для оператора «поднять консоль-кандидат и написать агенту в node-1»
  (команды и URL), проверенный самим исполнителем.

## Проверки
```
bash dev/check.sh
docker compose -f docker/compose.yml build node-1
```
Стенд: `mc-server body-1 mind-1 node-1 hive zai-egress` (+ сервис кандидата). Сессий Pi — не
больше 6 коротких: квота Z.ai общая с ZCode. Ожидание — через `dev/wait_for.sh`.

## Ограничения
- ADR-033: в песочнице нет ключей провайдеров, кроме токена Z.ai; новых выходов наружу из
  `node-N` не открывать (кандидаты консоли ставятся при сборке образа или в отдельном сервисе).
- Пути bind-mount разрешает демон, а не шелл.
- Сырых команд uc и команд сервера в инструментах нет.
- Выбор консоли — за надзором и оператором; задача даёт данные и рекомендацию.

## Заметки надзора
