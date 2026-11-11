"""T-061: `skin/` in the sister template is the operator's source material for the
server-side skin mod, not a sister's file — a born workplace must not carry it.
"""

from __future__ import annotations

from pathlib import Path

from runtime.lastorder.traits import Traits
from runtime.node.workplace import create_workplace


class _FixedPersona:
    def render_core(self, node) -> str:
        return f"# the rendered core of node {node.serial}"


def test_create_workplace_leaves_the_template_skin_dir_out(tmp_path: Path) -> None:
    template = tmp_path / "template"
    (template / "skin").mkdir(parents=True)
    (template / "skin" / "sister.png").write_bytes(b"\x89PNG-not-a-real-skin")
    (template / "PERSONA.md").write_text("core placeholder", encoding="utf-8")
    (template / "MEMORY.md").write_text("state", encoding="utf-8")

    root = create_workplace(
        workplaces=tmp_path / "workplaces",
        template_dir=template,
        shared_dir=tmp_path / "no-shared-dir",
        serial=7,
        traits=Traits(1, 2, 3, 4, 5, "collects buttons", seed=42),
        persona=_FixedPersona(),
        body_base="http://body-1:8700",
        hive_url="http://hive:8000",
        node_key="sk-test",
        gateway_url="http://llm-gateway:4000",
        opencode_model="openrouter/test",
    )
    assert not (root / "skin").exists()  # the operator's material stays out
    assert (root / "PERSONA.md").exists() and (root / "MEMORY.md").exists()
