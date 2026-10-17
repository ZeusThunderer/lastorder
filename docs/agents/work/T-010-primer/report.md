# T-010. Отчёт исполнителя

## Раунд 1 · 2026-10-17
Коммиты: 7b77300, 9655aca

### Сделано
- `agent/last_order/primer/` — 10 тем из критериев (shelter, tools, food, mobs, falling,
  lava, water, bed, death, light), 28 записей `PrimerEntry`: 8 основных (блок 2 промпта,
  470/500 токенов по `estimate_tokens` на каноническом рендере `entry_line`) и 20
  расширенных (`extended: true`, в промпт не идут). Формулы свои, короткие; ключи `about`
  — plain-форма T-009 (`blk:oak_log`, `ent:creeper`, …), `[]` для знаний о мире вообще
  (падение, кровать, вещи после смерти).
- `agent/last_order/primer/README.md` — что это, формат записи, budgets, правило
  «в `masked` праймер не грузится» (ADR-020, загрузчик T-012 читает каталог только в
  `plain`), как править и что текст утверждает пользователь.
- `dev/primer_check.py` — валидация всех записей схемой T-007 (`PrimerEntry.from_dict`),
  ключей `about` нормализацией T-009 (`normalize` round-trip + `Perception.handle`),
  сборка записи через `to_record` → `Record.from_dict` (source=primer, status=confirmed),
  бюджет основного праймера (`PRIMER_MAX_ENTRIES`, `B_PRIMER`), правила содержания
  (русский текст, ≤ 2 предложений, запрет @/altoclef/tungsten/ссылок), таблица тем;
  exit 1 при любой проблеме.
- `tests/test_primer.py` — 12 тестов: вызов проверки из `dev/primer_check.py`, темы
  1–4 записи, бюджет, расширенный праймер вне бюджета, загрузка как confirmed primer
  записей, ключи и тексты, отказы на сломанных/переполненных/чужих файлах.

### Проверки
```
$ uv run python dev/primer_check.py
main primer: 8/8 records, 470/500 tokens
primer: ok
$ uv run python tools/check.py
CHARACTERS: last_order, vesper
Every workplace is intact: files present, links resolve, boxes parse. (exit 0)
$ uv run pytest
14 failed, 291 passed in 13.81s
```
`tests/test_primer.py` — 12 passed. Упавшие 14 — тесты влитого T-012, см. «Вопросы».

### Отклонения от задачи
- нет

### Вне задачи
- нет

### Вопросы
- Создание `agent/last_order/primer/` (суть задачи) ломает 14 тестов влитого T-012:
  13 в `tests/test_knowledge_store.py` и 1 в `tests/test_knowledge_slice.py`
  (`test_store_slice_and_inheritance_together`). Свежая база в `plain` теперь грузит
  реальный праймер (`store.py:237-238`, `PRIMER_DIR` по умолчанию —
  `CHARACTER_DIR / "primer"`), а фикстуры T-012 писались, когда каталога не было:
  `test_init_creates_layout_and_git` ждёт `git_log == ["init: layout"]`, а получает
  `["init: layout", "primer: load 28 records"]`; в остальных сдвинулись id записей
  (`k-0001` теперь праймерная) и счётчики. Проверил: с `PRIMER_DIR`, указывающим на
  несуществующий каталог, зелёные 58 из 59 этих файлов; остаётся только
  `test_git_log_shows_every_op_as_hive` (`tests/test_knowledge_store.py:596-600`),
  который сам делает `monkeypatch.delenv("PRIMER_DIR")` с комментарием
  «no primer dir: nothing to load» — предположение больше не верно. Оба файла вне
  «Можно трогать» этой задачи. Варианты:
  1) расширить «Можно трогать» до минимальной правки этих двух тестовых файлов —
     autouse-фикстура, заводящая `PRIMER_DIR` на пустой tmp-каталог (герметичность от
     репозиторного праймера), а в `test_git_log_shows_every_op_as_hive` заменить delenv
     `PRIMER_DIR` на setenv на несуществующий путь и поправить комментарий. Выбрал бы
     этот: ~6 строк, поведение T-012 не трогаем;
  2) прямая правка надзора (`lo-direct-edit`) тем же содержанием;
  3) менять поведение загрузчика в `runtime/` не предлагаю: это территория T-012 и
     против «Нельзя трогать».
- Отдельно напоминаю критерий приёмки про подтверждение текста пользователем: текст
  записей готов к показу, по нему жду замечаний (после закрытия вопроса выше сдам
  обычным порядком — самопроверка, затем `review`).

### Вызовы надзора
- нет
