"""NodeConfig: everything the runner of one slot reads from the environment/secret files.

Secrets never ride `os.environ` where the harness could inherit them: the runner key
(hive registry writes, mind commands, the will-session proxy) and the Z.ai token are
read once from 0400 files mounted by compose (`*_FILE` below); the entrypoint renders
the Z.ai provider and the opencode auth into the sister's home before the runner starts.
The node MCP key is NOT a runner secret — it is the agent's own identity and lives in
the workplace harness configs by design (ADR-033: keys are names inside the network).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from runtime.config import CHARACTER_DIR, REPO_ROOT, env
from runtime.node.workplace import PI_CONTEXT_WINDOW, PI_KEEP_RECENT_TOKENS

HARNESSES = ("pi", "opencode", "zcode", "hermes")
QUOTA_ACTIONS = ("wait", "switch")


def _from_file(path_value: str | None, *, env_name: str) -> str:
    """Read a secret file the entrypoint materialized; a MISSING file is simply an
    unset secret (the entrypoint writes only what it got) — a present but unreadable
    one is a startup error, not a silent empty key."""
    if not path_value:
        return ""
    try:
        return Path(path_value).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise ValueError(f"{env_name}: cannot read {path_value}: {exc}") from None


def _float(name: str, default: float) -> float:
    raw = env(name, default=str(default)) or str(default)
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}") from None


@dataclass(frozen=True)
class NodeConfig:
    slot: str  # the slot's name, the Minecraft username (e.g. LastOrder01)
    body_base: str  # mind-N's runner/MCP endpoints (body-N's network namespace)
    hive_url: str
    workplaces: Path  # this slot's workplaces volume root (agent/, archive/, shared/)
    template_dir: Path  # agent/last_order (T-029)
    shared_dir: Path  # agent/shared
    harness: str  # primary adapter
    failover: str  # "" or the other adapter
    quota_action: str  # wait | switch (NETWORK.md section 9)
    node_key: str  # the agent's X-Node-Key
    runner_key: str  # the runner's own key: hive /registry/*, mind /runner/*, the will proxy
    zai_key: str  # Z.ai Coding Plan token (ADR-033 exception; rendered by the entrypoint)
    hapi_url: str  # the console hub (ADR-042): pi rides `hapi pi` when a token is set too
    hapi_token: str  # the NAMESPACED hub token (<base>:node-N, T-044 R1-1/R1-3); "" = direct pi
    gateway_url: str  # llm-gateway for the opencode adapter
    gateway_key: str  # the node's virtual gateway key (opencode auth, entrypoint-rendered)
    zcode_model: str
    opencode_model: str
    hermes_model: str
    pi_model: str
    pi_context_window: int  # the compaction budget, not the model's real window (T-066)
    pi_keep_recent_tokens: int
    naming: str
    will_timeout_s: float  # a COLD will session (fresh context, files, submit_will) is slower than the spike's warm 2.5 min
    poll_s: float  # /runner/status poll while a session runs
    restart_pause_s: float
    restart_pause_max_s: float
    restart_reset_after_s: float  # a session this stable resets the restart pause
    stuck_s: float  # no session-file growth this long → the board `stuck` alarm
    kill_grace_s: float  # SIGTERM → SIGKILL window
    hive_retry_pause_s: float
    hive_retry_max_s: float
    hive_retry_rounds: int  # bounded retries (mark_dead); retire/birth retry forever
    pending_wills: Path | None  # hive's run volume mounted ro (staged submit_will files)
    sister_uid: int | None  # the harness runs as this uid; None = no privilege drop
    sister_gid: int | None
    dev_open: bool = False  # NODE_DEV_OPEN=1: the explicit open dev mode (review R2-6)

    @classmethod
    def from_env(cls) -> "NodeConfig":
        harness = env("HARNESS", default="pi") or "pi"
        failover = env("HARNESS_FAILOVER", default="") or ""
        quota_action = env("QUOTA_ACTION", default="wait") or "wait"
        if harness not in HARNESSES:
            raise ValueError(f"HARNESS must be one of {HARNESSES}, got {harness!r}")
        if failover and failover not in HARNESSES:
            raise ValueError(f"HARNESS_FAILOVER must be one of {HARNESSES} or empty, got {failover!r}")
        if failover == harness:
            raise ValueError("HARNESS_FAILOVER must name the other adapter, not HARNESS itself")
        if quota_action not in QUOTA_ACTIONS:
            raise ValueError(f"QUOTA_ACTION must be one of {QUOTA_ACTIONS}, got {quota_action!r}")
        if quota_action == "switch" and not failover:
            raise ValueError("QUOTA_ACTION=switch needs HARNESS_FAILOVER")
        slot = env("NODE_SLOT", default="") or ""
        if not slot:
            raise ValueError("NODE_SLOT is required (the slot name, e.g. LastOrder01)")
        pending = env("HIVE_PENDING_WILLS", default="") or ""
        def _secret(file_env: str, plain_env: str) -> str:
            # the entrypoint materializes secrets as 0400 root-only files; the plain env
            # names stay as dev/test fallbacks
            return _from_file(env(file_env, default=""), env_name=file_env) or (
                env(plain_env, default="") or ""
            )

        runner_key = _secret("RUNNER_KEY_FILE", "HIVE_RUNNER_KEY")
        node_key = _secret("HIVE_NODE_KEY_FILE", "HIVE_NODE_KEY")
        zai_key = _secret("ZAI_KEY_FILE", "ZAI_API_KEY")
        hapi_token = _secret("HAPI_TOKEN_FILE", "HAPI_TOKEN")
        if hapi_token and ":" not in hapi_token:
            raise ValueError(
                "HAPI_TOKEN must be the namespaced form (<token>:node-N) — the base "
                "console token reads the whole network (T-044)"
            )
        gateway_key = _secret("GATEWAY_KEY_FILE", "GATEWAY_NODE_KEY")
        posix_uid = env("SISTER_UID", default="") or ""
        uid = int(posix_uid) if posix_uid.isdigit() else None
        gid = int(env("SISTER_GID", default=posix_uid) or 0) if uid is not None else None
        zcode_model = env("ZCODE_MODEL", default="GLM-5.3-Flash") or "GLM-5.3-Flash"
        return cls(
            slot=slot,
            body_base=env("BODY_BASE", default="http://body-1:8700") or "http://body-1:8700",
            hive_url=env("HIVE_URL", default="http://hive:8000") or "http://hive:8000",
            workplaces=Path(env("WORKPLACES_DIR", default="workplaces") or "workplaces"),
            template_dir=Path(env("TEMPLATE_DIR", default=str(CHARACTER_DIR)) or str(CHARACTER_DIR)),
            shared_dir=Path(env("SHARED_DIR", default=str(REPO_ROOT / "agent" / "shared"))),
            harness=harness,
            failover=failover,
            quota_action=quota_action,
            node_key=node_key,
            runner_key=runner_key,
            zai_key=zai_key,
            hapi_url=env("HAPI_URL", default="http://hapi-hub:3006") or "http://hapi-hub:3006",
            hapi_token=hapi_token,
            gateway_url=env("GATEWAY_URL", default="http://llm-gateway:4000") or "",
            gateway_key=gateway_key,
            zcode_model=zcode_model,
            opencode_model=(
                env("OPENCODE_MODEL", default="openrouter/qwen/qwen3.7-flash")
                or "openrouter/qwen/qwen3.7-flash"
            ),
            # HERMES_MODEL rides the same Z.ai plan as zcode, so its default IS
            # ZCODE_MODEL (compose passes only ZCODE_MODEL; HERMES_MODEL is the
            # per-harness override for the spike without a compose change).
            hermes_model=env("HERMES_MODEL", default=zcode_model) or zcode_model,
            # Pi rides the same Z.ai plan too: PI_MODEL defaults to ZCODE_MODEL
            pi_model=env("PI_MODEL", default=zcode_model) or zcode_model,
            pi_context_window=int(_float("PI_CONTEXT_WINDOW", PI_CONTEXT_WINDOW)),
            pi_keep_recent_tokens=int(_float("PI_KEEP_RECENT_TOKENS", PI_KEEP_RECENT_TOKENS)),
            naming=env("NODE_NAMING_PRESET", default="lab") or "lab",
            will_timeout_s=_float("WILL_TIMEOUT_S", 600.0),
            poll_s=_float("MIND_POLL_S", 2.0),
            restart_pause_s=_float("RESTART_PAUSE_S", 5.0),
            restart_pause_max_s=_float("RESTART_PAUSE_MAX_S", 60.0),
            restart_reset_after_s=_float("RESTART_RESET_AFTER_S", 300.0),
            stuck_s=_float("STUCK_S", 600.0),
            kill_grace_s=_float("KILL_GRACE_S", 10.0),
            hive_retry_pause_s=_float("HIVE_RETRY_PAUSE_S", 2.0),
            hive_retry_max_s=_float("HIVE_RETRY_MAX_S", 60.0),
            hive_retry_rounds=max(1, int(_float("HIVE_RETRY_ROUNDS", 10))),
            pending_wills=Path(pending) if pending else None,
            sister_uid=uid,
            sister_gid=gid if uid is not None else None,
            dev_open=(env("NODE_DEV_OPEN", default="") or "") == "1",
        )
