# T-013. Ревью надзора

## Раунд 1 · 2026-10-17 · вердикт: changes · headless
Проверено: `git diff main...z/T-013-death-will-path` @ 7e74783

### Проверки (прогнаны надзором)
- `uv run pytest` — 427 passed
- `uv run python tools/check.py` — Every workplace is intact
- Стенд (`death_scenario.py`) в этом раунде не прогонял: вердикт определён находкой R1-1 в коде,
  после её исправления путь hive-down меняется. Прогоню в раунде 2 командой из docstring сценария.

### Критерии приёмки
- [x] `DeathDetector`: одно событие на смерть, двойная строка, повторная смерть только после hp > 0 —
  `tests/test_death.py`, код `death.py:137-149`
- [x] Причина `ttl` → `stuck` → `game` со строкой — `classify_death`, тесты на границу и приоритет
- [x] `write_will`: один вызов `smart`, проверка 2–4 строк (`_will_body`), числа черт, сырые id;
  четыре отказа → аварийное завещание `emergency: true` — `tests/test_will.py`
- [x] Порядок `on_death` — `test_on_death_calls_the_steps_in_the_documented_order`
- [ ] Hive недоступен, завещание доходит ровно один раз — не выполнено для гонки retire↓/birth↑, R1-1
- [x] Ошибка `respawn()` → повтор → `BodyUnavailable`, завещание сохранено — тесты есть
- [~] Стенд — по отчёту пройден (обе ветки, $0.0003); надзором не перепроверен, см. выше

### Находки
- R1-1 · major · `runtime/lastorder/lifecycle.py:221-245`, `:281-286` · «Закрыватель гонки» C-3
  теряет настоящее завещание. Настоящий hive (`runtime/hive/lifecycle.py:211-227`) на `birth` закрывает
  живой узел слота как `lost` и пишет ему своё аварийное завещание (`_close_lost` → `store.retire`).
  Поэтому после успешного `birth` повторный `retire` старого серийника всегда получает 409
  `already_retired`, а `_deliver_after_birth` считает это доставкой и удаляет `pending/will-NNNNN.md`.
  Итог: в базе шаблон hive без последнего действия, единственная копия завещания узла стёрта.
  `FakeRegistry` в тестах эту семантику hive не моделирует, поэтому
  `test_retire_down_birth_up_delivers_the_parked_will_after_birth` зелёный на неверном поведении.
  Ожидается: `birth` не вызывается, пока завещание не доставлено или не отвергнуто навсегда
  (не-409 4xx). В раунде, где `retire` дал `HiveUnavailable`, — пауза и повтор раунда без `birth`.
  NETWORK.md §9 («birth — после восстановления») это допускает. `_deliver_after_birth` после этого
  не нужен. Тест: `FakeRegistry.birth` закрывает живой узел слота, и последующий `retire` этого
  серийника даёт 409. Проверить, что при retire↓ и birth↑ в одном раунде `birth` не вызывается до
  успешного `retire`, а в `retire_calls` попадает текст узла.
- R1-2 · minor · `runtime/lastorder/lifecycle.py:173-175` · 409 `already_retired` на самом первом
  `retire` (до него этот узел никто не ретайрил) значит одно: hive уже закрыл узел сам (lost-close,
  reconcile при старте). Завещание узла при этом не паркуется и пропадает молча. Ожидается: на первом
  вызове 409 → `_park` с причиной и лог `will.superseded`, `will_delivered=False`. В цикле повторов
  409 после `HiveUnavailable` оставить доставкой: там это потерянный ответ на свой же вызов.
- R1-3 · minor · `runtime/lastorder/death.py:125-135` · `_death_text` берёт первую подходящую строку
  окна `recent_chat`. Если в окне осталась строка прошлой смерти, текст причины будет от неё. Кроме того,
  метод дублирует `is_death_line`. Ожидается: последняя подходящая строка окна через
  `is_death_line` (с `self._phrases`), плюс тест на окно со строками двух смертей.

### Чек-лист проекта
- Фильтр сырых id: тело завещания проверяется на `minecraft:`-id, служебные строки uc в детекторе
  отсекаются. `DeathCause.text` (ванильная строка с именем моба) в промпт завещания сейчас не идёт.
  T-014 должна пропустить её через `runtime/perception`, прежде чем класть в `ReflectionInput`.
  Это заметка для T-014, не находка.
