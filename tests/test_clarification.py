"""The tool asks instead of guessing when key information is unclear."""

import pytest

from preapproval.config import load_checklists
from preapproval.models import ApplicationData, FeeAmount
from preapproval.pipeline import (
    NeedsClarification,
    ReviewOverrides,
    categories_matching_title,
    check_for_ambiguity,
)

CHECKLISTS = load_checklists()


def make_app(**overrides) -> ApplicationData:
    base = dict(
        category="community_class",
        form_title="Community Class Pre-approval Checklist",
        participant_name="Aaron M.",
        participant_age=26,
        requested_item="Recreational Group Riding",
        provider_name="GallopNYC",
        url="https://gallopnyc.org/recreational-riding",
        fee_text="Fee per Session: $80",
        fees=[FeeAmount(amount=80, unit="per session")],
    )
    base.update(overrides)
    return ApplicationData(**base)


def test_complete_application_passes_without_asking():
    check_for_ambiguity(make_app(), CHECKLISTS, ReviewOverrides())


def test_missing_url_asks_for_it():
    with pytest.raises(NeedsClarification) as e:
        check_for_ambiguity(make_app(url=None), CHECKLISTS, ReviewOverrides())
    assert e.value.field == "url"


def test_missing_requested_item_asks_for_it():
    with pytest.raises(NeedsClarification) as e:
        check_for_ambiguity(make_app(requested_item="  "), CHECKLISTS, ReviewOverrides())
    assert e.value.field == "requested_item"


def test_title_hints_map_to_categories():
    assert categories_matching_title("Community Class Pre-approval Checklist", CHECKLISTS) == [
        "community_class"
    ]
    assert categories_matching_title("Pre-Approval Appeals Form", CHECKLISTS) == ["appeal"]
    assert categories_matching_title("Some Unrelated Document", CHECKLISTS) == []


def test_category_conflicting_with_the_form_title_asks():
    """An appeal misread as a community class must be queried, not silently accepted."""
    app = make_app(category="community_class", form_title="Pre-Approval Appeals Form")
    with pytest.raises(NeedsClarification) as e:
        check_for_ambiguity(app, CHECKLISTS, ReviewOverrides())
    assert e.value.field == "category"
    assert set(e.value.options) == {"appeal", "community_class"}


def test_unrecognised_title_does_not_block_the_review():
    """Hints are a cross-check, not a whitelist — an unusual title isn't an error."""
    check_for_ambiguity(
        make_app(form_title="Scanned form 2026-07 (rev B)"), CHECKLISTS, ReviewOverrides()
    )


def test_reviewer_supplied_category_wins():
    app = make_app(category="appeal", form_title="Community Class Pre-approval Checklist")
    check_for_ambiguity(app, CHECKLISTS, ReviewOverrides(category="appeal"))


def test_overrides_reject_unknown_fields():
    with pytest.raises(ValueError):
        ReviewOverrides().set("participant_name", "x")
