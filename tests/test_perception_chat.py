"""Chat hygiene (ADR-020): uc service lines are dropped before any prompt, in both modes.

Samples are the E1 lines from the task and docs/agents/unionclef-bridge.md.
"""

import pytest

from runtime.perception.chat import clean_chat, is_service_line, strip_formatting
from runtime.perception.translate import Perception

UC_SERVICE = [
    "§2§l§o[Alto Clef] §rPICKUPDEC gettingPickaxeFirst=true",
    "§2§l§o[Tungsten] §rWalker: BFS 20 wp",
    "§2§l§oWARNING:§rTungsten: no route to BlockPos{x=12, y=64, z=-5}",
    "  [Alto Clef] Task started",
]
KEEP = [
    "<LastOrder01> probe: body online",
    "LastOrder01 was killed",
]


def test_strip_formatting_removes_color_codes_only():
    assert strip_formatting("§2§l§o[Alto Clef] §rPICKUPDEC") == "[Alto Clef] PICKUPDEC"
    assert strip_formatting("<LastOrder01> probe: body online") == "<LastOrder01> probe: body online"


@pytest.mark.parametrize("line", UC_SERVICE)
def test_uc_samples_are_service_lines(line):
    assert is_service_line(line)


@pytest.mark.parametrize(
    "line",
    [
        *KEEP,
        "somebody said [Alto Clef] mid-sentence",  # speech quoting the marker: not a service line
        "",
    ],
)
def test_speech_and_vanilla_lines_are_not_service(line):
    assert not is_service_line(line)


def test_clean_chat_drops_service_keeps_speech_and_vanilla():
    assert clean_chat(UC_SERVICE + KEEP) == KEEP
    assert clean_chat(["§r<LastOrder01> colored words"]) == ["<LastOrder01> colored words"]
    assert clean_chat([]) == []


@pytest.mark.parametrize("mode", ["plain", "masked"])
def test_no_service_line_survives_clean_chat_in_either_mode(mode):
    p = Perception({}, salt="s", mode=mode)
    assert clean_chat(UC_SERVICE) == []
    kept = clean_chat(UC_SERVICE + KEEP)
    assert kept == KEEP
    assert p.leaks("\n".join(kept)) == []


def test_body_flags_service_lines_with_the_shared_implementation():
    from runtime.game import is_service_line as body_is_service_line

    assert body_is_service_line is is_service_line
