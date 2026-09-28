# ABOUTME: Opt-in live checks against the real services: one arXiv paper, one open-access DOI through Unpaywall.
# ABOUTME: Deselected by default; run with `CONTACT_EMAIL=... uv run pytest -m live`.
import os

import pytest

from scholar_fetch.chain import resolve
from scholar_fetch.http import Client

pytestmark = pytest.mark.live


def test_arxiv(tmp_path):
    f = resolve("https://arxiv.org/abs/1706.03762", Client(tmp_path))
    assert f.served_by == "arxiv" and "attention" in f.text.lower()


@pytest.mark.skipif(not os.environ.get("CONTACT_EMAIL"), reason="Unpaywall needs CONTACT_EMAIL")
def test_open_access_doi(tmp_path):
    f = resolve("https://doi.org/10.1371/journal.pone.0000308", Client(tmp_path))  # PLOS ONE, gold OA
    assert f.served_by in ("unpaywall", "semantic-scholar", "openalex"), f.tried
