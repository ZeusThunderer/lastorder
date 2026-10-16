# T-007. Отчёт исполнителя

## Раунд 1 · 2026-10-17
Коммиты: 93afb8b, 80ad7e7, df4d3c2 (+ этот, с отчётом)

### Сделано
- `docs/architecture/KNOWLEDGE.md` переписан в v2, ровно 350 строк, 11 разделов по критериям:
  слои и раскладка `knowledge` (git, пишет hive) / `run` (вне git, пишет mind); запись знания
  (поля, `source: primer|node`, `about` в формате T-009); таблица триггеров с важностью и
  порогом внеочередной рефлексии 12; дедуп (нормализация, Жаккар, порог 0.6) и таблица
  переходов статусов; `will.md` (frontmatter serial/slot/cause/born_at/died_at/lifetime_s/
  emergency/traits/model + тело 2–4 строки) и аварийный шаблон; `birth.json` и `core.md`;
  `Inheritance` (K=3 завещания, сводки); праймер (файлы, бюджет 500, расширенный);
  сборка шага (10 блоков, порядок под кеш, 2390+110 ≤ 2500, резка, plain/masked);
  вход/выход рефлексии и завещания (4000/600, 3000/300, smart); «на потом».
- `docs/architecture/NETWORK.md` — только §5 (ключ `plain` = `blk:birch_log` с префиксом,
  устаревший пример поправлен) и §8 (ссылки на разделы v2, порог дедупа, бюджет праймера).
- `runtime/knowledge/schema.py` — `Record`, `Evidence`, `RecordVote`, `PrimerEntry`,
  `WillMeta`, `Will`, `BirthFile`, `TriggerEvent`, `ReflectionInput`, `ReflectionOutput`,
  `emergency_will`; у каждого from_dict/to_dict, неверные поля и значения отклоняются
  (`SchemaError`); `Record.to_entry() -> ports.KnowledgeEntry`; таблица важности и порог 12.
- `runtime/knowledge/budget.py` — `estimate_tokens` (офлайн, консервативная: 0.6/крил. символ,
  0.3/остальное, правило и запас в docstring), 10 констант блоков шага (сумма 2390), бюджеты
  рефлексии/завещания, `CORE_MEMORY_LIMIT_BYTES = 1536`, квоты среза и наследия.
- `tests/test_knowledge_schema.py` — 36 тестов: каждый JSON- и markdown-пример вытаскивается
  из KNOWLEDGE.md регуляркой по меткам (`json Record`, `markdown Will invalid`, …) и
  разбирается схемой (плюс roundtrip); неверные примеры отклоняются; аварийное завещание
  собирается из подставных полей без LLM (lifetime_s вычисляется, чисел черт и seed в тексте
  нет); отказы (kind, about, author/source, term, will, traits, importance, кандидаты ≥ 4);
  сумма блоков бюджета под потолком.

### Проверки
```
$ uv run pytest
111 passed in 10.50s
$ uv run python tools/check.py
CHARACTERS: last_order, vesper
Every workplace is intact: files present, links resolve, boxes parse.
```

### Отклонения от задачи
- `PrimerEntry` добавлен в schema.py сверх списка классов из критериев: формат праймера —
  критерий приёмки п. 8 документа, а T-010 валидирует записи именно схемой T-007.
- `confidence` записи стал опциональным (в v1 §2 был обязательным): это внутренняя оценка
  hive, в промпт не идёт и у свежих записей её нет.
- `votes` сериализуются списком объектов, а не словарём по serial, как в v1: уникальность
  голоса на узла обеспечивает store (T-012), порядок списка полезен для git-истории.
- Таблица стоимости v1 §5 не переносилась: она уже живёт в NETWORK.md §6 (реестр вызовов),
  v2 §10 ссылается на неё.
- ports.py не менялся: новых полей payload не потребовалось.

### Вне задачи
- В KNOWLEDGE v1 §5 была оценка «3M токенов/ч» — ушла вместе с разделом стоимости; актуальный
  расчёт — NETWORK.md §6.
- NETWORK.md §11 (таблица сверки задач) не трогал: правки §5/§8 её не меняют.

### Вопросы
- нет

### Вызовы надзора
- нет

## Раунд 2 · 2026-10-17 (сдача после самопроверки)
Коммиты: c6437db + этот (статус `review`).

### Сделано
- Без изменений кода: раунд сдачи. Критерии приёмки закрыты раундом 1 (разделы 1–11 v2,
  порядок блоков с обоснованием, schema/budget, тесты примеров из документа, аварийное
  завещание без LLM, новых вызовов LLM нет, рефлексия и завещание — `smart`).

### Проверки
```
$ uv run pytest
111 passed in 3.95s
$ uv run python tools/check.py
CHARACTERS: last_order, vesper
Every workplace is intact: files present, links resolve, boxes parse.
```
Diff `main...HEAD` — только «Можно трогать»: KNOWLEDGE.md, NETWORK.md (§5, §8),
`runtime/knowledge/{__init__,schema,budget}.py`, `tests/test_knowledge_schema.py`, файлы задачи.

### Ответ на самопроверку lo-checker
- C-1 (minor) · оставлено как есть: дополнительные методы схемы не добавлены — они вне
  критериев приёмки; публичные конструкторы/методы появятся вместе с реальным потребителем
  (T-012 store, T-023 reflection), чтобы схема не обгоняла задачи.
- C-2 (minor) · принято к сведению: вердикт `clean`, на сдачу не влияет; если надзор
  включит её в находки ревью — закрою в следующем раунде.

### Отклонения от задачи
- нет (см. раунд 1)

### Вне задачи
- нет (см. раунд 1)

### Вопросы
- нет

### Вызовы надзора
- нет

