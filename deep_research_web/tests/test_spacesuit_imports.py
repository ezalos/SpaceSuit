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


def test_scholar_fetch_is_the_real_package_not_the_project_dir():
    import scholar_fetch.chain
    assert Path(scholar_fetch.chain.__file__).parent.name == "scholar_fetch"
    assert Path(scholar_fetch.chain.__file__).parent.parent.name == "scholar_fetch"  # .../scholar_fetch/scholar_fetch/chain.py
