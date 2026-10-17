# T-009. Ревью надзора

<!-- Проверка плана: ## План N · YYYY-MM-DD · вердикт: ok | revise [· headless], находки P<N>-<k>.
     Раунд, вызванный ZCode через dev/call_supervisor.sh, помечается в заголовке: · headless -->

## Раунд 1 · 2026-10-17 · вердикт: changes · headless
Проверено: `git diff main...z/T-009-perception-modes` @ 50d3568, построчно (critical).

### Проверки (прогнаны надзором)
- `uv run pytest` — 95 passed
- `python tools/check.py` — Every workplace is intact
- Префиксы uc сверены с исходниками в корне: `Settings.java:94`, altoclef `Debug.java:28,48,73,94`,
  tungsten `Debug.java:16,48,71`. Других путей в чат нет: `sendMessage` есть только в этих двух
  `Debug`, вызовов `logMessage(…, false)` нет. `[Baritone]` не нужен: `baritone/` и `shredder/`
  не компилируются (`settings.gradle.kts:23`, `build.gradle:118`). `chatLogPrefix` в нашей
  конфигурации не переопределён. Многострочный `logError` altoclef приходит одной строкой
  `onChatMessage` с маркером в начале и отсекается целиком.

### Критерии приёмки
- [ ] `PERCEPTION_MODE=plain|masked`, по умолчанию `plain`, неизвестное — ошибка при старте —
  `config.perception_mode()` есть и проверен тестом, но `Perception` переменную не читает (R1-1).
- [x] Стабильный ключ в обоих режимах, display и `minecraft:` дают один ключ —
  `test_plain_key_is_stable_and_bridges_display_and_registry_forms`.
- [x] `plain`: `see()` даёт ключ, имя и термин в «», `raw()` работает — `test_plain_see_*`,
  `test_plain_raw_*`; `raw()` для masked — прежние тесты.
- [x] `masked`: 8 прежних тестов зелёные, проверки не изменены (только явный `mode="masked"`
  в фикстуре); `see/scrub/leaks` в masked по коду прежние. `normalize` теперь сводит пробелы
  к `_`. Для masked это строже: display-форма даёт тот же handle, `_forms` её по-прежнему ловит.
- [x] `chat.py`: `strip_formatting`, `is_service_line` со списком и источником, `clean_chat` —
  `test_perception_chat.py`.
- [x] Ни одна служебная строка из образцов не проходит `clean_chat` в обоих режимах —
  `test_no_service_line_survives_clean_chat_in_either_mode`.

### Находки
- R1-1 · major · `runtime/perception/translate.py:75` · `Perception(..., mode="plain")` по
  умолчанию берёт `plain` из литерала и `PERCEPTION_MODE` не читает. При `PERCEPTION_MODE=masked`
  любой вызов без явного `mode` всё равно даёт `plain` и пускает в промпт имена игры. Это
  fail-open в критичной зоне. Уже сейчас так работают `tools/snapshot_replies.py:76` и
  `dev/llm_bench.py:264`. Переменная пока ни на что не влияет, а критерий 1 требует, чтобы режим
  задавала она. · Нужно: `mode: str | None = None`, при `None` брать `runtime.config.perception_mode()`
  (явный `mode` остаётся для тестов). Тест: `monkeypatch` `PERCEPTION_MODE=masked` →
  `Perception(desc, salt=…)` в режиме masked, его `see().render()` без утечек сырого id;
  `PERCEPTION_MODE=stealth` → `ValueError` при создании `Perception`. Импорт из `runtime.config`
  в translate.py циклов не даёт (config зависит только от `os`/`pathlib`).
- R1-2 · minor · `runtime/perception/translate.py:26`, `runtime/config.py:11` · список режимов
  задан дважды (`MODES` и `PERCEPTION_MODES`). · Один источник: translate.py импортирует
  `PERCEPTION_MODES` из config (это сделает и R1-1).
- R1-3 · minor · `runtime/perception/translate.py:42` · `normalize` сводит display-имя к id
  простой заменой пробелов. Для `Birch Leaves` это верно, для `Block of Iron` (`iron_block`) и
  `Jack o'Lantern` (`jack_o_lantern`) — нет: один предмет получит два ключа, а `raw()` вернёт
  несуществующий id. Критерий выполнен на образцах E1, в этой задаче не исправлять. · Учесть
  в T-002/T-011: брать реестровый id из uc, где он есть, или завести карту display → id.
- R1-4 · minor · `runtime/perception/chat.py:43` · `is_service_line(line)` получает сырой `line`,
  а следующая строка приводит его через `str(line)`. Не-строка упадёт в `re.sub` раньше. ·
  Привести один раз в начале цикла или убрать `str()`.

### Чек-лист проекта
Без замечаний. LLM-вызовов нет, `unionclef/` и `NetTyan/` не тронуты, изменения в пределах
«Можно трогать» (в `py4j_body.py` — только импорт вместо копии и строка докстринга), трейлеры
`Agent: zcode` на месте.
