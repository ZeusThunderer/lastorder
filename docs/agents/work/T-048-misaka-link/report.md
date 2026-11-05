# T-048. Отчёт исполнителя

## Раунд 1 · 2026-11-06
Коммиты: c7b243e, feel921 (feea921)

### Сделано
- `runtime/pi/misaka-link/` — расширение Pi (TypeScript, без рантайм-зависимостей):
  - `envelope.ts` — конверты раздела 11, формат переиспользован из моста T-050
    (`boardlink.py`): префикс только из структурных полей, текст автора
    сворачивается в одну строку и не может стать вторым конвертом; пачка ≤ 10 строк
    + маркер `… N more, \`board\``; `[net from <имя>] текст`.
  - `naming.ts` — пресет имён (ADR-018) читается из PERSONA.md рабочего места
    («узел 00007» / «Мисака 00007»); без него конверт несёт сырой номер. Не зашит:
    оба пресета поддержаны, новый = маленькая правка регулярки.
  - `hive.ts` — клиент hive по node key из `mcp.json` агент-дира: `GET /board/feed`,
    `POST /board/ack`, `POST /board/alarms`; Мисака-нет — stateless MCP
    `tools/call inbox` на `/node/mcp` (HTTP `/comms/inbox` говорит только с ключом
    раннера — расширение его не имеет и не должно). Ответ MCP читается
    пофреймово: FastMCP держит SSE-поток ответа открытым [проверено: живой hive],
    `response.text()` ждал бы EOF вечно — `firstJsonRpcFrame` резолвится на первом
    фрейме с нужным id и отменяет поток.
  - `link.ts` — цикл доставки (контракт T-050): inbox первым (одноразовый дрейн),
    ack только после приёма Pi, память 200 `#id:seq`, backoff 2→60 c, после 5
    неудач подряд — `delivery_failing` первым же ответившим hive; отказ (401/409)
    — конфигурация, не авария доставки.
  - `misaka-link.ts` + `index.ts` — фабрика: `session_start` → конфиг из mcp.json
    + PERSONA.md → цикл; `session_shutdown` → стоп. Режим: `ctx.isIdle()` →
    `followUp`, иначе `steer` (API Pi 1.4.0: `pi.sendUserMessage(text, {deliverAs})`
    [проверено: dist/core/extensions/types.d.ts из npm-тарбола 1.4.0 — в доках
    расширений режим не описан, сверено с типами]).
- `runtime/node/workplace.py` — `drop_pi_agent_dir` кладёт `extensions/misaka-link/`
  (root 0444, список файлов явный: 6 исходников, без тестов и package.json) при
  каждом рендере — тот же контракт «лечения» заменённого файла, что у конфигов.
- Тесты: 28 `node --test` (конверты/экранирование/пачка, naming, HTTP+MCP клиент
  включая незакрывающийся SSE-ответ, цикл: режимы доставки, ack после приёма,
  повтор, 25→10+маркер, пустой snapshot, backoff+alarm, отказ, net + потеря
  дрейна при отказе Pi с записью в журнал) + pytest-тест отрисовки
  (`test_drop_pi_agent_dir_installs_the_misaka_link_extension`: байт-в-байт,
  права, хил после подмены).

### Живая проверка (стенд `HAPI_PROJECT=t048`, порты 25576/18082/3008, из worktree)
- Расширение грузится в живой `hapi pi` (обёртка запускает `pi --mode rpc` с
  agent-dir сестры): журнал `misakalink.start {serial:1}` [проверено: лог
  /home/sister/.hapi/logs, [pi][stderr]]. PERSONA.md в cwd сессии → serial и
  префикс распознаны.
