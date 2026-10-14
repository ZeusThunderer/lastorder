# План работ: MVP «сеть узлов Last Order»

Статус: утверждён к старту по команде пользователя. Решения — [open-questions.md](open-questions.md),
топология — [docker-topology.md](docker-topology.md), память — [KNOWLEDGE.md](../architecture/KNOWLEDGE.md).

## Критерий MVP (конец E3, занятие 3)
2–3 слота в одном мире работают **2 часа без вмешательства**, и за это время:
- сменилось ≥6 поколений узлов, у каждого свои черты в `birth.json` и в логе;
- ≥70% новых узлов в первых 3 репликах ссылаются на запись предшественника: номер, термин или урок
  (автопроверка по базе);
- ни одной смерти без `will.md`;
- ни одного сырого id Minecraft в промптах (промпты пишутся в отладочный лог).

Отчёт выдаёт `tools/mvp_report.py` по volume `knowledge`.

## Цикл мозга
Раз в 8–15 с или по событию (урон, новый объект, сообщение в чате):
1. `observe()`: `getGameState`, `getEntitiesInfo(16)`, `getBlocksAround(4)`, `getInventoryFull`,
   `getRecentChat(5)` → perception (id → handle + описание/термин) → компактный JSON.
2. `options()` — код строит ≤8 вариантов:
   - подойти к handle, ударить handle, добыть handle;
   - отойти от угрозы, осмотреться, ждать, поставить, съесть.
   Варианты исполняются рычагами uc. `@gamer` не даём.
3. Один вызов LLM, ответ в JSON: `{option, why, say?, new_term?, note?, votes?}`.
   - Порядок промпта под кеш: личность и каталог (статично) → core memory → знания о видимых handle-ах →
     наследие → кандидаты на голос → наблюдение.
4. `act()`:
   - результат → `log.jsonl`;
   - `say` → игровой чат с лимитом частоты, в манере пресета (ADR-018);
   - `new_term`, `note`, `votes` → hive.
5. Смерть определяется по hp = 0 в `getGameState` и ванильной строке смерти в чате (проверено в E1). Далее:
   1. вызов «рефлексия + завещание»;
   2. `will.md`;
   3. `hive.retire`;
   4. `respawnPlayer()`: авторесп uc без задачи не срабатывает;
   5. `hive.birth`.
   Если LLM упала — аварийное завещание из шаблона.
6. Сбои: таймаут вызова, пауза с растущей задержкой, после трёх сбоев узел «ждёт». Цикла без пауз нет.

Порты по ARCHITECTURE §5.3: `GameBody` (`Py4jBody`), `PersonaProvider`, `Memory` (`HiveKnowledge`).
Потом C1, Redis и FastJev встают за те же интерфейсы.

## Шаги

### R1. Перестройка репозитория (первым делом, по команде)
- [x] `git branch -m master main`
- [x] удалить сабмодуль aw: `git submodule deinit -f agent-workplace`, `git rm -f agent-workplace`, `rm -rf .git/modules/agent-workplace`
- [x] `git remote add upstream https://github.com/3ndetz/agent-workplace.git && git fetch upstream`
- [x] `git merge upstream/main --allow-unrelated-histories` (или первый коммит наших файлов, затем merge)
- [x] `unionclef` зафиксировать на `v0.95.59`; `.gitmodules` с `branch`/`shallow` по желанию
- [x] NetTyan: правки пользователя — в локальную ветку nt или `stash` (с подтверждением)
- [x] `docs/context/` ← переписки; удалить корневой `LAST_ORDER.md`; `.gitignore` (`.github/modernize/`, `.env`, `__pycache__`, `run/`)
- [x] первый коммит; CLAUDE.md — убрать абзац «Состояние репозитория: сейчас…»

