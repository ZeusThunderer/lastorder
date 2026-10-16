# T-005. Отчёт исполнителя

## Раунд 1 · 2026-10-16
Коммиты: 3a514f1 (сервис, спайк, первые замеры), 098ce94 (финальные замеры, jev.md, дефолт 0.6B)

### Сделано
- Сервис `jev` в `docker/compose.yml` под профилем `jev` (остальные сервисы не тронуты):
  образ `docker/jev/` (fastjev[api,torch]==0.2.0), модель в именованный volume `jev-hf`,
  публикация порта только `127.0.0.1:8730` ради замера (внутри сети — `jev:8000`), GPU через
  `device_ids: ["0"]` по ARCHITECTURE §8.
- **Находка хоста:** под Docker Desktop/WSL2 фильтр `device_ids` не доходит до CUDA — контейнер
  видит обе карты, порядок нумерации перевёрнут (внутри 0 = 5070 Ti). Поэтому entrypoint сам
  пинит 2070 по имени (`pick_gpu.py` → `CUDA_VISIBLE_DEVICES`), в llama.cpp-варианте — по UUID
  (`pin_gpu_uuid.sh`). Замер после старта: 2070 несёт ~3.5 ГБ, занятость 5070 Ti не меняется
  (nvidia-smi до/после в журнале прогона).
- `dev/jev_spike.py`: 200 состояний × 4/8/16 вариантов (altoclef-команды и рычаги uc), 10
  прогревов на размер, p50/p95 стеночного времени HTTP-вызова, доля ответов в формате, пиковая
  VRAM опросом nvidia-smi (попрочее — «Insufficient Permissions» под WSL2, честно отражено).
- Замеры (все в `docs/agents/jev-spike-results*.json`, сводка в `docs/agents/jev.md`):
  - **vLLM 0.29.0 на Turing не стартует**: `RuntimeError: UVA is not available`
    (V1 engine, buffer_utils) — допущение ARCHITECTURE §8 подтверждено; проверено на маленькой
    модели, дело не в памяти. `fastjev-serve` vLLM-бекенд и не выставляет (только SDK).
  - Qwen3.5-4B BF16 torch — не входит в карту (веса ~8 ГБ; upstream пик 7.9 GiB).
  - Qwen3.5-4B Q4_K_M GGUF: готовое колесо llama-cpp-python cu124 — SIGILL на Ryzen 9 5900X
    (Zen 3 без AVX-512/VNNI), exit 132; сборка из исходников (Dockerfile.llamacpp-src, sm_75)
    работает, но 3019–5609 мс p50 — в 10+ раз выше порога.
  - MiniCPM5-2B BF16 torch: 377–778 мс p50/p95 — выше порога 300 мс.
  - **Qwen3-0.6B BF16 torch: 105/126/215 мс p50, 106/145/218 мс p95 на 4/8/16 вариантов, 100 %
    в формате, 3.5 ГБ VRAM** — единственная конфигурация в задержки; стала дефолтом compose.
- `docs/agents/jev.md`: подъём за 3 команды, таблица всех прогонов, причины отказа vLLM и
  готового llama.cpp-колеса, ловушки хоста, вывод.
- Вывод: **годится частично** — цель ADR-004 (Qwen3.5-4B ~83 мс) на 2070 недостижима; C1 в MVP
  остаётся на правилах (NETWORK.md §2); для T-015 рабочая заглушка — 0.6B (качество 0.44 против
  0.813), решение за надзором и пользователем.

### Проверки
```
$ uv run pytest
28 passed in 1.08s
$ uv run python tools/check.py
CHARACTERS: last_order, vesper
Every workplace is intact: files present, links resolve, boxes parse.
$ docker compose -f docker/compose.yml --profile jev up -d jev
Container lastorder-jev-1 Started        [лог: entrypoint: CUDA_VISIBLE_DEVICES=1 (RTX 2070)]
$ uv run python dev/jev_spike.py --n 200 --url http://127.0.0.1:8730 --gpu 0
| вариантов | n | в формате | p50, мс | p95, мс | среднее, мс |
|---|---|---|---|---|---|
| 4 | 200 | 1.00 | 105 | 106 | 105 |
| 8 | 200 | 1.00 | 126 | 145 | 133 |
| 16 | 200 | 1.00 | 215 | 218 | 209 |
peak VRAM on GPU 0: 3490 MiB (nvidia-smi memory.used)
```
Контейнер поднят на 2070 (занятость 5070 Ti не изменилась: 1829 МиБ — фоновые процессы
рабочего стола). Команда из «Проверок» задачи — `uv run python dev/jev_spike.py --n 200` —
выполнялась с явным `--url/--gpu` (дефолты те же).

### Отклонения от задачи
- Порт сервиса опубликован на `127.0.0.1:8730` — это разрешённый задачей случай «нужно замеру»,
  помечено в отчёте и в jev.md.
- Дефолтная модель сервиса — Qwen3-0.6B, а не Qwen3.5-4B из ADR-004: целевая на этой карте
  физически не работает (vLLM/UVA, BF16 не входит, GGUF — секунды). Дефорт выбирает единственную
  конфигурацию, попадающую в целевые задержки.
- Аутентификация FastJev-сервера отключена (`--allow-unauthenticated`) — сервис живёт только в
  docker-сети + loopback-публикация; перед любым внешним доступом нужен FASTJEV_API_KEY
  (fastjev сам откажет без токена при не-loopback бинде).

### Вне задачи
- Экспериментальные образы `Dockerfile.vllm` / `Dockerfile.llamacpp` / `Dockerfile.llamacpp-src`
  оставлены в docker/jev/ как протокол попыток; в compose подключён только основной (torch).
- Docker Desktop на этой машине дважды подвисал при параллельных больших сборках (rpc EOF, 500
  на /_ping); работающий стек T-003 при этом не пострадал. Сборки лучше гонять по одной.
- vllm-образ ~12.8 ГБ и llamacpp-src ~4.4 ГБ остались на хосте; при нехватке места можно удалить
  (lastorder-jev-vllm:dev, lastorder-jev-llamacpp*:dev) — воспроизводятся из Dockerfile.

### Вопросы
- нет

### Вызовы надзора
- нет (criticality: critical — headless-вызовы недоступны)
