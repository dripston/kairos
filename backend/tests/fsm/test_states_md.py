"""STATES.md (repo root) is rendered from the declarations: every machine's table, diagram and
fingerprint must be in it verbatim. Re-render with `python scripts/render_states.py`."""
from __future__ import annotations

from pathlib import Path

import pytest

from kairos_backend.machines import catalog

STATES_MD = Path(__file__).resolve().parents[3] / "STATES.md"


@pytest.fixture(scope="module")
def text():
    assert STATES_MD.exists(), "STATES.md missing at the repo root: run backend/scripts/render_states.py"
    return STATES_MD.read_text(encoding="utf-8")


def test_top_section_explains_deterministic_ai(text):
    assert "## Deterministic AI in Kairos" in text
    for must in ("### Where an LLM is allowed", "### Where an LLM is never used", "### How this is enforced"):
        assert must in text


@pytest.mark.parametrize("name", list(catalog()))
def test_every_machine_is_rendered_from_its_declaration(text, name):
    m = catalog()[name]
    assert f"`{m.name}` (v{m.version})" in text
    assert m.to_table() in text, f"{name}: transition table drifted, re-render STATES.md"
    assert m.to_mermaid() in text, f"{name}: diagram drifted, re-render STATES.md"
    assert f"Fingerprint `{m.fingerprint}`" in text
