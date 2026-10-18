# T-028 · отчёт исполнителя

## План 1 · 2026-10-18

Формат MCP — как в T-024 (`mcp.server.mcpserver.MCPServer`, stateless streamable HTTP), врезанный
в существующее FastAPI-приложение hive: тот же порт 8000, compose не трогается.

### Шаги

1. **`runtime/knowledge/schema.py`** — решение Last Order на записи (NETWORK §7 «решения Last
   Order двигают статусы»):
   - `Record.decision: {by, accept, why, ts} | None` — новый optional-атрибут, валидатор по
     образцу `Evidence`, `by` — строка (`"last_order"`), обратная совместимость: поле опционально;
   - `status_of(..., decision=None)`: primer → confirmed; decision → confirmed|retired (решение
     авторитетно, ADR-036); ниже — прежние правила §4 без изменений (для записей без решения
     пороги §4 остаются нижней границей, шапка KNOWLEDGE.md v2).
2. **`runtime/knowledge/store.py`** — два публичных метода:
   - `apply_decision(record_id, *, by, accept, why)` — ставит decision, пересчитывает статус,
     один коммит; отказы: unknown record, primer, повторное решение;
   - `commit_layer(message, files)` — обёртка `_locked + _commit_op` для собственных слоёв hive
     (очередь предложений, летопись, общие инструкции, общие навыки, цели): базу по-прежнему
     пишет только hive, серийный номер записи выдаёт только store.
   - Это выходит за буквальное «только kind: skill и статусы» из «Можно трогать» — вынесено
     в план для решения надзора. Альтернатива (второй git-писатель в proposals.py) хуже: свой
     контур блокировок и коммитов рядом с store.
3. **`runtime/hive/proposals.py`** (новый) — очередь предложений и общие слои:
   - схема `ProposalFile`: id `p-NNNN`, kind `knowledge|skill|instructions`, author (serial),
     payload, ts, status `pending|accepted|rejected`, decision `{by, why, ts}` у решённых;
     файлы `base/proposals/p-NNNN.json` в volume knowledge под git;
   - `submit`: знание → `KnowledgeCandidate` (danger|fact|recipe|term) → `store.submit_candidate`
     (дубль — evidence, не новая запись, dedup.py как есть); навык → description (≤ 280, без
     сырых id) + script (≤ 16 KB); инструкции → unified diff только на `base/instructions.md`
     (структура проверяется при подаче);
   - `decide`: knowledge → `store.apply_decision`; skill accept → запись `Record(kind=skill,
     claim=description, status=confirmed, decision)` + файл скрипта `base/skills/<record id>`;
     instructions accept → применить дифф к `base/instructions.md` (не лёг — отказ, предложение
     остаётся pending); решение пишется в proposal-файл с автором и причиной; повторное decide —
     отказ;
   - `CommonLayers.set_goals(goals)` — `base/goals.md` (формат `_read_goals`, лимиты
     goals_schema: ≤ 3 целей, текст ≤ 120, why ≤ 160); `append_chronicle(text)` — летопись
     `base/chronicle.md`, одна запись — один коммит. Оба через `commit_layer`.
4. **`runtime/hive/mcp.py`** (новый) — два сервера:
   - сестра (`/node/mcp`): `knowledge(about, query)`, `inheritance()`, `propose(kind, payload)`,
     `vote(entry_id, accept, why)`, `send(to, text)`, `inbox()`, `submit_will(text)`;
   - Last Order (`/last-order/mcp`): `proposals()`, `decide(proposal_id, accept, why)`,
     `set_goals(goals)`, `chronicle(text)`; у сестры этих инструментов нет вообще (отдельный
     сервер, не отказ в рантайме);
   - идентификация: виртуальный ключ (заголовок `X-Node-Key`, карта из env
     `HIVE_MCP_NODE_KEYS`, формат `7=sk-a;8=sk-b`) или заголовок раннера `X-Runner-Node:
     <serial>`; Last Order — ключ env `HIVE_MCP_LO_KEY`; без ключа, с чужим/неизвестным номером
     или номером умершей — отказ; заголовки читаются из `ctx.request_context.request.headers`;
   - `submit_will`: только при заголовке `X-Runner-Session: will` (сессию завещания открывает
     раннер после смерти), `Will.from_markdown` + совпадение serial во frontmatter с вызывающим,
     staging в `run/pending_wills/<serial>.md`, один раз; `retire` не меняю (интеграция с
     раннером — T-027, отмечу в отчёте);
   - `send`: `to` — номер живой сестры или `all` (веером всем живым, кроме себя); длина
     ≤ `MSG_MAX_CHARS` (160) — отказ ToolError, CommsBook остаётся страховкой;
   - `knowledge(query)` — подстрочный поиск по claim живых записей (все слова, без регистра),
     BM25 после ~300 записей (KNOWLEDGE §11).
