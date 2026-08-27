"""Medicine identity normalization.

Normalization answers "which product is this?". It never answers "is this
safe?". Those are separate adapters on purpose: a terminology service that is
excellent at resolving names has no opinion on interactions, and treating a
successful normalization as clinical reassurance is exactly the mistake this
service exists to prevent.

The shipped adapter is offline. It resolves names against the tenant's own
drug_inventory rows and makes no outbound request, so no patient's medication
list leaves the hospital network to normalize a name. A live terminology
adapter (RxNorm or similar) can be added behind this same protocol later
without any change to the engine.

Substring matching appears here, and only here, for one narrowly bounded
purpose: offering candidates for a human to confirm. It never decides that two
medicines are the same, and it never feeds a clinical conclusion directly. An
unconfirmed or ambiguous match makes the whole check needs_review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Protocol

from app.cds.contracts import (
    MedicineInput,
    NormalizationCandidate,
    NormalizedMedicine,
    NormalizeResult,
    ResolutionState,
    ReviewReason,
)

# Dose forms recognised well enough to strip from a display name so that
# "Amoxicillin 500mg capsule" and "amoxicillin caps" resolve to one product.
_FORM_WORDS: dict[str, str] = {
    "tablet": "tablet",
    "tablets": "tablet",
    "tab": "tablet",
    "tabs": "tablet",
    "capsule": "capsule",
    "capsules": "capsule",
    "cap": "capsule",
    "caps": "capsule",
    "syrup": "syrup",
    "suspension": "suspension",
    "solution": "solution",
    "injection": "injection",
    "inj": "injection",
    "infusion": "infusion",
    "cream": "cream",
    "ointment": "ointment",
    "gel": "gel",
    "drops": "drops",
    "drop": "drops",
    "inhaler": "inhaler",
    "suppository": "suppository",
    "patch": "patch",
    "powder": "powder",
}

_UNITS = r"mg|mcg|g|ml|l|iu|units?|%"

# Combination products are written both ways: "875mg/125mg" and "875/125 mg".
# The shared-unit form is tried first, because the other pattern would otherwise
# match only its second half and leave the first number stranded in the name.
_SHARED_UNIT_STRENGTH = re.compile(
    rf"\b(\d+(?:[.,]\d+)?)\s*/\s*(\d+(?:[.,]\d+)?)\s*({_UNITS})\b",
    re.IGNORECASE,
)

_STRENGTH_PATTERN = re.compile(
    rf"\b(\d+(?:[.,]\d+)?)\s*({_UNITS})\b(?:\s*/\s*(\d+(?:[.,]\d+)?)\s*({_UNITS}))?",
    re.IGNORECASE,
)

_PUNCTUATION = re.compile(r"[^a-z0-9\s]")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class TerminologyEntry:
    """One product in the tenant's formulary, as the normalizer sees it."""

    canonical_key: str
    canonical_name: str
    ingredient_key: str | None = None
    therapeutic_class: str | None = None
    strength: str | None = None
    form: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ParsedName:
    base: str
    strength: str | None
    form: str | None


class Terminology(Protocol):
    """The seam a terminology source has to satisfy."""

    name: str

    def describe(self) -> str: ...

    def normalize(self, medicine: MedicineInput) -> NormalizeResult: ...

    def to_normalized(self, medicine: MedicineInput) -> NormalizedMedicine: ...


def normalize_text(value: str | None) -> str:
    """Lowercase, strip punctuation, and collapse whitespace."""
    if not value:
        return ""
    lowered = _PUNCTUATION.sub(" ", value.strip().lower())
    return _WHITESPACE.sub(" ", lowered).strip()


def parse_display_name(value: str) -> ParsedName:
    """Split a typed name into base name, strength, and dose form.

    Deterministic and reversible: the same input always yields the same parse,
    which is what lets an alert be reproduced from its recorded inputs.
    """
    text = value or ""

    strength: str | None = None
    shared = _SHARED_UNIT_STRENGTH.search(text)
    if shared:
        unit = shared.group(3).lower()
        first = shared.group(1).replace(",", ".")
        second = shared.group(2).replace(",", ".")
        strength = f"{first}{unit}/{second}{unit}"
        text = text[: shared.start()] + " " + text[shared.end() :]
    else:
        match = _STRENGTH_PATTERN.search(text)
        if match:
            amount = match.group(1).replace(",", ".")
            unit = match.group(2).lower()
            strength = f"{amount}{unit}"
            if match.group(3) and match.group(4):
                strength += f"/{match.group(3).replace(',', '.')}{match.group(4).lower()}"
            text = text[: match.start()] + " " + text[match.end() :]

    tokens = normalize_text(text).split()
    form: str | None = None
    kept: list[str] = []
    for token in tokens:
        mapped = _FORM_WORDS.get(token)
        if mapped and form is None:
            form = mapped
            continue
        kept.append(token)

    return ParsedName(base=" ".join(kept).strip(), strength=strength, form=form)


