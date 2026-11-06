# T-045. Отчёт исполнителя

## Раунд 1 · 2026-11-06
Коммиты: 7cbf3ca, 719b759, 0cc28ae, 3325665, c8bf3df, 43a7021, 664b0a6 (последний — итог)

### Сделано
- `PiAdapter` (adapters.py): при именованном токене хаба (`<base>:node-N`, базовый — отказ в конструкторе
  и в NodeConfig) жизнь и воля едут `hapi pi`: промпт не в argv (stdin у HAPI) — опенер доставляется
  через REST хаба (`POST /api/sessions/<hub-id>/messages`, логин тем же namespace-токеном); boot-report —
  две строки лога обёртки (hub-id «Reporting session…» + pi-id «persisted to metadata», обе нужны: REST
  адресует hub-id, `--session-id` — pi-id [проверено: живой стенд]); расход — сумма `usage` из файла
  сессии Pi (`session.ended` несёт tokens/cost_usd); без токена — прямой pi из T-066 без изменений.
- Раннер: одна длинная сессия на жизнь — boot-wait, опенер, resume через `--session-id`, потерянный
  resume сбрасывается после двух мгновенных смертей, `session_missing` при смерти процесса с живым телом;
  `stuck` — по росту файла сессии (STUCK_S, по умолчанию 600); воля в консольном режиме кончается по
  файлу завещания (таймаут — как прежде); тревоги доски (§7) через X-Runner-Key: harness_failover,
  session_missing, stuck, quota, emergency_will; отказ Z.ai → тревоги quota+harness_failover и переход
  на OpenCode через шлюз (QUOTA_ACTION=switch).
- boardlink выключен при harness=pi (заметка надзора 2026-11-05: мост и misaka-link ack-ают одну ленту).
- `drop_pi_agent_dir` пишет файл `slot` (root 0444, лечится при каждом рендере, R2-3) — misaka-link
  читает его для delivery_failing (T-048 R1-4): `misakalink.start {slot:"LastOrder01"}` [проверено: стенд].
- Сессии Pi — в рабочий место (settings.json `sessionDir: .pi-sessions`): tmpfs-дом умирает с контейнером
  вместе с ключом resume [проверено: «No session found», починено, resume через рестарт контейнера живой].
- Docker: `HAPI_TOKEN_<N>` выводится в up.sh из `HAPI_CLI_KEY` (namespace на слот), entrypoint
  материализует `/run/node/secrets/hapi_key` и чистит env; в x-node добавлены HAPI_URL/HAPI_TOKEN_FILE/STUCK_S.
  Сети node-2/3 → hapi-hub уже были готовы (svc-N у хаба) — правок не потребовалось.
- AGENTS.md сестры: строка оператора в консоли (без конверта — «голос оператора, не доска»), конверты
  `[net from …]` как доска, заявки оператору через доску в пределах потолка, §13.

### Стенд (3 слота, 13:45–14:38 UTC, порт хаба 3007 — 3006 занят стойкой t044)
- Все три сестры видны в HAPI в своих namespace: активные сессии, summary смены; после рестарта
  контейнера node-1 сессия возобновилась (`--session-id`, hub active=true) [проверено].
- Оператор → чат (14:22): сестра 154 назвала сообщение сообщением оператора («Оператор спросил статус
  через консоль. Ответил в чат…» — GOALS.md п.2, DONE) — остаток критерия 8 T-048 закрыт [проверено].
