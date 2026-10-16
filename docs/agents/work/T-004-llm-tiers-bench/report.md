# T-004. Отчёт исполнителя

## Раунд 1 · 2026-10-16
Коммиты: 7cb4f70 (уровни и статистика вызовов), 044c97d (бенч и case-файлы), b18167f (отчёт
замера, raw-логи, фикс $/ч)

### Сделано
- `LLMConfig.for_tier("node"|"smart")` — переменные `LLM_<TIER>_{MODEL,API_BASE,REASONING_OFF,
  MAX_TOKENS,TEMPERATURE,TIMEOUT_S}` с откатом на базовые `LLM_*` / `CORE_*`; старый `from_env()`
  не изменился (`runtime/llm/client.py`).
- `CallStats` + `client.last_call`: модель, токены вход/выход, задержка, стоимость от провайдера
  через litellm (`response_cost`), когда отдаётся.
- Клиент переносит ошибку «Reasoning is mandatory» (OpenRouter, gpt-5-nano): повтор без флага
  отключения reasoning — обязательный reasoning больше не роняет вызов.
- `dev/llm_bench.py`: `--tier node|smart --models … --repeat N --max-usd X`, `--from` для
  пересчёта сводки из raw-лога без вызовов, `--report-md`. Бюджет-стоп, цены/оценки стоимости,
  стилевые флаги (emoji, >3 предложений, числа черт, кириллица), проверка схемы по типу случая,
  пересчёт «$/3 узла/ч» по реестру ADR-022 (NETWORK.md §6) с измеренными токенами.
- `dev/bench_cases/` — 7 файлов-случаев: шаг (наблюдение plain → предварительная схема StepReply
  NETWORK.md §5, помечена как предварительная), 3 запроса task1 (тексты и проверки берёт из
  `tools/snapshot_replies.py` на исполнении), рефлексия / завещание / цели сети для smart.
- `.env.example` — только имена переменных и примеры id, без ключей.
- Отчёт замера `docs/agents/llm-bench/2026-10-16.md` + raw-логи всех прогонов
  (`docs/agents/llm-bench/raw/*.jsonl`). Цены сверены с OpenRouter (авторизованный
  `/api/v1/models`, 2026-10-16): часть цифр шорт-листа совпадала только с `:batch`-эндпоинтами;
  Gemini 2.5 Flash Lite истекает 2026-10-20. Рекомендация: node = `qwen/qwen3.7-flash`
  ($0.071/ч, p50 2.6 с, схема 2/2), smart = `deepseek/deepseek-v4.1-flash` ($0.015/ч, p50 2.3 с,
  схема 4/4), глубже — `z-ai/glm-5.3-flash` (обязательный reasoning: MAX_TOKENS ≥ 4000, p95 16 с,
  $0.096/ч). Выбор за надзором и пользователем.
- Суммарная стоимость прогонов: **$0.0289** из лимита $1 (в отчёте).

### Проверки
```
$ uv run pytest
43 passed in 4.20s
$ uv run python tools/check.py
CHARACTERS: last_order, vesper
Every workplace is intact: files present, links resolve, boxes parse.
$ uv run python dev/llm_bench.py --tier node --models openrouter/qwen/qwen3.7-flash,openrouter/openai/gpt-5-nano --repeat 1 --max-usd 0.05
calls: 8 (failed 0), spent $0.0009   [после добавления повтора без флага reasoning;
 gpt-5-nano при потолке 1500 отдаёт пустой контент на шаге — это finding, а не сбой утилиты]
```
Проверка из задачи выполнялась дважды (до/после фикса reasoning); финальный прогон зелёный.

### Отклонения от задачи
- Голый `python` на этом хосте — заглушка Windows Store (exit 49), проверки запускались как
  `uv run python tools/check.py`.
- В бенче потолок выходных токенов для обязательных reasoning-моделей поднят перепрогонами до
  4000 (по умолчанию 1500/2000): иначе их ответы пусты/обрезаны — это Finds отчёта, перепрогоны
  вынесены в отдельные raw-файлы.
- `python tools/check.py` в «Проверках» задачи заменён на `uv run python tools/check.py` — тот же
  скрипт, рабочий вызов на Windows.

### Вне задачи
- `tools/snapshot_replies.py` при запуске из worktree не видит `.env` (секреты не коммитятся) —
  бенч решает это сам (`git rev-parse --git-common-dir`); сам snapshot-скрипт не трогал.
- В `runtime/perception/translate.py` сейчас всегда хэндлы (режим plain появится в T-009);
  case «шаг» собирал plain-наблюдение напрямую, без правки perception.

### Вопросы
- нет

### Вызовы надзора
- нет (criticality: critical — headless-вызовы недоступны)
