#!/usr/bin/env bash
# zcode-harness: рендерит provider_config.json из секрета и запускает zcode headless.
set -euo pipefail

: "${ZCODE_WORKDIR:=/workspace}"
: "${ZCODE_MODE:=yolo}"            # build | edit | guarded | yolo
: "${ZCODE_MODEL:=GLM-5.3}"        # GLM-5.3 | GLM-5.3-Flash
: "${ZCODE_PROVIDER_ID:=zai-cp}"
: "${ZCODE_DATA_DIR:=$HOME/.zcode}"
PROVIDER_FILE="${ZCODE_PERSONAL_PROVIDER_CONFIG_FILE:-$ZCODE_DATA_DIR/v2/provider_config.json}"
export ZCODE_WORKDIR ZCODE_MODE ZCODE_MODEL ZCODE_PROVIDER_ID

log() { printf '[zcode-harness] %s\n' "$*" >&2; }

load_key() {
  # Приоритет: docker secret / файл -> env. Ключ никогда не печатается.
  local f="${ZAI_API_KEY_FILE:-/run/secrets/zai_api_key}"
  if [[ -z "${ZAI_API_KEY:-}" && -r "$f" ]]; then
    ZAI_API_KEY="$(tr -d '\r\n' < "$f")"
  fi
  if [[ -z "${ZAI_API_KEY:-}" ]]; then
    log "нет ключа: задай ZAI_API_KEY или смонтируй /run/secrets/zai_api_key"
    exit 64
  fi
  export ZAI_API_KEY
}

render_provider() {
  # Мёржим наш provider в существующий файл (не трогаем чужие правила), пишем атомарно с 0600.
  mkdir -p "$(dirname "$PROVIDER_FILE")"
  umask 077
  node - "$PROVIDER_FILE" <<'NODE'
const fs = require('fs');
const file = process.argv[2];
const id = process.env.ZCODE_PROVIDER_ID;
const model = process.env.ZCODE_MODEL;
let doc = { schemaVersion: 1, config: {} };
try { doc = JSON.parse(fs.readFileSync(file, 'utf8')); } catch {}
const c = (doc.config ??= {});
const pr = ((c.providerConfigRules ??= {}).providerRules ??= []);
const mr = (c.modelConfigRules ??= {});
mr.providerModelRules ??= [];
mr.manualProviderModelRules ??= [];
const rule = {
  providerId: id,
  templateId: 'zai-api',                 // Z.ai Coding Plan -> https://api.z.ai/api/anthropic
  providerName: 'Z.ai Coding Plan (harness)',
  enabled: true,
  config: {
    group: 'standard-personal',
    access: { type: 'zhipu-coding-plan-api-key', apiKey: process.env.ZAI_API_KEY },
    visibility: 'visible',
  },
};
if (process.env.ZCODE_BASE_URL_OVERRIDE) {
  rule.config.api = { type: 'anthropic-messages', baseUrl: process.env.ZCODE_BASE_URL_OVERRIDE };
}
const i = pr.findIndex(r => r.providerId === id);
if (i >= 0) pr[i] = rule; else pr.unshift(rule);
c.providerOrder = [id, ...(c.providerOrder ?? []).filter(x => x !== id)];
c.defaultModelSelection = { providerId: id, modelId: model };
const tmp = `${file}.tmp-${process.pid}`;
fs.writeFileSync(tmp, JSON.stringify(doc, null, 2), { mode: 0o600 });
fs.renameSync(tmp, file);
NODE
}

base_args() {
  ARGS=(--json --no-color --mode "$ZCODE_MODE" --cwd "$ZCODE_WORKDIR" --model "$ZCODE_PROVIDER_ID/$ZCODE_MODEL")
  if [[ -n "${ZCODE_DISALLOWED_TOOLS:-}" ]]; then
    ARGS+=(--disallowed-tools "$ZCODE_DISALLOWED_TOOLS")
  fi
}

usage() {
  cat >&2 <<'EOF'
zcode-harness <command>

  run "<prompt>"            одноразовый headless-ход (JSON в stdout, exit!=0 при фейле)
  run -f /path/prompt.md    промпт из файла
  goal "<objective>"        длинная задача с session goal (--target)
  resume <sess_id> "<msg>"  продолжить сессию
  app-server                ZCode Protocol поверх stdio (для своего оркестратора)
  doctor                    диагностика runtime
  sh | <любая команда>      passthrough (например: zcode --help)

ENV: ZAI_API_KEY | ZAI_API_KEY_FILE, ZCODE_MODEL, ZCODE_MODE, ZCODE_WORKDIR,
     ZCODE_DISALLOWED_TOOLS, ZCODE_MODEL_RETRY_MAX_RETRIES
EOF
}

cmd="${1:-help}"; shift || true

case "$cmd" in
  help|-h|--help) usage; exit 0 ;;
  sh|bash) exec bash "$@" ;;
esac

load_key
render_provider
unset ZAI_API_KEY   # дальше ключ живёт только в provider_config.json (0600)

case "$cmd" in
  run)
    if [[ "${1:-}" == "-f" ]]; then prompt="$(cat "$2")"; else prompt="$*"; fi
    [[ -n "$prompt" ]] || { log "пустой промпт"; exit 64; }
    base_args
    exec zcode -p "$prompt" "${ARGS[@]}"
    ;;
  goal)
    base_args
    exec zcode --target "$*" "${ARGS[@]}"
    ;;
  resume)
    sid="$1"; shift
    base_args
    exec zcode --resume "$sid" -p "$*" "${ARGS[@]}"
    ;;
  app-server)
    exec zcode app-server "$@"
    ;;
  doctor)
    exec zcode doctor "$@"
    ;;
  *)
    exec "$cmd" "$@"
    ;;
esac
