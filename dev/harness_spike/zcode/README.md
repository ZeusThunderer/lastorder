# zcode headless harness в Docker (Z.ai Coding Plan)

Runtime: официальный ZCode agent runtime 0.16.9, упакованный в npm `zcode-app-cli@3.15.3-36`
(неофициальная обёртка, но сам агент/провайдеры/сессии — upstream-код из zai-org/ZCode).

## Как работает авторизация

Personal Coding Plan ключ (`<id>.<secret>`) подставляется как персональный провайдер
на шаблоне `zai-api` в `~/.zcode/v2/provider_config.json`:

```json
{ "providerId": "zai-cp", "templateId": "zai-api",
  "config": { "group": "standard-personal",
              "access": { "type": "zhipu-coding-plan-api-key", "apiKey": "<KEY>" } } }
```

Шаблон сам даёт `anthropic-messages` → `https://api.z.ai/api/anthropic` и модели
`GLM-5.3` / `GLM-5.3-Flash`. OAuth / `zcode login` / `/login` не нужны.
Entrypoint рендерит файл при каждом старте из secret/env (0600, атомарно), в образ ключ не попадает.

## Старт

```bash
mkdir -p secrets work/a && printf '%s' 'xxxxxxxx.yyyyyyyy' > secrets/zai_api_key && chmod 600 secrets/zai_api_key
docker compose build
docker compose run --rm agent-a run "reply PONG"            # smoke
docker compose run --rm agent-a run "добавь --json флаг в cli.py и тесты"
docker compose run --rm agent-a run -f /workspace/TASK.md
docker compose run --rm agent-a goal "довести покрытие tests/ до 80%"
docker compose run --rm agent-a resume sess_xxx "продолжай"
```

Без compose:

```bash
docker run --rm -i --read-only --tmpfs /tmp --cap-drop ALL \
  -e ZAI_API_KEY_FILE=/run/secrets/zai_api_key \
  -v "$PWD/secrets/zai_api_key:/run/secrets/zai_api_key:ro" \
  -v "$PWD:/workspace" -v zcode-state:/home/agent/.zcode \
  zcode-harness:0.16.9 run "explain this repo"
```

## Контракт для оркестратора

- `run` → `zcode -p … --json --mode $ZCODE_MODE --cwd /workspace --model zai-cp/$ZCODE_MODEL`.
  stdout — JSON (sessionId/response/usage), логи — stderr. **Exit code ≠ 0 при фейле хода** (проверено).
- Стриминг/многоходовость/permission-события — `app-server` (ZCode Protocol, JSON-RPC по stdio,
  `docker run -i … app-server`). Методы с 0.16: `session/create {workspace}`, `session/send`,
  `session/subscribe {deliveryKind}`, `session/stop`; конверт без `jsonrpc`. Сервер шлёт обратный
  вызов `session/requestRuntimePreferences` — на него обязательно отвечать, иначе ход висит.
  Протокол не стабилен между билдами (см. tizerluo/zcode-open-bridge, recheck-заметки) — пинь версию.
- Режимы: `yolo` (без подтверждений, дефолт для `-p`), `guarded`, `edit`, `build`. `plan` в headless нет.
- Read-only ревьюер: `ZCODE_DISALLOWED_TOOLS="Bash Edit Write MultiEdit ApplyPatch js"` —
  удаляет тулы целиком (раньше permission-слоя). Node REPL (`js`) убирать обязательно,
  иначе через `execSync` обходится запрет Bash.

## Грабли

1. **Rate limit плана общий на ключ.** N параллельных контейнеров = N× давление → 429 / код 1302.
   Держи семафор на стороне оркестратора (2–3 параллельных хода на ключ — разумный старт),
   ретраи только на 429/1302, не на 401.
2. **Отдельный state-том на воркер.** `~/.zcode/cli/db/db.sqlite` — sqlite; шарить между
   одновременными процессами нельзя. Для одноразовых задач можно вообще `--tmpfs /home/agent/.zcode`
   (но тогда `resume` не работает).
3. **Проектный конфиг из репо исполняется.** `zcode.json` / `.zcode/config.json` в рабочей
   директории мёржатся в настройки, их `mcp.servers` спавнятся на старте. Для чужих репо —
   проверяй/удаляй эти файлы перед запуском или клади репо в отдельную подпапку.
4. **Сеть.** Нужен egress на `api.z.ai:443` (+ zcode.z.ai для обновления каталога провайдеров —
   без него в stderr будет `ZCode Built-in 刷新失败`, на работу с personal-провайдером не влияет).
   Остальное (npm/pypi/github) — по задачам; если хочется allowlist, ставь egress-proxy сбоку.
5. **`yolo` + `--cap-drop ALL` + read-only rootfs** — песочница тут Docker, не агент.
   Не монтируй docker.sock и `~/.ssh` хоста.
6. **Апгрейд runtime ломает протокол app-server**, CLI-флаги тоже дрейфуют (в 0.16.9 исчезли
   `--permission-mode`, `--allowed-tools`, `--max-turns`). Меняй `ZCODE_APP_CLI_VERSION` осознанно.
7. Coding Plan по ToS ограничен «поддерживаемыми инструментами». ZCode — first-party продукт Z.ai,
   обёртка `zcode-app-cli` — сторонняя. Если хочется полностью официальный путь — собери CLI
   из `zai-org/ZCode` (`pnpm build:zcode`, Node 24.14) и подмени установку в Dockerfile.

## Переопределения

| ENV | дефолт | |
|---|---|---|
| `ZCODE_MODEL` | `GLM-5.3` | или `GLM-5.3-Flash` (дешевле по квоте) |
| `ZCODE_MODE` | `yolo` | `build` / `edit` / `guarded` |
| `ZCODE_MODEL_RETRY_MAX_RETRIES` | 5 (runtime) | ретраи внутри runtime |
| `ZCODE_BASE_URL_OVERRIDE` | — | свой прокси перед api.z.ai (anthropic-messages) |
| `ZCODE_DISALLOWED_TOOLS` | — | список тулов на удаление |
