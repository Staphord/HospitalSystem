"""Server-side access checks and the allowlist of data this service may read.

Two rules govern everything in this module.

**The tenant is structural, not a parameter.** The database session handed in
here was opened for the tenant named in the verified token. No query below
accepts a tenant, a database name, or a connection string, so there is no
argument a caller could tamper with to reach another hospital's data. A visit id
belonging to another tenant simply does not exist in this session.

**Columns are named, never selected wholesale.** Every query lists its columns
explicitly and the allowlists below are the single source of truth for what may
leave the database. `SELECT *` is never used. A deny-list would be worse than
useless here: the moment another developer adds a column to `patients` for an
unrelated feature, a deny-list would start exposing it with nobody having
decided that it should be. The test suite fails if any of these sets change, so
widening one is a deliberate act that has to be reviewed.

Patient allergy text is treated as untrusted data throughout. It is compared for
equality against ruleset allergen names and is never interpreted as an
instruction, a rule, or anything a model reads.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.cds.contracts import MedicineInput
from app.cds.terminology import InventoryTerminology, TerminologyEntry, normalize_text

logger = logging.getLogger("cds.access")

# The complete set of columns this service may read, per table. Minimum
# necessary: enough to identify a product and check a contraindication, and
# nothing more. Patient name is deliberately absent — the clinician already
# chose the visit, and a medication check does not need to know whose it is.
VISIT_COLUMNS: frozenset[str] = frozenset({"visit_id", "patient_id", "status", "visit_date"})
PATIENT_COLUMNS: frozenset[str] = frozenset(
    {"id", "patient_number", "date_of_birth", "gender", "allergies"}
)
# The parent prescription row carries no drug detail in the migrated tenant
# schema: drug_name, dose and frequency live on the item rows, and no route is
# recorded anywhere. See "route is not captured" in the phase notes.
PRESCRIPTION_COLUMNS: frozenset[str] = frozenset(
    {"prescription_id", "visit_id", "status"}
)
PRESCRIPTION_ITEM_COLUMNS: frozenset[str] = frozenset(
    {"prescription_item_id", "prescription_id", "drug_name", "dose", "frequency", "status"}
)
INVENTORY_COLUMNS: frozenset[str] = frozenset(
    {"inventory_id", "drug_name", "brand_name", "drug_code", "category", "unit"}
)

ALLOWLISTS: dict[str, frozenset[str]] = {
    "visits": VISIT_COLUMNS,
    "patients": PATIENT_COLUMNS,
    "pharmacy_prescriptions": PRESCRIPTION_COLUMNS,
    "pharmacy_prescription_items": PRESCRIPTION_ITEM_COLUMNS,
    "drug_inventory": INVENTORY_COLUMNS,
}

# Visits in these states are historical or void. Running a medication check
# against one would produce an alert nobody can act on.
_CLOSED_VISIT_STATES: frozenset[str] = frozenset({"cancelled", "canceled", "void"})

VISIT_SQL = text(
    "SELECT visit_id, patient_id, status, visit_date FROM visits WHERE visit_id = :visit_id"
)

PATIENT_SQL = text(
    "SELECT id, patient_number, date_of_birth, gender, allergies FROM patients WHERE id = :patient_id"
)

PRESCRIPTION_SQL = text(
    "SELECT p.prescription_id, p.status, "
    "i.prescription_item_id, i.drug_name, i.dose, i.frequency, i.status AS item_status "
    "FROM pharmacy_prescriptions p "
    "JOIN pharmacy_prescription_items i ON i.prescription_id = p.prescription_id "
    "WHERE p.visit_id = :visit_id"
)

INVENTORY_SQL = text(
    "SELECT inventory_id, drug_name, brand_name, drug_code, category, unit "
    "FROM drug_inventory WHERE is_active = true"
)


class VisitAccessError(Exception):
    """The visit is not reachable for this caller. Carries no database detail."""


@dataclass(frozen=True)
class VisitContext:
    """The allowlisted slice of a visit a medication check is allowed to see."""

    visit_id: UUID
    patient_id: UUID
    visit_status: str
    patient_number: str | None
    # None means an allergy history was never recorded, which is not the same as
    # a recorded history with nothing in it. The engine treats them differently.
    allergies: list[str] | None
    prescribed: list[MedicineInput] = field(default_factory=list)


def parse_allergies(raw: str | None) -> list[str] | None:
    """Split a recorded allergy string into terms, or report that none exist."""
    if raw is None:
        return None
    text_value = str(raw).strip()
    if not text_value:
        return None
    terms = [normalize_text(part) for part in text_value.split(",")]
    return [term for term in terms if term]


async def load_visit_context(
    db: AsyncSession, visit_id: UUID, max_medicines: int
) -> VisitContext:
    """Load one visit, its patient's allergy history, and its prescribed items.

    Raises VisitAccessError when the visit is not in this tenant's database or
    is in a state a check cannot act on. The message is deliberately identical
    in both cases so a caller cannot use the difference to discover whether a
    visit id exists in some other hospital.
    """
    visit_row = (await db.execute(VISIT_SQL, {"visit_id": str(visit_id)})).mappings().first()
    if visit_row is None:
        raise VisitAccessError("visit not available")

    visit_status = str(visit_row["status"] or "").strip().lower()
    if visit_status in _CLOSED_VISIT_STATES:
        raise VisitAccessError("visit not available")

    patient_row = (
        await db.execute(PATIENT_SQL, {"patient_id": str(visit_row["patient_id"])})
    ).mappings().first()
    if patient_row is None:
        raise VisitAccessError("visit not available")

    # PostgreSQL hands back UUID objects and other drivers hand back strings.
    # Normalizing here keeps the identifier a UUID everywhere downstream,
    # including in the audit row it is written to.
    try:
        patient_id = UUID(str(visit_row["patient_id"]))
    except (ValueError, AttributeError, TypeError) as exc:
        raise VisitAccessError("visit not available") from exc

    rows = (await db.execute(PRESCRIPTION_SQL, {"visit_id": str(visit_id)})).mappings().all()

    prescribed: list[MedicineInput] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        name = (row["drug_name"] or "").strip()
        if not name:
            continue
        dose = row["dose"] or None
        key = (name.lower(), str(dose or ""))
        if key in seen:
            continue
        seen.add(key)
        # Route is deliberately left unset: the migrated prescription schema
        # records none, and inventing one would be inventing a clinical input.
        # The engine turns the gap into needs_review, which is the honest
        # outcome until route is captured in the prescribing workflow.
        prescribed.append(
            MedicineInput(
                display_name=name[:200],
                dose=str(dose)[:100] if dose else None,
            )
        )
        if len(prescribed) >= max_medicines:
            break

    return VisitContext(
        visit_id=visit_id,
        patient_id=patient_id,
        visit_status=visit_status,
        patient_number=patient_row["patient_number"],
        allergies=parse_allergies(patient_row["allergies"]),
        prescribed=prescribed,
    )


async def load_terminology(db: AsyncSession) -> InventoryTerminology:
    """Build the offline normalizer from this tenant's own formulary."""
    rows = (await db.execute(INVENTORY_SQL)).mappings().all()

    entries: list[TerminologyEntry] = []
    for row in rows:
        name = (row["drug_name"] or "").strip()
        if not name:
            continue
        brand = (row["brand_name"] or "").strip()
        code = (row["drug_code"] or "").strip()
        aliases = tuple(alias for alias in (brand,) if alias)
        entries.append(
            TerminologyEntry(
                # The canonical key is the hospital's own code where it has one,
                # so two inventory rows for the same product collapse to one.
                canonical_key=(code or normalize_text(name))[:200],
                canonical_name=name[:200],
                ingredient_key=normalize_text(name)[:200] or None,
                therapeutic_class=((row["category"] or "").strip() or None),
                form=None,
                aliases=aliases,
            )
        )

    return InventoryTerminology(entries)
