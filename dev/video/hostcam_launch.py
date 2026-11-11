"""Launch the GPU spectator camera client directly (T-067 films, 2026-11-08).

HeadlessMC on Windows patches rendering out for an offline account (its Xvfb escape is Linux-only),
so the camera reuses the game files HeadlessMC downloaded and builds the Java command itself:
Fabric loader over vanilla 1.21.11, offline profile, quick play into the stand.

    python dev/video/hostcam_launch.py ROOT JAVA USER WIDTH HEIGHT SERVER
"""
import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path

root, java, user, width, height, server = sys.argv[1:7]
H = Path(root)
MC = H / ".minecraft"
VERS = MC / "versions"
LIB = MC / "libraries"


def load(vid: str) -> dict:
    return json.loads((VERS / vid / f"{vid}.json").read_text(encoding="utf-8"))


def allowed(rules) -> bool:
    if not rules:
        return True
    ok = False
    for r in rules:
        osr = r.get("os", {})
        if r.get("features"):
            continue
        match = (not osr.get("name") or osr["name"] == "windows") and (not osr.get("arch") or osr["arch"] == "x86")
        if match:
            ok = r["action"] == "allow"
    return ok


def lib_path(lib: dict) -> Path:
    art = lib.get("downloads", {}).get("artifact")
    if art and art.get("path"):
        return LIB / art["path"]
    g, a, v, *cls = lib["name"].split(":")
    tail = f"-{cls[0]}" if cls else ""
    return LIB / Path(*g.split(".")) / a / v / f"{a}-{v}{tail}.jar"


fab_id = next(p.name for p in VERS.iterdir() if p.name.startswith("fabric-loader-"))
fab = load(fab_id)
van = load(fab["inheritsFrom"])
cp, seen = [], set()
for lib in fab["libraries"] + van["libraries"]:
    if not allowed(lib.get("rules")):
        continue
    key = ":".join(lib["name"].split(":")[:2] + lib["name"].split(":")[3:])
    if key in seen:          # fabric's copy wins over vanilla's (asm etc.)
        continue
    seen.add(key)
    p = lib_path(lib)
    if p.exists():
        cp.append(str(p))
    else:
        print(f"missing library: {p}", file=sys.stderr)
client = VERS / van["id"] / f"{van['id']}.jar"
cp.append(str(client if client.exists() else VERS / fab_id / f"{fab_id}.jar"))   # HeadlessMC keeps it here

uid = uuid.UUID(bytes=hashlib.md5(f"OfflinePlayer:{user}".encode()).digest(), version=3)
game_dir = H / "game"
natives = H / "natives"
natives.mkdir(exist_ok=True)
subst = {
    "auth_player_name": user, "version_name": fab_id, "game_directory": str(game_dir),
    "assets_root": str(MC / "assets"), "assets_index_name": van["assetIndex"]["id"],
    "auth_uuid": str(uid), "auth_access_token": "0", "clientid": "0", "auth_xuid": "0",
    "user_type": "legacy", "version_type": "release", "natives_directory": str(natives),
    "launcher_name": "lo-hostcam", "launcher_version": "1", "classpath": ";".join(cp),
    "classpath_separator": ";", "library_directory": str(LIB),
}


def expand(args) -> list[str]:
    out = []
    for a in args:
        if isinstance(a, dict):
            if not allowed(a.get("rules")) or any(r.get("features") for r in a.get("rules", [])):
                continue
            vals = a["value"] if isinstance(a["value"], list) else [a["value"]]
        else:
            vals = [a]
        for v in vals:
            for k, s in subst.items():
                v = v.replace("${%s}" % k, s)
            out.append(v)
    return out


jvm = ["-Xmx3G", "-Xms1G"] + expand(van["arguments"]["jvm"]) + expand(fab.get("arguments", {}).get("jvm", []))
game = expand(van["arguments"]["game"]) + expand(fab.get("arguments", {}).get("game", []))
game += ["--width", width, "--height", height, "--quickPlayMultiplayer", server]
cmd = [java] + jvm + [fab["mainClass"]] + game
log = open(H / "launch.log", "w", encoding="utf-8")
p = subprocess.Popen(cmd, cwd=game_dir, stdout=log, stderr=subprocess.STDOUT,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
print(f"camera pid {p.pid}, {len(cp)} jars, log {H / 'launch.log'}")