5. **`runtime/hive/app.py`** — подключение: `build_mcp(...)` в `create_app`, mount `"/node"` и
   `"/last-order"`; lifespan подприложений (session managers SDK) запускается в существующем
   lifespan через `AsyncExitStack` + `router.lifespan_context` — монтированные Starlette-приложения
   не получают lifespan автоматически.
6. **Тесты**: `tests/test_proposals.py` — три вида propose, дубль→evidence, decide по видам
   (в т.ч. дифф применяется, ядро/чужой путь — отказ, повтор — отказ), set_goals/chronicle;
   `tests/test_hive_mcp.py` — набор инструментов сестры, набор LO и их отсутствие у сестры,
   идентификация (ключ, заголовок раннера, чужой номер, без идентичности), submit_will (не в
   сессии завещания — отказ, чужой serial — отказ, повтор — отказ), send (лимит, all),
   `knowledge` срез; приёмочный прогон: два подставных клиента MCP — предложение одной,
   решение Last Order, срез у другой. MCP гоняется через настоящий streamable HTTP (uvicorn на
   порту 0), как test_mcp_body.py.

### Проверки
`uv run pytest`; `uv run python tools/check.py`; `docker compose -f docker/compose.yml up -d hive`
(стенд; env `HIVE_MCP_*` по умолчанию пустые — MCP поднимется, ключи задаются при разводке T-027).

### Риски
- Монтирование MCP в FastAPI: lifespan подприложений — см. шаг 5, проверяется тестом через
  реальный HTTP-транспорт.
- Заголовки внутри инструмента: SDK кладёт starlette Request в `ctx.request_context.request`
  (runner.py `_make_context` + streamable_http metadata); если путь изменится — тест упадёт.
- `commit_layer` расширяет публичную поверхность store — ожидает решения надзора (шаг 2).

## План 2 · 2026-10-18

Правки по ревью Плана 1 (P1-1…P1-6), остальное из Плана 1 без изменений.

1. **Идентификация (P1-1, P1-2)** — только по виртуальному ключу: `HIVE_MCP_NODE_KEYS` вида
   `LastOrder01=sk-a;LastOrder02=sk-b` — ключ → **слот**; номер — текущий живой узел слота из
   `Lifecycle.alive()` (несколько живых в слоте — новейший; умерший/lost ключом недоступен).
   Рождение нового номера в слоте — тот же ключ даёт новый номер (тест). Заголовок
   `X-Runner-Node`, если есть, — только сверка с номером из ключа: не совпал или нет ключа —
   отказ (тесты: заголовок без ключа, чужой номер при валидном ключе). Last Order — ключ
   `HIVE_MCP_LO_KEY`; ключи сестёр на сервере LO и наоборот — отказ.
2. **`submit_will` (P1-3)** — в `runtime/hive/lifecycle.py` добавляю `mark_dead(serial)`
   (отметка «тело умерло» от сигнала смерти; разводка вызова от `mind-N`/раннера — T-027,
   сейчас — метод-заглушка с тестом) и чтение отметки; `kill_confirmed` тоже значит «мёртв».
   Условия `submit_will`: узел родился и не retired, есть отметка смерти, заголовок
   `X-Runner-Session: will`, `Will.from_markdown`, serial во frontmatter совпадает с
   вызывающим, staging ещё не было. Живой узел с заголовком `will` — отказ (тест). Правка
   lifecycle.py — по прямому указанию ревью (расширение «Можно трогать»), пересечений с T-031
   нет.
