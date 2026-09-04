"""How a medicine question reaches the hospital medicines reference.

The reference itself - the monographs, the interaction rules, and the lookup
over them - lives in `shared/medicines`, because the pharmacy dispensing gate
raises its alerts from the same rules. What lives here is everything that is
only about answering a question in words: deciding whether a question is even
about medicines, reading which population it concerns, rendering an extract for
the model, and checking what the model wrote before anybody reads it.

The capability is gated three ways before any of it runs: the operator flag
`ASSISTANT_MEDICATION_CHECK_ENABLED`, the role matrix in `permissions.py`
(doctor and pharmacist only, never hospital_admin, never a super admin), and the
tenant resolved from the verified token. With the flag off, a medicine question
is refused exactly as it was before this package existed.
"""

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
]
