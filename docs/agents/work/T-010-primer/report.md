# T-010. Отчёт исполнителя

## Раунд 1 · 2026-10-17
Коммиты: 7b77300, 9655aca, 6b489d8

### Сделано
- `agent/last_order/primer/` — 10 тем из критериев (shelter, tools, food, mobs, falling,
  lava, water, bed, death, light), 28 записей `PrimerEntry`: 8 основных (блок 2 промпта,
  470/500 токенов по `estimate_tokens` на каноническом рендере `entry_line`) и 20
  расширенных (`extended: true`, в промпт не идут). Формулировки свои, короткие; ключи
  `about` — plain-форма T-009 (`blk:oak_log`, `ent:creeper`, …), `[]` для знаний о мире
  вообще (падение, кровать, вещи после смерти).
- `agent/last_order/primer/README.md` — что это, формат записи, бюджеты, правило
  «в `masked` праймер не грузится» (ADR-020, загрузчик T-012 читает каталог только в
  `plain`), как править и что текст утверждает пользователь.
- `dev/primer_check.py` — валидация всех записей схемой T-007 (`PrimerEntry.from_dict`),
  ключей `about` нормализацией T-009 (`normalize` round-trip + `Perception.handle`),
  сборка записи через `to_record` → `Record.from_dict` (source=primer, status=confirmed),
  бюджет основного праймера (`PRIMER_MAX_ENTRIES`, `B_PRIMER`), правила содержания
  (русский текст, ≤ 2 предложений, запрет @/altoclef/tungsten/ссылок), таблица тем;
  exit 1 при любой проблеме.
- `tests/test_primer.py` — 13 тестов: вызов проверки из `dev/primer_check.py`, темы
  1–4 записи, бюджет, расширенный праймер вне бюджета, загрузка как confirmed primer
  записей, ключи и тексты, отказы на сломанных/переполненных/чужих файлах, загрузка
  настоящего праймера свежей базой в `plain` (28 записей, один коммит
  `primer: load 28 records`).
- По ответу надзора 1: `tests/test_knowledge_store.py` и `tests/test_knowledge_slice.py`
  — autouse-фикстура `no_repo_primer` ставит `PRIMER_DIR` на пустой каталог в `tmp_path`
  (тесты T-012 герметичны от репозиторного праймера); в `test_git_log_shows_every_op_as_hive`
  `delenv("PRIMER_DIR")` заменён оставлением значения фикстуры, комментарий поправлен.
  `runtime/` не тронут.

### Проверки
```
$ uv run pytest
306 passed in 14.74s
$ uv run python tools/check.py
CHARACTERS: last_order, vesper
Every workplace is intact: files present, links resolve, boxes parse. (exit 0)
$ uv run python dev/primer_check.py
main primer: 8/8 records, 470/500 tokens
primer: ok
```

### Отклонения от задачи
- нет

### Вне задачи
- нет

### Вопросы
- Вопрос раунда о 14 упавших тестах T-012 закрыт ответом надзора 1 (review.md): «Можно
  трогать» расширено, выполнено вариантом 1, проверено тестом загрузки настоящего
  праймера. Открытых вопросов нет.
- Остался критерий приёмки про подтверждение текста: задача в `blocked` со строкой
  «Blocked: нужен оператор — подтверждение текста праймера». Текст записей готов
  к прочтению (agent/last_order/primer/, сводка — таблица `dev/primer_check.py`);
  после подтверждения внесу правки по замечаниям, допишу сюда дату и список изменений
  и сдам в `review`.

### Вызовы надзора
- 2026-10-17 · answer · 5c26eb8 · VERDICT: answered · cost_usd: unknown (stdout-парсинг
  упал на python-стабе Windows)
