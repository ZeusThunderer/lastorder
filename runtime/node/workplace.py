"""Birth and archive of one node's workplace (T-029 harness README, ADR-034).

Birth is the README's recipe, in order: copy the whole `agent/last_order/` template,
render `PERSONA.md` through `PersonaProvider.render_core` (name and speech manner of
the preset, dealt traits as words — the numbers stay in birth.json), drop the harness
configs at the workplace root (MCP `body` of this slot + MCP `hive` with the node key),
ship `agent/shared/` next to the workplace as the network instructions.

Ownership is the agent/runner boundary of plan review P1-2: the runner (root in the
container) owns the volume root, `archive/`, `shared/`, the core files and the harness
configs; the workplace directory itself is sticky and sister-writable — she writes her
memory, goals and skills, but cannot delete, replace or chmod the runner's files. On a
non-POSIX host (unit tests) only the mode bits apply; the stand probe exercises the
uid side (`docker compose exec -u sister`).
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from collections.abc import Iterator
from pathlib import Path

from runtime.knowledge.slice import format_serial
from runtime.lastorder.persona import Node, PersonaProvider
from runtime.lastorder.traits import Traits

# Mode plan of the boundary (P1-2); the sticky bit is what keeps runner-owned files
# undeletable inside the sister-writable workplace directory.
WORKPLACE_DIR_MODE = stat.S_IFDIR | 0o1777
READ_ONLY_FILE = 0o444
READ_ONLY_DIR = 0o555
SISTER_FILE = 0o666
SISTER_DIR = 0o777
CORE_FILES = ("PERSONA.md", "AGENTS.md", "SESSION_PROMPT.md")
SISTER_WRITABLE_DIRS = ("memory", "skills", "policy")
RUNNER_DIR = ".runner"


def serial_dir(serial: int) -> str:
    return format_serial(serial)


def _chmod(path: Path, mode: int) -> None:
    os.chmod(path, mode)


def _iter_tree_no_links(root: Path) -> Iterator[Path]:
    """root and everything under it, never following symlinks (review R2-1): a symlink
    planted by the sister is yielded as a leaf, its target never entered — neither for
    recursion (os.walk(followlinks=False), unlike rglob on Python 3.13) nor for chmod,
    which follows the link and would re-mode a root file outside the workplace."""
    yield root
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            yield Path(dirpath) / name


def _chmod_tree(root: Path, dir_mode: int, file_mode: int) -> None:
    """Mode a whole runner-owned tree, symlinks untouched (see _iter_tree_no_links)."""
    for item in _iter_tree_no_links(root):
        if item.is_symlink():
            continue
        _chmod(item, dir_mode if item.is_dir() else file_mode)


def _own(path: Path, uid: int | None, gid: int | None) -> None:
    if uid is not None and hasattr(os, "chown"):
        try:
            os.chown(path, uid, gid if gid is not None else -1)
        except OSError:
            pass  # non-POSIX or unprivileged: the mode plan still applies


def _write_readonly(path: Path, text: str, *, uid: int | None = None, gid: int | None = None) -> None:
    """The workplace write path: the sticky root dir (01777) already keeps the
    sister from unlinking or replacing the runner's names there, so the plain
    tmp+replace is enough — unlike HERMES_HOME, see _write_file_no_links."""
    tmp = path.with_name(path.name + ".runner-tmp")
    tmp.write_text(text, encoding="utf-8")
    _own(tmp, uid, gid)
    tmp.replace(path)
    _chmod(path, READ_ONLY_FILE)


def _unlink_no_follow(path: Path) -> None:
    """Remove a NAME in a sister-owned dir (review R2-1): unlink never follows a
    symlink, so a planted link only loses its name and its target is never touched.
    Windows-only pre-step: unlink there refuses the readonly bit, and POSIX never
    needs the file's own mode to unlink it — so no chmod-by-path happens on the
    platform where the attack lives."""
    if os.name == "nt" and not path.is_symlink():
        try:
            os.chmod(path, 0o644)
        except OSError:
            pass
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _write_file_no_links(
    path: Path, text: str, *, mode: int, uid: int | None = None, gid: int | None = None
) -> None:
    """The HERMES_HOME write path (review R2-1). That directory belongs to the
    sister (0700, the R1-2 deviation the gateway forces), so she CAN unlink and
    re-create names there — every step must refuse to follow a symlink:
      - the target and the tmp name are unlinked first (never chmod'd by path);
      - the tmp file is BORN via open(O_EXCL|O_NOFOLLOW): a raced-in link fails
        the open instead of being written through (bounded retries, then raise —
        the session fails closed, it never writes through her name);
      - ownership and the mode go through the SAME open descriptor (fchown/fchmod);
      - os.replace lands the file; nothing touches `path` by name afterwards.
    """
    tmp = path.with_name(path.name + ".runner-tmp")
    for _ in range(3):
        _unlink_no_follow(tmp)
        _unlink_no_follow(path)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(tmp, flags, mode)
        except FileExistsError:
            continue  # the name lost a race: drop it again, never write through it
        break
    else:
        raise FileExistsError(f"{tmp}: the tmp name keeps existing; refusing to write")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            if uid is not None and hasattr(os, "fchown"):
                try:
                    os.fchown(f.fileno(), uid, gid if gid is not None else -1)
                except OSError:
                    pass  # non-POSIX or unprivileged: the mode plan still applies
            if hasattr(os, "fchmod"):
                os.fchmod(f.fileno(), mode)
    except BaseException:
        try:
            os.close(fd)  # only when fdopen never took it over
        except OSError:
            pass
        raise
    os.replace(tmp, path)


def _copy_tree_readonly(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst)
    _chmod_tree(dst, READ_ONLY_DIR, READ_ONLY_FILE)


def _force_rmtree(path: Path) -> None:
    """Remove a read-only tree (the shared refresh of the next birth): give the modes
    back first — POSIX refuses rmdir on 0555 dirs, Windows unlink on 0444 files. A
    symlink is only unlinked, never chmod'd: chmod follows the link and would loosen
    whatever root-owned file it points at (review R2-1)."""

    def _loosen(func, target, _exc) -> None:
        try:
            if not os.path.islink(target):
                os.chmod(target, 0o700 if os.path.isdir(target) else 0o600)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onexc=_loosen)


def _make_sister_writable(root: Path, uid: int | None, gid: int | None) -> None:
    for name in SISTER_WRITABLE_DIRS:
        path = root / name
        if not path.exists():
            continue
        for item in _iter_tree_no_links(path):
            if item.is_symlink():
                continue  # the runner never re-modes or re-owns a symlink's target (R2-1)
            _own(item, uid, gid)
            _chmod(item, SISTER_DIR if item.is_dir() else SISTER_FILE)
    goals = root / "GOALS.md"
    if goals.exists():
        _own(goals, uid, gid)
        _chmod(goals, SISTER_FILE)


def hive_mcp_url(hive_url: str) -> str:
    return f"{hive_url.rstrip('/')}/node/mcp"


def render_zcode_config(body_base: str, hive_url: str, node_key: str) -> dict:
    """zcode.json of the workplace: the body MCP of this slot plus the network MCP."""
    return {
        "mcp": {
            "servers": {
                "body": {
                    "type": "http",
                    "url": f"{body_base.rstrip('/')}/mcp",
                    "enabled": True,
                },
                "hive": {
                    "type": "http",
                    "url": hive_mcp_url(hive_url),
                    "enabled": True,
                    "headers": {"X-Node-Key": node_key},
                },
            }
        }
    }


def render_opencode_config(
    model: str, gateway_url: str, body_base: str, hive_url: str, node_key: str
) -> dict:
    """opencode.json: the node tier through the gateway (ADR-033) plus both MCP servers."""
    return {
        "$schema": "https://opencode.ai/config.json",
        "autoupdate": False,
        "share": "disabled",
        "model": f"llm-gateway/{model}",
        "provider": {
            "llm-gateway": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "LLM Gateway",
                "options": {"baseURL": f"{gateway_url.rstrip('/')}/v1"},
                "models": {model: {"name": "node tier"}},
            }
        },
        "mcp": {
            "body": {
                "type": "remote",
                "url": f"{body_base.rstrip('/')}/mcp",
                "enabled": True,
            },
            "hive": {
                "type": "remote",
                "url": hive_mcp_url(hive_url),
                "enabled": True,
                "headers": {"X-Node-Key": node_key},
            },
        },
    }


# Hermes Agent (ADR-032 third harness) keeps its home on the sister's tmpfs, NOT in
# the workplace: state.db, sessions and the .env with the Z.ai key are per-container
# runtime state, while the workplace is the persona that gets archived read-only.
# The entrypoint lays the home out once per container (drop_hermes_home); the
# adapter then re-renders config.yaml before EVERY life session (drop_hermes_config,
# review R1-1) — one home serves all generations of the slot, and the will session
# of the previous generation leaves the proxy URL in it. The will session itself
# gets a fresh render with hive at the proxy (set_will_mode, R2-2), and every write
# into the sister-owned home goes link-safe (R2-1). The node MCP key inside
# config.yaml is the agent's own identity, same rule as zcode.json (ADR-033: keys
# are names inside the network).
HERMES_HOME = Path("/home/sister/.hermes")
# The coding endpoint of the GLM Coding Plan (harness-spike step 0): Hermes would
# otherwise probe endpoints itself and could land on the billed-by-balance one.
HERMES_GLM_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
# The identity slot SOUL.md has to be non-empty: an empty one makes Hermes inject
# its own DEFAULT_AGENT_IDENTITY ("You are Hermes Agent, built by Nous Research…")
# on top of the sister. This note occupies the slot and points identity back to
# the workplace files; what Hermes still adds on its own is in harness-spike.md.
HERMES_SOUL_MD = (
    "# Identity\n\n"
    "Your identity, duties and manner of speech are defined by this workspace's "
    "AGENTS.md and PERSONA.md. Follow them. Do not invent or add another persona.\n"
)
# T-024 parity (supervisor note): the built-in memory, the user profile and skill
# self-creation stay off — the sister's memory and skills live in the workplace.


def _glm_model(model: str) -> str:
    """Z.ai's OpenAI-compatible endpoint knows lowercase model ids (its own probe
    list is glm-5.3/glm-5.3-flash/…), while ZCODE_MODEL habitually travels cased."""
    return model.strip().lower()


def render_hermes_config(model: str, body_base: str, hive_url: str, node_key: str) -> dict:
    """Hermes config.yaml: the Z.ai coding provider, both MCP servers of the slot,
    the file/terminal/body/hive toolsets (T-035: the two server names ride the CLI
    flag too — it gates the MCP servers and their toolsets) and every Hermes extra
    (memory, skills, self-review, messenger/cron/browser surfaces, update checks)
    off. No key material here: GLM_API_KEY/GLM_BASE_URL ride HERMES_HOME/.env
    (written by the entrypoint)."""
    return {
        # the config-version stamp of the pinned release (doctor's "outdated v0 → v51"
        # nudge in the dry run): a freshly rendered config IS current, no migration
        "_config_version": 51,
        "model": {"default": _glm_model(model), "provider": "zai"},
        "mcp_servers": {
            "body": {
                "url": f"{body_base.rstrip('/')}/mcp",
                # fail fast: body/hive restarts (the death path) must not stall a
                # session start for the upstream default connect timeout of 60 s
                "connect_timeout": 10,
                "sampling": {"enabled": False},
            },
            "hive": {
                "url": hive_mcp_url(hive_url),
                "headers": {"X-Node-Key": node_key},
                "connect_timeout": 10,
                "sampling": {"enabled": False},
            },
        },
        # The CLI surface, kept equal to the adapter's --toolsets flag (T-035): file,
        # terminal and the bare MCP server names, which the flag matches as the spawn
        # allowlist and the registry knows as toolset aliases. The messenger/browser/
        # cron/delegation toolsets exist upstream but never load.
        "platform_toolsets": {"cli": ["file", "terminal", "body", "hive"]},
        "memory": {"memory_enabled": False, "user_profile_enabled": False, "nudge_interval": 0},
        "skills": {"creation_nudge_interval": 0},
        "updates": {"check": False},
        "auxiliary": {
            # The post-turn "save memory / patch a skill" fork is a memory/skill
            # feature dressed as an aux call — off for the T-024-parity spike.
            "background_review": {"enabled": False},
            # Titles stay the instant derived kind: no model call per session.
            "title_generation": {"model_upgrade_enabled": False},
        },
    }


# The render params of the last drop per home (review R2-2): set_will_mode rebuilds
# the will config from THEM, never from the config.yaml file — in HERMES_HOME that
# file is a name the sister can replace (her dir, 0700). The adapter refreshes the
# entry before every life session; a runner restart between the last life render
# and the will leaves it empty, and the will then runs unmarked on the leftover
# config — the real hive refuses an unmarked submit_will and the emergency will
# covers it: degraded, never unsafe.
_HERMES_RENDER: dict[Path, tuple[str, str, str, str]] = {}


def drop_hermes_config(
    home: Path,
    *,
    model: str,
    body_base: str,
    hive_url: str,
    node_key: str,
    hive_mcp_override: str | None = None,
) -> None:
    """(Re)render HERMES_HOME/config.yaml, root-owned 0444 — the runner's file, not
    the sister's (review R1-2). The entrypoint calls it once per container; the
    hermes adapter calls it again before EVERY life session (review R1-1): the will
    session before this one pointed the hive MCP at the will proxy, and without this
    render every later generation of the slot would sit on the stopped proxy.
    `hive_mcp_override` is the will-mode hook (R2-2): the full MCP url of the will
    proxy replaces the hive entry of an otherwise normal render. The write goes
    through _write_file_no_links — the sister owns the directory and could plant
    symlinks on these names (R2-1).
    This heals a replaced or deleted config.yaml FILE only — a sister who removes
    the whole home gets a root-owned mkdir here (the gateway then refuses it) and
    loses her .env; that state needs a container restart, not a re-render."""
    home.mkdir(parents=True, exist_ok=True)
    _HERMES_RENDER[home] = (model, body_base, hive_url, node_key)
    config = render_hermes_config(model, body_base, hive_url, node_key)
    if hive_mcp_override:
        config["mcp_servers"]["hive"]["url"] = hive_mcp_override.rstrip("/")
    _write_file_no_links(
        home / "config.yaml",
        json.dumps(config, indent=2),
        mode=READ_ONLY_FILE,
        uid=0,
        gid=0,
    )


def drop_hermes_home(
    home: Path,
    *,
    model: str,
    body_base: str,
    hive_url: str,
    node_key: str,
    glm_key: str = "",
    uid: int | None = None,
    gid: int | None = None,
) -> None:
    """Lay out HERMES_HOME (review R1-2). config.yaml and SOUL.md are the runner's:
    root-owned 0444, so the sister cannot edit them in place. The DIRECTORY has to
    stay hers (0700): the detached gateway refuses any other owner or a group/world
    -writable home ("unsafe_control_path … chown it to the gateway user", verified
    in the container) — a sticky root-owned dir, the workplace plan, cannot apply
    here. A replaced/deleted config.yaml file is healed by the adapter's per-life
    re-render (R1-1); a removed whole home is not (see drop_hermes_config). The
    .env with the Z.ai key is her own (0600, same boundary as the zcode provider
    config, ADR-033). Everything else Hermes creates itself on first run."""
    drop_hermes_config(
        home, model=model, body_base=body_base, hive_url=hive_url, node_key=node_key
    )
    # SOUL.md and .env are names in the sister's dir too: the same no-follow write
    # (R2-1) — she could have pointed them at a root file before a container re-run.
    _write_file_no_links(home / "SOUL.md", HERMES_SOUL_MD, mode=READ_ONLY_FILE, uid=0, gid=0)
    if glm_key:
        _write_file_no_links(
            home / ".env",
            f"GLM_API_KEY={glm_key}\nGLM_BASE_URL={HERMES_GLM_BASE_URL}\n",
            mode=0o600,
            uid=uid,
            gid=gid,
        )
    _own(home, uid, gid)
    _chmod(home, 0o700)


# Pi (ADR-041 primary harness; the adapter is T-066, the `hapi pi` wrapper T-045). Like
# Hermes, Pi keeps its agent dir on the sister's tmpfs home, NOT in the workplace.
# ~/.pi/agent carries models.json (the Z.ai coding provider — the same base URL and
# plan the zcode and hermes configs use), mcp.json (both MCP servers of the slot; MCP
# is builtin since 1.0 — pi-mcp ships as a core dependency), settings.json (the zai
# defaults and the compaction cap) and zai-key (the plan token, the sister's own
# 0400 file — the zcode provider-config pattern: readable by her, never in the
# process env, so no `env` in a bash tool can leak it into a session or an archive).
PI_AGENT_DIR = Path("/home/sister/.pi/agent")
PI_KEY_FILE = "zai-key"
PI_SLOT_FILE = "slot"  # the slot name for misaka-link's delivery_failing alarm (T-048 R1-4)
PI_ZAI_BASE_URL = HERMES_GLM_BASE_URL  # the coding endpoint of the GLM Coding Plan
# where pi keeps its session files: the WORKPLACE (a volume), relative to the
# process cwd — the tmpfs home dies with the container and takes the resume key
# with it (verified live, T-045). Same value the direct mode's --session-dir uses.
PI_WORKPLACE_SESSION_DIR = ".pi-sessions"
# the will's own session dir, one per death (R1-2): empty and unique — the wrapper
# cannot continue anything from there
PI_WILL_SESSION_PREFIX = ".pi-will-"
# misaka-link (T-048): the environment's delivery extension of the live `hapi pi`
# session (ADR-042 — stdin belongs to HAPI, so the board feed and Misaka-net reach
# the agent only through it). Dropped from runtime/pi/misaka-link/ into the agent
# dir's extensions/ on every render — same heal-a-replaced-file contract as the
# configs. Explicit list, no tests and no package.json: only what Pi loads.
MISAKA_LINK_DIR = Path(__file__).resolve().parents[1] / "pi" / "misaka-link"
MISAKA_LINK_FILES = (
    "index.ts",
    "misaka-link.ts",
    "naming.ts",
    "envelope.ts",
    "hive.ts",
    "link.ts",
)
# The context cap (T-066). Pi compacts when the context passes
# contextWindow - reserveTokens and keeps the last keepRecentTokens verbatim; the
# window here is a budget, not the model's real one. On zcode one life grew to
# 200–240k tokens and every wake resent all of it. The gap between "after a
# compaction" (base context + summary + keepRecentTokens, ~45k [предположение]) and
# the threshold (~80k) must stay wide, or Pi compacts on every other turn.
PI_CONTEXT_WINDOW = 96_000
PI_RESERVE_TOKENS = 16_384
PI_KEEP_RECENT_TOKENS = 20_000
# A sister's life sessions think at `low` (operator, 2026-11-07, D-007): thinking is
# resent with the history and was 19 % of the input (T-056 report), and it is the
# output that makes a turn slow. The will session keeps Pi's default (`medium`).
PI_SISTER_THINKING_LEVEL = "low"


def render_pi_configs(
    model: str,
    body_base: str,
    hive_url: str,
    node_key: str,
    *,
    context_window: int = PI_CONTEXT_WINDOW,
    keep_recent_tokens: int = PI_KEEP_RECENT_TOKENS,
    key_file: str = str(PI_AGENT_DIR / PI_KEY_FILE),
    session_dir: str = PI_WORKPLACE_SESSION_DIR,
    thinking_level: str | None = None,
) -> dict[str, dict]:
    """The Pi agent-dir configs: models.json + mcp.json + settings.json, by content.
    No key material in the configs: apiKey is a `!command` reference (Pi resolves a
    leading `!` by running the rest as a shell command, verified in the 1.4.0 bundle
    — execSync, .trim(), 10 s timeout) that cats the sister-owned key file at
    request time; the key never rides the process env, mcp.json carries the node
    key — the agent's own identity, same rule as zcode.json/config.yaml (ADR-033).
    Both servers are `direct`: Pi's default `codemode` exposure declares no MCP tool
    to the model at all (Pi docs mcp.md). `session_dir` lands in settings.json so
    the WRAPPED pi (whose argv the runner does not build) still stores its sessions
    in the workplace volume — the tmpfs home dies with the container and takes the
    resume key with it (verified live: "No session found matching …")."""
    glm = _glm_model(model)
    return {
        "models.json": {
            "providers": {
                "zai": {
                    "baseUrl": PI_ZAI_BASE_URL,
                    "api": "openai-completions",
                    "apiKey": f"!cat {key_file}",
                    # the endpoint's verified quirks (docs models-configuration.md):
                    # no developer role, thinking rides thinking.type — the same
                    # compat the zai guides prescribe
                    "compat": {"supportsDeveloperRole": False, "thinkingFormat": "zai"},
                    "models": [
                        {"id": glm, "reasoning": True, "contextWindow": context_window}
                    ],
                }
            }
        },
        "mcp.json": {
            "autoEnableCodemode": False,
            "mcpServers": {
                "body": {
                    "url": f"{body_base.rstrip('/')}/mcp",
                    "timeout": 10,
                    "exposure": "direct",
                },
                "hive": {
                    "url": hive_mcp_url(hive_url),
                    "headers": {"X-Node-Key": node_key},
                    "timeout": 10,
                    "exposure": "direct",
                },
            },
        },
        "settings.json": {
            "defaultProvider": "zai",
            "defaultModel": glm,
            "enableInstallTelemetry": False,
            "enableAnalytics": False,
            # the wrapped pi reads this (its argv is the wrapper's): sessions into
            # the workplace volume, resolved against the process cwd — the runner
            # spawns every session in the life's workplace root
            "sessionDir": session_dir,
            "compaction": {
                "enabled": True,
                "reserveTokens": PI_RESERVE_TOKENS,
                "keepRecentTokens": keep_recent_tokens,
            },
            # Pi's startup level (docs/settings.md: off|minimal|low|medium|high…);
            # absent means Pi's default `medium`
            **({"defaultThinkingLevel": thinking_level} if thinking_level else {}),
        },
    }


# The render params of the last drop per agent dir — set_will_mode rebuilds the will
# config from THEM, never from the file in the sister's dir (the Hermes rule, R2-2).
_PI_RENDER: dict[Path, dict] = {}


def drop_pi_agent_dir(
    agent_dir: Path,
    *,
    model: str,
    body_base: str,
    hive_url: str,
    node_key: str,
    zai_key: str = "",
    slot: str = "",
    context_window: int = PI_CONTEXT_WINDOW,
    keep_recent_tokens: int = PI_KEEP_RECENT_TOKENS,
    session_dir: str = PI_WORKPLACE_SESSION_DIR,
    uid: int | None = None,
    gid: int | None = None,
    hive_mcp_override: str | None = None,
) -> None:
    """(Re)render PI_AGENT_DIR: the three configs root-owned 0444 (the runner's files,
    the same boundary as HERMES_HOME/config.yaml, R1-2), written link-safe (the dir is
    hers, R2-1), plus zai-key — her own 0400 copy of the plan token (empty `zai_key`
    writes none; the runner gates the pi harness on the key being present) — plus the
    misaka-link extension under extensions/ (T-048) and the `slot` file it reads for
    its delivery_failing alarm (T-048 R1-4). The
    adapter calls it before every life session: that heals a replaced file and moves
    hive back from the will proxy. The dir and its parent (~/.pi) stay the sister's
    0700 — Pi keeps its own state there. `hive_mcp_override` is the will-mode hook:
    the full MCP url of the will proxy."""
    fresh_parent = not agent_dir.parent.exists()
    agent_dir.mkdir(parents=True, exist_ok=True)
    if fresh_parent:
        _own(agent_dir.parent, uid, gid)
        _chmod(agent_dir.parent, 0o700)
    params = dict(
        model=model, body_base=body_base, hive_url=hive_url, node_key=node_key,
        zai_key=zai_key, slot=slot, session_dir=session_dir,
        context_window=context_window, keep_recent_tokens=keep_recent_tokens,
    )
    if hive_mcp_override is None:
        _PI_RENDER[agent_dir] = {**params, "uid": uid, "gid": gid}
    if zai_key:
        _write_file_no_links(
            agent_dir / PI_KEY_FILE, zai_key, mode=0o400, uid=uid, gid=gid
        )
    if slot:
        _write_file_no_links(agent_dir / PI_SLOT_FILE, slot, mode=READ_ONLY_FILE, uid=0, gid=0)
    configs = render_pi_configs(
        model, body_base, hive_url, node_key,
        context_window=context_window, keep_recent_tokens=keep_recent_tokens,
        key_file=str(agent_dir / PI_KEY_FILE),
        session_dir=session_dir,
        thinking_level=None if hive_mcp_override else PI_SISTER_THINKING_LEVEL,
    )
    if hive_mcp_override:
        configs["mcp.json"]["mcpServers"]["hive"]["url"] = hive_mcp_override.rstrip("/")
    for name, config in configs.items():
        _write_file_no_links(
            agent_dir / name, json.dumps(config, indent=2), mode=READ_ONLY_FILE, uid=0, gid=0
        )
    extensions = agent_dir / "extensions" / "misaka-link"
    extensions.mkdir(parents=True, exist_ok=True)
    for name in MISAKA_LINK_FILES:
        _write_file_no_links(
            extensions / name,
            (MISAKA_LINK_DIR / name).read_text(encoding="utf-8"),
            mode=READ_ONLY_FILE,
            uid=0,
            gid=0,
        )
    _own(agent_dir, uid, gid)
    _chmod(agent_dir, 0o700)


def _set_pi_will_mode(proxy_url: str, serial: int | None = None) -> None:
    """The Pi twin of _set_hermes_will_mode: a fresh render with hive at the proxy from
    the last life render's params — and the session dir swapped to a per-death
    `.pi-will-<serial>` (R1-2): the wrapper may continue the cwd's latest session,
    and the will must start where there is nothing to continue (live: a will process
    once answered the board in the life's session instead of writing a will).
    Without params (a runner restart between the last life render and the will)
    nothing is written and the will runs unmarked — the real hive refuses an
    unmarked submit_will and the emergency will covers it."""
    params = _PI_RENDER.get(PI_AGENT_DIR)
    if params is None:
        return
    will_dir = PI_WORKPLACE_SESSION_DIR if serial is None else f"{PI_WILL_SESSION_PREFIX}{serial_dir(serial)}"
    drop_pi_agent_dir(
        PI_AGENT_DIR, **{**params, "session_dir": will_dir},
        hive_mcp_override=f"{proxy_url.rstrip('/')}/mcp",
    )


def _drop_harness_configs(
    root: Path,
    template: Path,
    *,
    body_base: str,
    hive_url: str,
    node_key: str,
    gateway_url: str,
    opencode_model: str,
) -> None:
    """Both adapters' files land at the root at birth: the failover switch (section 9)
    then needs no workplace surgery, and the harness config stays the runner's."""
    _write_readonly(
        root / "zcode.json",
        json.dumps(render_zcode_config(body_base, hive_url, node_key), indent=2),
    )
    _write_readonly(
        root / "opencode.json",
        json.dumps(
            render_opencode_config(opencode_model, gateway_url, body_base, hive_url, node_key),
            indent=2,
        ),
    )
    zcode_skills = template / "harness" / "zcode" / "skills"
    if zcode_skills.is_dir():
        _copy_tree_readonly(zcode_skills, root / ".zcode" / "skills")
    opencode_command = template / "harness" / "opencode" / "command"
    if opencode_command.is_dir():
        _copy_tree_readonly(opencode_command, root / ".opencode" / "command")


def create_workplace(
    *,
    workplaces: Path,
    template_dir: Path,
    shared_dir: Path,
    serial: int,
    traits: Traits,
    persona: PersonaProvider,
    node: Node | None = None,
    body_base: str,
    hive_url: str,
    node_key: str,
    gateway_url: str,
    opencode_model: str,
    sister_uid: int | None = None,
    sister_gid: int | None = None,
) -> Path:
    """The birth recipe of the T-029 harness README; returns the workplace root."""
    root = workplaces / "agent" / serial_dir(serial)
    if root.exists():
        raise FileExistsError(f"workplace of {serial_dir(serial)} already exists")
    root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template_dir, root)

    # PERSONA.md: the template's placeholder file becomes this node's rendered core.
    _write_readonly(
        root / "PERSONA.md", persona.render_core(node or Node(serial, traits))
    )
    for name in CORE_FILES:
        path = root / name
        if path.exists():
            _chmod(path, READ_ONLY_FILE)
    _drop_harness_configs(
        root,
        template_dir,
        body_base=body_base,
        hive_url=hive_url,
        node_key=node_key,
        gateway_url=gateway_url,
        opencode_model=opencode_model,
    )

    # The shared rules travel with every workplace: network instructions, read-only.
    shared_dst = workplaces / "shared"
    if shared_dst.exists():
        _force_rmtree(shared_dst)
    if shared_dir.is_dir():
        _copy_tree_readonly(shared_dir, shared_dst)

    (root / RUNNER_DIR).mkdir()
    _chmod(root / RUNNER_DIR, 0o755)

    # The workplace directory last: sticky + sister-writable (P1-2).
    _make_sister_writable(root, sister_uid, sister_gid)
    _chmod(root, WORKPLACE_DIR_MODE)
    return root