3. **Коммиты решений (P1-4)** — сообщение коммита несёт автора и причину:
   `decide p-NNNN accept by last_order: <why>; proposer 00007`; тест читает `git log` стора.
   `apply_decision`/`write_skill` стора принимают `extra_files`/`message`, чтобы proposal-файл
   ехал тем же коммитом.
4. **Инструкции (P1-5)** — в отчёте зафиксирую: доставку `base/instructions.md` в рабочее место
   сестры делают T-027/T-029; ядро и `agent/*` вне `base/` — отказ по пути (тест остаётся).
5. **`vote` (P1-6)** — обёртка над `HiveService.submit_vote`(`Vote`), без новой логики; голос за
   своё — как решает текущий store.
6. **`commit_layer`** — принимает пути только внутри `base/`; путь вне — отказ, тест (условие
   ревью из решения по шагу 2 Плана 1).

Расширение «Можно трогать» по итогам ревью Плана 1: `runtime/hive/lifecycle.py` (только
`mark_dead`/чтение отметки) — согласовано надзором в `review.md` (P1-3).

## Раунд 1 · 2026-10-18
Коммиты: fd4aabf (план 1) · e3846ce (план 2 по ревью) · 0a69acc (реализация) · e453477 (правки по чекеру)

### Сделано
- `runtime/hive/mcp.py` — два MCP-сервера в формате T-024 (stateless streamable HTTP):
  `/node/mcp` с инструментами сестры (`knowledge`, `inheritance`, `propose`, `vote`, `send`,
  `inbox`, `submit_will`) и `/last-order/mcp` с инструментами Last Order (`proposals`, `decide`,
  `set_goals`, `chronicle`); у сестры админ-инструментов структурно нет (другой сервер).
  Идентификация по виртуальному ключу → слот → текущий живой узел (`HIVE_MCP_NODE_KEYS`,
  вид `LastOrder01=sk-a;…`); `X-Runner-Node` — только сверка (P1-2); ключ Last Order —
  `HIVE_MCP_LO_KEY`. `submit_will` — только при отметке смерти в Lifecycle, заголовке
  `X-Runner-Session: will`, совпадении serial во frontmatter и отсутствии staging; файл ложится
  в `run/pending_wills/<serial>.md`. `send` — лимит `MSG_MAX_CHARS` (160) и адресат `all` веером.
- `runtime/hive/proposals.py` — очередь `base/proposals/p-NNNN.json` под git: знание
  (danger|fact|recipe|term → `submit_candidate`, дубль — evidence по dedup.py), навык
  (description+script), правка общих инструкций (unified diff только на `base/instructions.md`;
  ядро и любые чужие пути — отказ на подаче). `decide` — одним коммитом с эффектом и proposal-файлом,
  сообщение `decide p-NNNN accept by last_order: <why>; proposer NNNNN` (P1-4); знания: accept →
  confirmed, reject → retired, запись, ушедшая в retired голосами, закрывает предложение без
  правки записи (§4 терминален). Навык: accept → запись `kind=skill` + скрипт `base/skills/<id>`.
  `CommonLayers`: `set_goals` (base/goals.md, лимиты goals_schema) и `append_chronicle`
  (base/chronicle.md).
- `runtime/knowledge/schema.py` — `Record.decision {by, accept, why, ts}` (optional, обратная
  совместимость) и `status_of(..., decision=)`: решение авторитетно (accept → confirmed,
  reject → retired), пороги §4 — нижняя граница для записей без решения.
- `runtime/knowledge/store.py` — `apply_decision` (отказы: unknown, primer, retired, повтор),
  `write_skill`, `commit_layer` и `append_layer` (пути только внутри `base/` — условие ревью;
  чтение-запись летописи под одной блокировкой, C-5).
- `runtime/hive/lifecycle.py` — `mark_dead`/`body_dead`: отметка «тело умерло» (сигнал смерти,
  разводка от mind-N — T-027) + `kill_confirmed` как смерть; гейт `submit_will` (по P1-3).
