# T-026. Отчёт исполнителя

## Раунд 1 · 2026-10-18
Коммиты: 67b334a, 42cd292, 1ffa504, 3d68b23, 9c9feb6

### Сделано
- `docker/gateway/config.yaml` — конфиг LiteLLM proxy (образ `ghcr.io/berriai/litellm:main-stable`,
  версия 1.106.0): уровни node (`openrouter/qwen/qwen3.7-flash`) и smart
  (`openrouter/z-ai/glm-5.3-flash`) как настройки шлюза, по два `model_name` на уровень — полное
  имя и голый id провайдера (см. «Грабли»); ключ провайдера, мастер-ключ и БД — только
  `os.environ/…`-ссылки.
- `docker/compose.yml` — сервис `llm-gateway` + `gateway-db` (postgres:15, приватная сеть
  `gateway-internal`, без опубликованных портов; у шлюза портов наружу нет вовсе); volume
  `gateway-db`; `mind-1` и `hive` получают `LLM_API_BASE=http://llm-gateway:4000` и свой
  виртуальный ключ (`GATEWAY_KEY_MIND_1` / `GATEWAY_KEY_HIVE`).
- `docker/gateway/up.sh` — подъём шлюза с лифтом секретов из корневого `.env` (см. «Отклонения»).
- `runtime/llm/client.py` — виртуальный ключ уровня: `LLM_<TIER>_API_KEY` перекрывает общий
  `LLM_API_KEY`; адрес шлюза — прежний `LLM_<TIER>_API_BASE`/`LLM_API_BASE`; строка модели не
  меняется (шлюз маршрутизирует по ней).
- `dev/gateway_keys.py` — issue/revoke/list; потолки на ключе: `rpm_limit` и `max_budget` с
  `budget_duration: 1h` (рекуррентный бюджет в час). Вызовы идут через `docker compose exec`:
  мастер-ключ не покидает контейнер, в argv и файлах ключей нет.
- `dev/gateway_report.py` — расход по ключам (с алиасами; отозванные помечены) и моделям за окно
  `--since`, итог и $/ч.
- `tests/test_gateway_config.py` — 26 проверок: конфиг (уровни, env-ссылки, drop_params), compose
  (нет портов, изоляция БД, ключ провайдера только у шлюза, env потребителей), «нет секретов в
  файлах», клиент (ключ уровня и фолбэк), полезные нагрузки скриптов, парсер `--since`, рендер
  отчёта, лифт в up.sh.
- `.env.example` — имена `GATEWAY_MASTER_KEY`, `GATEWAY_DB_PASSWORD` (с ограничением на символы),
  `GATEWAY_KEY_NODE_1/HIVE/MIND_1`.

### Проверки
```
$ uv run pytest
537 passed in 28.09s
$ uv run python tools/check.py
CHARACTERS: last_order, vesper
Every workplace is intact: files present, links resolve, boxes parse.
$ bash docker/gateway/up.sh        # вместо голого `docker compose up -d llm-gateway`, см. Отклонения
 Container lastorder-gateway-db-1 Healthy
 Container lastorder-llm-gateway-1 Started
$ docker compose -f docker/compose.yml ps llm-gateway gateway-db --format '{{.Name}} {{.Status}}'
lastorder-gateway-db-1 Up About an hour (healthy)
lastorder-llm-gateway-1 Up 16 minutes (healthy)
```

Стенд, живые прогоны (шлюз поднят, ключи выдавались скриптом и отозваны после):

Потолок ходов/мин (ключ `t026-client-probe`, rpm=3, четыре запроса подряд):
```
1 -> 200 ok
2 -> 200 ok
3 -> 200 ok
4 -> HTTP 429 : {"error":{"message":"Rate limit exceeded for api_key: dc1879…e7.
   Limit type: requests. Current limit: 3, Remaining: 0.
   Limit resets at: 2026-10-18 16:54:36 UTC", …}}
```

