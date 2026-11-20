"""Send marked prerecorded questions through a real guest mic and capture both Pulse tracks."""
import argparse
import json
import subprocess
import time
from pathlib import Path

GUEST = "t082-voice-guest"


def run(*args, **kw):
    return subprocess.run(args, check=True, **kw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("label")
    parser.add_argument("questions", type=int, nargs="+")
    parser.add_argument("--seconds", type=int, default=22)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    env = ["docker", "exec", "-e", "PULSE_RUNTIME_PATH=/tmp/pulse", GUEST]
    for index in args.questions:
        name = f"{args.label}-q{index}"
        # Both monitor streams are timestamped by the same Pulse server.
        recorder = subprocess.Popen([*env, "ffmpeg", "-nostdin", "-loglevel", "error",
            "-y", "-copyts", "-start_at_zero", "-f", "pulse", "-i", "lo_mic",
            "-f", "pulse", "-i", "lo_out.monitor", "-map", "0:a", "-map", "1:a",
            "-t", str(args.seconds), "-ac", "1", "-ar", "16000", "-c:a", "flac",
            "/tmp/" + name + ".mka"])
        try:
            time.sleep(2)
            started = time.time()
            run(*env, "paplay", "--latency-msec=60", "--device=lo_voice",
                f"/tmp/questions/q{index}.wav", timeout=15)
            end = time.time()
            if recorder.wait(timeout=args.seconds + 12):
                raise RuntimeError("guest capture failed")
        finally:
            if recorder.poll() is None:
                recorder.terminate()
                recorder.wait()
        run("docker", "cp", f"{GUEST}:/tmp/{name}.mka", str(args.directory / (name + ".mka")))
        row = {"name": name, "play_started": started, "play_drained": end}
        with (args.directory / "trials.jsonl").open("a", encoding="utf8") as f:
            print(json.dumps(row), file=f)
        print(row, flush=True)


if __name__ == "__main__":
    main()
