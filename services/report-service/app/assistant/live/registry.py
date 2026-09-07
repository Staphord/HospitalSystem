from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.assistant.live.contracts import MetricParams, MetricTier

# Bind markers actually present in a metric's SQL. Used by the registry test to
# assert that a metric declares exactly the parameters it uses, so a metric can
# neither be sent a bind it ignores nor reference one it never declared.
#
# The lookbehind keeps a Postgres cast out of the result: in "admission_date::date"
# the second colon is not a bind marker, and counting it as one would have every
# metric that casts a timestamp appear to reference a parameter called "date".
_BIND = re.compile(r"(?<!:):([a-z_][a-z0-9_]*)")

# Column names that must never appear in a metric's SQL. This is the structural
# guarantee behind "the assistant reads allowed columns only": it is asserted
# over every registered metric by tests, so a new metric that reaches for a name,
# a phone number, an address, or clinical narrative fails the build rather than
# shipping. Patient-tier metrics select identifying values too, but only through
# the aliasing path in aliases.py, which replaces them before the model is
# called; they are named in PSEUDONYMISED_COLUMNS below rather than allowed here.
FORBIDDEN_SQL_COLUMNS: frozenset[str] = frozenset(
    {
        "national_id",
        "phone_primary",
        "phone_secondary",
        "next_of_kin_name",
        "next_of_kin_phone",
        "next_of_kin_relationship",
        "date_of_birth",
        "address",
        "email",
        "blood_group",
        "allergies",
        "admitting_diagnosis",
        "discharge_diagnosis",
        "discharge_instructions",
        "chief_complaint",
        "triage_notes",
        "clinical_history",
        "history_of_presenting_illness",
        "examination_findings",
        "clinical_impression",
        "referral_notes",
        "admission_reason",
        "note_text",
        "result_value",
        "result_notes",
        "reference_range",
        "findings",
        "impression",
        "policy_number",
        "old_values",
        "new_values",
        "ip_address",
        # The patients table carries three more that no metric has needed until
        # patient lookup made the table reachable at all. They are named here so
        # the build refuses them, rather than relying on the next metric author
        # not reaching for them.
        "medical_history",
        "emergency_contact_name",
        "emergency_contact_phone",
    }
)

# Values a PATIENT-tier metric may select even though they identify someone,
# because execution.py routes them through the alias table and they are replaced
# by an opaque label before any prompt is assembled. No other tier may name them.
PSEUDONYMISED_COLUMNS: frozenset[str] = frozenset({"full_name", "patient_number"})

# Values a PATIENT-tier metric may read in its SQL but may never expose.
#
# date_of_birth is forbidden above, and stays forbidden as a value: it is a
# strong re-identifier, and a date of birth beside a ward name narrows a person
# very fast. An age band is not, and is what a receptionist or a nurse actually
# wants - but there is no way to derive a band without naming the column the
# band is derived from. So the column may appear inside the expression that
# buckets it, and the guarantee that the value itself never leaves is carried by
# exposed_fields, which is a deny-by-default allowlist: a column absent from it
# is dropped by MetricDefinition.project before any row is built. A registry
# test asserts no metric exposes one of these, so the two halves cannot drift.
DERIVED_ONLY_COLUMNS: frozenset[str] = frozenset({"date_of_birth"})


