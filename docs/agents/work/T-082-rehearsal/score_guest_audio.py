"""Measure speech on two timestamped guest tracks; match the transcript before scoring."""
import argparse
import audioop
import json
import subprocess
from pathlib import Path

import httpx


def read_track(path, track):
    return subprocess.check_output(["ffmpeg", "-loglevel", "error", "-i", str(path),
                                    "-map", f"0:a:{track}", "-ar", "48000", "-ac", "1",
                                    "-f", "s16le", "-"])


def intervals(pcm, threshold=90):
    frame = 1920
    voiced = [i for i in range(0, len(pcm), frame) if audioop.rms(pcm[i:i + frame], 2) >= threshold]
    groups = []
    for offset in voiced:
        if groups and offset - groups[-1][1] <= frame * 10:
            groups[-1][1] = offset + frame
        else:
            groups.append([offset, offset + frame])
    return [(round(lo / 96000, 3), round(hi / 96000, 3))
            for lo, hi in groups if hi - lo >= 4 * frame]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--transcribe", action="store_true")
    args = parser.parse_args()
    for path in sorted(args.directory.glob("*.mka")):
        mic, game = (read_track(path, i) for i in (0, 1))
        question, answer = intervals(mic), intervals(game)
        # ffmpeg decodes each stream from its own first packet.
        probe = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
            "stream=start_time", "-of", "json", str(path)])
        start = [float(s["start_time"]) for s in json.loads(probe)["streams"]]
        result = dict(name=path.stem, mic=question, game=answer)
        if question and answer:
            result["candidate_s"] = round(start[1] + answer[0][0] - start[0] - question[-1][1], 3)
        if args.transcribe and answer:
            lo, hi = max(0, answer[0][0] - .15), answer[-1][1] + .2
            reply = httpx.post("http://127.0.0.1:8790/transcribe",
                               content=game[2 * int(lo*48000):2 * int(hi*48000)], timeout=45)
            reply.raise_for_status()
            result["heard_on_guest"] = reply.json()["text"]
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