def set_will_mode(root: Path, harness: str, proxy_url: str, serial: int | None = None) -> None:
    """Point the hive MCP of the given adapter's config at the runner's will proxy for
    the will session: the proxy adds X-Runner-Session: will and the runner key, so the
    mark cannot be forged from the workplace (plan review P1-1)."""
    if harness == "hermes":
        _set_hermes_will_mode(proxy_url)
        return
    if harness == "pi":
        _set_pi_will_mode(proxy_url, serial)
        return
    name = "zcode.json" if harness == "zcode" else "opencode.json"
    path = root / name
    if not path.exists():
        return
    config = json.loads(path.read_text(encoding="utf-8"))
    mcp = config.get("mcp", {})
    entry = mcp["servers"].get("hive") if isinstance(mcp.get("servers"), dict) else mcp.get("hive")
    if entry is None:
        return
    entry["url"] = f"{proxy_url.rstrip('/')}/mcp"
    # the file is read-only and runner-owned: rewrite through the same atomic path
    _chmod(path, 0o644)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    _chmod(path, READ_ONLY_FILE)


def _set_hermes_will_mode(proxy_url: str) -> None:
    """The hermes twin of set_will_mode (R2-2): a FRESH render of the whole config
    with the hive entry at the will proxy — never a read-and-patch of the file on
    disk, because HERMES_HOME is the sister's (0700) and the config.yaml name could
    be her substitution (extra MCP servers, memory back on, another model). The
    params come from the last drop_hermes_config (the adapter refreshes them every
    life turn); without them — a runner restart between the last life render and
    the will — nothing is written and the will runs unmarked: submit_will without
    the runner mark is refused by the real hive and the emergency will covers it,
    degraded, never unsafe. The next life turn re-renders back onto the real hive
    (R1-1), so no restore is needed here."""
    params = _HERMES_RENDER.get(HERMES_HOME)
    if params is None:
        return
    model, body_base, hive_url, node_key = params
    drop_hermes_config(
        HERMES_HOME,
        model=model,
        body_base=body_base,
        hive_url=hive_url,
        node_key=node_key,
        hive_mcp_override=f"{proxy_url.rstrip('/')}/mcp",
    )


