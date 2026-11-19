import os
import socket
from types import SimpleNamespace
from runtime.node.workplace import render_pi_configs
from runtime.voice.speaker import Speaker

def test_pi_go_uses_virtual_key_and_preserves_mcp(monkeypatch):
    monkeypatch.setenv("PI_API_ROUTE", "gateway")
    c = render_pi_configs("GLM-5.3", "http://mind:8700", "http://hive:8000", "node-id")
    p = c["models.json"]["providers"]["opencode-go-gateway"]
    assert p["baseUrl"] == "http://llm-gateway:4000/v1"
    assert p["models"][0]["id"] == "opencode-go/" + __import__("json").loads(__import__("pathlib").Path("runtime/node/go_models.json").read_text())[0]["id"]
    assert "pi-gateway-key" in p["apiKey"]
    assert "zai" not in c["models.json"]["providers"]
    assert len(p["models"]) == 32
    assert c["settings.json"]["defaultProvider"] == "opencode-go-gateway"
    assert "body" in c["mcp.json"]["mcpServers"]

def test_stream_plays_before_generator_finishes():
    sent, chat, records = [], [], []
    class Sock:
        def settimeout(self, _): pass
        def sendall(self, b): sent.append(b)
        def shutdown(self, _): pass
        def recv(self, _): return b""
        def close(self): pass
    def chunks():
        yield b"\x00\x00" * 10
        assert sent and chat
        yield b"\x00\x00" * 10
    speaker = Speaker(synth=lambda _: None, connect=Sock, send_chat=chat.append,
                      journal=SimpleNamespace(record=lambda *a, **k: records.append(k)))
    part = SimpleNamespace(pcm=chunks(), first=True, text="hello", line=SimpleNamespace(id=1,text="hello",said_at=0,state="waiting"))
    speaker._stream_part(part)
    assert len(sent) == 2 and len(chat) == 1
    assert records[-1]["ok"]

def test_stream_failure_after_audio_does_not_repeat_chat():
    chat, records = [], []
    class Sock:
        def settimeout(self, _): pass
        def sendall(self, b): pass
        def close(self): pass
    def chunks():
        yield b"\x00\x00"
        raise RuntimeError("decoder failed")
    speaker=Speaker(synth=lambda _: None, connect=Sock, send_chat=chat.append,
                    journal=SimpleNamespace(record=lambda *a, **k: records.append(k)))
    part=SimpleNamespace(pcm=chunks(),first=True,text="hello",line=SimpleNamespace(id=1,text="hello",said_at=0,state="waiting"))
    speaker._stream_part(part)
    assert chat == ["hello"]
    assert records[-1]["ok"] is False


def test_stream_http_pcm_and_empty_text():
    from fastapi.testclient import TestClient
    from runtime.tts.server import create_app
    class Engine:
        name = "test"
        def stream(self, text):
            yield b"\x01\x00" * 4
            yield b"\x02\x00" * 4
    with TestClient(create_app(Engine())) as c:
        assert c.get("/health").json()["stt_ready"] is False
        r = c.post("/speak/stream", json={"text": "hello"})
        assert r.content == b"\x01\x00" * 4 + b"\x02\x00" * 4
        assert r.headers["x-audio-rate"] == "48000"
        assert c.post("/speak/stream", json={"text": ""}).status_code == 422
        assert c.post("/speak/stream", json={"text": "   "}).status_code == 204


def test_stream_http_error_before_audio_releases_lock():
    from fastapi.testclient import TestClient
    from runtime.tts.server import create_app
    class Engine:
        name = "test"
        calls = 0
        def stream(self, text):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("failed")
            yield b"\x00\x00"
    with TestClient(create_app(Engine())) as c:
        assert c.post("/speak/stream", json={"text": "hello"}).status_code == 502
        assert c.post("/speak/stream", json={"text": "again"}).content == b"\x00\x00"


def test_stream_response_advances_on_demand_and_closes_source():
    import asyncio
    from runtime.tts.server import create_app, SpeakIn
    events = []
    class Engine:
        name = "test"
        def stream(self, text):
            try:
                events.append("first")
                yield b"\x00\x00"
                events.append("second")
                yield b"\x01\x00"
            finally:
                events.append("closed")
    async def run():
        app = create_app(Engine())
        async with app.router.lifespan_context(app):
            route = next(r for r in app.routes if r.path == "/speak/stream")
            response = await route.endpoint(SpeakIn(text="hello"))
            assert events == ["first"]
            assert await anext(response.body_iterator) == b"\x00\x00"
            assert events == ["first"]
            await response.body_iterator.aclose()
            assert events == ["first", "closed"]
            # A disconnected response must not leave the inference lock held.
            response = await asyncio.wait_for(route.endpoint(SpeakIn(text="again")), 2)
            await anext(response.body_iterator)
            await response.body_iterator.aclose()
    asyncio.run(run())


def test_stream_timeout_before_audio_falls_back_once():
    import httpx
    chat, records = [], []
    def chunks():
        raise httpx.ReadTimeout("tts stalled")
        yield b""
    speaker = Speaker(synth=lambda _: None, connect=lambda: None, send_chat=chat.append,
                      journal=SimpleNamespace(record=lambda *a, **k: records.append(k)))
    part = SimpleNamespace(pcm=chunks(), first=True, text="hello", line=SimpleNamespace(id=1,text="hello",said_at=0,state="waiting"))
    speaker._stream_part(part)
    assert chat == ["hello"] and records[-1]["ok"] is False


def test_new_line_replaces_stream_still_waiting_for_first_audio():
    import threading
    import time
    entered, release, played = threading.Event(), threading.Event(), threading.Event()
    chat, sent = [], []
    class Sock:
        def settimeout(self, _): pass
        def sendall(self, b): sent.append(b); played.set()
        def shutdown(self, _): pass
        def recv(self, _): return b""
        def close(self): pass
    def synth(text):
        def chunks():
            if text == "old":
                entered.set()
                assert release.wait(2)
            yield text.encode()
        return chunks(), ""
    speaker = Speaker(synth=synth, connect=Sock, send_chat=chat.append,
                      journal=SimpleNamespace(record=lambda *a, **k: None)).start()
    try:
        speaker.say("old")
        assert entered.wait(2)
        assert speaker.say("new") == 1
        release.set()
        assert played.wait(2)
        deadline=time.monotonic()+2
        while len(chat)<2 and time.monotonic()<deadline:time.sleep(.01)
        assert chat == ["old", "new"]
        assert sent == [b"new"]
    finally:
        release.set()
        speaker.close()


def test_server_error_after_first_chunk_closes_and_releases_lock():
    import asyncio
    import pytest
    from runtime.tts.server import create_app, SpeakIn
    closed = []
    class Engine:
        name = "test"
        def stream(self, text):
            try:
                yield b"\x00\x00"
                raise RuntimeError("late failure")
            finally:
                closed.append(text)
    async def run():
        app = create_app(Engine())
        async with app.router.lifespan_context(app):
            route = next(r for r in app.routes if r.path == "/speak/stream")
            response = await route.endpoint(SpeakIn(text="one"))
            await anext(response.body_iterator)
            with pytest.raises(RuntimeError, match="late failure"):
                await anext(response.body_iterator)
            assert closed == ["one"]
            response = await asyncio.wait_for(route.endpoint(SpeakIn(text="two")), 2)
            await anext(response.body_iterator)
            await response.body_iterator.aclose()
            assert closed == ["one", "two"]
    asyncio.run(run())
