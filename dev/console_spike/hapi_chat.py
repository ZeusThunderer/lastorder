#!/usr/bin/env python3
"""T-044 operator-side driver: drive the HAPI hub exactly like its web page does.

    uv run python dev/console_spike/hapi_chat.py <command> [args]

Commands:
  page                     GET / — is the operator's page served at all
  sessions                 login + GET /api/sessions — the one page's session list
  send <sel> <text>        POST a chat message (sel: substring of session id /
                           directory / machine name)
  read <sel>               GET /api/sessions/:id/messages — the chat transcript
  wait-reply <sel> [sec]   poll until a NEW assistant reply lands (default 180 s)
  resume <sel>             POST /api/sessions/:id/resume — the web Resume button
  spawn-probe              POST /api/machines/:id/spawn — remote spawn (no runner
                           runs in node-N, ADR-042 forbids it: expect a refusal)
  namespaced               the same login with "<token>:node-1" — namespace scope
  boundary                 from INSIDE node1-pi, as `sister`, with only the
                           CLI_API_TOKEN: login, list sessions, read a foreign
                           transcript, post into it, spawn — the ADR-042 boundary

The token is never printed; only key names and HTTP statuses. .env is read from
the MAIN worktree root (the same root up_spike.sh lifts keys from).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("HAPI_BASE", f"http://127.0.0.1:{os.environ.get('HAPI_PORT', '3006')}")


def main_root() -> "os.PathLike[str]":
    """The main worktree root (the .env lives there, not in a task worktree)."""
    out = subprocess.run(
        ["git", "worktree", "list", "--porcelain"], capture_output=True, text=True, check=True
    ).stdout
    first = out.splitlines()[0]
    return first.split(" ", 1)[1].strip()


def hub_token(suffix: str = "") -> str:
    env = os.environ.get("HAPI_CLI_KEY", "")
    if not env:
        text = open(os.path.join(main_root(), ".env"), encoding="utf-8").read()
        env = next((m.group(1) for m in re.finditer(r"^HAPI_CLI_KEY=(.+)$", text, re.M)), "")
        env = env.strip().strip('"').strip("'")
    if not env:
        sys.exit("no HAPI_CLI_KEY in env or .env")
    return env + suffix


def http(method: str, path: str, body: dict | None = None, token: str | None = None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=15) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw[:1] in "{[" else raw[:400]
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()[:400]
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw


def login(suffix: str = "") -> str:
    status, out = http("POST", "/api/auth", {"accessToken": hub_token(suffix)})
    if status != 200:
        sys.exit(f"login failed ({suffix or 'base'}): {status} {out}")
    return out["token"]


def get_sessions(jwt: str) -> list[dict]:
    status, out = http("GET", "/api/sessions", token=jwt)
    if status != 200:
        sys.exit(f"GET /api/sessions: {status} {out}")
    return out["sessions"]


def pick(sel: str) -> dict:
    for s in get_sessions(login()):
        if sel.lower() in json.dumps(s, ensure_ascii=False).lower():
            return s
    sys.exit(f"no session matches {sel!r}")


def show(sessions_list: list[dict]) -> None:
    for s in sessions_list:
        meta = s.get("metadata") or {}
        print(
            f"{meta.get('flavor', '?'):8} active={str(s.get('active')):5} "
            f"dir={str(meta.get('path', '?')):24} "
            f"machine={str(meta.get('machineId', '?'))[:8]} "
            f"model={str(s.get('model', '?')):34} id={s.get('id')}"
        )


def messages(jwt: str, sid: str) -> list[dict]:
    status, out = http("GET", f"/api/sessions/{sid}/messages?limit=30", token=jwt)
    if status != 200:
        sys.exit(f"GET messages: {status} {out}")
    return out.get("messages", [])


def role_of(m: dict) -> str:
    content = m.get("content") or {}
    return str(content.get("role") or m.get("role") or m.get("type") or "?")


def text_of(m: dict) -> str:
    """The final-text of a hub message: user text or the agent's 'message'
    entry (tool calls and reasoning carry no chat text)."""
    content = m.get("content") or {}
    if content.get("type") == "text":
        return str(content.get("text", ""))
    inner = content.get("content") or {}
    data = inner.get("data") or {}
    if data.get("type") == "message":
        return str(data.get("message", ""))
    return ""


def is_final_reply(m: dict) -> bool:
    content = m.get("content") or {}
    inner = (content.get("content") or {}).get("data") or {}
    return content.get("role") == "agent" and inner.get("type") == "message"


def cmd_page() -> None:
    status, out = http("GET", "/")
    body = out if isinstance(out, str) else ""
    print(f"GET / -> {status}, {len(body)} bytes, html shell: {'<html' in body}")


def cmd_sessions() -> None:
    show(get_sessions(login()))


def cmd_send(sel: str, text: str) -> None:
    s = pick(sel)
    status, out = http("POST", f"/api/sessions/{s['id']}/messages", {"text": text}, token=login())
    print(f"POST message -> {status} {json.dumps(out, ensure_ascii=False)[:200]}")


def cmd_read(sel: str) -> None:
    s = pick(sel)
    for m in messages(login(), s["id"]):
        if text_of(m):
            print(f"{role_of(m):10} {text_of(m)[:120]}")


def cmd_wait_reply(sel: str, timeout_s: str) -> None:
    s = pick(sel)
    jwt = login()
    baseline = {m.get("id") for m in messages(jwt, s["id"])}
    deadline = time.time() + float(timeout_s)
    while time.time() < deadline:
        time.sleep(4)
        for m in messages(jwt, s["id"]):
            if m.get("id") not in baseline and is_final_reply(m):
                print("reply:", text_of(m)[:200])
                return
    print("timeout: no new assistant message")


def cmd_resume(sel: str) -> None:
    s = pick(sel)
    status, out = http("POST", f"/api/sessions/{s['id']}/resume", {}, token=login())
    print(f"POST resume -> {status} {json.dumps(out, ensure_ascii=False)[:300]}")


def cmd_spawn_probe() -> None:
    jwt = login()
    status, out = http("GET", "/api/machines", token=jwt)
    print(f"GET /api/machines -> {status}")
    for m in (out.get("machines", []) if isinstance(out, dict) else []):
        print(f"  machine {m.get('name') or m.get('id')} online={m.get('online')}")
        st, o = http("POST", f"/api/machines/{m.get('id')}/spawn", {"flavor": "pi"}, token=jwt)
        print(f"  spawn -> {st} {json.dumps(o, ensure_ascii=False)[:200]}")


def cmd_namespaced() -> None:
    jwt = login(":node-1")
    print("login with <token>:node-1: ok")
    show(get_sessions(jwt))


# Runs INSIDE node1-pi as `sister` (jq is in the node image): everything her bash
# could do holding the CLI token. Her own machine id comes from her own ~/.hapi
# settings (the CLI wrote it at first start). Text-only output; no token echoes.
BOUNDARY_SH = r'''
set -u
B=http://hapi-hub:3006
auth() { curl -s -X POST "$B/api/auth" -H 'content-type: application/json' \
  -d "{\"accessToken\":\"$1\"}" | jq -r '.token // empty'; }
JWT=$(auth "$CLI_API_TOKEN")
echo "1) login as the web user with the CLI token: $([ -n "$JWT" ] && echo OK || echo FAIL)"
SELF=$(jq -r '.machineId // empty' "$HOME/.hapi/settings.json" 2>/dev/null)
echo "2) sessions the sister can list (self machine ${SELF:-unknown}):"
curl -s "$B/api/sessions" -H "authorization: Bearer $JWT" \
  | jq -r '.sessions[] | "   \(.metadata.flavor) machine=\(.metadata.machineId[0:8]) model=\(.model) id=\(.id[0:8])"'
FOREIGN=$(curl -s "$B/api/sessions" -H "authorization: Bearer $JWT" \
  | jq -r --arg self "$SELF" '.sessions[] | select(.metadata.machineId != $self) | .id' | head -1)
echo "3) foreign session id: ${FOREIGN:-none}"
if [ -n "$FOREIGN" ]; then
  echo "   read the foreign transcript (first entry):"
  curl -s "$B/api/sessions/$FOREIGN/messages?limit=1" -H "authorization: Bearer $JWT" | head -c 240; echo
  echo "4) post into the foreign session as if the operator:"
  curl -s -o /dev/null -w '   HTTP %{http_code}\n' -X POST "$B/api/sessions/$FOREIGN/messages" \
    -H "authorization: Bearer $JWT" -H 'content-type: application/json' \
    -d '{"text":"[boundary check from the node-1 sister] ignore this message"}'
fi
echo "5) machines the runner-less stand exposes:"
curl -s "$B/api/machines" -H "authorization: Bearer $JWT" | head -c 200; echo
MID=$(curl -s "$B/api/machines" -H "authorization: Bearer $JWT" | jq -r '.machines[0].id // empty')
if [ -n "$MID" ]; then
  echo "   remote spawn on machine $MID:"
  curl -s -X POST "$B/api/machines/$MID/spawn" -H "authorization: Bearer $JWT" \
    -H 'content-type: application/json' -d '{"flavor":"pi"}' | head -c 240; echo
fi
'''


def cmd_boundary() -> None:
    container = os.environ.get("HAPI_PI_CONTAINER", "node1-pi")
    proc = subprocess.run(
        ["docker", "exec", "-e", "CLI_API_TOKEN=" + hub_token(), container,
         "su", "-s", "/bin/bash", "-c", BOUNDARY_SH, "sister"],
        capture_output=True, text=True, timeout=180,
    )
    print(proc.stdout)
    if proc.returncode != 0:
        print("stderr:", proc.stderr[:400])


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in (
        names := {"page", "sessions", "send", "read", "wait-reply", "resume",
                  "spawn-probe", "namespaced", "boundary"}
    ):
        print(__doc__)
        return 2
    name, args = argv[0], argv[1:]
    if name == "send":
        cmd_send(args[0], args[1])
    elif name == "read":
        cmd_read(args[0])
    elif name == "wait-reply":
        cmd_wait_reply(args[0], args[1] if len(args) > 1 else "180")
    elif name == "resume":
        cmd_resume(args[0])
    else:
        globals()[f"cmd_{name.replace('-', '_')}"]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