Потолок $/ч (ключ `t026-budget-probe2`, бюджет $0.0004/ч, запросы с длинным выводом):
```
5 -> HTTP 422 : {"error":{"message":"Budget has been exceeded! Key=t026-budget-probe2
   (sk-...iqIg) Current cost: 0.00040828749999999997, Max budget: 0.0004",
   "type":"budget_exceeded", …}}
```

`runtime/llm/client.py` через шлюз (образ mind, `LLM_API_BASE=http://llm-gateway:4000`,
модель `openrouter/qwen/qwen3.7-flash`, виртуальный ключ в `LLM_API_KEY`):
```
reply: ok
cost_usd: 2.34e-05 | prompt_tokens: 26 | completion_tokens: 174
```

Выдача и отзыв (`dev/gateway_keys.py`): `issue --alias node-1 --rpm 12 --hourly-usd 0.12` печатает
алиас, потолки и токен один раз; `list` показывает alias/spend/rpm/$/ч/budget_reset_at; после
`revoke` запрос с тем же токеном:
```
revoked key -> 401 {"error":{"message":"Authentication Error, Invalid proxy server token
   passed. Received API Key = sk-...iqIg, …}}
```

Отчёт расхода (после отзыва всех пробных ключей; свежий вывод):
```
$ uv run python dev/gateway_report.py --since 1h
window: last 1 h
by key:
  11cc4201… (revoked)      $0.00041
  dc187920… (revoked)      $0.00015
  0c4657d2… (revoked)      $0.00002
  53b4f5c0… (revoked)      $0.00000
  42022e6c… (revoked)      $0.00000
by model:
  openrouter/qwen/qwen3.7-flash                $0.00059
  unknown-model                                $0.00000
  openrouter/z-ai/glm-5.3-flash                $0.00000
  qwen/qwen3.7-flash                           $0.00000
total $0.00059 -> $0.00059/h over the window
```

Суммарный расход прогона T-026 на OpenRouter ≈ $0.0006.

### Критерии приёмки
- Шлюз в compose, ключи провайдеров только у него, порт только во внутренней сети — да; тесты
  `TestComposeWiring`, `TestNoSecretsInFiles`, живой прогон.
- Виртуальные ключи на слот/Last Order/mind-N, скрипт выдаёт и отзывает — да; live: issue/list/
  revoke→401. Боевые ключи выдаст оператор после вписывания `GATEWAY_MASTER_KEY` в корневой
  `.env` (см. «Отклонения»).
- Потолки ходов/мин и $/ч с понятным текстом — да, живые 429/422 выше (проверено и чекером:
  rpm=1, 200 → 429).
- `dev/gateway_report.py` — да, вывод выше.
- GLM Coding Plan через шлюз — **не проводим**: условия плана разрешают только поддерживаемые
  инструменты, LiteLLM/прокси в списке нет (T-024, `docs/agents/harness-spike.md` шаг 0, ADR-033:
  отзыв закрыт отрицательно). Отметка также в комментарии `docker/gateway/config.yaml`.
- Клиент ходит через шлюз, прежние тесты зелёные — да (`reply: ok`, цена от прокси; 537 passed,
  `tests/test_llm_client.py` не менялся).

### Отклонения от задачи
- `docker compose -f docker/compose.yml up -d llm-gateway` из «Проверок» выполняется как
  `bash docker/gateway/up.sh`: compose читает `.env` из каталога compose (docker/), а не из корня
  репозитория, и без `GATEWAY_MASTER_KEY` LiteLLM отказывается стартовать вовсе (проверено,
  fail-closed: «refused to start … weak or unset master key»). Обёртка лифтит
  GATEWAY_MASTER_KEY/OPENROUTER_API_KEY/GATEWAY_DB_PASSWORD из `.env` главного worktree
  по-переменно (не env_file, P1-6), снимая кавычки (грабля T-025); `.env` не трогал.
