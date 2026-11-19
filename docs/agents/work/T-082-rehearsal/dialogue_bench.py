"""A real Minecraft guest asks one question at a time and measures incoming SVC."""
import json
import queue
import subprocess
import threading
import time

QUESTIONS = [
    "Какой дом ты сейчас строишь?", "Как тебя называть?", "Как зовут меня?",
    "Из чего здесь пол?", "Какого цвета будет цветок?", "Куда поставить цветок?",
    "Кто оставил тебе завещание?", "Что видно рядом?", "Что у тебя в руке?",
    "Как называется башенка?", "Чем освещена башенка?", "Как выглядит вход?",
    "Из чего окна?", "Далеко ли отсюда маяк?", "Сколько этажей у дома?",
    "Ты помнишь нашу договорённость?", "Чем я могу помочь?", "Как продвигается стройка?",
    "Что осталось проверить?", "Что передашь следующей сестре?",
]


def main():
    events = queue.Queue()
    proc = subprocess.Popen(["docker", "logs", "--follow", "--since", "1s", "t082-guest-probe"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding="utf-8")

    def read():
        for line in proc.stdout:
            events.put(json.loads(line))

    threading.Thread(target=read, daemon=True).start()
    write = ("from pathlib import Path;import sys;p=Path('/tmp/guest-draft');"
             "p.write_text(sys.argv[1],encoding='utf-8');p.replace('/tmp/guest-line')")
    try:
        for question in QUESTIONS:
            subprocess.run(["docker", "exec", "t082-guest-probe", "python", "-c", write, question],
                           check=True, timeout=15)
            start = None
            answer = None
            sounds = []
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                try:
                    row = events.get(timeout=max(0.1, deadline - time.monotonic()))
                except queue.Empty:
                    break
                if row["kind"] == "asked" and row["text"] == question:
                    start = row["t"]
                if start is None or row["t"] < start:
                    continue
                if row["kind"] == "audio" and row["speaker"] == "LastOrder01":
                    sounds.append(row["t"])
                if row["kind"] == "chat" and row["text"].startswith("<LastOrder01>"):
                    answer = row
                if answer and any(abs(t - answer["t"]) < 2 for t in sounds):
                    break
            matched = [t for t in sounds if answer and abs(t - answer["t"]) < 2]
            print(json.dumps({"question": question, "asked_at": start, "reply": answer,
                              "first_received_s": round(min(matched) - start, 3) if matched else None},
                             ensure_ascii=False), flush=True)
            time.sleep(4)
    finally:
        proc.terminate()
        proc.wait()


if __name__ == "__main__":
    main()
