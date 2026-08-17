"""The integrity gates — the guarantees behind "no hallucinated findings".

These are the highest-consequence rules in the tool: they are what makes a
fabricated "Found" impossible rather than merely discouraged. They run without
the SDK, a browser or an API key, because the validation lives on VerifySession
rather than inside the tool closures.
"""

import pytest
from PIL import Image

from preapproval.agent import VerifySession
from preapproval.config import load_checklists
from preapproval.evidence import EvidenceStore
from preapproval.models import ApplicationData, FeeAmount, Status

PAGE_TEXT = (
    "GallopNYC Sunrise Stables\n"
    "We offer limited public riding lessons, including private and group lessons.\n"
    "Pricing\n30-Minute Group - $80\n45-Minute Group - $100\n"
)


class FakeBrowser:
    """Stands in for Playwright — the gates never need a real browser."""

    class _Page:
        url = "https://gallopnyc.org/recreational-riding"

    page = _Page()


def make_session(tmp_path) -> VerifySession:
    app = ApplicationData(
        category="community_class",
        form_title="Community Class Pre-approval Checklist",
        participant_name="Aaron M.",
        participant_age=26,
        requested_item="Recreational Group Riding",
        provider_name="GallopNYC",
        url="https://gallopnyc.org/recreational-riding",
        fee_text="Fee per Session: $80 per 30-minute session",
        fees=[FeeAmount(amount=80, unit="per session")],
    )
    session = VerifySession(
        app=app,
        checklist=load_checklists()["community_class"],
        browser=FakeBrowser(),
        store=EvidenceStore(tmp_path),
        page_text_limit=12000,
        log=lambda _msg: None,
    )
    session.visited[FakeBrowser._Page.url] = PAGE_TEXT
    return session


def add_capture(session: VerifySession, label: str = "Evidence: published fees") -> str:
    path = session.store.next_path("evidence", label)
    Image.new("RGB", (400, 200), (240, 240, 240)).save(path, format="PNG")
    return session.store.register(
        path, kind="targeted", label=label, url=FakeBrowser._Page.url
    ).file


# --- gate 1: "found" requires real, manifested evidence --------------------


def test_found_without_evidence_is_rejected(tmp_path):
    session = make_session(tmp_path)
    result = session.apply_finding(
        "published_fees", "found", "Fees are published.", quote="30-Minute Group - $80"
    )
    assert result.startswith("REJECTED")
    assert "requires evidence_file" in result
    assert "published_fees" not in session.findings


def test_found_citing_unmanifested_file_is_rejected(tmp_path):
    session = make_session(tmp_path)
    result = session.apply_finding(
        "published_fees",
        "found",
        "Fees are published.",
        quote="30-Minute Group - $80",
        evidence_file="evidence/99-i-made-this-up.png",
    )
    assert result.startswith("REJECTED")
    assert "not in the evidence manifest" in result
    assert "published_fees" not in session.findings


def test_found_with_real_evidence_is_accepted(tmp_path):
    session = make_session(tmp_path)
    evidence = add_capture(session)
    result = session.apply_finding(
        "published_fees",
        "found",
        "The pricing block lists the group rate.",
        quote="30-Minute Group - $80",
        evidence_file=evidence,
    )
    assert result.startswith("Recorded published_fees = found")
    finding = session.findings["published_fees"]
    assert finding.status == Status.FOUND
    assert finding.evidence_files == [evidence]
    assert finding.evidence_url == FakeBrowser._Page.url
    assert finding.source == "agent"


# --- gate 2: quotes must be verbatim from a visited page -------------------


def test_fabricated_quote_is_rejected(tmp_path):
    session = make_session(tmp_path)
    evidence = add_capture(session)
    result = session.apply_finding(
        "published_fees",
        "found",
        "Fees are published.",
        quote="Group lessons cost $65 for OPWDD participants",  # never on the page
        evidence_file=evidence,
    )
    assert result.startswith("REJECTED")
    assert "does not appear on any page visited" in result
    assert "published_fees" not in session.findings