### R2. Документы-решения
- [x] DECISIONS.md: ADR-015 (Docker), ADR-016 (сеть сразу), ADR-017 (Мисака-нет, hive — единственный писатель), ADR-018 (имя слота и имя узла)
- [x] бриф LAST_ORDER.md §1, §5, §7: ссылки на новые ADR, имя «Мисака NNNNN», манера речи
- [x] `prompt_template.md`: имя и манера речи — плейсхолдеры, пресеты `lab` / `misaka` (ADR-018). task1.md не трогаем — это сданная домашка

### E0. Каркас (до занятия 2)
- [x] `agent/last_order/`: PERSONA (operator-часть = база), SESSION_PROMPT, GOALS, `memory/`, `prompt_template.md`
- [x] `aw-fix:` `AGENT_NAME`, проверка аргументов скриптов
- [x] `pyproject.toml` (uv, Python 3.12): litellm, py4j, fastapi, uvicorn, pydantic, pytest
- [x] `runtime/lastorder/traits.py` и `naming.py`: черты, quirk, seed, пресеты имени; `persona.py`: подстановка, проверка «нет пустых `{}`»
- [x] `runtime/llm/client.py`: LiteLLM, JSON-ответ, таймаут, повтор, опция reasoning off
- [x] `runtime/perception/`: таблица описаний (пустой шаблон для пользователя), handle = хэш id с солью эпохи, обратная карта
- [x] тесты: черты, промпт, perception (регулярка `minecraft:` и словарь id → 0 вхождений)
- [x] снапшот трёх запросов из task1 на облачной модели: `docs/agents/snapshots/`
- [ ] выбрать локальную модель и добавить `docker/llm` (vLLM) — перенесено в E2

### E1. Тело в Docker — спайк главного риска (до занятия 2)
- [x] `docker/body/Dockerfile`: Java 21, HeadlessMC, Fabric 1.21.11, fabric-api, jar uc (sha256), `altoclef_settings.json` (autoConnect, autoRespawn)
- [x] `docker/compose.yml`: `mc-server` + `body-1` + `mind-1` (network_mode)
- [x] из `mind-1` (`tools/body_probe.py`): `inGame`, геттеры, `gotoXYZ` + `pathStatus`, `@get log 1`, RCON `/kill`,
  `respawnPlayer()`, callback `onChatMessage`. Находки — в unionclef-bridge.md, раздел «Py4J: проверено в E1»
- [x] без GL работает; вариант Xvfb остаётся в образе (`BODY_RENDER=xvfb`) для показа

### E2. Один мозг (занятие 2)
- [ ] локальная модель: `docker/llm` (vLLM), профиль `local`
- [ ] `runtime/game/py4j_body.py` (`GameBody`), `options.py`, `death.py`
- [ ] `runtime/lastorder/mind.py`: цикл, реплики, завещание, аварийное завещание
- [ ] `runtime/hive/`: birth/retire/summary, `knowledge/store.py` (записи, git-коммит на изменение)
- [ ] проверка: узел живёт до смерти или 45 мин, `will.md` в базе, следующий узел ссылается на него

### E3. Сеть (занятие 3) — критерий MVP
- [ ] `body-2`, `body-3`, `mind-2`, `mind-3`; сквозные номера из hive
- [ ] ротация: смерть, срок жизни, застревание; сводка живых и мёртвых
- [ ] минимум памяти: завещания + термины + `note` как `hypothesis`; знания по видимым handle-ам в промпте
- [ ] `tools/mvp_report.py`; 2-часовой прогон

### E4. Память сети (занятие 4)
- [ ] рефлексия раз в 5–10 мин, core memory, кандидаты, дедуп, попутное голосование, статусы
- [ ] метрики поколений: время жизни, подтверждённые знания, вехи

### Дальше
- Telegram (бот) — после 1–2 итерации разума; затем голос в созвоне.
- Публичный сервер из whitelist (ADR-013), автозапуск, сон, сновидец.
- Стрим: рендерящее тело, режиссёр.

## Проверка на каждом шаге
`uv run pytest`, `python tools/check.py`, `docker compose -f docker/compose.yml up`, `tools/mvp_report.py`.
