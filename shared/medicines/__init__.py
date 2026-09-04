"""The hospital medicines reference: monographs, interaction rules, and lookup.

One pack, imported by every part of the system that has anything to say about
what two medicines do together. It lives here rather than inside the assistant
because it stopped being the assistant's: the pharmacy dispensing gate raises
its alerts from the same rules, so a pharmacist reading an alert and a doctor
asking the assistant cannot be told two different things.

Adding a medicine is one entry in `pack.py`. Adding an interaction is one rule,
and a rule written between two classes answers every pair in them, including the
pairs added afterwards.
"""

from shared.medicines.matching import (
    find_medicines,
    interactions_between,
    normalise,
    pack_version,
    worst_severity,
)
from shared.medicines.models import (
    ApprovalState,
    InteractionRule,
    Monograph,
    Population,
    PregnancyStance,
    Severity,
)
from shared.medicines.pack import (
    INTERACTION_RULES,
    MEDICINES_PACK_VERSION,
    MONOGRAPHS,
)

__all__ = [
    "ApprovalState",
    "INTERACTION_RULES",
    "InteractionRule",
    "MEDICINES_PACK_VERSION",
    "MONOGRAPHS",
    "Monograph",
    "Population",
    "PregnancyStance",
    "Severity",
    "find_medicines",
    "interactions_between",
    "normalise",
    "pack_version",
    "worst_severity",
]
