"""T-041 spike driver: one Pi RPC session inside the node container.

Run as root in the container, script fed over stdin (no repo mount needed):

  docker exec -i -e PI_PROMPT='...' node1-pi /app/.venv/bin/python - < dev/console_spike/pi_rpc.py

It renders the Pi agent dir (drop_pi_agent_dir) from the container's own secret
files, then drives `pi --mode rpc --no-session` as the sister uid: a free get_state
first (model + provider), then the PI_PROMPT turn with optional PI_STEER_AFTER_S /
PI_FOLLOWUP_AFTER_S mid-run injections, printing every RPC line briefly (<< is Pi).
The Z.ai key travels via a 0600 env file in the sister's home, never argv; stdout
below must stay key-free (the report greps it for the key prefix).
"""
import json
import os
import pathlib
import selectors
import subprocess
import sys
import time

AGENT_DIR = pathlib.Path("/home/sister/.pi/agent")
SPIKE_DIR = pathlib.Path("/tmp/pi-spike")


def render() -> None:
    from runtime.node.workplace import drop_pi_agent_dir

    key = pathlib.Path("/run/node/secrets/zai_api_key").read_text(encoding="utf-8").strip()
    node_key = pathlib.Path("/run/node/secrets/node_mcp_key").read_text(encoding="utf-8").strip()
    drop_pi_agent_dir(
        AGENT_DIR,
        model=os.environ.get("PI_MODEL", "GLM-5.3-Flash"),
        body_base=os.environ.get("BODY_BASE", "http://body-1:8700"),
        hive_url=os.environ.get("HIVE_URL", "http://hive:8000"),
        node_key=node_key,
        uid=1001,
        gid=1001,
    )
    env_file = AGENT_DIR.parent / "spike-env"
    if env_file.is_symlink() or env_file.exists():
        env_file.unlink()  # root re-creates it every run: no stale or planted copy
    env_file.write_text(f"export ZAI_API_KEY={key}\n", encoding="utf-8")
    os.chmod(env_file, 0o600)
    os.chown(env_file, 1001, 1001)
    SPIKE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SPIKE_DIR, 0o700)
    os.chown(SPIKE_DIR, 1001, 1001)


def brief(line: str) -> None:
    try:
        obj = json.loads(line)
    except ValueError:
        print("<<", line[:160], flush=True)
        return
    kind = obj.get("type", "?")
    if kind == "message_update":  # streaming deltas: noise for the report
        return
    out = {"type": kind}
    if kind == "response":
        out.update(
            command=obj.get("command"),
            success=obj.get("success"),
            data=str(obj.get("data"))[:220],
            error=str(obj.get("error"))[:220],
        )
    else:
        out["line"] = json.dumps(obj, ensure_ascii=False)[:260]
    print("<<", json.dumps(out, ensure_ascii=False), flush=True)


def main() -> int:
    render()
    prompt = os.environ.get("PI_PROMPT", "")
    timeout = float(os.environ.get("PI_TIMEOUT_S", "240"))
    steer_after = float(os.environ.get("PI_STEER_AFTER_S", "0") or 0)
    follow_after = float(os.environ.get("PI_FOLLOWUP_AFTER_S", "0") or 0)
    inner = (
        f". {AGENT_DIR.parent}/spike-env && cd {SPIKE_DIR} "
        "&& exec pi --mode rpc --no-session"
    )
    proc = subprocess.Popen(
        ["su", "-s", "/bin/bash", "-c", inner, "sister"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )

    def send(obj: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        print(">>", json.dumps(obj, ensure_ascii=False)[:200], flush=True)

    send({"id": "state-1", "type": "get_state"})
    if os.environ.get("PI_THOUGHT"):
        # extension commands execute immediately, no LLM turn: insert the agent's own
        # line, then the real prompt checks whether the model sees it
        send({"id": "thought-1", "type": "prompt", "message": "/thought " + os.environ["PI_THOUGHT"]})
        time.sleep(1.5)
    if prompt:
        send({"id": "turn-1", "type": "prompt", "message": prompt})
    run_started = None
    sent_steer = sent_follow = False
    deadline = time.time() + timeout
    sel = selectors.DefaultSelector()
    assert proc.stdout is not None
    sel.register(proc.stdout, selectors.EVENT_READ)
    try:
        while time.time() < deadline:
            for key, _ in sel.select(timeout=0.5):
                line = key.fileobj.readline()
                if not line:
                    print("<< (pi exited)", flush=True)
                    return 1
                brief(line.rstrip("\n"))
                payload = json.loads(line) if line.startswith("{") else {}
                kind = payload.get("type", "")
                if kind == "agent_start":
                    run_started = run_started or time.time()
                if kind == "agent_settled" and prompt:
                    print("== settled", flush=True)
                    time.sleep(0.5)
                    return 0
            if run_started:
                if steer_after and not sent_steer and time.time() - run_started >= steer_after:
                    send({"id": "steer-1", "type": "steer", "message": os.environ.get("PI_STEER_MSG", "Держись, это проверка руля.")})
                    sent_steer = True
                if follow_after and not sent_follow and time.time() - run_started >= follow_after:
                    send({"id": "follow-1", "type": "follow_up", "message": os.environ.get("PI_FOLLOWUP_MSG", "Закончи одной строкой: руль дошёл.")})
                    sent_follow = True
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    print("== timeout reached", flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