- Добавлен сервис `gateway-db` (postgres): виртуальные ключи, потолки и учёт расхода в LiteLLM
  требуют БД; БД приватна для шлюза (отдельная сеть, без портов).
- Боевые ключи узлов на стенде не оставлены: мастер-ключ стенда — сессионный (мой/чекера),
  оператор выпишет свои после `GATEWAY_MASTER_KEY` в корневом `.env` и пересоздания шлюза через
  up.sh; все пробные ключи отозваны (`list`: no virtual keys).

### Вне задачи
- Работавшие до T-026 контейнеры (mc-server, body-1, mind-1, hive, jev) не трогал; их env
  сойдётся с compose при ближайшем полном `up -d`.
- `runtime/llm/client.py` ретраит и 429/422 потолков (2 лишние попытки с бэкоффом): для
  «исчерпан потолок → ждать» это лишние запросы; кандидат в будущую задачу (клиент вне поворота
  при исчерпании — поведение задаётся ceilings, не кодом).
- Неудачные запросы оставляют в spend-таблицах строки с $0 (`unknown-model`, голые имена моделей)
  — косметика учётных данных LiteLLM, не чинил.

### Грабли (для поиска следующим читателем)
- SDK litellm при переопределённом `api_base` отрезает префикс провайдера: клиент присылает шлюзу
  `model=qwen/qwen3.7-flash`. Отсюда двойные `model_name` в конфиге. Симптом, если имени нет в
  конфиге: вводящий в заблуждение 400 OpenRouter «Invalid model name» по существующему id.
- Пустой `OPENROUTER_API_KEY` у шлюза → 401 «No cookie auth credentials found» → LiteLLM кладёт
  деплоймент в кулдаун (в памяти роутера) → последующие 429 «All deployments in cooldown».
  Лечится правильным env (рестарт тоже снимает — состояние в памяти).
- `/key/generate`: рабочий ключ в поле `key` (начинается с `sk-`), `token` — голый хэш для БД;
  `/key/delete` принимает и то, и другое. `/key/info?key=<хэш>` отдаёт данные вложенно в `info`.
- `rpm_limit` считает и неуспешные запросы (ретраи клиента съедают лимит — так и было задумано
  проверкой); `budget_duration: 1h` выравнивается на границу часа (`budget_reset_at` = :00).
- Первый старт шлюза: 213 миграций Prisma ≈ 4 мин; healthcheck (start_period 60s, retries 5)
  пережил и дошёл до healthy.
- MSYS: `docker run … --config /app/config.yaml` переписывается в `C:/Program Files/Git/app/…` —
  нужен `MSYS_NO_PATHCONV=1`; bind-mount из `/tmp` Git Bash демону не виден — только
  `cygpath -w`. Внутри `docker compose exec` (stdin) проблема не проявляется.

### Вопросы
- нет.

### Вызовы надзора
- нет.

### Самопроверка
- lo-checker: раундов 1, CHECK: clean.
- C-1 · fixed 9c9feb6 — пометка `<хэш8>… (revoked)` в `dev/gateway_report.py` (тест + живой вывод
  выше).
- C-2 · fixed 9c9feb6 — предупреждение в `.env.example` и комментарии compose: пароль БД
  интерполируется в `DATABASE_URL` как есть, только буквы/цифры (@ : / # ? ломают URL).
- C-3 · оставлено: шлюз внизу — спроектированный случай повтора (NETWORK.md §9, ADR-035: при
  исчерпании потолка «узел ждёт»); mind-1 и hive сегодня LLM-вызовов не делают (T-031/T-030), а
  `service_healthy` на шлюзе подвешивал бы чистые `up` без `GATEWAY_MASTER_KEY` (fail-closed) и
  ломал бы `up -d mc-server body-1 mind-1` спайк-оверлея T-024 (R1-3); обоснование — в комментарии
  у env обоих сервисов.