- `runtime/hive/app.py` — подключение: mount `/node` и `/last-order`, lifespan обоих MCP
  запускается в lifespan приложения (монтированные Starlette-приложения его не получают).
- `runtime/hive/client.py` — публичный алиас `entry_to_json` (C-4), больше ничего.
- Тесты: `tests/test_proposals.py` (28) — три вида propose, дубль→evidence, decide по видам,
  дифф-гварды (ядро, чужой путь, мультифайл, дрейф контекста), git-сообщение решения, цели,
  летопись, гварды стора; `tests/test_hive_mcp.py` (18, настоящий streamable HTTP через uvicorn)
  — наборы инструментов, идентификация (нет ключа, чужой ключ, сверка заголовка, ключ следует
  за слотом при перерождении), `submit_will` гейты, `send` лимиты/all, `vote`-обёртка,
  приёмочный прогон: предложение одной сестры → решение Last Order → срез у другой.

### Проверки
```
$ uv run pytest
583 passed in 36.82s
$ uv run python tools/check.py
CHARACTERS: last_order, vesper
Every workplace is intact: files present, links resolve, boxes parse.
$ docker compose -f docker/compose.yml up -d --build hive
Container lastorder-hive-1 Started
$ docker compose -f docker/compose.yml logs hive --tail 3
StreamableHTTP session manager started   (x2: /node и /last-order)
INFO: Application startup complete.
$ docker compose -f docker/compose.yml exec -T hive python -c «urllib …»
health: {"status": "ok"} · /node/mcp 200 · /last-order/mcp 200
keyless inbox -> 200 refused ( MCP is_error: «identify yourself: … X-Node-Key» )
```

### Отклонения от задачи
- Идентификация «по ключу или заголовку раннера» из критерия сужена до «только ключ,
  заголовок — сверка» по ревью плана P1-2 (заголовок в песочнице подделываем агентом).
- `runtime/hive/lifecycle.py` (mark_dead) и `runtime/knowledge/` (decision, apply_decision,
  write_skill, commit_layer, append_layer) чуть шире буквального «Можно трогать» — одобрено
  ревью плана (решение по шагу 2, P1-3); `runtime/hive/client.py` — одна публичная строка по
  C-4 чекера. Единственного git-писателя базы это сохраняет.
- `submit_will` staging не подключён к `retire`: раннер забирает staged-файл сам (точка
  интеграции T-027), порт `retire` не менялся.

### Вне задачи
- `agent/last_order/primer/` ещё нет (T-010): тест праймера ходит через PRIMER_DIR.
- Раскладку volume (base/proposals|skills|instructions.md|chronicle.md) стоит вписать в
  KNOWLEDGE.md §1 — документ вне зоны задачи.
- Доставку `base/instructions.md` в рабочее место сестры делают T-027/T-029 (P1-5): принятая
  правка сейчас никем не читается автоматически.
- compose не разводит `HIVE_MCP_*` (env по умолчанию пустые: MCP поднимается, ключи задаются
  при подключении песочниц T-027) — compose.yml в задаче не трогаем.

### Вопросы
- нет

### Вызовы надзора
- 2026-10-18 · plan · 1285c58 · VERDICT: revise (P1-1…P1-6 учтены в Плане 2 и коде) · cost_usd: n/a — хвост вывода скрипта оборвался, значения не прочитано

### Самопроверка
- lo-checker: раундов 1, CHECK: clean
- C-1 · оставлено: коммит 1285c58 «T-028: plan review 1» — это коммит headless-надзора (Agent: claude в теле), не мой; по указанию чекера не переписываю. Мои коммиты (fd4aabf, e3846ce, 0a69acc, e453477) несут `Agent: zcode`, впредь — в каждом.
- C-2 · fixed e453477 (docstring: dead-not-retired достижим ключом — сессия завещания)
- C-3 · fixed e453477 (пара ---/+++ после содержимого хунка — отказ «multi-file diffs are not accepted»; одиночная «--- »-строка внутри хунка остаётся контентом, тест добавлен)
- C-4 · fixed e453477 (публичный `entry_to_json` в client.py)
- C-5 · fixed e453477 (`append_layer` стора: чтение и запись летописи под одной блокировкой)
