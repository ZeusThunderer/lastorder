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
