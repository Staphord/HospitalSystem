"""Resolving a medicine name, and looking up what the reference says about a pair.

The two things every consumer of the pack needs, and the only two. The
assistant needs far more - which population the question is about, how to render
an extract, whether a model's answer invented a number - and that stays in
report-service, because a dispensing gate has no use for any of it.

Nothing here reads a database, makes a request, or depends on a framework. It is
a lookup over a file, which is what lets the same rules run inside a dispensing
gate without putting a service dependency in the path of handing a patient their
medicine.
"""

from __future__ import annotations

import re

from shared.medicines.models import InteractionRule, Monograph, Severity
from shared.medicines.pack import INTERACTION_RULES, MEDICINES_PACK_VERSION, MONOGRAPHS

# How many medicines one lookup may pull in. A question naming more than this is
# a medication review rather than a question; a prescription with more than this
# is checked in pairs by whoever is reviewing it.
MAX_MEDICINES_PER_LOOKUP = 6

# Bounds the interaction list for the same reason.
MAX_INTERACTIONS = 10


def pack_version() -> str:
    return MEDICINES_PACK_VERSION


def normalise(text: str) -> str:
    """Fold the separators staff vary on, so one spelling matches all of them."""
    lowered = (text or "").lower()
    lowered = lowered.replace("-", " ").replace("/", " ")
    return re.sub(r"\s+", " ", lowered)


def _spellings(name: str) -> set[str]:
    """Every way a name is written, from one way of writing it.

    Staff type "co-trimoxazole", "co trimoxazole" and "cotrimoxazole", and all
    three mean the same medicine. Normalising the separator handles the first
    two; the run-together form has to be indexed in its own right, because there
    is no separator left in it to normalise.
    """
    normalised = normalise(name)
    return {normalised, normalised.replace(" ", "")}


_NAME_INDEX: list[tuple[str, Monograph]] = sorted(
    (
        (spelling, monograph)
        for monograph in MONOGRAPHS
        for name in monograph.names
        for spelling in _spellings(name)
    ),
    key=lambda pair: -len(pair[0]),
)


def find_medicines(text: str, limit: int = MAX_MEDICINES_PER_LOOKUP) -> list[Monograph]:
    """Every medicine in the pack that this text names, in the order written.

    Order matters for how an answer reads: a clinician who asked about ibuprofen
    and enalapril should not be answered about enalapril and ibuprofen.
    Duplicates collapse, so naming one medicine by two of its names does not
    double it.
    """
    haystack = normalise(text)
    if not haystack:
        return []

    positions: dict[str, int] = {}
    found: dict[str, Monograph] = {}
    for name, monograph in _NAME_INDEX:
        match = re.search(r"\b" + re.escape(name) + r"\b", haystack)
        if match is None:
            continue
        # First mention wins the position, so an alias used later in the
        # sentence does not move a medicine to the back.
        existing = positions.get(monograph.drug_id)
        if existing is None or match.start() < existing:
            positions[monograph.drug_id] = match.start()
        found[monograph.drug_id] = monograph

    ordered = sorted(found.values(), key=lambda m: (positions[m.drug_id], m.drug_id))
    return ordered[: max(0, limit)]


def interactions_between(
    monographs: list[Monograph], limit: int = MAX_INTERACTIONS
) -> list[tuple[Monograph, Monograph, InteractionRule]]:
    """Every rule that speaks to any pair among these medicines, worst first.

    Every pair is checked against every rule rather than looked up in an index,
    because a class rule has to be considered for pairs nobody enumerated. With
    a handful of medicines and a pack of this size that is a few hundred
    comparisons, in memory, over data that never changes at runtime.
    """
    hits: list[tuple[Monograph, Monograph, InteractionRule]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, first in enumerate(monographs):
        for second in monographs[index + 1:]:
            for rule in INTERACTION_RULES:
                if not rule.matches(first, second):
                    continue
                key = (first.drug_id, second.drug_id, rule.rule_id)
                if key in seen:
                    continue
                seen.add(key)
                hits.append((first, second, rule))

    hits.sort(key=lambda hit: (hit[2].severity.rank, hit[2].rule_id))
    return hits[: max(0, limit)]


def worst_severity(
    interactions: list[tuple[Monograph, Monograph, InteractionRule]],
) -> Severity | None:
    """The worst severity among these interactions, or None."""
    if not interactions:
        return None
    return min((hit[2].severity for hit in interactions), key=lambda s: s.rank)
