#!/usr/bin/env bash
# World snapshot and restore (T-062): take a rollback copy of the mc-server world volume
# before any server-type switch, and put it back when the switch went wrong.
#   bash dev/backup_world.sh [--dry-run] [-o <dir>] [--volume <name>]            # backup
#   bash dev/backup_world.sh restore <archive.tar.gz> [--volume <name>] [--force] [--dry-run]
# The archive streams through the container (no host bind mount), so the script works
# from any checkout. Default volume: lastorder_world (compose project `lastorder`).
# Restoring the LIVE world volume requires --force — verify an archive on a copy first
# (T-062 acceptance). A container is using the volume → stop it, the copy may be torn.
set -uo pipefail
# Self-contained on purpose (no dev/_common.sh): the script only touches docker
# volumes and ./backups, and it must run under any bash — including a WSL one that
# cannot follow a worktree's Windows-style .git pointer.

# Git Bash rewrites container paths like /world into Windows ones for docker.exe;
# every path this script passes to docker is a container path.
export MSYS_NO_PATHCONV=1

usage() { sed -n '2,9p' "$0" >&2; exit 2; }
die() { echo "backup_world: $*" >&2; exit 2; }

IMAGE=alpine:3.20   # pinned: the restore tool must not change under the backup
volume=lastorder_world
outdir="$PWD/backups"   # where you run it — never a repo-tracked place
mode=backup; dry=0; force=0; archive=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) dry=1 ;;
    --force) force=1 ;;
    -o) outdir="${2:?}"; shift ;;
    --volume) volume="${2:?}"; shift ;;
    restore) mode=restore ;;
    -*) usage ;;
    *) [ "$mode" = restore ] && [ -z "$archive" ] && archive="$1" || usage ;;
  esac
  shift
done

[ "$mode" = backup ] || [ -n "$archive" ] || usage   # restore needs the archive argument

# a dry run must print the commands without a docker daemon present
if [ "$dry" = 0 ]; then
  command -v docker >/dev/null || die "docker CLI not found"
  in_use() { [ -n "$(docker ps -q --filter "volume=$volume")" ]; }
else
  in_use() { return 1; }
fi

if [ "$mode" = backup ]; then
  ts="$(date -u +%Y%m%d-%H%M%S)"
  out="$outdir/${volume}-${ts}.tar.gz"
  mkdir -p "$outdir"
  in_use && echo "backup_world: warning: a container is using $volume — stop it for a clean copy" >&2
  # gzip -1: the world is a few hundred MB of region files, the bottleneck is the disk anyway
  cmd=(docker run --rm -v "$volume":/world:ro "$IMAGE" tar -C /world -czf - .)
  if [ "$dry" = 1 ]; then printf '# '; printf '%q ' "${cmd[@]}"; printf '> %s\n' "$out"
  else
    "${cmd[@]}" > "$out" || { rm -f "$out"; die "tar failed, removed the partial archive"; }
    entries="$(tar -tzf "$out" | wc -l)"
    size="$(du -h "$out" | cut -f1)"
    echo "backup_world: $out ($size, $entries entries)"
  fi
  exit 0
fi

# restore
if [ "$volume" = lastorder_world ] && [ "$force" != 1 ]; then
  die "refusing to restore into the live $volume without --force — restore into a copy: --volume <name>"
fi
[ -f "$archive" ] || die "archive not found: $archive"
in_use && die "a container is using $volume — stop it first"
wipe='find /world -mindepth 1 -delete'
unpack="tar -C /world -xzf -"
if [ "$dry" = 1 ]; then
  printf '# docker run --rm -v %s:/world %s sh -c %q < %s\n' "$volume" "$IMAGE" "$wipe" "$archive"
  printf '# docker run --rm -i -v %s:/world %s sh -c %q < %s\n' "$volume" "$IMAGE" "$unpack" "$archive"
else
  docker run --rm -v "$volume":/world "$IMAGE" sh -c "$wipe"
  docker run --rm -i -v "$volume":/world "$IMAGE" sh -c "$unpack" < "$archive" \
    || die "unpack failed — $volume is wiped, retry with the same archive"
  echo "backup_world: $archive restored into $volume"
fi
