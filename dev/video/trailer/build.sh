#!/usr/bin/env bash
# Rebuild the T-067 trailer from the local camera takes (dev/video/out/footage) in one command:
#   bash dev/video/trailer/build.sh [draft|standard]
# Music: Kevin MacLeod (incompetech.com), CC BY 4.0 — downloaded, not committed.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
Q="${1:-standard}"
cd "$HERE"
[ -d node_modules/hyperframes ] || npm install --no-fund --no-audit
mkdir -p film/assets/music
get() { [ -s "film/assets/music/$2" ] || curl -fsSL -o "film/assets/music/$2" "https://incompetech.com/music/royalty-free/mp3-royaltyfree/$1.mp3"; }
get "Volatile%20Reaction" volatile.mp3
get "Interloper" interloper.mp3
uv run --no-project python cut_clips.py
cd film
npx hyperframes check --json > /dev/null
npx hyperframes render -q "$Q" -w 4 -o ../../out/trailer.mp4
ffprobe -v error -show_entries format=duration:stream=codec_name,width,height ../../out/trailer.mp4