- Задача оператора `net` с доски дошла в сессию конвертом: в jsonl сессии
  user-строка `[board #1 task · from operator · to net · open] Поставь сундук…`
  [проверено: sessions/--tmp-hapi-agent--/*.jsonl], журнал `misakalink.delivered
  {lines:1, seq:1}`; ack прошёл — следующая жизнь (перезапуск агента) ленту
  пустой, повторов нет.
- Сообщение оператора из HAPI дошло чистым user-prompt без конверта — различимо
  от доски [проверено: тот же jsonl, строка без `[board`].
- Мисака-нет (comms/send от узла 1): доставлено конвертом
  `[net from Мисака 00001] Проверка доставки Мисака-нет…` [проверено: jsonl +
  `misakalink.net_delivered`].
- Hive недоступен (docker stop на ~85 c): 6 неудач с паузами 2/4/8/16/32/60,
  сессия живёт; после старта hive — `misakalink.recovered {after:6}` +
  `misakalink.alarm {failures:6}` и запись #2 на доске: kind=alarm,
  `board delivery to slot ? failed 6 times in a row`, alarm.type=delivery_failing,
  serial=1 [проверено: журнал + GET /board/entries?kind=alarm].
- НЕ проверено живьём: claim/reply агента и его реплика «сообщение оператора, а
  не доска» — LLM-ход падает: Z.ai отвечает 401 на ключ из корневого `.env`
  [проверено: прямой curl к api.z.ai/api/coding/paas/v4 с ключом из .env → 401;
  сессия агента: `API key auth failed`→ после материализации zai-key →
  `401 身份验证失败`]. Код доставки при этом отработал полностью (конверт в
  сессии). Это секрет оператора, не код: нужен валидный `ZAI_API_KEY` — после
  замены живой прогон закрывается рецептом ниже без правок кода.

### Проверки
```
$ bash dev/check.sh
pytest: ok · 857 passed, 3 skipped in 91.84s
check.py: ok · Every workplace is intact: files present, links resolve, boxes parse.
$ node --test runtime/pi/misaka-link          # Node 24 (хост)
ℹ tests 28, pass 28, fail 0
$ docker exec t048-node1-pi sh -c 'cd /app/runtime/pi/misaka-link && node --test --experimental-strip-types'   # Node 22.23.4 (образ)
# tests 27→28 после доп. теста; pass 28, fail 0 (последний прогон: 28/28)
$ git diff --stat main...HEAD — только «Можно трогать»
```

### Отклонения от задачи
- Мисака-нет забирается не HTTP `/comms/inbox` (его гейт — X-Runner-Key слота,
  которого у расширения нет и быть не должно), а MCP-инструментом `inbox` сёстры
  на `/node/mcp` по X-Node-Key — тот же канал, что у самой сестры; HiveRejected
  (нет живого узла/ключ) трактуется как конфигурация, без backoff.
- `slot` в тревоге пустой (`slot ?`, serial передан): расширение не знает имени
  слота — его нет ни в mcp.json, ни в PERSONA.md. Для человека достаточно
  serial+ключа узла; если надзор захочет слот — это строка в рендер agent-dir
  (вне текущего «Можно трогать» adapters.py).
- Маркер пачки — `… 15 more, \`board\`` (формат моста T-050), не «ещё 15»: конверт
  на английском, формат общий по заметке надзора.
- Драйвер спайка `hapi_agent.py` не кладёт `zai-key` (новый паттерн моделей.json
  после T-044): в прогоне файл материализован руками (`cp` из /run/node/secrets).
  Файл вне «Можно трогать» — правка за надзором/T-045.

### Вне задачи
- Двойная доставка с мостом T-050: раннер сейчас пускает `boardlink` при каждом
  `_life` независимо от харнесса; под живым `hapi pi` (T-045) мост и расширение
  будут конкурентно ack'ать одну ленту (у hive курсор один, по serial).
  Выключение моста при harness=pi — зона runner.py (T-045), не трогал.
- Docker: пул подсетей исчерпан старыми спайками — удалены 5 пустых сетей
  (lastorder_console-spike, lastorder-jevprobe×2, lastorder-harness-spike,
  lastorder_egress-out), ни одна не имела контейнеров.
- `t044-hapi-hub-1` (старый стенд T-044) поднят автостартом Docker Desktop — не
  трогал.
- Стенд `t048` оставлен поднятым (hive/body-1/mind-1/node1-pi; агент запущен,
  квоту не жжёт — ходы падают на 401). Снять:
  `HAPI_PROJECT=t048 MC_HOST_PORT=25576 HIVE_BOARD_PORT=18082 HAPI_PORT=3008 bash dev/console_spike/up_spike.sh down`.

### Вопросы
- Живой остаток критерия 8 (claim/reply + реплика про источник) упирается в
  невалидный `ZAI_API_KEY` (401, см. выше). Решение за оператором: заменить ключ
  в `.env` — прогон повторю по готовому рецепту (birth → доска → hapi_chat send),
  правок кода не нужно. Если надзор считает доказательства доставки достаточными —
  критерий можно закрыть юнит-тестами + конвертом в сессии.
