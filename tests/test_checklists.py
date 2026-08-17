"""The checklist configs are the rulebook — verify they encode the program rules correctly."""

from preapproval.config import load_checklists

ALL_CATEGORIES = {
    "community_class",
    "coaching",
    "membership",
    "hri",
    "otps",
    "transition_program",
    "appeal",
}


def test_all_seven_categories_load():
    assert set(load_checklists()) == ALL_CATEGORIES


def test_every_category_has_website_and_internal_split():
    for name, cl in load_checklists().items():
        assert cl.website_items, f"{name} has no website-verifiable items"
        if name != "appeal":  # the appeal form's internal work goes to the committee
            assert cl.internal_items, f"{name} has no internal items"
        for item in cl.website_items:
            assert item.requirement, f"{name}.{item.id} missing requirement"
            assert item.check_hint, f"{name}.{item.id} missing check_hint"
        for item in cl.internal_items:
            assert item.internal_reason, f"{name}.{item.id} missing internal_reason"


def test_community_class_website_subset_matches_brief():
    cl = load_checklists()["community_class"]
    ids = {i.id for i in cl.website_items}
    assert ids == {
        "open_to_public",
        "published_fees",
        "fees_identical",
        "subject_based",
        "no_college_credits",
        "not_clinical",
        "published_schedule",
    }


def test_exclusion_lists_present_for_hri_and_otps():
    cls = load_checklists()
    assert any("computer" in e.lower() for e in cls["hri"].exclusion_list)
    assert any("cable" in e.lower() for e in cls["otps"].exclusion_list)
    assert "laptop" in cls["hri"].exclusion_keywords


def test_fee_caps_match_program_rules():
    cls = load_checklists()
    coaching = {c.id: c.max_amount for c in cls["coaching"].fee_caps}
    assert coaching["cap_group_class"] == 55
    assert coaching["cap_private_class"] == 111
    assert coaching["cap_program_year"] == 500
    hri = {c.id: c.max_amount for c in cls["hri"].fee_caps}
    assert hri["cap_budget_year"] == 1500
    otps = {c.id: c.max_amount for c in cls["otps"].fee_caps}
    assert otps["cap_budget_year"] == 3000
    transition = {c.id: c.max_amount for c in cls["transition_program"].fee_caps}
    assert transition["cap_per_course"] == 350
    assert transition["cap_per_month"] == 800


def test_adults_only_flags():
    cls = load_checklists()
    assert cls["coaching"].adults_only
    assert cls["hri"].adults_only
    assert cls["transition_program"].adults_only
    assert not cls["community_class"].adults_only


def test_appeal_reuses_community_class_checks_with_framing():
    cls = load_checklists()
    appeal_ids = {i.id for i in cls["appeal"].website_items}
    community_ids = {i.id for i in cls["community_class"].website_items}
    assert appeal_ids == community_ids
    assert cls["appeal"].appeal_framing