def test_quote_matching_is_whitespace_insensitive(tmp_path):
    """Page text wraps unpredictably; a quote differing only in spacing is fine."""
    session = make_session(tmp_path)
    assert session.quote_appears_on_a_visited_page("30-Minute   Group  -  $80")
    assert session.quote_appears_on_a_visited_page("we offer LIMITED public riding lessons")
    assert not session.quote_appears_on_a_visited_page("$65 special rate")


def test_quote_is_checked_even_for_not_found(tmp_path):
    session = make_session(tmp_path)
    result = session.apply_finding(
        "published_schedule", "not_found", "No schedule.", quote="Invented schedule text"
    )
    assert result.startswith("REJECTED")


def test_found_without_quote_is_rejected(tmp_path):
    session = make_session(tmp_path)
    evidence = add_capture(session)
    result = session.apply_finding(
        "published_fees", "found", "Fees are published.", evidence_file=evidence
    )
    assert result.startswith("REJECTED")
    assert "requires a verbatim quote" in result


# --- honest negatives are always allowed ------------------------------------


@pytest.mark.parametrize("status", ["not_found", "needs_review"])
def test_negative_findings_need_no_evidence(tmp_path, status):
    """"Couldn't verify" must never be harder to record than "verified"."""
    session = make_session(tmp_path)
    result = session.apply_finding(
        "published_schedule", status, "No schedule is published anywhere on the site."
    )
    assert result.startswith("Recorded")
    assert session.findings["published_schedule"].status == Status(status)


# --- input validation --------------------------------------------------------


def test_unknown_item_id_is_rejected(tmp_path):
    session = make_session(tmp_path)
    result = session.apply_finding("not_a_real_item", "needs_review", "x")
    assert result.startswith("REJECTED")
    assert "unknown item_id" in result


def test_internal_item_cannot_be_answered_by_the_agent(tmp_path):
    """Internal items are filled in by the pipeline — the agent must not touch them."""
    session = make_session(tmp_path)
    result = session.apply_finding("budget_approved", "found", "Looks approved to me")
    assert result.startswith("REJECTED")
    assert "budget_approved" not in session.findings


def test_invalid_status_is_rejected(tmp_path):
    session = make_session(tmp_path)
    result = session.apply_finding("published_fees", "approved", "x")
    assert result.startswith("REJECTED")
    assert "status must be one of" in result


def test_later_finding_overwrites_earlier_one(tmp_path):
    session = make_session(tmp_path)
    session.apply_finding("published_schedule", "needs_review", "first pass")
    session.apply_finding("published_schedule", "not_found", "checked the calendar page too")
    assert session.findings["published_schedule"].status == Status.NOT_FOUND
    assert list(session.findings) == ["published_schedule"], "must not duplicate the item"


# --- rate comparison gate ----------------------------------------------------


def test_rate_match_without_evidence_is_rejected(tmp_path):
    session = make_session(tmp_path)
    result = session.apply_rate_comparison(
        "$80 per 30-minute group lesson", "matches application exactly", "Same rate."
    )
    assert result.startswith("REJECTED")
    assert session.rate_comparison is None


def test_rate_not_published_needs_no_evidence(tmp_path):
    session = make_session(tmp_path)
    result = session.apply_rate_comparison("", "not published", "No prices anywhere on the site.")
    assert result.startswith("Rate comparison recorded")
    assert session.rate_comparison.verdict == "not published"
    assert session.rate_comparison.published_fee is None
    assert session.rate_comparison.form_fee == session.app.fee_text


def test_rate_match_with_evidence_is_accepted(tmp_path):
    session = make_session(tmp_path)
    evidence = add_capture(session, "Evidence: rate table")
    result = session.apply_rate_comparison(
        "30-Minute Group - $80", "matches application exactly", "Matches.", evidence
    )
    assert result.startswith("Rate comparison recorded")
    assert session.rate_comparison.evidence_files == [evidence]


def test_invalid_verdict_is_rejected(tmp_path):
    session = make_session(tmp_path)
    result = session.apply_rate_comparison("$80", "looks fine", "x")
    assert result.startswith("REJECTED")
    assert session.rate_comparison is None
