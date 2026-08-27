"""Medicine identity normalization.

Normalization resolves names. It never concludes anything clinical, and an
identity it could not pin down has to stay unresolved rather than being guessed
into something checkable.
"""

import pytest

from app.cds.contracts import MedicineInput, ResolutionState, ReviewReason
from app.cds.terminology import (
    EmptyTerminology,
    InventoryTerminology,
    TerminologyEntry,
    parse_display_name,
)

CATALOGUE = [
    TerminologyEntry(
        canonical_key="WAR-5",
        canonical_name="Warfarin",
        ingredient_key="warfarin",
        therapeutic_class="Anticoagulant",
        aliases=("Coumadin",),
    ),
    TerminologyEntry(
        canonical_key="IBU-400",
        canonical_name="Ibuprofen",
        ingredient_key="ibuprofen",
        therapeutic_class="NSAID",
        aliases=("Brufen",),
    ),
    TerminologyEntry(
        canonical_key="PCM-500",
        canonical_name="Paracetamol",
        ingredient_key="paracetamol",
        therapeutic_class="Analgesic",
        aliases=("Panadol",),
    ),
    TerminologyEntry(
        canonical_key="PCM-EX",
        canonical_name="Paracetamol Extra",
        ingredient_key="paracetamol extra",
        therapeutic_class="Analgesic",
        aliases=("Panadol Extra",),
    ),
]


@pytest.fixture
def terminology():
    return InventoryTerminology(CATALOGUE)


def full(name: str, **overrides) -> MedicineInput:
    """A medicine with every critical input present."""
    values = {"display_name": name, "dose": "5mg", "route": "oral", "form": "tablet"}
    values.update(overrides)
    return MedicineInput(**values)


# Parsing


@pytest.mark.parametrize(
    "value,base,strength,form",
    [
        ("Warfarin 5mg tablet", "warfarin", "5mg", "tablet"),
        ("Amoxicillin 500 mg caps", "amoxicillin", "500mg", "capsule"),
        ("PARACETAMOL", "paracetamol", None, None),
        ("Ibuprofen 400mg", "ibuprofen", "400mg", None),
        ("Co-amoxiclav 875/125 mg tab", "co amoxiclav", "875mg/125mg", "tablet"),
    ],
)
def test_display_names_parse_deterministically(value, base, strength, form):
    parsed = parse_display_name(value)
    assert parsed.base == base
    assert parsed.strength == strength
    assert parsed.form == form


def test_the_same_input_always_parses_the_same_way():
    # Reproducibility is what lets an alert be re-derived from its inputs.
    first = parse_display_name("Warfarin 5mg tablet")
    second = parse_display_name("Warfarin 5mg tablet")
    assert first == second


# Resolution


def test_an_exact_name_resolves(terminology):
    result = terminology.normalize(full("Warfarin 5mg tablet"))
    assert result.resolution is ResolutionState.RESOLVED
    assert [c.canonical_key for c in result.candidates] == ["WAR-5"]


def test_a_brand_name_resolves_to_the_same_product(terminology):
    result = terminology.normalize(full("Coumadin"))
    assert result.resolution is ResolutionState.RESOLVED
    assert result.candidates[0].canonical_key == "WAR-5"


def test_a_partial_name_matching_two_products_is_ambiguous(terminology):
    result = terminology.normalize(full("Paracetamol"))
    # "Paracetamol" is an exact name, so it resolves rather than colliding with
    # "Paracetamol Extra".
    assert result.resolution is ResolutionState.RESOLVED

    ambiguous = terminology.normalize(full("Panadol"))
    assert ambiguous.resolution is ResolutionState.RESOLVED

    partial = terminology.normalize(full("Paracet"))
    assert partial.resolution is ResolutionState.AMBIGUOUS
    assert len(partial.candidates) == 2


def test_an_unknown_name_stays_unresolved(terminology):
    result = terminology.normalize(full("Definitely Not A Medicine"))
    assert result.resolution is ResolutionState.UNRESOLVED
    assert result.candidates == []


def test_an_empty_catalogue_resolves_nothing():
    result = EmptyTerminology().normalize(full("Warfarin"))
    assert result.resolution is ResolutionState.UNRESOLVED


def test_confirmation_is_always_required_even_for_an_exact_match(terminology):
    # A single exact match is still a machine's opinion about what a human
    # typed. The human confirms before anything is checked.
    result = terminology.normalize(full("Warfarin"))
    assert result.resolution is ResolutionState.RESOLVED
    assert result.requires_confirmation is True


# Missing critical inputs


@pytest.mark.parametrize(
    "missing,reason",
    [
        ("dose", ReviewReason.MISSING_DOSE),
        ("route", ReviewReason.MISSING_ROUTE),
    ],
)
def test_missing_critical_inputs_are_reported(terminology, missing, reason):
    result = terminology.normalize(full("Warfarin 5mg tablet", **{missing: None}))
    assert reason in result.missing_critical_inputs


def test_a_form_in_the_name_counts_as_the_form(terminology):
    result = terminology.normalize(
        MedicineInput(display_name="Warfarin 5mg tablet", dose="5mg", route="oral")
    )
    assert ReviewReason.MISSING_FORM not in result.missing_critical_inputs


def test_no_form_anywhere_is_reported(terminology):
    result = terminology.normalize(
        MedicineInput(display_name="Warfarin", dose="5mg", route="oral")
    )
    assert ReviewReason.MISSING_FORM in result.missing_critical_inputs


# Confirmation


def test_a_confirmed_key_marks_the_medicine_confirmed(terminology):
    normalized = terminology.to_normalized(full("Warfarin", confirmed_key="WAR-5"))
    assert normalized.confirmed is True
    assert normalized.canonical_key == "WAR-5"
    assert normalized.ingredient_key == "warfarin"


def test_an_unconfirmed_medicine_is_not_marked_confirmed(terminology):
    normalized = terminology.to_normalized(full("Warfarin"))
    assert normalized.resolution is ResolutionState.RESOLVED
    assert normalized.confirmed is False


def test_a_confirmed_key_the_server_did_not_offer_is_discarded(terminology):
    # A key that did not come from this catalogue is not trusted just because a
    # client sent it back.
    normalized = terminology.to_normalized(full("Warfarin", confirmed_key="SOMETHING-ELSE"))
    assert normalized.confirmed is False
    assert normalized.resolution is ResolutionState.UNRESOLVED
    assert normalized.canonical_key is None


def test_normalization_reports_which_source_answered(terminology):
    normalized = terminology.to_normalized(full("Warfarin", confirmed_key="WAR-5"))
    assert normalized.source == "tenant-inventory-offline"
