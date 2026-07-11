from app.agents.intent_agent import ExtractedConstraints, merge_extracted
from app.models.constraints import Constraints, PartyComposition


def test_merge_scalars_only_overwrite_when_present():
    base = Constraints(destination="Lisbon", budget_usd=2000)
    extracted = ExtractedConstraints(budget_usd=2500)
    merged = merge_extracted(base, extracted)
    assert merged.destination == "Lisbon"
    assert merged.budget_usd == 2500


def test_merge_creates_party_and_defaults_children_from_existing():
    base = Constraints(party=PartyComposition(adults=1, children=2, child_ages=[4, 7]))
    extracted = ExtractedConstraints(adults=2)
    merged = merge_extracted(base, extracted)
    assert merged.party.adults == 2
    assert merged.party.children == 2
    assert merged.party.child_ages == [4, 7]


def test_merge_party_overwrites_children_when_provided():
    base = Constraints(party=PartyComposition(adults=1, children=2, child_ages=[4, 7]))
    extracted = ExtractedConstraints(adults=2, children=0, child_ages=[])
    merged = merge_extracted(base, extracted)
    assert merged.party.children == 0
    assert merged.party.child_ages == []


def test_merge_accumulates_list_fields_without_duplicates():
    base = Constraints(vibe_tags=["walkable"], non_negotiables=["ocean view"])
    extracted = ExtractedConstraints(vibe_tags=["walkable", "boutique"], non_negotiables=["pet-friendly"])
    merged = merge_extracted(base, extracted)
    assert merged.vibe_tags == ["walkable", "boutique"]
    assert merged.non_negotiables == ["ocean view", "pet-friendly"]


def test_merge_does_not_mutate_input_constraints():
    base = Constraints(destination="Lisbon")
    merge_extracted(base, ExtractedConstraints(destination="Kyoto"))
    assert base.destination == "Lisbon"
