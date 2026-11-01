# T-059. Ревью надзора

## Раунд 1 · 2026-11-01 · вердикт: changes · headless
Проверено: `git diff main...z/T-059-lo-on-zai` @ 5c1d1f5, построчно (задача критичная).

### Проверки (прогнаны надзором)
- `bash dev/check.sh` — pytest 855 passed, 3 skipped; check.py ok.
- `uv run pytest tests -k "admin"` — входит в полный прогон выше.
- Живая проверка не прогонялась (см. критерий 4).

### Критерии приёмки
- [x] `ADMIN_HARNESS=pi` по умолчанию: `pi -p --mode json …`, `ADMIN_PI_MODEL=GLM-5.3`, mcp.json —
  только hive `direct` `/last-order/mcp`, сжатие 96000 с обоснованием — `test_pi_session_command_and_agent_dir`,
  `test_from_env_defaults_to_pi`. Рендер повторяет `drop_pi_agent_dir` (link-safe, root 0444, zai-key 0400).
- [x] Квота → следующая сессия на opencode + `harness_failover` от `admin` (право есть в
  `board._ALARM_RIGHTS`), без готового failover — `quota.waits`, рестарт — снова pi. Тесты есть.
- [x] Ключи: тот же путь, что у `node-N` (entrypoint materialize 0400 + unset), env сессии —
  только `PI_CODING_AGENT_DIR`; `svc-lo` получает только `zai-egress` с алиасом `api.z.ai`.
  Проверено чтением compose/entrypoint и тестом `test_stand_compose`; сухой прогон — по отчёту.
- [ ] Живая проверка не сделана: TLS из контейнеров наружу рвётся, образ не собирается (pypi).
  Как в T-066, переносится на первый подъём после починки сети — само по себе не блокирует.
- [x] `bash dev/check.sh` зелёный.

### Находки
- R1-1 · major · `runtime/hive/admin.py:709` (`_session`), комментарий в `__init__` (~`:446`) ·
  Основной харнесс не проверяется на готовность. Комментарий говорит «no token means pi is not ready
  and the desk waits on it (the runner's `_adapter_ready` gate)», а код этого не делает:
  без токена Z.ai (пустой `ZCODE_API_KEY` в `.env`, ключ не поднят `up.sh`) каждая сессия запускает
  `pi` без ключа и падает. Маркеров квоты в выводе нет, поэтому переключения и тревоги тоже нет.
  `test_no_zai_key_still_assembles_the_command` закрепляет именно такое поведение. До T-059 стол
  работал на opencode без токена, после T-059 он молча мёртв — для критичного узла это регрессия. ·
  Ожидается: перед запуском — `_adapter_ready(self._harness_name)`. Если основной не готов, а
  failover готов, сессия идёт на failover с тем же `harness_failover` на доске (одна тревога, а не
  на каждую сессию) и событием в логе. Если не готов ни один — событие `harness.waits`, без spawn.
  Тест — вместо нынешнего keyless-теста. Комментарий в `__init__` привести к коду.
- R1-2 · minor · `runtime/hive/admin.py` `_quota_switch` · текст тревоги зашит под пару pi→opencode
  («the Z.ai quota ran out … through the gateway»). При `ADMIN_HARNESS=opencode` и failover `pi`
  текст будет неверным. Лучше собирать текст из `from`/`to`.
- R1-3 · minor · `runtime/hive/admin.py` `_post_board_alarm` · повторяет `HiveClient.board_alarm_admin`
  (`runtime/hive/client.py:603`). Можно оставить, если клиент тянет лишнее; тогда — фраза в отчёте.

### Чек-лист проекта
- Ключ Z.ai: экспозиция та же, что у сестёр в T-066 (zai-key 0400 у uid сессии, ADR-033). Других
  ключей провайдеров в `last-order` нет. Без замечаний.
- Уровни LLM: при основном харнессе `smart` не тратится, opencode через шлюз остаётся запасным. Без замечаний.
- Путь смерти, аварийное завещание, единственный писатель базы и фильтр сырых id не тронуты.
- Объём: в пределах «Можно трогать».
