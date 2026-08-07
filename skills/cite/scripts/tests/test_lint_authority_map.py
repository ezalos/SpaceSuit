# ABOUTME: Confirms the seeded skill-global authority map passes its own md/yaml sync lint.
import subprocess, sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "lint_authority_map.py"
FIXTURES = Path(__file__).parent / "fixtures"

def test_seeded_map_in_sync():
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

# --- ported from Markdowns2Teach scripts/cite/tests/test_lint_authority_map.py ---
# (drift-detection branch — neither error path was previously covered here)

def _run_lint(md_path, yaml_path):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--md", str(md_path), "--yaml", str(yaml_path)],
        capture_output=True, text=True,
    )
    return r.returncode, r.stdout, r.stderr

def test_yaml_extra_domain_fails(tmp_path):
    extra_yaml = tmp_path / "extra.yaml"
    extra_yaml.write_text(
        (FIXTURES / "sample_authority_map.yaml").read_text()
        + '      - name: "GhostPublisher"\n        domains: ["ghost.example"]\n'
    )
    rc, _, stderr = _run_lint(FIXTURES / "sample_authority_map.md", extra_yaml)
    assert rc == 1
    assert "ghost.example" in stderr

def test_md_missing_yaml_entry_fails(tmp_path):
    trimmed_md = tmp_path / "trimmed.md"
    trimmed_md.write_text("# Only\n\n## Tier 1 — Primary\n\n- **SEC** (`sec.gov`)\n")
    rc, _, stderr = _run_lint(trimmed_md, FIXTURES / "sample_authority_map.yaml")
    assert rc == 1
    # Bloomberg (tier 4) is in yaml but not in trimmed md
    assert "bloomberg.com" in stderr.lower() or "Bloomberg" in stderr
