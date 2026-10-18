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
