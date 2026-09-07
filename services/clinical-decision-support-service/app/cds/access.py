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

Patient allergy text and prescribed medicine names are treated as untrusted data
throughout. They are rendered into a labelled data block for the clinician and
the model to read, and are never interpreted as instructions.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import datetime as _datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("cds.access")

# The complete set of columns this service may read, per table. Minimum
# necessary: enough to describe the clinical picture of one visit, and nothing
# more. Patient name is deliberately absent — the clinician already chose the
# visit, and a differential suggestion does not need to know whose it is.
VISIT_COLUMNS: frozenset[str] = frozenset({"visit_id", "patient_id", "status", "visit_date"})
PATIENT_COLUMNS: frozenset[str] = frozenset(
    {"id", "patient_number", "date_of_birth", "gender", "allergies"}
)
# What the patient is currently on, as context for a differential. Names only:
# this service does not check interactions and has no use for dose or route.
PRESCRIPTION_COLUMNS: frozenset[str] = frozenset({"prescription_id", "visit_id", "status"})
PRESCRIPTION_ITEM_COLUMNS: frozenset[str] = frozenset(
    {"prescription_item_id", "prescription_id", "drug_name", "status"}
)
CONSULTATION_PRESCRIPTION_COLUMNS: frozenset[str] = frozenset(
    {"id", "visit_id", "drug_name", "status"}
)
# Vitals, with the time they were taken so a clinician can see how fresh they
# are. The free-text presenting complaint, triage notes, and triage category on
# this table are deliberately excluded: the clinician states the complaint in the
# request, and pulling in another service's free text would widen both what
# reaches the model and what has to be reviewed, for no gain.
TRIAGE_COLUMNS: frozenset[str] = frozenset(
    {
        "visit_id",
        "blood_pressure",
        "temperature",
        "pulse",
        "oxygen_saturation",
        "respiratory_rate",
        "weight",
        "created_at",
    }
)

ALLOWLISTS: dict[str, frozenset[str]] = {
    "visits": VISIT_COLUMNS,
    "patients": PATIENT_COLUMNS,
    "pharmacy_prescriptions": PRESCRIPTION_COLUMNS,
    "pharmacy_prescription_items": PRESCRIPTION_ITEM_COLUMNS,
    "prescriptions": CONSULTATION_PRESCRIPTION_COLUMNS,
    "triage_assessments": TRIAGE_COLUMNS,
}

# Visits in these states are historical or void. Producing considerations for
# one would produce advice nobody can act on.
_CLOSED_VISIT_STATES: frozenset[str] = frozenset({"cancelled", "canceled", "void"})

VISIT_SQL = text(
    "SELECT visit_id, patient_id, status, visit_date FROM visits WHERE visit_id = :visit_id"
)

PATIENT_SQL = text(
    "SELECT id, patient_number, date_of_birth, gender, allergies FROM patients WHERE id = :patient_id"
)

PRESCRIPTION_SQL = text(
    "SELECT p.prescription_id, p.status, "
    "i.prescription_item_id, i.drug_name, i.status AS item_status "
    "FROM pharmacy_prescriptions p "
    "JOIN pharmacy_prescription_items i ON i.prescription_id = p.prescription_id "
    "WHERE p.visit_id = :visit_id"
)

CONSULTATION_PRESCRIPTION_SQL = text(
    "SELECT id, drug_name, status FROM prescriptions WHERE visit_id = :visit_id"
)

TRIAGE_SQL = text(
    "SELECT visit_id, blood_pressure, temperature, pulse, oxygen_saturation, "
    "respiratory_rate, weight, created_at "
    "FROM triage_assessments WHERE visit_id = :visit_id"
)


class VisitAccessError(Exception):
    """The visit is not reachable for this caller. Carries no database detail."""


