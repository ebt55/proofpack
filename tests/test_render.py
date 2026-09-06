"""The report the reviewer actually reads: it renders, and nothing is silently dropped.

Offline: renders a committed package's report.json in a tmp dir. No browser, no
network, no API key.
"""

import html as html_mod
import json
import shutil

import pytest

from preapproval.config import REPO_ROOT
from preapproval.models import Status
from preapproval.report import render_package, status_counts

PACKAGE = REPO_ROOT / "output" / "01-community-class-gallopnyc"


@pytest.fixture()
def rendered(tmp_path):
    """report.html rendered from the committed package, in a throwaway directory."""
    source = PACKAGE / "report.json"
    if not source.exists():
        pytest.skip(f"committed package {PACKAGE.name} is not present")
    shutil.copy(source, tmp_path / "report.json")
    out = render_package(tmp_path)
    assert out == tmp_path / "report.html"
    return out.read_text(encoding="utf-8")


@pytest.fixture()
def data():
    source = PACKAGE / "report.json"
    if not source.exists():
        pytest.skip(f"committed package {PACKAGE.name} is not present")
    return json.loads(source.read_text(encoding="utf-8"))


def test_render_package_keeps_every_piece_of_the_record(rendered, data):
    # Compare against unescaped text: apostrophes and quotes in model-written
    # notes are HTML-escaped on the way out, which is the point.
    text = html_mod.unescape(rendered)

    assert data["application"]["participant_name"] in text
    assert data["application"]["requested_item"] in text
    assert data["rate_comparison"]["verdict"] in text

    for capture in data["evidence"]:
        assert capture["file"] in rendered, f"missing evidence {capture['file']}"
        assert capture["sha256"] in rendered, "the full hash belongs in the appendix"
        assert f'id="{capture["file"]}"' in rendered, "figures keep their anchors"

    for finding in data["findings"]:
        assert finding["form_question"] in text

    # Items the website cannot answer are named, never quietly dropped.
    assert "Internal" in text
    # The disclaimer is the whole point of the document.
    assert "does not approve or deny anything" in text


def test_no_template_leftovers_and_no_external_requests(rendered):
    assert "{{" not in rendered and "{%" not in rendered and "{#" not in rendered
    # Self-contained: the shared stylesheet is inlined, nothing is fetched.
    assert "<link" not in rendered
    assert "<script src" not in rendered
    assert "--mint:" in rendered, "theme.css must be inlined into the report"
    assert "https://cdn" not in rendered


def test_lightbox_and_progressive_fallback(rendered):
    assert '<dialog id="lightbox"' in rendered
    # Every thumbnail is a real link to the PNG, so the report works without JS.
    assert 'class="thumb" href="evidence/' in rendered


def test_render_package_without_report_json(tmp_path):
    with pytest.raises(FileNotFoundError):
        render_package(tmp_path)


def test_status_counts_always_reports_every_status():
    counts = status_counts([])
    assert counts == {
        "found": 0,
        "not_found": 0,
        "needs_review": 0,
        "internal": 0,
        "needs_document": 0,
        "pass": 0,
        "flag": 0,
    }
    assert set(counts) == {s.value for s in Status}
