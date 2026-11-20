"""Probe overlap on the live Luna route with local F5. Produces timings, never plays audio."""
import concurrent.futures
import json
import os
import re
import time

import httpx

from runtime.mind.mcp_body import check_say
from runtime.voice.speaker import MIN_PART, split_parts


def candidate(arguments):
    start = re.match(r'^\s*\{\s*"text"\s*:\s*"', arguments)
    if not start:
        return None
    # Only inspect a complete sentence followed by whitespace in the growing JSON string.
    boundary = re.search(r"[.!?…]\s", arguments[start.end():])
    if not boundary:
        return None
    prefix = arguments[start.end():start.end() + boundary.end() - 1]
    try:
        sentence = json.loads('"' + prefix + '"')
        return check_say(sentence) if len(sentence) >= MIN_PART else None
    except (ValueError, RuntimeError):
        return None


def main():
    schema = {"type": "function", "function": {"name": "say", "parameters": {
        "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}}
    headers = {"Authorization": "Bearer " + os.environ["LLM_API_KEY"]}
    gateway = os.environ["LLM_API_BASE"].rstrip("/") + "/chat/completions"
    with httpx.Client(timeout=45) as client, concurrent.futures.ThreadPoolExecutor(1) as pool:
        def local_audio(sentence):
            resp = client.post(os.environ["TTS_URL"].rstrip("/") + "/speak", json={"text": sentence})
            resp.raise_for_status()
            return time.monotonic(), len(resp.content)

        for question in ("Какого цвета снег?", "Опиши, пожалуйста, в двух предложениях дом с берёзовой крышей."):
            request = {"model": "opencode-go/gpt-6-luna", "messages": [
                {"role": "system", "content": "Ответь игроку по-русски через say, до 256 символов."},
                {"role": "user", "content": question}], "tools": [schema], "stream": True,
                "tool_choice": {"type": "function", "function": {"name": "say"}}}
            start = time.monotonic()
            args, preview, work, early = "", None, None, None
            with client.stream("POST", gateway, headers=headers, json=request) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                        continue
                    for choice in json.loads(line[5:]).get("choices", []):
                        for call in choice["delta"].get("tool_calls", []):
                            if call.get("index", 0) != 0:
                                continue
                            args += call.get("function", {}).get("arguments") or ""
                            if work is None and (preview := candidate(args)):
                                early = time.monotonic()
                                work = pool.submit(local_audio, preview)
            committed = time.monotonic()
            final = check_say(json.loads(args)["text"])
            first = split_parts(final)[0]
            if work:
                ready, size = work.result()
            matched = work is not None and first == preview
            if not matched:
                ready, size = local_audio(first)
            print(json.dumps({"question": question, "first": first, "matched": matched,
                              "response_s": round(committed-start, 3),
                              "available_lead_s": round(committed-early, 3) if early else None,
                              "confirmed_pcm_s": round(max(ready, committed)-start, 3),
                              "pcm_bytes": size}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
