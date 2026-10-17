# T-012. Отчёт исполнителя

## План 1 · 2026-10-17

Три новых модуля в `runtime/knowledge/` (schema.py и budget.py из T-007 не трогаю),
тесты на каждый критерий приёмки. `runtime/knowledge/__init__.py` не в «Можно трогать» —
не трогаю, импорт подмодулями.

### Файлы
- `runtime/knowledge/dedup.py` — поиск дубля на уровне записей: пересечение непустых `about`
  (пустые совпадают с пустыми) + `claims_duplicate` из schema.py (J ≥ 0.6); retired-записи
  дублями не считаются (исправление — новая запись); из нескольких дублей — максимальный J.
- `runtime/knowledge/store.py` — `KnowledgeStore(root: Path, git: bool = True)`,
  `StoreError(ValueError)`; пишет базу только он.
- `runtime/knowledge/slice.py` — чистые сборщики `build_slice(...)` и `build_inheritance(...)`
  с бюджетами budget.py; store их вызывает.
- Тесты: `tests/test_knowledge_store.py`, `tests/test_knowledge_dedup.py`,
  `tests/test_knowledge_slice.py`.

### Решения по спорным местам (прошу проверить)
1. **Статус — чистая функция от evidence+голосов** (KNOWLEDGE §4 «пересчитывается после
   каждого изменения», «порядок событий не важен»): retired после замены голоса
   опровергнувшего может стать disputed. «Терминален» понимаю как практическую
   необратимость: retired исчезает из срезов и из очередей голосования, новых голосов не
   приходит. Если надзор хочет жёсткий замок на retired — добавлю одну проверку.
2. **`birth(slot, traits, seed)`**: `seed: int | None = None`, по умолчанию `traits.seed`;
   расхождение seed ≠ traits.seed — StoreError (черты обязаны воспроизводиться из seed).
   Слот не проверяю на занятость: тест атомарности «3 процесса × 20 рождений» без retire
   иначе не проходит; очерёдность birth/retire в слоте держит hive (T-022).
3. **`retire(serial, cause, will)`**: `will` — тело (2–4 строки, валидирует schema);
   фронтматер собирается из birth.json (slot, born_at, traits) + `died_at=None` (сейчас UTC);
   опциональные kwargs `model=None` (env `LLM_SMART_MODEL`/`LLM_MODEL`, запас `unknown`),
   `emergency=False` (модель `template`). Повторный retire — StoreError, файл не трогается.
   Мёртвый узел = есть will.md (файлы раскладки не расширяю).
4. **Скраб perception в submit_candidate** (KNOWLEDGE §2 помечает T-012): оставляю в
   hive-сервисе T-022 — стор не знает режима и Perception; в критериях приёмки его нет.
5. **Счётчик номеров** `<root>/.serial` под файловой блокировкой `<root>/.lock`
  (msvcrt/fcntl), в `.gitignore` базы: survives перезапуск, при утере — восстановление из
   max(nodes/). Одна блокировка сериализует все записи (hive — единственный писатель).
6. **Инициализация**: пустой root → раскладка + `git init` (core.autocrlf=false) + коммит
   `init: layout` (.gitignore, пустые catalog.md/goals.md). Праймер — только при пустом
   root и `PERCEPTION_MODE=plain`, каталог из env `PRIMER_DIR` (по умолчанию
   `agent/last_order/primer`, T-010 не влита — тест на фикстуре из двух записей), один
   коммит `primer: load N records`, грузятся и extended (в базу, не в промпт).
7. **Каталог** `base/catalog.md`: пересборка в каждом коммите с записями, строка
   `<id> <kind> <about-ключи>` по не-retired записям, ≤ B_CATALOG; снимок «при рождении» =
   чтение при вызове inheritance() (hive зовёт её на birth, файл под узел раскладка
   не выделяет).
8. **Коммиты**: `<op>: <serial|id> …` — `birth: 00007 LastOrder02`, `retire: 00007 game`,
   `core: 00007`, `record: k-0042 hypothesis by 7`, `evidence: k-0042 by 15`,
   `vote: k-0042 by 15`, автор `hive <hive@last-order>` через GIT_AUTHOR/COMMITTER env.
   Публичного метода «записать файл как есть» нет.
9. **Evidence при дубле**: один узел — одно evidence (повторный дубль того же узла не
   дублирует строку); голос узла заменяет прежний (по §4).
