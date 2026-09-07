"""Shapes of the hospital medicines reference.

This is the "documentation and manuals" half of the medicines capability: a
versioned, approved, repo-shipped reference that the assistant answers a
clinician's medicine question *from*. It carries no patient data and reads no
database.

The split that matters is between the two things in a medicines answer:

* **What is true about a medicine** - its usual adult dose, what it must not be
  combined with, what happens in pregnancy - comes from this reference, and only
  from here. The model never decides whether two medicines interact, how serious
  an interaction is, or what a dose should be.
* **How that is worded for the clinician who asked** - which of the retrieved
  facts actually bear on their question, in their language, in a few sentences -
  is what the model does.

That division is the whole safety design. A model asked to recall pharmacology
produces a fluent, confident, occasionally wrong milligram figure, and a wrong
milligram figure in a hospital is not a bad answer, it is a harmed patient. A
model asked to organise supplied monograph text cannot invent a dose, because
the dose it is shown is the only one it has, and `reference.validate_doses`
rejects the whole answer if a number appears in it that was never supplied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Severity(str, Enum):
    """How an interaction is classified by the reference, never by the model.

    Ordered worst first. The wording each one carries is fixed here rather than
    left to the model, so "avoid" always reads as avoid and cannot soften into
    "may be used with caution" on its way through a language model.
    """

    AVOID = "avoid"
    SERIOUS = "serious"
    MODERATE = "moderate"
    MINOR = "minor"

    @property
    def headline(self) -> str:
        return {
            Severity.AVOID: "Avoid this combination",
            Severity.SERIOUS: "Serious interaction",
            Severity.MODERATE: "Moderate interaction",
            Severity.MINOR: "Minor interaction",
        }[self]

    @property
    def rank(self) -> int:
        """Sort key. The worst interaction in a question leads the answer."""
        return [Severity.AVOID, Severity.SERIOUS, Severity.MODERATE, Severity.MINOR].index(self)


class PregnancyStance(str, Enum):
    """What the reference says about a medicine in pregnancy.

    Deliberately not a letter category. Letter categories were withdrawn by the
    regulators that invented them because a single letter reads as a verdict,
    and a verdict is exactly what a reference should not hand to a prescriber
    for a decision that depends on the indication, the trimester, and the
    alternatives.
    """

    # Established as harmful; the reference says do not use in pregnancy.
    CONTRAINDICATED = "contraindicated"
    # Used in pregnancy only when the indication justifies it, or restricted to
    # particular trimesters.
    CAUTION = "caution"
    # Widely used in pregnancy at usual doses.
    ACCEPTABLE = "acceptable"
    # The reference carries no pregnancy statement for this medicine.
    NOT_STATED = "not_stated"


class ApprovalState(str, Enum):
    """Publication state. Only APPROVED material may reach a clinician."""

    APPROVED = "approved"
    DRAFT = "draft"


@dataclass(frozen=True)
class Monograph:
    """One medicine, as the hospital's reference describes it.

    Every field is short on purpose. A monograph here is not a replacement for
    the formulary on the shelf; it is the subset a prescriber asks the assistant
    about between patients - what is the usual dose, can it go with this other
    thing, what about in pregnancy - and it says so in the footer of every
    answer built from it.

    `synonyms` carries brand names and the spellings staff actually type, so
    "panadol", "paracetamol" and "acetaminophen" all reach the same monograph. A
    question that names a medicine the reference does not carry is answered as
    exactly that, never by answering about a different medicine.
    """

    drug_id: str
    generic_name: str
    # How the class reads in a sentence, e.g. "NSAID painkiller".
    class_label: str
    # Every class this medicine belongs to, for interaction-rule matching. A set
    # rather than one value because the classes that matter cross-cut: a
    # fluoroquinolone is also QT-prolonging, rifampicin is also an enzyme
    # inducer, and a rule about either has to reach it.
    drug_classes: frozenset[str]
    used_for: str
    adult_dose: str
    # What the reference will not go past. Kept separate from adult_dose so a
    # ceiling can be stated even where the usual dose is a range.
    max_adult_dose: str = ""
    pregnancy_stance: PregnancyStance = PregnancyStance.NOT_STATED
    pregnancy: str = ""
    breastfeeding: str = ""
    renal: str = ""
    hepatic: str = ""
    monitoring: str = ""
    cautions: tuple[str, ...] = ()
    synonyms: frozenset[str] = field(default_factory=frozenset)
    version: str = "1.0.0"
    effective_from: date = date(2026, 1, 1)
    approval_state: ApprovalState = ApprovalState.APPROVED
    # Which reference this entry was written from, shown to nobody but recorded
    # so a statement can be traced back to what it was taken from.
    source: str = ""

    def __post_init__(self) -> None:
        if not self.generic_name.strip():
            raise ValueError(f"monograph {self.drug_id} must have a generic name")
        if not self.adult_dose.strip():
            raise ValueError(f"monograph {self.drug_id} must state an adult dose")

    @property
    def names(self) -> frozenset[str]:
        """Every spelling that resolves to this monograph, lowercased."""
        return frozenset({self.generic_name.lower()}) | frozenset(
            s.lower() for s in self.synonyms
        )


@dataclass(frozen=True)
class InteractionRule:
    """One pair the reference says something about.

    A rule names either two medicines or two classes. Class rules are what make
    the reference cover more than the pairs somebody remembered to write down:
    "warfarin with any NSAID" is one rule that answers ibuprofen, diclofenac and
    aspirin alike, and adding an NSAID monograph extends it with no rule change.

    `effect` says what happens; `management` says what to do about it. They are
    separate fields because an answer that states a risk without saying what to
    do with it leaves the clinician exactly where they started.
    """

    rule_id: str
    severity: Severity
    effect: str
    management: str
    # Either side may name a specific medicine or a whole class. Exactly one of
    # each pair is required; a rule naming neither matches nothing, and a rule
    # naming both on one side would be ambiguous.
    drug_a: str = ""
    class_a: str = ""
    drug_b: str = ""
    class_b: str = ""
    version: str = "1.0.0"
    effective_from: date = date(2026, 1, 1)
    approval_state: ApprovalState = ApprovalState.APPROVED
    source: str = ""

    def __post_init__(self) -> None:
        for side, (drug, klass) in (
            ("a", (self.drug_a, self.class_a)),
            ("b", (self.drug_b, self.class_b)),
        ):
            if bool(drug) == bool(klass):
                raise ValueError(
                    f"interaction rule {self.rule_id} side {side} must name "
                    "exactly one of a medicine or a class"
                )

    def _side_matches(self, monograph: Monograph, drug: str, klass: str) -> bool:
        if drug:
            return monograph.drug_id == drug
        return klass in monograph.drug_classes

    def matches(self, first: Monograph, second: Monograph) -> bool:
        """Whether this rule speaks to this pair, in either order.

        A rule never matches a medicine with itself: two prescriptions of the
        same medicine is a duplication question, and the duplication rules say
        so in their own words rather than being inferred here.
        """
        if first.drug_id == second.drug_id:
            return False
        return (
            self._side_matches(first, self.drug_a, self.class_a)
            and self._side_matches(second, self.drug_b, self.class_b)
        ) or (
            self._side_matches(second, self.drug_a, self.class_a)
            and self._side_matches(first, self.drug_b, self.class_b)
        )


class Population(str, Enum):
    """A group the question is about, when the question says so.

    Detected from the clinician's own words and used only to decide which
    monograph fields lead the answer. It is never inferred from a patient
    record: this capability reads no patient data at all, so the pregnancy
    fields surface because the clinician said "pregnant", not because the
    assistant went looking.
    """

    PREGNANCY = "pregnancy"
    BREASTFEEDING = "breastfeeding"
    RENAL = "renal"
    HEPATIC = "hepatic"
    CHILD = "child"
    ELDERLY = "elderly"

    @property
    def label(self) -> str:
        return {
            Population.PREGNANCY: "pregnancy",
            Population.BREASTFEEDING: "breastfeeding",
            Population.RENAL: "impaired kidney function",
            Population.HEPATIC: "impaired liver function",
            Population.CHILD: "a child",
            Population.ELDERLY: "an older patient",
        }[self]
