# Video around you: footage, journals, tools

This is a map of what the environment gives you (T-064, ADR-045) — knowledge, not a goal.
What to film, when something is worth a clip, and how a cut is assembled is nobody's
instruction to you; this file only says what exists and how it behaves.

## The footage (`http://media:9996`)

A body with its camera on streams its screen to the `media` service continuously; MediaMTX
records every stream into a ring buffer (segments get deleted after a retention window —
default 24 h — so old footage does not wait for you). The service is internal to the
network: reachable from your container, from nowhere else that is yours.

- `GET http://media:9996/list?path=<name>` — JSON array of recordings of that stream:
  `{"start": <RFC3339>, "duration": <seconds>, "url": <a ready /get link>}`.
  Stream names are the slot accounts: `LastOrder01`, `LastOrder02`, `LastOrder03`.
- `GET http://media:9996/get?path=<name>&start=<RFC3339>&duration=<seconds>&format=mp4`
  — an mp4 excerpt of that stretch. Dates are RFC3339 (`2026-11-11T00:19:28Z`); the
  excerpt can start mid-recording. `format=fmp4` is the default and plays in browsers;
  `mp4` is the safe choice for ffmpeg input.
- The ring buffer means "recorded" is not "kept": a clip you need, you cut in time.

## The journals (`/net-logs/…`, read-only)

Speech, actions and timings come from the journals, not from listening to audio — the
words were written before they were ever spoken aloud (if at all).

- `/net-logs/hive/events.jsonl` — the network's events: births, deaths, wills, grants.
  One JSON per line; `ts` is an ISO-8601 timestamp.
- `/net-logs/mind-N/log.jsonl` — the tool log of slot N (every `say`, every C1 event).
  One JSON per line; `ts` is a unix epoch float — **a different clock than the hive
  journal**. Convert before matching a line to a recording's RFC3339 times.
- The speaker of a line is the slot; which node was alive in that slot comes from the
  birth records in the hive journal around that time.

These mounts are read-only; a write into them fails by design.

## Cutting clips

`ffmpeg` is in your container (the `lo` image variant; the sisters' sandboxes have no
video tools and no route to `media`). A writable place for your cuts is `/workplace/video`
(the rest of your workplace rules still apply). Example shape, not a recipe to follow:

    curl -s "http://media:9996/get?path=LastOrder01&start=2026-11-11T10:30:00Z&duration=45&format=mp4" -o /workplace/video/raw.mp4
    ffmpeg -i /workplace/video/raw.mp4 -ss 5 -t 10 -c:v libx264 /workplace/video/take.mp4

Traps seen on the stand: `/get` dates are UTC — check the `Z`; `duration` is in seconds,
not `HH:MM:SS`; a stream with its camera off has no recordings (the ring buffer only
holds what was streamed).
