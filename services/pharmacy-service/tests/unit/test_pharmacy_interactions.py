"""The dispensing interaction gate reads the shared medicines reference.

Before this, the gate had one pair written into it - warfarin with ibuprofen -
and everything else dispensed silently. The assistant meanwhile grew a pack of
interaction rules. Two checkers over the same question is one too many: they
disagree the moment either changes, and the one that blocks a dispense was the
weaker of the two.

These tests hold the join in place. They assert what the gate now reaches, that
it says the same thing the reference says, and that the one thing it cannot do -
recognise a medicine the pack does not carry - fails silently in the direction of
raising nothing rather than raising something wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.pharmacy import _PHARMACY_SEVERITY, _prescribed_medicines
from shared.medicines import Severity, find_medicines, interactions_between


@dataclass
class Item:
    """A prescription item, as far as the interaction check is concerned."""

    drug_name: str


def resolve(*names: str):
    return _prescribed_medicines([Item(name) for name in names])


class TestResolvingWhatWasPrescribed:
    def test_a_prescription_line_resolves_to_a_monograph(self):
        resolved = resolve("Warfarin 5mg tablet")
        assert [m.drug_id for _, m in resolved] == ["warfarin"]

    def test_the_name_as_written_is_kept_for_the_alert(self):
        """A pharmacist is holding the prescription, not the reference. An alert
        that renames what they are holding makes them check twice."""
        (written, _), = resolve("Warfarin 5mg tablet")
        assert written == "Warfarin 5mg tablet"

    def test_a_brand_name_on_the_prescription_still_resolves(self):
        assert [m.drug_id for _, m in resolve("Flagyl 400mg")] == ["metronidazole"]

    def test_the_same_medicine_prescribed_twice_resolves_once(self):
        resolved = resolve("Ibuprofen 400mg", "Brufen 200mg")
        assert [m.drug_id for _, m in resolved] == ["ibuprofen"]

    def test_a_medicine_the_pack_does_not_carry_resolves_to_nothing(self):
        """It raises no alert, which is not the same as there being nothing to
        raise. The pack is what to extend; the matching is not what to loosen."""
        assert resolve("Amiodarone 200mg") == []

    def test_an_empty_prescription_line_is_survivable(self):
        assert resolve("") == []


class TestWhatTheGateNowCatches:
    def _severity(self, *names: str):
        monographs = [m for _, m in resolve(*names)]
        hits = interactions_between(monographs)
        return _PHARMACY_SEVERITY[hits[0][2].severity] if hits else None

    def test_the_pair_the_old_check_knew_is_still_caught(self):
        assert self._severity("Warfarin 5mg tablet", "Ibuprofen 400mg tablet") == "high"

    @pytest.mark.parametrize(
        "first,second",
        [
            ("Enalapril 5mg", "Diclofenac 50mg"),
            ("Simvastatin 20mg", "Clarithromycin 500mg"),
            ("Warfarin 5mg", "Metronidazole 400mg"),
            ("Morphine 10mg", "Diazepam 5mg"),
            ("Gentamicin 80mg", "Furosemide 40mg"),
            ("Losartan 50mg", "Ibuprofen 400mg"),
        ],
    )
    def test_pairs_the_old_check_dispensed_silently_are_now_flagged(
        self, first, second
    ):
        """Every one of these was handed over without a word before."""
        assert self._severity(first, second) == "high"

    def test_a_moderate_interaction_is_reported_as_moderate(self):
        assert self._severity("Doxycycline 100mg", "Ferrous sulfate 200mg") == "moderate"

    def test_an_unremarkable_pair_raises_nothing(self):
        assert self._severity("Paracetamol 500mg", "Amoxicillin 500mg") is None

    def test_a_medicine_outside_the_pack_raises_nothing_against_one_inside_it(self):
        assert self._severity("Warfarin 5mg", "Amiodarone 200mg") is None


class TestTheSeverityMapping:
    def test_every_severity_the_reference_can_return_has_a_dispensing_level(self):
        """A severity with no mapping would raise KeyError inside the dispensing
        path, which is the worst place in this service to raise one."""
        for severity in Severity:
            assert _PHARMACY_SEVERITY[severity] in {"high", "moderate", "low"}

    def test_avoid_and_serious_both_read_as_high(self):
        """The gate has three levels and the reference has four. A pharmacist
        deciding whether to hand something over needs to know it is serious
        either way; which of the two it was is in the detail line."""
        assert _PHARMACY_SEVERITY[Severity.AVOID] == "high"
        assert _PHARMACY_SEVERITY[Severity.SERIOUS] == "high"


class TestTheGateAndTheAssistantCannotDisagree:
    def test_both_read_the_same_rule_for_the_same_pair(self):
        """The whole point of the move. If these ever diverge, one of them has
        grown rules of its own again."""
        prescribed = [m for _, m in resolve("Warfarin 5mg tablet", "Ibuprofen 400mg")]
        asked = find_medicines("can warfarin and ibuprofen be given together")

        from_gate = [r.rule_id for _, _, r in interactions_between(prescribed)]
        from_assistant = [r.rule_id for _, _, r in interactions_between(asked)]
        assert from_gate == from_assistant
