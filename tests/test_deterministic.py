"""Deterministic pipeline checks: fee caps, age eligibility, exclusion backstop."""

from preapproval.config import load_checklists
from preapproval.models import ApplicationData, FeeAmount, Finding, Status
from preapproval.pipeline import (
    apply_exclusion_backstop,
    eligibility_findings,
    fee_cap_findings,
)


def make_app(**overrides) -> ApplicationData:
    base = dict(
        category="coaching",
        form_title="Coaching for Parents/Spouse Pre-approval Form",
        participant_name="Test T.",
        participant_age=28,
        requested_item="Parenting class",
        provider_name="Test Provider",
        url="https://example.org",
        fee_text="Fee per Class: $50 | Fee per Course: $400",
        fees=[FeeAmount(amount=50, unit="per class"), FeeAmount(amount=400, unit="per course")],
    )
    base.update(overrides)
    return ApplicationData(**base)


def test_coaching_fees_within_caps_pass():
    cl = load_checklists()["coaching"]
    findings = {f.item_id: f for f in fee_cap_findings(make_app(), cl)}
    assert findings["cap_group_class"].status == Status.PASS
    assert findings["cap_program_year"].status == Status.PASS


def test_coaching_fee_over_cap_flags():
    cl = load_checklists()["coaching"]
    app = make_app(fees=[FeeAmount(amount=75, unit="per class")])
    findings = {f.item_id: f for f in fee_cap_findings(app, cl)}
    assert findings["cap_group_class"].status == Status.FLAG
    assert "EXCEEDS" in findings["cap_group_class"].note


def test_transition_caps_route_by_unit():
    cl = load_checklists()["transition_program"]
    app = make_app(
        category="transition_program",
        fees=[FeeAmount(amount=300, unit="per course")],
    )
    findings = {f.item_id: f for f in fee_cap_findings(app, cl)}
    assert findings["cap_per_course"].status == Status.PASS
    # No monthly fee on the form -> cap can't be checked -> honest needs_review
    assert findings["cap_per_month"].status == Status.NEEDS_REVIEW


def test_adults_only_age_pass_and_flag():
    cl = load_checklists()["coaching"]
    assert eligibility_findings(make_app(participant_age=28), cl)[0].status == Status.PASS
    assert eligibility_findings(make_app(participant_age=16), cl)[0].status == Status.FLAG
    assert eligibility_findings(make_app(participant_age=None), cl)[0].status == Status.FLAG


def test_non_adult_category_has_no_age_finding():
    cl = load_checklists()["community_class"]
    assert eligibility_findings(make_app(category="community_class"), cl) == []


def test_exclusion_backstop_downgrades_laptop_found():
    """Sample-07 trap: if the agent ever verified a laptop as 'not excluded',
    the deterministic keyword backstop must downgrade it."""
    cl = load_checklists()["hri"]
    app = make_app(
        category="hri",
        requested_item="Laptop computer for schoolwork",
        fees=[FeeAmount(amount=450, unit="one-time item price")],
    )
    finding = Finding(
        item_id="not_excluded",
        form_question="x",
        status=Status.FOUND,
        note="agent thought it was fine",
        source="agent",
    )
    warnings = apply_exclusion_backstop(app, cl, [finding])
    assert finding.status == Status.NEEDS_REVIEW
    assert warnings, "backstop should surface a warning"


def test_exclusion_backstop_leaves_grab_bar_alone():
    cl = load_checklists()["hri"]
    app = make_app(category="hri", requested_item="Bathroom safety grab bar (wall-mounted)")
    finding = Finding(
        item_id="not_excluded", form_question="x", status=Status.FOUND, note="", source="agent"
    )
    warnings = apply_exclusion_backstop(app, cl, [finding])
    assert finding.status == Status.FOUND
    assert not warnings