def normalize_text(value: str | None) -> str:
    """Lowercase and collapse whitespace. Used for comparing recorded terms."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def parse_allergies(raw: str | None) -> list[str] | None:
    """Split a recorded allergy string into terms, or report that none exist.

    None means an allergy history was never taken, which is not the same as a
    recorded history with nothing in it. The caller reports the two differently.
    """
    if raw is None:
        return None
    text_value = str(raw).strip()
    if not text_value:
        return None
    terms = [normalize_text(part) for part in text_value.split(",")]
    return [term for term in terms if term]


def _as_datetime(value) -> _datetime | None:
    """Normalize a timestamp column to a datetime.

    PostgreSQL hands back a datetime and other drivers hand back a string.
    Normalizing here means a clinician sees "recorded at" rather than a blank
    wherever the service runs.
    """
    if value is None:
        return None
    if isinstance(value, _datetime):
        return value
    try:
        return _datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _age_years(date_of_birth) -> int | None:
    """Age in whole years, or None when no date of birth is recorded."""
    if not date_of_birth:
        return None
    try:
        born = (
            date_of_birth
            if isinstance(date_of_birth, _date)
            else _date.fromisoformat(str(date_of_birth)[:10])
        )
    except (ValueError, TypeError):
        return None
    today = _date.today()
    years = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return years if 0 <= years < 150 else None


@dataclass(frozen=True)
class DifferentialContext:
    """The allowlisted clinical context one differential request may see.

    Everything here is retrieved server-side from the visit the caller was
    already authorized for. Nothing in it comes from the browser, and each
    retrieved value carries when it was recorded so a clinician reviewing the
    suggestion can see how stale its inputs were.
    """

    visit_id: UUID
    patient_id: UUID
    visit_status: str
    age_years: int | None
    gender: str | None
    # None means an allergy history was never recorded, which is not the same as
    # a recorded history with nothing in it.
    allergies: list[str] | None
    current_medicines: list[str] = field(default_factory=list)
    vitals: list[tuple[str, str, object]] = field(default_factory=list)
    # True when part of the record could not be read. The suggestion must not
    # treat an unreadable record as an empty one.
    sources_incomplete: bool = False


async def load_differential_context(
    db: AsyncSession, visit_id: UUID, max_medicines: int
) -> DifferentialContext:
    """Load one visit, its patient, its current medicines, and its vitals.

    Raises VisitAccessError when the visit is not in this tenant's database or
    is in a state a suggestion cannot act on. The message is deliberately
    identical in both cases so a caller cannot use the difference to discover
    whether a visit id exists in some other hospital.
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

    medicines: list[str] = []
    seen: set[str] = set()
    sources_incomplete = False

    def _add(name: str | None) -> bool:
        """Add one medicine name. Returns False once the cap is reached."""
        if len(medicines) >= max_medicines:
            return False
        cleaned = (name or "").strip()
        if not cleaned:
            return True
        key = cleaned.lower()
        if key in seen:
            return True
        seen.add(key)
        medicines.append(cleaned[:200])
        return True

    # The prescriber's own record first, then the pharmacy view, so an item that
    # reached dispensing without a matching consultation row is still listed.
    for sql in (CONSULTATION_PRESCRIPTION_SQL, PRESCRIPTION_SQL):
        try:
            rows = (await db.execute(sql, {"visit_id": str(visit_id)})).mappings().all()
        except Exception:
            # Saying "nothing was prescribed" when the record could not be read
            # would be a false reassurance, so the gap is carried forward and
            # reported as a limitation on the result.
            logger.exception("cds prescription read failed")
            sources_incomplete = True
            continue
        for row in rows:
            if not _add(row["drug_name"]):
                break

    vitals: list[tuple[str, str, object]] = []
    try:
        triage_row = (
            await db.execute(TRIAGE_SQL, {"visit_id": str(visit_id)})
        ).mappings().first()
    except Exception:
        # No vitals is a fact a clinician can act on; unreadable vitals is not.
        logger.exception("cds triage vitals read failed")
        triage_row = None
        sources_incomplete = True

    if triage_row is not None:
        recorded_at = _as_datetime(triage_row["created_at"])
        for label, key, unit in (
            ("Blood pressure", "blood_pressure", ""),
            ("Temperature", "temperature", " C"),
            ("Pulse", "pulse", " bpm"),
            ("Oxygen saturation", "oxygen_saturation", " %"),
            ("Respiratory rate", "respiratory_rate", " /min"),
            ("Weight", "weight", " kg"),
        ):
            value = triage_row[key]
            if value is None or str(value).strip() == "":
                continue
            vitals.append((label, f"{value}{unit}", recorded_at))

    return DifferentialContext(
        visit_id=visit_id,
        patient_id=patient_id,
        visit_status=visit_status,
        age_years=_age_years(patient_row["date_of_birth"]),
        gender=(
            (str(patient_row["gender"]).strip() or None) if patient_row["gender"] else None
        ),
        allergies=parse_allergies(patient_row["allergies"]),
        current_medicines=medicines,
        vitals=vitals,
        sources_incomplete=sources_incomplete,
    )
