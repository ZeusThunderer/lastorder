# T-026. Ревью надзора

## Раунд 1 · 2026-10-18 · вердикт: accept · headless
Проверено: `git diff main...z/T-026-llm-gateway` @ c64dbee, построчно.

### Проверки (прогнаны надзором)
- `uv run pytest` — 537 passed.
- `uv run python tools/check.py` — workplaces intact (last_order, vesper).
- `docker compose -f docker/compose.yml ps llm-gateway gateway-db` — оба `healthy` (подняты исполнителем
  через `docker/gateway/up.sh`; заново не поднимал).
- `uv run python dev/gateway_report.py --since 1h` — вывод совпал с отчётом: пять отозванных ключей,
  $0.00059, разбивка по моделям.
- `uv run python dev/gateway_keys.py list` — `no virtual keys` (пробные ключи отозваны).
- Свой короткий цикл запросов на потолок (rpm=1, два запроса) и `docker port` — **не прогнаны**:
  права headless-сессии не дали запустить. Потолки подтверждены выводом исполнителя и чекера;
  косвенно — отчётом расхода: ключ `11cc4201…` с $0.00041 соответствует бюджетной пробе
  (422 при 0.000408 > 0.0004), `dc187920…` — rpm-пробе. Отсутствие портов — статикой compose и
  тестом `test_gateway_and_db_publish_no_ports`.

### Критерии приёмки
- [x] Шлюз в compose, ключ провайдера только у него, портов наружу нет — `docker/compose.yml`,
  `TestComposeWiring`, `TestNoSecretsInFiles`; `OPENROUTER_API_KEY` в compose только у `llm-gateway`.
- [x] Виртуальные ключи на слот, Last Order, mind-N; выдача и отзыв — `dev/gateway_keys.py`,
  живой issue/list/revoke→401 в отчёте.
- [x] Потолки rpm и $/ч с понятным текстом — `rpm_limit`, `max_budget` + `budget_duration: 1h`;
  живые 429/422 в отчёте (свой повтор не прогнан, см. выше).
- [x] `dev/gateway_report.py` — расход по ключам и моделям, $/ч; прогнан.
- [x] GLM Coding Plan — «не проводим» с причиной (условия Z.ai, T-024), в отчёте и в конфиге.
- [x] Клиент через шлюз — `LLM_<TIER>_API_KEY` → `LLM_API_KEY`; прежние тесты зелёные.

### Находки
- R1-1 · minor · `docker/compose.yml:81,112` · `GATEWAY_KEY_MIND_1` / `GATEWAY_KEY_HIVE`
  интерполируются из `.env` каталога `docker/`, которого нет; `up.sh` поднимает только шлюз. При
  обычном `up -d hive mind-1` ключи будут пустыми. Сегодня LLM-вызовов там нет — закрыть в T-030/T-031
  (лифт переменных потребителей тем же способом, что в `up.sh`).
- R1-2 · minor · `dev/gateway_keys.py:161` · `revoke --key sk-…` кладёт виртуальный ключ в argv хоста,
  а docstring обещает обратное. `/key/delete` принимает хэш — стоит отзывать по хэшу/алиасу из `list`.
  Не блокирует: ключ отзываемый, после команды недействителен.
- R1-3 · minor · `runtime/llm/client.py` · ретраи на 429/422 потолка (отмечено исполнителем в «Вне
  задачи») — кандидат в отдельную задачу вместе с T-030/T-031.

### Чек-лист проекта
- Уровни LLM: в шлюзе `node` и `smart`; облачный `jev` не заведён — модель ещё не выбрана (T-031),
  ключ `mind-1` уже есть. Принято.
- Фильтр сырых id, путь смерти, единственный писатель базы — изменения их не задевают.
- Объём: все правки в «Можно трогать». `docker/gateway/up.sh` и сервис `gateway-db` — внутри
  `docker/gateway/` и `docker/compose.yml`, обоснованы в «Отклонениях». Принято.