- Аварийное завещание: широкий `except Exception` плюс минимальный файл при `SchemaError` — без
  замечаний.
- Единственный писатель базы — hive: мозг пишет только `run/<slot>/pending/` — без замечаний.
- Уровни LLM: один вызов `smart` на смерть, новых вызовов нет — без замечаний.
- Отклонение «Проверок» (команда стенда через stdin с `-e`) принято. Проброс LLM-переменных в
  compose mind-1 относится к T-014.

### Вне задачи
- `runtime/hive/lifecycle.py:193` читает `NAME_PRESET`, мозг — `NODE_NAMING_PRESET`. Имя пресета
  в аварийном завещании hive может разойтись с мозгом. Сфера T-022/T-014.

## Раунд 2 · 2026-10-17 · вердикт: accept · headless
Проверено: `git diff main...z/T-013-death-will-path` @ 29e5d1f (фиксы — ccaebe9)

### Проверки (прогнаны надзором)
- `uv run pytest` — 429 passed
- `uv run python tools/check.py` — Every workplace is intact
- Стенд: `docker compose … build mind-1` (образ уже с текущим `runtime/`, все слои из кэша), затем
  `docker compose … run -T --rm -e LLM_SMART_MODEL=openrouter/no-such/emergency-branch-test mind-1
  python - < dev/death_scenario.py` → `scenario passed`. Serial 00023 → 00025, причины `game` и
  `stuck`, наследник видит оба завещания, слот держит 00025, pending пуст, $0.0000. Лог пути:
  `will.emergency → will.retired → body.respawned → life.birth` для обеих смертей.
- Умную ветку с ключом сам не прогонял: права не дают извлечь `OPENROUTER_API_KEY` из `.env`.
  Код вызова `smart` (`will.py`, `will_prompt.md`) в раунде 2 не менялся. Умный прогон после фиксов
  есть в отчёте раунда 2 (00020 → 00022, $0.0003), тексты — в раунде 1. Принимаю.

### Критерии приёмки
- [x] `DeathDetector` — без изменений с раунда 1 плюс R1-3
- [x] Причина `ttl` → `stuck` → `game`
- [x] `write_will` и четыре отказа
- [x] Порядок `on_death`
- [x] Hive недоступен, завещание доходит ровно один раз: `birth` не идёт, пока доставка не решена
  (`lifecycle.py:255-287`). Тесты `test_birth_waits_for_the_delivery_with_real_hive_semantics` и
  `test_delivery_and_birth_both_flaky_birth_still_waits` идут на `FakeRegistry` с семантикой hive
  (birth закрывает живой узел, затем retire даёт 409)
- [x] Ошибка `respawn()` → `BodyUnavailable`, завещание сохранено
- [x] Стенд — см. «Проверки»

### Ответы на находки раунда 1
- R1-1 · закрыта. 409 внутри цикла повторов считается доставкой, и это верно: hive закрывает узел
  сам только на `birth` и в startup reconcile (`runtime/hive/lifecycle.py:176-189`, `:215-227`).
  `birth` теперь ждёт доставку. Reconcile трогает только узлы с пометкой lost и дубли слота,
  а единственный живой узел слота туда не попадает. Значит, во время простоя hive 409 — это
  потерянный ответ на собственный вызов.
- R1-2 · закрыта: на первом `retire` 409 даёт `SUPERSEDED` и парк с настоящим текстом, тест есть.
- R1-3 · закрыта: берётся последняя строка окна через `is_death_line`, тест есть.

### Находки
- нет

### Чек-лист проекта
- Фильтр сырых id, аварийное завещание, единственный писатель базы, уровни LLM — без изменений
  с раунда 1, без замечаний. Заметка для T-014 про `DeathCause.text` через perception остаётся.

### Вне задачи
- Шаблон `emergency_will` (`runtime/knowledge`, T-007) на стенде режет строку посреди слова
  («раньше первого шаг.») и склеивает урок с «Проверь сам» без точки. Это косметика шаблона
  схемы, а не T-013.
