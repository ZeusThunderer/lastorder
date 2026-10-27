"""T-044 spike driver: one `hapi pi` agent inside a node/last-order sandbox.

Run as root in the container, script fed over stdin (no repo mount needed):

  docker exec -i -e HAPI_AGENT_MODE=slot -e HAPI_ACTION=start node1-pi \
    /app/.venv/bin/python - < dev/console_spike/hapi_agent.py

Modes (ADR-042: the runner and the admin launch `hapi pi`; here the spike does
it by hand, the runner integration is T-045/T-043):
  slot — the slot 1 sister: drop_pi_agent_dir configs (zai model + body/hive
         MCP) in /home/sister/.pi/agent, ZAI_API_KEY from /run/node/secrets;
  lo   — a minimal test agent in the last-order sandbox: gateway provider
         (llm-gateway + the LO gateway key the entrypoint rendered into the
         opencode auth store), no MCP — it exists to prove the console path,
         not the tool wiring.

Actions: start (render configs + env file, spawn `hapi pi` as `sister` with no
TTY, report pid/log), status (alive + log tail), stop (SIGTERM, report exit).

Env the caller passes: HAPI_API_URL, CLI_API_TOKEN (the hub's token — the very
thing whose reach the boundary checks measure), HAPI_AGENT_MODE, HAPI_ACTION,
PI_MODEL (slot, default GLM-5.3-Flash), LO_MODEL (default the node tier
qwen3.7-flash through the gateway). Secrets travel as a 0600 env file in the
sister's home, never argv; the log below must stay key-free (the report greps
it for prefixes).
"""
import json
import os
import pathlib
import signal
import subprocess
import sys
import time

SISTER = "sister"
SISTER_UID = 1001
HOME = pathlib.Path("/home/sister")
AGENT_DIR = HOME / ".pi" / "agent"
ENV_FILE = HOME / ".hapi-agent-env"
SPIKE_DIR = pathlib.Path("/tmp/hapi-agent")
LOG = SPIKE_DIR / "hapi.log"
PID_FILE = SPIKE_DIR / "hapi.pid"
GATEWAY_URL = "http://llm-gateway:4000/v1"


def own_sister(path: pathlib.Path) -> None:
    os.chown(path, SISTER_UID, SISTER_UID)


def write_env(values: dict[str, str]) -> None:
    """(Re)create the sister's env file: root rewrites it every start, so no
    stale or planted copy survives (same rule as the T-041 spike-env)."""
    if ENV_FILE.is_symlink() or ENV_FILE.exists():
        ENV_FILE.unlink()
    ENV_FILE.write_text(
        "".join(f"export {k}={v}\n" for k, v in values.items()), encoding="utf-8"
    )
    os.chmod(ENV_FILE, 0o600)
    own_sister(ENV_FILE)


def render_slot() -> dict[str, str]:
    from runtime.node.workplace import drop_pi_agent_dir

    key = pathlib.Path("/run/node/secrets/zai_api_key").read_text(encoding="utf-8").strip()
    node_key = pathlib.Path("/run/node/secrets/node_mcp_key").read_text(encoding="utf-8").strip()
    drop_pi_agent_dir(
        AGENT_DIR,
        model=os.environ.get("PI_MODEL", "GLM-5.3-Flash"),
        body_base=os.environ.get("BODY_BASE", "http://body-1:8700"),
        hive_url=os.environ.get("HIVE_URL", "http://hive:8000"),
        node_key=node_key,
        uid=SISTER_UID,
        gid=SISTER_UID,
    )
    return {"ZAI_API_KEY": key}


