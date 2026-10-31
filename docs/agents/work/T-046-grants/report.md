
## Раунд 1 · 2026-10-31
Коммиты: 9484834, 355092a

### Сделано
- Выбран **вариант Б** (обоснование ниже): секрет живёт в хранилище hive, наружу его тянет
  только процесс инструмента своим ключом (`X-Tool-Key`, env `HIVE_TOOL_KEY`); еgress — через
  отдельный прокси. Bash песочниц (включая саму Last Order) секрета не видит.
- `runtime/hive/grants.py` (новый): `grants.sqlite` в RUN_DIR — записи грантов
  (уникальная пара (kind, what), повторное одобрение реактивирует), секреты, журнал
  `grant_log`; события `grants.approve|revoke` — имена и длины, без значений.
- `runtime/hive/board.py`: `set_grant_hook` — после коммита одобрения запроса с `grant`
  хук создаёт запись; сбой хука не откатывает одобрение (5.3: approved без записи = доступ
  закрыт), событие `grants.hook_failed` с типом исключения. Секрет едет в transition до
  хранилища: 422 на секретный грант без значения, на egress со значением, на не-approve.
- `runtime/hive/mcp.py`: `request_grant(kind, what, why)` (валидация домена/snake_case
  имени) и `grants()` — только сервер Last Order (сёстры структурно). `GrantError` →
  ToolError.
- `runtime/hive/app.py`: `/grants/egress`, `/grants/secret` под `X-Tool-Key`;
  `/grants/revoke` под ключом оператора (запись → revoked, на доске реплика «грант
  отозван», статус остаётся approved); transition принимает `secret` (строка).
- `docker/egress/` (новый): stdlib CONNECT-прокси — список доменов спрашивает у hive
  на каждом коннекте (кэш 3 c), TLS end-to-end, только 443, plain-запросы 405;
  compose-сервис `egress` в `egress-out` + `svc-lo`. Hive получил `HIVE_TOOL_KEY` в env
  (забыл в первом коммите — нашёл на стенде, гейты были закрыты).
- Страница доски: строка `grant: kind what` на карточке запроса, поле «значение секрета»
  при одобрении секретного гранта, кнопка «отозвать грант» у одобренных.
- `dev/stand_keys.py`: `HIVE_TOOL_KEY`; `agent/network/AGENTS.md`: оба инструмента;
  `tests/test_hive_mcp.py`: пин LO-инструментов +2.

### Проверки
```
$ bash dev/check.sh
pytest: ok · 798 passed, 3 skipped in 82.71s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ uv run pytest tests/test_hive_grants.py tests/test_hive_mcp.py tests/test_hive_board*.py tests/test_stand_keys.py -q
84 passed   # все критерии: запрос, одобрение/отказ ключом оператора, грепы секрета,
            # сбой хука (approved без записи и домена), правила секретного поля,
            # отзыв (домен закрыт, реплика на доске), гейты /grants/*
```
Стенд (docker, проект `t046-check` из worktree, hive `t046-mind:check` + mc-server +
egress, порт 18083, LLM-ходов нет; после проверки свёрнут, `down -v`; стенд оператора не
тронут):
```
$ MCP /last-order/mcp: request_grant(egress, "example.com") -> {"id":1}
$                               request_grant(secret, "stand_token") -> {"id":2}
$ curl POST /board/entries/{1,2}/transition (X-Operator-Key) -> оба approved,
  секретный — со значением stand-secret-T046 со страницы-эквивалентным curl
$ GET /grants/egress (X-Tool-Key) -> {"domains":["example.com"]}
$ исполнитель (one-off python:3.12-slim в сети svc-lo, образец процесса инструмента):
  секрет из хранилища (17 симв.) -> GET https://example.com через egress:3128 с
  Authorization: Bearer <секрет> -> HTTP 200
  GET https://example.org (не одобрен) -> Tunnel connection failed: 403 Forbidden
$ curl POST /grants/revoke (оператор) -> {"status":"revoked"}; GET /grants/egress -> []
  повтор исполнителя: example.com -> 403 Forbidden (домен закрыт без пересборки)
$ позиция узла (one-off в сети svc-1): HIVE_TOOL_KEY в окружении нет; прямой
  https://example.com -> Errno -3 name resolution (наружу хода нет);
  /grants/secret без ключа -> HTTP 401 tool_key
$ MCP grants() -> egress example.com revoked …; secret stand_token approved … (без значений)
$ утечки: docker logs hive | grep -c stand-secret-T046 -> 0; в /hive-run значение есть
  только в grants.sqlite(-wal) — само хранилище; board.sqlite и events.jsonl чисты (тест)
```

### Отклонения от задачи
- Вариант Б без отдельного сервиса-исполнителя в этой задаче: исполнителем будет
  мастерская T-047 (расширения Pi Last Order); механизм T-046 — хранилище, ключ
  инструмента и прокси. На стенде паттерн показан one-off контейнером в svc-lo.
- Прокси пускает только порт 443 (TLS end-to-end — суть гранта); не-CONNECT запросы — 405.
- Кэш списка доменов 3 с (`EGRESS_CACHE_S`): отзыв закрывает домен в пределах трёх секунд.
- Секрет для egress-гранта запрещён (422) — egress не несёт значения.

### Вне задачи
- `unionclef` подмодуль грязный (тот же шум Eclipse/gradle, см. T-042/T-049).
- `docker/node/up.sh` не поднимает HIVE_TOOL_KEY (вне «Можно трогать»); ключ доходит до
  hive из окружения запуска compose, как HIVE_MCP_LO_KEY. Если надзору нужно — строка
  в списке lift.

### Вопросы
- нет