def link_current(workplaces: Path, serial: int) -> bool:
    """`current` → this serial's workplace: mind-N reads the Jev policy through it
    (JEV_POLICY_PATH of compose), re-read on every call, so the link moves at birth.
    False when the host refuses symlinks (a Windows dev run): the policy path just
    stays empty and C1 runs without a policy, same as before this link existed."""
    link = workplaces / "current"
    target = Path("agent") / serial_dir(serial)
    tmp = workplaces / "current.runner-tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    try:
        tmp.symlink_to(target)
        tmp.replace(link)
    except OSError:
        return False
    return True


def archive_workplace(workplaces: Path, serial: int) -> Path:
    """agent/<serial> → archive/<serial>, everything read-only (NETWORK.md section 6.5).
    Symlinks the sister left behind are moved as links and never chmod'd: the runner
    works as root, and a chmod through `memory/k -> /run/node/secrets/runner_key` would
    re-mode the secret itself (review R2-1)."""
    root = workplaces / "agent" / serial_dir(serial)
    if not root.exists():
        raise FileNotFoundError(f"no workplace of {serial_dir(serial)} to archive")
    archive = workplaces / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    _chmod(root, 0o755)  # undo the sticky+writable mode to move it
    dst = archive / serial_dir(serial)
    if dst.exists():
        _force_rmtree(dst)
    root.replace(dst)
    _chmod_tree(dst, READ_ONLY_DIR, READ_ONLY_FILE)
    return dst