- Задача `net` #27: взята 153 (`claimed_by=153`, `done`); 154 и 152 ответили сообщениями оператору
  (#28/#29, с координатами) — «видно, кто взял» [проверено].
- Воли от агента: 153, 152 (`will.written origin=agent`, retire) и 154 (`will.staged` 371 символ,
  hive events) [проверено]. Аварийные воли (#18–20) — эпоха починки, каждая честная.
- Тревоги на доске: session_missing (#24–26, #31), emergency_will — живьём; quota/harness_failover —
  юнит-тесты (квота не кончалась); delivery_failing не срабатывала (доставка не падала).
- Расход (сумма usage файлов сессий, totalTokens с кэшем; $ = 0 — подписка): node-1 2.12M, node-2 1.71M,
  node-3 1.06M токенов за прогон. Узлы остановлены мной (квота), инфраструктура стоит.

### Проверки
```
$ bash dev/check.sh
pytest: ok · 888 passed, 3 skipped, 2 warnings in 98.73s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ node --test runtime/pi/misaka-link/*.test.ts   # ℹ tests 38, pass 38, fail 0
$ git diff --stat main...HEAD — только зона задачи (см. отклонения)
```

### Отклонения от задачи
- Тронуты сверх списка (проводка/заметки надзора): `runtime/node/__main__.py` (передача hapi-параметров
  и BoardAlarms), `docker/node/up.sh` + `entrypoint.sh` (подъём токена — прямо в заметке надзора),
  `runtime/pi/misaka-link/misaka-link.ts` (чтение slot-файла — заметка T-048 R1-4, 11 строк).
- Опенер жизни/воли идёт REST-сообщением хаба от namespace-токена (T-044: иного входа в сессию нет).
- Прогон длиннее окна (≈53 мин): 4 живых бага чинились по ходу, каждый — коммит + тест.

### Вне задачи
- `hapi pi` без `--session-id` иногда продолжает последнюю сессию cwd: опенер воли однажды лёг в файл
  жизни, а волевой процесс (hub 2f5521be) отвечал на доску #30 вместо смерти. Воли дошли, но «свежая
  воля» не гарантирована — нужно решение надзора (флаг новой сессии у обёртки? отдельный cwd воли?).
- Рестарт контейнера в момент смерти обрубает волю (аварийная воли на старте следующего раннера);
  аварийный путь покрывает, но воля теряется — следствие «no unattended resurrect», не чинил.
- У стенда t044/t048 поднятые хабы держат порт 3006 и подсети; мой хаб — на 3007 (HAPI_PORT).

### Вопросы
- нет

## Раунд 2 · 2026-11-06
Коммиты: 5522451 (R1-1..R1-4)

### Ответ на ревью
- R1-1 · fixed 5522451 — `_wait_session` для long-running зондирует `output_of` (лог обёртки этого
  запуска) в темпе опроса при живом процессе: метка квоты гасит процесс (`session.quota_live`) и
  уходит в общий путь quota (тревога + switch/wait). При смене харнесса `state.session_id`
  сбрасывается. Тест на hapi-адаптере: `test_hapi_live_quota_kills_the_session_switches_and_resets_the_resume_key`
  (живой процесс + квота в логе → тревоги quota и harness_failover, следующий ход `opencode:life`
  без resume, session_missing не поднимается).
- R1-2 · fixed 5522451 — `set_will_mode(…, serial=…)` рендерит воле свой пустой каталог на смерть:
  `settings.json.sessionDir = .pi-will-<serial>` (жизнь остаётся в `.pi-sessions`); страховка:
  boot воли с pi-id жизни → `will.wrong_session`, процесс гасится и перезапускается один раз.
  Тесты: `test_pi_will_mode_swaps_the_session_dir_to_a_per_death_one`,
  `test_hapi_will_restarts_once_when_it_boots_the_life_session`. Стенд [проверено: 15:19 UTC,
  RCON-смерть 158]: воля в свежей сессии (`.pi-will-00158/…01a1b171` ≠ жизнь `…01a1b16f`),
  `will.staged` через 49 c → `will.written origin=agent`; первая попытка (156) дала аварийную волю
  по вине самой сестры — `submit_will` дважды отклонён hive («expected 2-4 lines, got 1», затем
  перебор квоты 220 токенов), сестра честно остановилась по навыку, аварийный путь сработал как
  задуман (таймаут 900 c, тревога, retire).
- R1-3 · fixed 5522451 — `will.session_timeout` пишется для всех харнессов (условие
  `not long_running or staged is None`); проверка добавлена в
  `test_will_session_timeout_falls_back_to_emergency`.
- R1-4 · fixed 5522451 — фраза о границе доверия в докстринге `_wait_session`: лог обёртки — файл
  сестры, подделка даёт лишь переход на шлюз, потолок шлюза ограничивает расход.
- R1-5 · принято надзором, не трогал.

### Проверки
```
$ bash dev/check.sh
pytest: ok · 891 passed, 3 skipped, 2 warnings in 93.02s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ node --test runtime/pi/misaka-link/*.test.ts   # ℹ tests 38, pass 38 (раунд 1, без изменений)
```

### Отклонения от задачи
- нет новых.

### Вне задачи
- Воле 156 сестра не смогла оформить тело (две ошибки формата — навык write-will, не код);
  аварийная воля закрыла смерть. Возможно, навык стоит смягчить (подсказка формата в ошибке
  hive уже есть) — зона надзора/персонажей.
- В сессии воли 156 misaka-link 17 раз не дотянул ленту доски (TimeoutError) — hive отвечал
  медленно в момент смерти; доставка восстановилась, тревога не поднята (5-порог не достигнут).

### Вопросы
- нет
