# ABOUTME: Guards the import contract with SpaceSuit: only charter and verify may be used.
# ABOUTME: Anything else would couple this private engine to v1 internals that can change.
import re
from pathlib import Path

ALLOWED = {"deep_research.charter", "deep_research.verify"}
PACKAGE = Path(__file__).resolve().parents[1] / "deep_research_web"


def test_only_charter_and_verify_are_imported_from_spacesuit():
    seen = set()
    for py in PACKAGE.glob("*.py"):
        for m in re.finditer(r"^\s*(?:from|import)\s+(deep_research(?:\.\w+)*)", py.read_text(), re.M):
            seen.add(m.group(1))
    assert seen <= ALLOWED, f"forbidden SpaceSuit imports: {seen - ALLOWED}"