def _missing_critical_inputs(medicine: MedicineInput, resolved: TerminologyEntry | None) -> list[ReviewReason]:
    """Which critical inputs are absent.

    Dose, route, and form change whether an interaction matters. A check run
    without them is not a check that concluded; it is a check with a hole in it,
    and the engine turns each of these into needs_review.
    """
    parsed = parse_display_name(medicine.display_name)
    missing: list[ReviewReason] = []
    if not (medicine.dose or "").strip():
        missing.append(ReviewReason.MISSING_DOSE)
    if not (medicine.route or "").strip():
        missing.append(ReviewReason.MISSING_ROUTE)
    form = (medicine.form or "").strip() or parsed.form or (resolved.form if resolved else None)
    if not form:
        missing.append(ReviewReason.MISSING_FORM)
    return missing


class InventoryTerminology:
    """Resolves medicine names against this tenant's own drug inventory.

    Deliberately modest about what it is. It performs identity matching over a
    hospital's stock list; it is not a clinical terminology service and claims
    no ingredient science. The therapeutic class it reports is whatever the
    hospital recorded in the inventory category column, which is why a duplicate
    therapy finding from it is reported as needs_review rather than as a decided
    alert.
    """

    name = "tenant-inventory-offline"

    def __init__(self, entries: Iterable[TerminologyEntry]) -> None:
        self._entries: list[TerminologyEntry] = list(entries)
        self._by_exact: dict[str, list[TerminologyEntry]] = {}
        for entry in self._entries:
            for candidate in (entry.canonical_name, *entry.aliases):
                key = parse_display_name(candidate).base
                if not key:
                    continue
                self._by_exact.setdefault(key, []).append(entry)

    def describe(self) -> str:
        return f"{self.name} ({len(self._entries)} products)"

    def _candidates(self, base: str) -> list[TerminologyEntry]:
        if not base:
            return []

        exact = self._by_exact.get(base)
        if exact:
            # Deduplicate by canonical key: one product listed under several
            # aliases is still one product, not an ambiguity.
            seen: dict[str, TerminologyEntry] = {}
            for entry in exact:
                seen.setdefault(entry.canonical_key, entry)
            return list(seen.values())

        # No exact match. Offer near matches for a human to confirm, capped so a
        # vague name cannot return a catalogue.
        partial: dict[str, TerminologyEntry] = {}
        for key, entries in self._by_exact.items():
            if base in key or key in base:
                for entry in entries:
                    partial.setdefault(entry.canonical_key, entry)
        return sorted(partial.values(), key=lambda e: e.canonical_name)[:10]

    def normalize(self, medicine: MedicineInput) -> NormalizeResult:
        parsed = parse_display_name(medicine.display_name)
        candidates = self._candidates(parsed.base)

        if len(candidates) == 1:
            resolution = ResolutionState.RESOLVED
        elif len(candidates) > 1:
            resolution = ResolutionState.AMBIGUOUS
        else:
            resolution = ResolutionState.UNRESOLVED

        resolved = candidates[0] if resolution is ResolutionState.RESOLVED else None

        return NormalizeResult(
            submitted_name=medicine.display_name,
            resolution=resolution,
            candidates=[
                NormalizationCandidate(
                    canonical_key=entry.canonical_key,
                    canonical_name=entry.canonical_name,
                    ingredient_key=entry.ingredient_key,
                    therapeutic_class=entry.therapeutic_class,
                    strength=parsed.strength or entry.strength,
                    form=medicine.form or parsed.form or entry.form,
                )
                for entry in candidates
            ],
            # Always true. Even a single exact match is a machine's opinion about
            # what a human typed, and the human confirms it before a check runs.
            requires_confirmation=True,
            missing_critical_inputs=_missing_critical_inputs(medicine, resolved),
        )

    def to_normalized(self, medicine: MedicineInput) -> NormalizedMedicine:
        result = self.normalize(medicine)
        parsed = parse_display_name(medicine.display_name)

        chosen: NormalizationCandidate | None = None
        confirmed = False

        if medicine.confirmed_key:
            # The clinician picked one. Honour it only if it is genuinely one of
            # the candidates this server computed; a key that did not come from
            # this catalogue is discarded rather than trusted.
            for candidate in result.candidates:
                if candidate.canonical_key == medicine.confirmed_key:
                    chosen = candidate
                    confirmed = True
                    break
        elif result.resolution is ResolutionState.RESOLVED:
            chosen = result.candidates[0]

        resolution = result.resolution
        if medicine.confirmed_key and not confirmed:
            # They confirmed something this catalogue does not contain.
            resolution = ResolutionState.UNRESOLVED
            chosen = None

        return NormalizedMedicine(
            submitted_name=medicine.display_name,
            resolution=resolution,
            canonical_key=chosen.canonical_key if chosen else None,
            canonical_name=chosen.canonical_name if chosen else None,
            ingredient_key=chosen.ingredient_key if chosen else None,
            therapeutic_class=chosen.therapeutic_class if chosen else None,
            strength=(chosen.strength if chosen else None) or parsed.strength,
            form=medicine.form or parsed.form or (chosen.form if chosen else None),
            route=medicine.route,
            dose=medicine.dose,
            confirmed=confirmed,
            source=self.name,
            missing_critical_inputs=result.missing_critical_inputs,
        )


class EmptyTerminology(InventoryTerminology):
    """A terminology source with nothing in it.

    Used when a tenant has no inventory rows. Every name comes back unresolved,
    which turns the check into needs_review instead of quietly checking a
    medicine the server could not identify.
    """

    name = "empty-catalogue"

    def __init__(self) -> None:
        super().__init__([])