10. **Срез**: about-записи — не-retired с пересечением about∩keys, ≤ ABOUT_ENTRIES на ключ,
    порядок релевантности (число совпавших ключей, confirmed>disputed>hypothesis, свежее);
    fresh — FRESH_ENTRIES свежих не-retired; бюджет B_ABOUT на about+fresh канонической
    строкой `<id> [<kind>] <claim> <about>`; режется fresh → потом about с конца.
    pending_votes ≤ VOTE_CANDIDATES: hypothesis, автор ≠ serial, этот узел ещё не голосовал,
    бюджет B_VOTES. goals.md (T-016) — читатель YAML-списка NetworkGoal, пусто → ().

### Тесты (по критериям)
- init: раскладка, git, праймер plain одним коммитом / masked не грузится;
- next_serial: 3 процесса × 20 birth (subprocess, git=False) → 60 номеров подряд без дыр;
  перезапуск стора продолжает нумерацию;
- birth/retire: birth.json по схеме; will.md валиден (Will.from_markdown roundtrip), узел
  мёртв; повторный retire — StoreError, файл не перезаписан;
- кандидат: новая запись hypothesis c author/ts/id; дубль (общий about, J≥0.6) → evidence
  без новой записи, статус confirmed; не-дубль (J<0.6) — конкурентная запись; порог
  тестируется на паре из KNOWLEDGE §4;
- статусы: параметризованная таблица §4 (hypothesis/confirmed(2 пути)/disputed/retired,
  голос автора не считается, «против» без why блокирует confirm, замена голоса);
- core memory: круглый путь, резка 1.5 KB по границе строки;
- git log: сообщения и автор hive по каждой операции;
- inheritance: 3 завещания, ≤10 строк предшественников + счётчик, живые, термины ≤10,
  бюджет наследия;
- срез: выбор about по ключам, fresh, ≤2 кандидата не свои и не голосованные, бюджеты;
- ошибки: неизвестный узел, битый кандидат/ключи — StoreError, база не меняется.

### Проверки
`uv run pytest`, `uv run python tools/check.py`.
Дополнительно: тест атомарности номеров гоняет 3 subprocess-процесса × 20 рождений
(git=False) и 2 × 5 с git=True.

## Раунд 1 · 2026-10-17
Коммиты: bd31a39 (модули и тесты), 4076f3e (каталог), 72f064c (C-2, C-3), ecc1503 (C-4, C-5),
плюс этот коммит отчёта. План-ревью надзора: fa07cd1.

### Сделано
- `runtime/knowledge/store.py` — `KnowledgeStore(root, git=True, *, primer_dir, leak_check, now)`:
  - пустой root → раскладка KNOWLEDGE v2 + `git init` (autocrlf false) + коммит `init: layout`;
    при `PERCEPTION_MODE=plain` праймер из `PRIMER_DIR` (по умолчанию `agent/last_order/primer`,
    T-010 не влита — тесты на фикстуре из двух записей, extended тоже грузится в базу) одним
    коммитом `primer: load N records`; в `masked` не грузится; существующую базу не перегружает;
  - `next_serial()` атомарен между потоками и процессами: файловая блокировка `.lock`
    (msvcrt/fcntl), счётчик `.serial` в `.gitignore`, восстановление из `max(nodes/)`;
    тесты 3×20 (git=False) и 2×5 (git=True) дают номера подряд без дыр; вывод 5-значный
    (`format_serial`, ADR-018);
  - `birth(slot, traits, seed=None, ttl_min=45)` → `BirthRecord` + `nodes/<serial>/birth.json`
    (валидация `BirthFile` до записи; расхождение seed ≠ traits.seed — StoreError);
  - `retire(serial, cause, will_text)` — завещание приходит целиком (frontmatter + тело,
    `Will.from_markdown`, аварийное в том числе): died_at/emergency/model и тело из него,
    slot/born_at/traits из birth.json, cause из аргумента, lifetime_s пересчитан с обнулением
    отрицательного; несовпадение serial и битый текст — `InvalidWillError` (отличим от
    «неизвестный узел» и «уже мёртв»), повторный retire — `AlreadyRetiredError` без перезаписи;
  - core memory: `store_core_memory` режет до 1.5 KB по границе строки (`fit_core_memory`),
    `core_memory` читает;
  - `submit_candidate`: новая запись `hypothesis` с author/ts/id `k-NNNN` (id под общей
    блокировкой, `max+1`, переживает перезапуск); дубль (общий about + J ≥ 0.6, schema.py) —
    новое evidence без новой записи, повтор тем же узлом идемпотентен; evidence автора —
    всегда первое свидетельство записи;
  - `submit_vote`: повторный голос заменяет прежний; статусы — чистая функция §4
    (`status_of`, параметризованный тест), retired терминален (голос/evidence для него —
    ошибка), «против» без why блокирует confirm, но не опровергает;
  - git: каждый write-op — один коммит `<op>: <serial|id> …` с автором `hive` (env
    GIT_AUTHOR/COMMITTER, gpgsign off, hooksPath пуст); сбой коммита откатывает файлы и индекс
    (`git reset -- paths`); публичного метода «записать файл как есть» нет;
  - ошибки ввода — свой тип `StoreError` и подклассы, база не меняется.
