# Shared helpers for dev/*.sh (two-agent protocol, docs/agents/coop-protocol.md). Source, do not run.
# The main worktree (repo root) stays on `main`; each task lives in .worktrees/T-NNN-<slug>.

die() { echo "${0##*/}: $*" >&2; exit 2; }

git rev-parse --git-dir >/dev/null 2>&1 || die "not inside the repository"
# First entry of `git worktree list` is always the main worktree.
ROOT="$(git worktree list --porcelain | sed -n '1s/^worktree //p')"
COMMON="$(cd "$(git rev-parse --git-common-dir)" && pwd)"
WORK=docs/agents/work

check_id() { [[ "$1" =~ ^T-[0-9]{3}$ ]] || die "bad task id '$1'"; }

# Folder name T-NNN-<slug> of a task, looked up in main.
task_dir() {
  local d
  d="$(git ls-tree -d --name-only main "$WORK/" | sed 's#.*/##' | grep "^$1-" || true)"
  [ -n "$d" ] && [ "$(printf '%s\n' "$d" | wc -l)" -eq 1 ] || die "expected exactly one $WORK/$1-* in main"
  printf '%s' "$d"
}

branch_exists() { git show-ref --verify --quiet "refs/heads/$1"; }

# Frontmatter field of task.md at a revision: field <rev> <task_dir> <name>
field() { git show "$1:$WORK/$2/task.md" 2>/dev/null | sed -n "s/^$3: *//p" | head -1 | tr -d '\r'; }

# Current status: the branch copy while the task branch exists and is not merged yet, otherwise main.
task_status() {
  local b; b="$(field main "$1" branch)"
  if branch_exists "$b" && ! git merge-base --is-ancestor "$b" main; then field "$b" "$1" status
  else field main "$1" status; fi
}

# Rewrite status in a task.md file on disk, keeping line endings.
set_status_file() { sed -i "0,/^status: [a-z_]*/s//status: $2/" "$1"; }

# Frontmatter field of a task.md file on disk: disk_field <file> <name>
disk_field() { sed -n "s/^$2: *//p" "$1" | head -1 | tr -d '\r'; }

# Queued tasks whose `after:` tasks are all done become ready (coop-protocol.md «Статусы»).
# Edits task.md files in the main worktree on disk and prints the promoted folders; the caller commits.
promote_queued() {
  local f d deps dep ok
  local -a dd
  for f in "$ROOT/$WORK"/T-[0-9][0-9][0-9]-*/task.md; do
    [ -f "$f" ] && [ "$(disk_field "$f" status)" = queued ] || continue
    deps="$(disk_field "$f" after | tr -d '[] ' | tr ',' ' ')"
    [ -n "$deps" ] || continue
    ok=1
    for dep in $deps; do
      dd=("$ROOT/$WORK/$dep"-*/task.md)
      [ -f "${dd[0]}" ] && [ "$(disk_field "${dd[0]}" status)" = done ] || { ok=0; break; }
    done
    [ "$ok" = 1 ] || continue
    set_status_file "$f" ready
    d="${f%/task.md}"; printf '%s\n' "${d##*/}"
  done
}

# Set the status cell of a BOARD row in a file on disk.
board_set() {
  uv run --project "$ROOT" python - "$1" "$2" "$3" <<'PY'
import io, sys
path, tid, status = sys.argv[1:]
lines = io.open(path, encoding="utf-8", newline="").read().split("\n")
for i, line in enumerate(lines):
    cells = line.split("|")
    if len(cells) > 5 and cells[1].strip() == tid:
        cells[4] = " %s " % status
        lines[i] = "|".join(cells)
io.open(path, "w", encoding="utf-8", newline="").write("\n".join(lines))
PY
}

# Lock for anything that writes to main in the main worktree (merge, BOARD, new tasks).
LOCK="$COMMON/lo-main.lock"
lock_main() {
  local waited=0
  until mkdir "$LOCK" 2>/dev/null; do
    if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +30 2>/dev/null)" ]; then
      echo "${0##*/}: removing stale lock $LOCK" >&2; rmdir "$LOCK" 2>/dev/null; continue
    fi
    [ "$waited" -lt 300 ] || die "main is locked for 5 min ($LOCK), another merge or plan is running"
    sleep 2; waited=$((waited + 2))
  done
  trap unlock_main EXIT
}
unlock_main() { rmdir "$LOCK" 2>/dev/null || true; }

require_root_on_main() {
  [ "$(git -C "$ROOT" branch --show-current)" = main ] \
    || die "the main worktree $ROOT is not on main: tasks belong in .worktrees/, see coop-protocol.md «Параллельная работа»"
}
