# T-044. Отчёт исполнителя

## Передача · 2026-10-26
- сделано: ничего по коду и стенду — бюджет чата (ADR-039, ~150 вызовов) кончился сразу после
  взятия задачи (в этом же чате закрыт T-042 с двумя раундами ревью);
- дальше: задача с чистого листа — прочитать HAPI-доки из «Контекста» задачи, затем
  `docker/hapi/`, слой CLI в `docker/node/Dockerfile`, compose `hapi-hub` (удалить
  `console-pi-web-ui`/`console-pi-web`), скрипты `dev/console_spike/`, стенд и ответы на
  вопросы 1–6;
- проверить: у оператора поднят живой стенд (проект `lastorder`, включая `node1-pi` и
  `console-pi-web-ui`) — поднимать стенд T-044 параллельно нельзя вслепую: сначала сверить
  порты/проекты с оператором; квота Z.ai общая с ZCode-сессией (≤ 4 коротких сессий Pi).

## Раунд 1 · 2026-10-27
Коммиты: 6c4203a (образ/скрипты), 5446c08 (стенд, фиксы, доки), 7344381 (отчёт)

### Сделано
- `docker/hapi/Dockerfile`: образ хаба `node:22-slim` + пин `@twsxtd/hapi@0.30.7`
  (пакет — лаунчер, платформенный бинарник с веб-ассетами идёт optionalDependency),
  `HAPI_HOME=/data`, non-root `node`, healthcheck `/health`, `hapi hub --no-relay`.
- `docker/node/Dockerfile`: слой CLI HAPI тем же пином; `hapi runner` НЕ ставится
  сервисом (ADR-042).
- `docker/compose.yml`: сервис `hapi-hub` (том `hapi-data`, `CLI_API_TOKEN` из
  `HAPI_CLI_KEY`, порт `127.0.0.1:${HAPI_PORT:-3006}`, сети svc-1..3+svc-lo и пустая
  не-internal `hapi-web` — только ради публикации: в internal-сетях маппинг мёртв,
  проверено); сервисы `console-pi-web-ui`/`console-pi-web` и сеть `console-spike`
  удалены.
- `dev/stand_keys.py`: локальный токен `HAPI_CLI_KEY` (именно `*_KEY` — тест
  классифицирует по `KEY|PASSWORD`; `*_TOKEN` падал). В `.env` главного корня
  сгенерированы `HAPI_CLI_KEY` и `HIVE_OPERATOR_KEY` (оба added).
- `dev/console_spike/`: `up_spike.sh` переписан под HAPI-стенд (+параллельный
  проект через `HAPI_PROJECT`/порты); `hapi_agent.py` — запуск `hapi pi` от uid
  `sister` без TTY (режимы slot/lo, start/status/stop); `hapi_chat.py` — драйвер
  оператора (те же REST-вызовы, что страница); `pi_rpc.py`, `thought.ts` оставлены
  (откатный путь ADR-042 и находка T-041).
- Доки: итог HAPI в `console-choice.md` (заголовок рецепта T-041 поправлен),
  раздел «Консоль» в `docker-topology.md`.

### Стенд
Поднят параллельным проектом `t044` (порты 25575/18081/3006): живой стенд
оператора (проект `lastorder`) автоподнялся вместе с Docker Desktop и занял
25565/18080 — по передаче вслепую не трогал, снят только мой `t044` (тома живы).
Две Pi-сессии с LLM-ходом (обе короткие): слот-1 (zai) и LO-тест (шлюз,
отдельный виртуальный ключ `t044-lo-spike2` в t044-шлюзе — в fresh gateway-db
нет ключей из .env оператора).