- `runtime/knowledge/dedup.py` — `find_duplicate`/`shares_about`: пересечение непустых about
  (пустые совпадают), J ≥ 0.6, максимальный J выигрывает; primer и retired дублями не считаются.
- `runtime/knowledge/slice.py` — `build_slice` (about по видимым ключам ≤ 6 на ключ, fresh-хвост,
  ≤ 2 кандидата на голос: hypothesis → disputed, не свои и не голосованные; бюджеты B_ABOUT/B_VOTES
  на канонической строке, режется fresh → потом about с конца), `build_inheritance` (K=3 завещания,
  ≤ 10 строк предшественников + счётчик, живые, термины ≤ 10, всё в квотах §7), `parse_utc`
  (мгновенья вместо строк), `format_serial`. Каталог `base/catalog.md` пересобирается в каждом
  коммите с записями, без primer/retired, ≤ B_CATALOG; goals.md — читатель NetworkGoal для T-016.
- Тесты: `tests/test_knowledge_dedup.py` (9), `tests/test_knowledge_store.py` (42),
  `tests/test_knowledge_slice.py` (14) — по тесту на каждый критерий приёмки, включая
  порог дедупа из §4 (J = 4/7 — конкурент), таблицу статусов, git-след, бюджеты, утечки id.

### Проверки
```
$ uv run pytest
187 passed in 11.21s
$ uv run python tools/check.py
CHARACTERS: last_order, vesper
Every workplace is intact: files present, links resolve, boxes parse.
```

### Отклонения от задачи
- Нет. Дополнительные kwargs конструктора (`primer_dir`, `leak_check`, `now`) — из плана,
  утверждённого ревью (P1-4, решение 6); `schema.py` и `budget.py` не тронуты.

### Вне задачи
- T-022 (hive-сервис) должен передать в `KnowledgeStore` параметр `leak_check`
  (например, `Perception.leaks`), когда T-009 будет влита — сейчас базовая проверка ловит
  только namespaced `minecraft:` id (P1-4).
- `runtime/knowledge/__init__.py` не в «Можно трогать» — новые модули импортируются
  подмодулями (`runtime.knowledge.store`), реэкспорта нет.

### Вопросы
- нет

### Ответ на ревью
План-ревью 1 (revise):
- P1-1 · fixed bd31a39 — retire принимает завещание целиком (frontmatter + тело), сервисные
  поля из birth.json, lifetime_s пересчитан, различимые ошибки; тесты на прошлое died_at,
  аварийное завещание, несовпадение serial, битый текст.
- P1-2 · fixed bd31a39 — дедуп, голосование, срезы и каталог исключают primer; голос за
  primer — StoreError; тест на каждом пункте, включая extended-запись.
- P1-3 · fixed bd31a39 — retired терминален: голос/evidence для retired-записи — StoreError
  без записи и коммита; для остальных статусов чистая функция §4.
- P1-4 · fixed bd31a39 — `leak_check: Callable[[str], Sequence[str]] | None`; базовая
  проверка namespaced id в claim и why всегда включена; тесты базовой и подставной проверки.
- P1-5 · fixed bd31a39 — id под той же блокировкой, ttl_min параметр (45), изоляция git
  (gpgsign/hooksPath/user через env), откат файлов и индекса при сбое коммита, тест 2×5 с git.
- P1-6 · fixed bd31a39 — pending_votes: сначала hypothesis, затем disputed; тест.

### Самопроверка
- lo-checker: раундов 2, CHECK: findings
- C-1 · fixed <этот коммит> — раунд отчёта с ответами на P1-1..P1-6 добавлен при сдаче.
- C-2 · fixed 72f064c — knowledge_slice и inheritance требуют живой узел; тест.
- C-3 · fixed 72f064c — порядок завещаний по распарсенному мгнову (`parse_utc`); тест
  смешанных офсетов.
- C-4 · fixed ecc1503 — откат сбойного коммита чистит индекс (`git reset -- paths`); тест
  на реальном git: add проходит, commit падает (битый GIT_COMMITTER_DATE), статус чист,
  следующий коммит поглощает только свои файлы.
- C-5 · fixed ecc1503 — порядок fresh-хвоста и очереди голосов запинен тестом со
  смешанными offset/naive.
- C-6 · fixed <этот коммит> — закрывается настоящим раундом.

### Вызовы надзора
- 2026-10-17 · plan · f472bc8 · VERDICT: revise · cost_usd: ? (скрипт не напечатал стоимость)

