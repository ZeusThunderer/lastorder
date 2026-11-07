# T-054. Отчёт исполнителя

## Раунд 1 · 2026-11-07
Коммиты: 6b428b8

### Сделано
- **Stale-dead створ** (`runtime/mind/executor.py`): путь смерти armed только после
  первого живого состояния этой жизни (`_saw_alive`). Тело, восстановленное мёртвым
  (hp=0 при in_game) до этого, — не смерть узла: `_stale_dead()` пишет одну запись
  `body.stale_dead` на эпизод и сам вызывает `body.respawn()` (ретрай каждый тик;
  ошибка моста — `error/stale_respawn`). `game.death` нет, завещание не запрашивается.
  `_after_respawn` сбрасывает створ: мёртвое сразу после runner-respawn тело — снова
  stale-труп, не смерть новорождённого. После живого состояния hp=0 — смерть как раньше.
- **Предел wait_events** (`runtime/mind/mcp_body.py`): кап из окружения
  `WAIT_EVENTS_MAX_S` (умолчание 600; вне (0, 600] — ошибка старта). Запрос сверх
  предела — не ошибка: ожидание урезается, ответ несёт применённое значение.
  Описание инструмента называет предел (`__doc__` присвоением: SDK читает его с
  `fn.__doc__`, f-string литералом docstring'ом не становится). t <= 0 — отказ.
- **Compose** (`docker/compose.yml`): `WAIT_EVENTS_MAX_S: "${WAIT_EVENTS_MAX_S:-25}"`
  в mind-env всех слотов — запас ниже 30 с, после которых zcode убивает вызов
  инструмента (7 убитых wait_events на node-2, T-052).
- **Тесты**: приёмочный `test_stale_dead_body_respawns_without_node_death` (hp=0 со
  старой строкой смерти, флейковый мост с ретраем, в логе один `body.stale_dead` и
  нет записей `kind: death`; затем обычная смерть после живого состояния);
  `test_wait_events_clamps_an_oversized_ask_to_the_cap`, `..._names_the_cap`,
  `..._zero_is_still_rejected`; пин compose `..._cap_stays_under_the_harness_kill`.
  Шесть тестов пути смерти, кормивших hp=0 первым тиком (`ttl_by_life_clock`,
  `status_shape`, `retries_bounded`, `birth_arms`, `birth_refused`, `drops_intent`),
  теперь делают живой тик первым — по новой семантике смерть валидна только после
  живого состояния. `Stack` ждёт первого живого опроса C1 и принимает `wait_max_s`.
  Гонка `test_several_chat_lines_...` починена ожиданием полной пачки из 3 строк
  (дрен C1 может разрезать пушы по тикам; ждали первую попавшуюся пачку — флейк
  2/4 прогона под нагрузкой полного файла).

### Проверки
```
$ bash dev/check.sh
pytest: ok · 921 passed, 3 skipped, 2 warnings in 169.95s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ uv run pytest tests -k "death or wait_events" -q
51 passed, 8 deselected in 13.57s
```
Комбинированный прогон изменённых файлов 5 раз подряд: 138 passed.

### Отклонения от задачи
- `runtime/node/adapters.py` не тронут: предел читает mind-N из своего окружения при
  старте контейнера (compose); адаптеры харнессов это окружение не формируют.

### Вне задачи
- Осознанная граница семантики: смерть тела в окне рестарта самого mind-N (тело умерло
  без наблюдателя) теперь тоже попадает под stale-исключение — завещания не будет.
  Старый код в этом окне давал ложный «second death» с повторной сессией завещания
  (112/113 из T-052); задача формулирует исключение без уточнения причины смерти.
- Прогон test_mcp_body оставляет мусор `.mcp-body-test-*.jsonl` (часть Stack'ов не
  удаляет tmp-файл; воспроизводится на чистом main, Windows). Не чинил: не связано
  с T-054.

### Вопросы
- нет
