# agent-workplace (aw): шаблон персонажа

aw — это шаблон, а не рантайм. Всё поведение живёт в Markdown и JSONL, код только собирает промпт.
В aw нет LLM-клиента, раннера, Telegram, Minecraft и тестов. Зависимости — только stdlib Python 3.

## Персонаж
- Персонаж = папка `agent/<name>/`, в которой есть `PERSONA.md`. Поиск — `tools/check.py: characters()`.
- Обязательные файлы (`check.py: EXPECTED`): `PERSONA.md`, `SESSION_PROMPT.md`, `GOALS.md`,
  `memory/README.md`, `memory/state.md`.
- `PERSONA.md` из двух частей:
  - «Set by the operator — you do not change this»: около 40 строк;
  - «Yours to develop».
- `GOALS.md`: пункты `## N. OPEN|DONE · <tag/date>`, сверху новые. Пункт 0 — от оператора.
- Образец — `agent/vesper/`, данные в нём выдуманные.

## Сборка промпта (`tools/compose_session.py`)
- `compose(name, from_operator)` собирает промпт в порядке:
  1. `=== NEW FROM THE OPERATOR ===`
  2. `NOW:`
  3. `UNANSWERED (n)`
  4. `WHO YOU ARE` (PERSONA)
  5. `WHAT YOU ARE DOING` (GOALS)
  6. `WHERE YOU LEFT OFF` (state)
  7. `THIS SESSION`
  8. `THE RULES` (AGENTS.md + shared/rules.md)
- `unanswered()` сопоставляет inbox `from` с outbox `to`. Если строки не совпадают точно, сообщение
  остаётся «без ответа» навсегда. `NOW` идёт в локальном времени, скрипты пишут UTC.
- Правило upstream: в `compose_session.py` нет веток под конкретного персонажа.
- Для Last Order сессия aw слишком медленная для игры (ADR-001). Её роль — C4 (рефлексия) и
  отладка промпта. Игровой промпт собирает `runtime/lastorder/persona.py`.

## Память в aw
- Три слоя:
  - PERSONA — пишет человек;
  - `memory/state.md` — переписывается в конце сессии, предупреждение при 16 KB;
  - `memory/entities/<name>.md` — `## Events`, `## Links` с `[[link]]`.
- По правилу upstream память пишется по-английски. У Last Order хроника по-русски, служебные поля по-английски.

## Ловушки
- В скриптах `agent/scripts/*.sh` `AGENT_NAME` по умолчанию `vesper`. Пути относительные, запускать из корня.
- `remember.sh`:
  - падает без `## Events`;
  - вставляет новое сверху, а примеры в vesper идут снизу;
  - допускает обход путей через `../../PERSONA` (ADR-002, исправить в `aw-fix:`).
- `ticket.sh` пишет в корневой `tickets/`, то есть нарушает собственную границу записи.
- Противоречие в документации: README и AGENTS разрешают читать всё, docs/ARCHITECTURE запрещает читать чужую память.

## Что меняем в форке
- `aw-fix:`
  - `AGENT_NAME` без значения по умолчанию;
  - проверка аргументов в скриптах;
  - ISO-время и лимит в `unanswered()`;
  - жёсткий лимит `state.md` в `check.py`.
- `aw-ext:`
  - `agent/last_order/`, где operator-часть PERSONA = база из task1;
  - `prompt_template.md`;
  - `check.py` знает про `prompt_template.md`.