### Проверки
```
$ bash dev/check.sh
pytest: ok · 783 passed, 3 skipped in 81.61s
check.py: ok · Every workplace is intact
$ docker compose build hapi-hub node-1  — ok; hapi --version в обоих: 0.30.7
$ GET http://127.0.0.1:3006/ -> 200; с LAN-IP хоста (172.18.128.1) -> connection refused
$ hapi_chat sessions — обе машины-агента в одном списке:
  pi glm-5.3-flash machine=013b3ca9 (node1-pi, слот) / pi qwen3.7-flash machine=37bbb4a8 (lo-pi)
$ оператор -> слот: POST message 200; транскрипт: mcp__body__observe (ok, 1.3s) -> «связь»
$ оператор -> LO: POST message 200 -> «связь» (через llm-gateway)
$ grep ZAI-префикса по /data хаба, docker logs, логам hapi в обоих песочницах — 0
$ namespace `<token>:node-1`: логин ok, чужих сессий 0
```

### Вопросы 1–6 (из задачи)
1. [проверено] `hapi pi` от `su sister`, stdin=/dev/null, без TTY; аргументов Pi не
   нужно (конфиги `~/.pi/agent`, HAPI добавляет свой title-extension); env
   `HAPI_API_URL`+`CLI_API_TOKEN`. Флага «первое сообщение» нет [источник:
   cli/src/commands/agentCommandOptions.ts]: веб, REST POST в сессию [проверено:
   дошло и отвечено] или расширение Pi.
2. [проверено] `~/.pi/agent/sessions/<slug>/*.jsonl`; в assistant-записях `usage`
   (input/output/cache/total, cost). `/session` в чате печатает сводку.
3. [проверено] SIGTERM в группу (su→bash→hapi) + добив платформенного бинарника,
   который выходит из группы (pkill по паттерну); конец со скрипта — exit процесса
   + pgrep; хаб помечает сессию inactive за секунды.
4. [проверено] Сессия остаётся в списке, транскрипт читается; `POST /resume` → 200,
   но лишь переметки: без раннера ничего не поднимает (`/api/machines` пуст);
   нативный resume — `hapi pi --session-id` на машине (раннер T-045).
5. [проверено] `hapi opencode` есть в списке агентов образа; конфиг OpenCode — T-045.
6. [источник: cli/src/pi/runPi.ts:593 + проверено по файлу сессии] ввод HAPI
   доходит как обычный user-prompt, метки источника нет; различение — подпись
   оператора текстом или расширение (решение надзора/T-045).

### Эскалация оператору (граница токена, ADR-042)
[проверено] С базовым `HAPI_CLI_KEY` из bash сестры node-1: логин веб-пользователем
(OK), список ВСЕХ сессий сети, чтение чужого транскрипта, **запись в чужую сессию
от имени оператора — HTTP 200, сообщение дошло, LO-агент ответил**. CLI-аналоги:
`hapi inspect-peer`, `hapi ping-peer`. Remote spawn невозможен без `hapi runner`
(не запущен, machines=[]). Митигация: namespace-суффикс `<token>:node-1` — чужих
сессий 0 [проверено], но оператор с базовым токеном сестру тогда не видит (одна
страница = один namespace). Решение за оператором: общие токены+namespace
по умолчанию или изоляция ценой отдельных логинов.

### Отклонения от задачи
- Переменная названа `HAPI_CLI_KEY`, не `HAPI_CLI_API_TOKEN`: тест
  test_stand_keys классифицирует стендовые ключи по подстроке KEY|PASSWORD.
- Тестовый агент LO поднят контейнером СЕРВИСА last-order (`compose run … sleep
  infinity`, раннер/admin не запущены) — как node1-pi в T-041; сам admin — T-043.

### Вне задачи
- `docker/node/up.sh` не поднимает `HAPI_CLI_KEY` (файл вне зоны) — полный стек
  получит хаб с самосгенерированным токеном; подъём ключа — T-043/T-045.
- Оператора `.env` осталась строка `HAPI_CLI_API_TOKEN=…` (первая генерация до
  переименования, значение не используется; stand_keys значения не удаляет).
- В default-сети хаб виден mc-server/бodies/hive (hapi-web нужен только для
  публикации порта; сам процесс наружу ничего не инициирует — relay off).
- Старый стенд оператора (lastorder) поднялся сам с Docker Desktop и работал всё
  время прогона параллельно; контейнер `node1-pi` (T-041, exited) не трогал — мои
  контейнеры префиксованы проектом.
