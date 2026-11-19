"""Public-to-operator fields accepted from the authenticated stand collector."""

from __future__ import annotations


def read_snapshot(body: dict) -> dict:
    def object_field(obj: dict, key: str) -> dict:
        value = obj.get(key, {})
        if not isinstance(value, dict):
            raise ValueError(key)
        return value

    def flag(obj: dict, key: str) -> bool | None:
        value = obj.get(key)
        if value is not None and type(value) is not bool:
            raise ValueError(key)
        return value

    def enum(obj: dict, key: str, *choices: str) -> str | None:
        value = obj.get(key)
        if value is not None and value not in choices:
            raise ValueError(key)
        return value

    def label(obj: dict, key: str) -> str | None:
        value = obj.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 160):
            raise ValueError(key)
        return value

    def container(obj: dict, key: str) -> dict:
        service = object_field(obj, key)
        return {
            "running": flag(service, "running"),
            "health": enum(service, "health", "healthy", "starting", "unhealthy"),
        }

    slots = object_field(body, "slots")
    result_slots = {}
    for slot in ("1", "2", "3"):
        item = object_field(slots, slot)
        runner = object_field(item, "runner")
        serial = runner.get("serial")
        if serial is not None and (type(serial) is not int or serial <= 0):
            raise ValueError("serial")
        result_slots[slot] = {
            **{key: container(item, key) for key in ("mind", "node", "body")},
            "runner": {
                "serial": serial, "dead": flag(runner, "dead"), "term": flag(runner, "term"),
                "body": enum(runner, "body", "none", "attached"),
            },
        }
    voice = object_field(body, "voice")
    return {
        "docker": flag(body, "docker"),
        "lo": {**container(body, "lo"), "process": flag(object_field(body, "lo"), "process")},
        "executor": {**container(body, "executor"), "busy": flag(object_field(body, "executor"), "busy")},
        "voice": {
            **container(body, "voice"),
            **{key: flag(voice, key) for key in ("reachable", "tts_ready", "stt_ready")},
            **{key: label(voice, key) for key in ("backend", "model", "gpu", "gpu_uuid")},
        },
        "slots": result_slots,
    }
