"""Run in the rehearsal guest's network namespace; JSONL records actual SVC PCM."""
import audioop
import json
import threading
import time
from pathlib import Path

from runtime.game.py4j_body import BodyCallback, _default_gateway_factory


def record(kind, **fields):
    print(json.dumps({"t": time.time(), "kind": kind, **fields}, ensure_ascii=False), flush=True)


class Guest(BodyCallback):
    def __init__(self):
        super().__init__(lambda *_: None)
        self.previous = 0
        self.lock = threading.Lock()

    def onChatMessage(self, s=""):
        record("chat", text=str(s))
        return ""

    def onVoiceFeed(self, s="", audio=None):
        pcm = bytes(audio or b"")
        if not pcm or audioop.rms(pcm, 2) < 90:
            return
        with self.lock:
            now = time.monotonic()
            if now - self.previous > 0.8:
                record("audio", speaker=str(s), rms=audioop.rms(pcm, 2))
            self.previous = now


if __name__ == "__main__":
    client = Guest()
    gateway = _default_gateway_factory(client)()
    try:
        gateway.entry_point.InitPythonCallback()
        record("ready")
        inbox = Path("/tmp/guest-line")
        while True:
            if inbox.exists():
                line = inbox.read_text(encoding="utf-8").strip()
                inbox.unlink()
                if line:
                    record("asked", text=line)
                    gateway.entry_point.ChatMessage(line)
            time.sleep(0.25)
    finally:
        gateway.close()