def render_lo() -> dict[str, str]:
    """The LO test agent's minimal configs: the gateway provider only. Written
    by hand (drop_pi_agent_dir is the SLOT's renderer — MCP and the node key
    would be wrong here); same 0444-not-needed shape as any pi agent dir."""
    model = os.environ.get("LO_MODEL", "openrouter/qwen/qwen3.7-flash")
    # A parallel stand's gateway has its own key table: HAPI_LO_GATEWAY_KEY
    # (issued for THIS gateway) wins over the entrypoint's auth.json key.
    env_key = os.environ.get("HAPI_LO_GATEWAY_KEY", "")
    if env_key:
        key = env_key
    else:
        auth = json.loads(
            (HOME / ".local/share/opencode/auth.json").read_text(encoding="utf-8")
        )
        key = auth["llm-gateway"]["key"]
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    for name, config in {
        "models.json": {
            "providers": {
                "gateway": {
                    "baseUrl": GATEWAY_URL,
                    "api": "openai-completions",
                    "apiKey": "$GATEWAY_KEY",
                    "models": [{"id": model}],
                }
            }
        },
        "settings.json": {"defaultProvider": "gateway", "defaultModel": model},
    }.items():
        target = AGENT_DIR / name
        if target.is_symlink() or target.exists():
            target.unlink()
        target.write_text(json.dumps(config, indent=2), encoding="utf-8")
    os.chmod(AGENT_DIR, 0o700)
    own_sister(AGENT_DIR)
    for name in ("models.json", "settings.json"):
        own_sister(AGENT_DIR / name)
    return {"GATEWAY_KEY": key}


def start() -> int:
    mode = os.environ.get("HAPI_AGENT_MODE", "slot")
    secrets = render_slot() if mode == "slot" else render_lo()
    secrets.update(
        {
            "HAPI_API_URL": os.environ.get("HAPI_API_URL", "http://hapi-hub:3006"),
            "CLI_API_TOKEN": os.environ["CLI_API_TOKEN"],
            "HAPI_HOME": str(HOME / ".hapi"),
        }
    )
    write_env(secrets)
    if SPIKE_DIR.exists():
        subprocess.run(["chown", "-R", f"{SISTER_UID}:{SISTER_UID}", str(SPIKE_DIR)], check=False)
    else:
        SPIKE_DIR.mkdir(parents=True)
        os.chmod(SPIKE_DIR, 0o700)
        own_sister(SPIKE_DIR)
    inner = f". {ENV_FILE} && cd {SPIKE_DIR} && exec hapi pi"
    log = open(LOG, "ab", buffering=0)
    log.write(f"\n== start {mode} {time.strftime('%H:%M:%S')}\n".encode())
    proc = subprocess.Popen(
        ["su", "-s", "/bin/bash", "-c", inner, SISTER],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    time.sleep(6)
    alive = proc.poll() is None
    print(f"pid={proc.pid} alive_after_6s={alive} mode={mode}")
    tail(LOG)
    return 0 if alive else 1


def tail(path: pathlib.Path, lines: int = 15) -> None:
    try:
        out = subprocess.run(
            ["tail", "-n", str(lines), str(path)], capture_output=True, text=True, check=False
        )
        print(out.stdout, end="")
    except OSError as exc:
        print(f"(no log: {exc})")


def read_pid() -> int:
    return int(PID_FILE.read_text(encoding="utf-8").strip())


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def status() -> int:
    try:
        pid = read_pid()
    except (OSError, ValueError):
        print("no pid file: not started")
        return 1
    print(f"pid={pid} alive={alive(pid)}")
    tail(LOG)
    return 0


def stop() -> int:
    pid = read_pid()
    if not alive(pid):
        print(f"pid={pid} already dead")
    else:
        # the pid is the session leader (start_new_session): su -> bash -> exec
        # hapi share the group, so killpg reaches them; the platform binary
        # re-detaches, so a pattern sweep follows (a bare kill only hits su and
        # the orphaned hapi keeps the hub session alive [проверено: T-044])
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            os.kill(pid, signal.SIGTERM)
        for _ in range(20):
            time.sleep(0.5)
            if not alive(pid):
                break
    subprocess.run(["pkill", "-TERM", "-f", "[h]api pi"], check=False)
    for _ in range(20):
        time.sleep(0.5)
        if subprocess.run(["pgrep", "-f", "[h]api pi"], capture_output=True).returncode != 0:
            print(f"pid={pid} stopped, no hapi pi left")
            tail(LOG)
            return 0
    subprocess.run(["pkill", "-KILL", "-f", "[h]api pi"], check=False)
    print(f"pid={pid} needed SIGKILL")
    return 1


def main() -> int:
    action = os.environ.get("HAPI_ACTION", "start")
    return {"start": start, "status": status, "stop": stop}[action]()


if __name__ == "__main__":
    sys.exit(main())
