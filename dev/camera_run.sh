#!/usr/bin/env bash
# The camera stand run (T-065): what three cameras cost on CPU, RAM, disk and C1.
# Boots body-1..3 + mind-1..3 + media + mc-server of one ISOLATED compose project,
# waits for health, samples dev/measure_resources.py over the window, checks /list
# for every body, pulls the minds' log.jsonl and the footage size, tears the project
# down (-v: its world is scratch). Raw artifacts land in .camera-run-data/, untracked.
#   bash dev/camera_run.sh [--minutes 30] [--distance 2] [--no-camera]
#                          [--project t065] [--port 25591] [--keep] [--dry-run]
# --no-camera is the comparison run: same stand shape, BODY_CAMERA_* unset.
# --distance feeds BODY_RENDER_DISTANCE (the task's 2/6/8 measurement points).
# Bodies build under an isolated <project>-body:dev tag — the shared lastorder-body:dev
# of the live stand is never rebuilt by a measurement. node-N is NEVER started (no
# quota); minds ride JEV_BACKEND=rules with NODE_DEV_OPEN=1 (no keys, no LLM).
# The shared stand (project lastorder) is refused: measuring it is the operator's call
# (dev/stand.sh lock, warm volumes — this script would otherwise throw the world away).
# Self-contained on purpose (no dev/_common.sh, no git): a WSL bash that cannot follow
# a worktree's Windows-style .git pointer must run it too.
set -uo pipefail

usage() { sed -n '2,16p' "$0" >&2; exit 2; }
die() { echo "camera_run: $*" >&2; exit 2; }

minutes=30 distance=2 project=t065 port=25591 keep=0 dry=0 camera=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --minutes) minutes="${2:?}"; shift ;;
    --distance) distance="${2:?}"; shift ;;
    --project) project="${2:?}"; shift ;;
    --port) port="${2:?}"; shift ;;
    --no-camera) camera=0 ;;
    --keep) keep=1 ;;
    --dry-run) dry=1 ;;
    *) usage ;;
  esac
  shift
done
[ "$minutes" -gt 0 ] || die "--minutes must be positive"
[ "$project" = lastorder ] && die "refusing project 'lastorder': the shared stand is not scratch (world, warm bodies); the operator measures it by hand under dev/stand.sh lock"

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root" || die "no repo root"
[ -f docker/compose.yml ] || die "run from the repo (worktree) root"
if [ "$dry" = 0 ]; then command -v docker >/dev/null || die "docker CLI not found"; fi

run() { if [ "$dry" = 1 ]; then printf '# %s\n' "$*"; else "$@"; fi; }

SVC="media mc-server body-1 body-2 body-3 mind-1 mind-2 mind-3"
cam_env=(MC_HOST_PORT="$port" BODY_RENDER_DISTANCE="$distance" NODE_DEV_OPEN=1)
if [ "$camera" = 1 ]; then cam_env+=(BODY_CAMERA_1=1 BODY_CAMERA_2=1 BODY_CAMERA_3=1); fi

ovf="$(mktemp "${TMPDIR:-/tmp}/camera-run-XXXXXX.yml")"
trap 'rm -f "$ovf"' EXIT
{ printf 'services:\n'
  for n in 1 2 3; do printf '  body-%s:\n    image: %s-body:dev\n' "$n" "$project"; done
} > "$ovf"
if [ "$dry" = 1 ]; then printf '# override: %s\n' "$(tr '\n' ' ' < "$ovf")"; fi

mkdir -p .camera-run-data

step() { printf '\n## %s\n' "$*"; }

step "up: project $project, camera=$camera, distance=$distance, window ${minutes} min"
run env "${cam_env[@]}" docker compose -f docker/compose.yml -f "$ovf" -p "$project" up -d --build $SVC
for b in body-1 body-2 body-3; do
  run bash dev/wait_for.sh 900 --healthy "${project}-${b}-1"
done

step "camera sanity: /list per body (empty is a failure, not a measurement)"
for name in LastOrder01 LastOrder02 LastOrder03; do
  run docker run --rm --network "${project}_media-net" alpine:3.20 \
    wget -q -T 10 -O- "http://media:9996/list?path=${name}"
done

step "resources: docker stats, one sample per 30 s across the window"
run uv run python dev/measure_resources.py --project "$project" \
  --services body-1,body-2,body-3,mind-1,mind-2,mind-3,media,mc-server \
  --samples $((minutes * 2)) --interval 30 --label "camera=$camera distance=$distance"

step "footage: per-body bytes and segment count on the footage volume"
run docker run --rm -v "${project}_footage:/f:ro" alpine:3.20 \
  sh -c 'for d in /f/*; do printf "%s " "${d##*/}"; du -sh "$d" | cut -f1; done'

step "C1 logs pulled for the with/without comparison (.camera-run-data/)"
for n in 1 2 3; do
  run docker cp "${project}-mind-${n}-1:/mind-run/log.jsonl" \
    ".camera-run-data/${project}-d${distance}-cam${camera}-mind${n}.jsonl"
done

if [ "$keep" = 0 ] && [ "$dry" = 0 ]; then
  step "teardown (down -v: the project's world is scratch)"
  docker compose -f docker/compose.yml -f "$ovf" -p "$project" down -v
fi
printf '\ncamera_run: done — label camera=%s distance=%s minutes=%s\n' "$camera" "$distance" "$minutes"
