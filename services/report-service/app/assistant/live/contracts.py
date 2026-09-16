from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any

# A metric is a fixed, parameterised, read-only aggregate over the tenant
# database. It is the only route by which live hospital data may reach the
# model, and it keeps every property the content tools in tools.py already have:
#
#   - The server chooses which metric runs. The model never selects one, never
#     supplies an argument, and never sees a metric id or any SQL.
#   - The SQL is fixed text declared here. No value derived from the question,
#     from retrieved content, or from a model response is ever concatenated into
#     it; parameters are bound, always.
#   - Every returned row is projected through a deny-by-default field allowlist,
#     so a column added to a query later cannot reach the model without a
#     deliberate change to the allowlist and to its test.
#
# The registry tests assert these properties for every registered metric.


class MetricTier(str, Enum):
    """How close to a patient a metric's rows sit.

    AGGREGATE  counts, sums and averages. No row refers to an individual.
    WORKLIST   the same, narrowed to the caller's own work by their verified sub.
    PATIENT    one patient's operational status, pseudonymised before the model
               sees it. Requires an explicit patient or visit identifier in the
               question; it is never reached by a general enquiry.
    """

    AGGREGATE = "aggregate"
    WORKLIST = "worklist"
    PATIENT = "patient"


@dataclass(frozen=True)
class MetricParams:
    """Parameter values a metric may be run with.

    Every field is resolved deterministically by the server: date windows from a
    fixed phrase table, ward and drug names matched against values that already
    exist in the tenant database, identifiers from a strict pattern. Nothing
    here is authored by the model.
    """

    start: date | None = None
    end: date | None = None
    ward_name: str | None = None
    drug_name: str | None = None
    queue_type: str | None = None
    patient_number: str | None = None
    visit_number: str | None = None
    actor_sub: str | None = None

    def as_binds(self, names: frozenset[str]) -> dict[str, Any]:
        """Return only the binds a metric declared, so nothing extra is sent."""
        available = {
            "start": self.start,
            "end": self.end,
            "ward_name": self.ward_name,
            "drug_name": self.drug_name,
            "queue_type": self.queue_type,
            "patient_number": self.patient_number,
            "visit_number": self.visit_number,
            "actor_sub": self.actor_sub,
        }
        return {name: available[name] for name in sorted(names)}


@dataclass(frozen=True)
class MetricRow:
    """One projected row. Keys are exactly a metric's exposed_fields."""

    values: dict[str, Any]


@dataclass(frozen=True)
class MetricResult:
    """The bounded, projected outcome of running one metric.

    Mirrors tools.ToolResult: a failure is a safe empty result, never an
    exception reaching the caller and never a database error reaching a reader.
    """

    metric_id: str
    label: str
    rows: tuple[MetricRow, ...] = ()
    read_at: datetime | None = None
    failed: bool = False
    # For a PATIENT-tier result, the identifier the row was looked up by: the
    # patient or visit number the staff member typed into their own question.
    #
    # It is rendered into the figure's heading, and it has to be, because the row
    # itself is pseudonymised. Asked "where is PT-20260829-0003 now", with a
    # figure headed only "Patient status" whose sole identifier was PATIENT_1,
    # the model answered "I do not have that information" - three times, through
    # the gateway, with the correct and complete row sitting in its prompt. It
    # had no way to tell that the labelled row was the patient in the question,
    # and it is right not to guess.
    #
    # This sends the vendor nothing new: the staff question is already in the
    # prompt verbatim, identifier and all. What stays out is the patient's name
    # and every database id, which is the guarantee this tier actually makes.
    subject: str | None = None
    # Every numeric value this result put in front of the model, as the exact
    # strings the model was shown. figures.validate_figures uses this to refuse
    # an answer containing a number the server never supplied.
    figures: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_empty(self) -> bool:
        return not self.rows
