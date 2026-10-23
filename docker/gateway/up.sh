#!/usr/bin/env bash
# Bring up the llm-gateway with its secrets lifted from the repo root .env.
#
# Why not a bare `docker compose -f docker/compose.yml up -d llm-gateway`: compose reads .env
# from the project directory (docker/), never from the repo root, so without lifting it would
# interpolate an empty GATEWAY_MASTER_KEY — and the proxy refuses to start without a master
# key (verified: fail-closed). Values are lifted as single variables, never echoed, never
# env_file'd over the whole .env (T-024 review P1-6), and quotes are stripped because
# hand-edited .env values may carry them (T-025: baked-in quotes mean 401s downstream).
#
#   bash docker/gateway/up.sh            # up gateway-db + llm-gateway
#   bash docker/gateway/up.sh mind-1     # extra args pass through to compose: with the
#   bash docker/gateway/up.sh hive       # consumer keys lifted below, consumers come up
#                                        # with real keys too (T-026 review R1-1)
set -euo pipefail

HERE="$(cd "$(dirname "$0")/../.." && pwd)"   # this checkout: its docker/compose.yml runs
# .env lives only in the MAIN worktree (it is gitignored, worktrees never have a copy), so
# resolve the main root the way dev/_common.sh does; fall back to this checkout.
ROOT="$(git -C "$HERE" worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')"
[ -n "${ROOT:-}" ] || ROOT="$HERE"

# Consumer virtual keys ride the same lift (T-026 review R1-1): a plain `docker compose up`
# would interpolate them empty, because compose reads .env from docker/, not the repo root.
# One physical line on purpose: a broken `\` continuation reads as a phantom key word that
# bash -n does not catch (checker C-2); tests/test_jev_policy.py pins the exact word list.
for key in GATEWAY_MASTER_KEY OPENROUTER_API_KEY GATEWAY_DB_PASSWORD GATEWAY_KEY_MIND_1 GATEWAY_KEY_HIVE GATEWAY_KEY_NODE_1; do
  if [ -z "${!key:-}" ] && [ -f "$ROOT/.env" ]; then
    val="$(sed -n "s/^${key}=//p" "$ROOT/.env" | head -1 | tr -d '\r')"
    val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
    [ -n "$val" ] && export "$key=$val"
  fi
done

if [ -z "${GATEWAY_MASTER_KEY:-}" ]; then
  echo "up.sh: GATEWAY_MASTER_KEY is not set and not in $ROOT/.env;" >&2
  echo "       the gateway refuses to start without it. Generate one, e.g.:" >&2
  echo "       uv run python -c \"import secrets; print('sk-'+secrets.token_urlsafe(32))\"" >&2
  exit 2
fi

exec docker compose -f "$HERE/docker/compose.yml" up -d gateway-db llm-gateway "$@"
