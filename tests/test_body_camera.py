"""T-063: the body camera and the media service — off by default, media internal-only.

The camera is a per-slot switch (BODY_CAMERA_N) on top of shared stream parameters;
the default stand must behave exactly as before (T-063 acceptance: BODY_CAMERA unset →
the body starts headless, media is optional). The media box records and serves clips
on the internal media-net only: node-N sandboxes and (until T-064) last-order have no
route to it, and nothing is published to the host.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker" / "compose.yml").read_text(encoding="utf-8"))
MEDIA_YML = yaml.safe_load((ROOT / "docker" / "media" / "mediamtx.yml").read_text(encoding="utf-8"))


def test_camera_is_a_per_slot_switch_off_by_default():
    for n in (1, 2, 3):
        env = COMPOSE["services"][f"body-{n}"]["environment"]
        assert env["BODY_CAMERA"] == f"${{BODY_CAMERA_{n}:-}}", f"body-{n}"
    shared = COMPOSE["services"]["body-1"]["environment"]
    assert shared["BODY_CAMERA_FPS"] == "${BODY_CAMERA_FPS:-10}"
    assert shared["BODY_CAMERA_SIZE"] == "${BODY_CAMERA_SIZE:-854x480}"
    assert shared["BODY_CAMERA_BITRATE"] == "${BODY_CAMERA_BITRATE:-1M}"


def test_media_service_is_pinned_and_internal():
    svc = COMPOSE["services"]["media"]
    assert svc["image"] == "bluenviron/mediamtx:1.21.3"   # pinned, never latest
    assert svc["restart"] == "on-failure"
    assert svc["networks"] == ["media-net"]
    assert "ports" not in svc   # nothing reaches media from the host
    assert COMPOSE["networks"]["media-net"]["internal"] is True
    assert "footage" in COMPOSE["volumes"]


def test_bodies_reach_media_nodes_do_not():
    for n in (1, 2, 3):
        assert "media-net" in COMPOSE["services"][f"body-{n}"]["networks"], f"body-{n}"
        assert "media-net" not in COMPOSE["services"][f"node-{n}"]["networks"], f"node-{n}"
    # a sister sandbox (and until T-064 the desk too) has no route to the footage
    assert "media-net" not in COMPOSE["services"]["last-order"]["networks"]


def test_retention_is_env_driven_with_a_24h_default():
    env = COMPOSE["services"]["media"]["environment"]
    assert env["MTX_PATHDEFAULTS_RECORDDELETEAFTER"] == "${MEDIA_RETENTION:-1d}"


def test_mediamtx_records_every_stream_and_serves_playback():
    # record* live under pathDefaults in v1.21.x (checked against the shipped default config)
    assert MEDIA_YML["pathDefaults"]["record"] is True
    assert MEDIA_YML["pathDefaults"]["recordFormat"] == "fmp4"
    assert MEDIA_YML["pathDefaults"]["recordDeleteAfter"] == "1d"
    assert MEDIA_YML["playback"] is True and MEDIA_YML["playbackAddress"] == ":9996"
    assert MEDIA_YML["rtsp"] is True and MEDIA_YML["rtspAddress"] == ":8554"
    for off in ("rtmp", "hls", "webrtc", "srt", "api", "metrics", "pprof"):
        assert MEDIA_YML[off] is False, off


def test_entrypoint_streams_the_fixed_display_to_media():
    text = (ROOT / "docker" / "body" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "rtsp://media:8554/${MC_USERNAME}" in text
    assert "BODY_CAMERA" in text and "Xvfb :99" in text
    # the stream survives a media restart: ffmpeg rides a restart loop
    assert "while true" in text and "sleep 3" in text