@dataclass(frozen=True)
class MetricDefinition:
    """One fixed, read-only, role-gated aggregate over the tenant database."""

    metric_id: str
    label: str
    tier: MetricTier
    allowed_roles: frozenset[str]
    # Terms that route a question to this metric. Scored by routing.py using the
    # same tokeniser and Swahili expansion the content retrieval already uses,
    # so a Swahili question routes exactly as its English equivalent does.
    triggers: frozenset[str]
    sql: str
    params: frozenset[str]
    exposed_fields: frozenset[str]
    max_rows: int = 25
    # Columns whose values are numbers the model may state. Recorded so the
    # answer validator can refuse a number the server never supplied.
    numeric_fields: frozenset[str] = frozenset()
    # A question this metric answers, offered to a permitted caller as a starting
    # point when they open the assistant with nothing typed.
    #
    # It is declared beside the metric so a suggestion cannot outlive what
    # answers it, and so it is only ever offered to somebody whose roles pass
    # allowed_roles above. test_assistant_suggestions.py asserts that every
    # example_question here actually routes to its own metric, which makes the
    # suggestion a tested claim rather than a hopeful string.
    #
    # A metric that filters on a ward or a drug name leaves this empty: whether
    # such a question can be answered depends on what this particular hospital
    # has on its shelves, and a suggestion that works in one tenant and fails in
    # the next is worse than no suggestion at all.
    example_question: str = ""
    # The same question in Swahili, offered when the reply is in Swahili.
    # Tested to route to this same metric, so it doubles as the Swahili
    # regression corpus for routing.
    swahili_example_question: str = ""

    def is_permitted(self, roles: frozenset[str], is_super_admin: bool = False) -> bool:
        """Role gate for this metric. Deny by default.

        Capability gating happens once, earlier, in service._authorize. This is
        the second, narrower gate: holding the live data capability does not mean
        reaching every figure, so a pharmacist cannot read takings and a cashier
        cannot read stock.
        """
        if is_super_admin:
            return False
        return bool(roles & self.allowed_roles)

    def declared_binds(self) -> frozenset[str]:
        """Bind names actually written in this metric's SQL."""
        return frozenset(_BIND.findall(self.sql))

    def project(self, row: Any) -> dict[str, Any]:
        """Project one database row to exactly this metric's exposed fields.

        Deny by default, and the same belt-and-braces check as tools._project:
        a real check rather than an assert, so it survives python -O. A query
        that starts returning an extra column raises here instead of quietly
        widening what the model is shown.
        """
        mapping = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
        item = {name: mapping[name] for name in sorted(self.exposed_fields) if name in mapping}
        if set(item) != set(self.exposed_fields):
            raise RuntimeError(
                "metric projection escaped its field allowlist: " + self.metric_id
            )
        return item


METRIC_REGISTRY: dict[str, MetricDefinition] = {}


def register(*definitions: MetricDefinition) -> None:
    """Register metrics. A duplicate id is a programming error, not a warning."""
    for definition in definitions:
        if definition.metric_id in METRIC_REGISTRY:
            raise RuntimeError("duplicate metric id: " + definition.metric_id)
        METRIC_REGISTRY[definition.metric_id] = definition


def get_metric(metric_id: str) -> MetricDefinition | None:
    """Look up a metric by id. Fail-closed on anything not registered."""
    if not metric_id or not isinstance(metric_id, str):
        return None
    return METRIC_REGISTRY.get(metric_id)


def permitted_metrics(
    roles: frozenset[str], is_super_admin: bool = False
) -> list[MetricDefinition]:
    """Return the metrics this caller may reach, in a stable order."""
    return [
        definition
        for _, definition in sorted(METRIC_REGISTRY.items())
        if definition.is_permitted(roles, is_super_admin=is_super_admin)
    ]


def reaches_patient_tier(roles: frozenset[str], is_super_admin: bool = False) -> bool:
    """Whether this caller may reach any patient-tier metric at all.

    Asked before the server does any work to resolve a name from a question, so
    a caller who would be refused the answer never causes a lookup against the
    patients table in the first place. The role gate is the same one the metric
    itself applies; this only moves the check earlier.
    """
    return any(
        definition.tier is MetricTier.PATIENT
        for definition in permitted_metrics(roles, is_super_admin=is_super_admin)
    )


def load_catalog() -> None:
    """Import every catalog module so its metrics self-register."""
    from app.assistant.live.catalog import beds  # noqa: F401
    from app.assistant.live.catalog import billing  # noqa: F401
    from app.assistant.live.catalog import flow  # noqa: F401
    from app.assistant.live.catalog import labs  # noqa: F401
    from app.assistant.live.catalog import patients  # noqa: F401
    from app.assistant.live.catalog import pharmacy  # noqa: F401
