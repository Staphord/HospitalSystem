"""The two boundaries that fail silently if nobody guards them.

**The data allowlist.** Every column this service may read is named explicitly.
These tests fail the moment that set changes, which is the point: a deny-list
would start exposing any column a future developer adds to `patients` for an
unrelated feature, and nobody would notice.

**The absence of a language model.** Phase 5's rule is that a model plays no
part in deciding whether medicines interact. The strongest available check is
that no vendor client, HTTP client, or provider module is reachable from the
clinical path at all.
"""

import ast
import re
from pathlib import Path

import pytest

from app.cds import access

CDS_PACKAGE = Path(access.__file__).resolve().parent

# The exact, complete set of columns this service reads, per table. Changing
# any of these sets is a deliberate widening of what leaves the database and
# has to be reviewed.
EXPECTED_ALLOWLISTS = {
    "visits": {"visit_id", "patient_id", "status", "visit_date"},
    "patients": {"id", "patient_number", "date_of_birth", "gender", "allergies"},
    "pharmacy_prescriptions": {"prescription_id", "visit_id", "status"},
    "pharmacy_prescription_items": {
        "prescription_item_id",
        "prescription_id",
        "drug_name",
        "dose",
        "frequency",
        "status",
    },
    "drug_inventory": {
        "inventory_id",
        "drug_name",
        "brand_name",
        "drug_code",
        "category",
        "unit",
    },
}


@pytest.mark.parametrize("table", sorted(EXPECTED_ALLOWLISTS))
def test_the_allowlist_for_each_table_is_exactly_what_was_approved(table):
    assert set(access.ALLOWLISTS[table]) == EXPECTED_ALLOWLISTS[table]


def test_no_table_is_read_that_was_not_approved():
    assert set(access.ALLOWLISTS) == set(EXPECTED_ALLOWLISTS)


def test_the_patient_name_is_not_readable():
    # A medication check does not need to know whose visit it is. The clinician
    # already chose the visit.
    assert "full_name" not in access.PATIENT_COLUMNS
    assert "phone" not in access.PATIENT_COLUMNS
    assert "address" not in access.PATIENT_COLUMNS


@pytest.mark.parametrize(
    "statement",
    [access.VISIT_SQL, access.PATIENT_SQL, access.PRESCRIPTION_SQL, access.INVENTORY_SQL],
)
def test_no_query_selects_everything(statement):
    # SELECT * is how a deny-list gets in through the back door.
    assert "*" not in str(statement)


def _selected_columns(statement) -> set[str]:
    sql = re.sub(r"\s+", " ", str(statement))
    select_clause = sql.split(" FROM ")[0].replace("SELECT ", "")
    columns = set()
    for part in select_clause.split(","):
        part = part.strip()
        if " AS " in part.upper():
            part = re.split(" AS ", part, flags=re.IGNORECASE)[0].strip()
        columns.add(part.split(".")[-1].strip())
    return columns


def test_every_selected_column_appears_in_an_allowlist():
    approved = set().union(*EXPECTED_ALLOWLISTS.values())
    for statement in (
        access.VISIT_SQL,
        access.PATIENT_SQL,
        access.PRESCRIPTION_SQL,
        access.INVENTORY_SQL,
    ):
        assert _selected_columns(statement) <= approved


def test_no_query_accepts_a_tenant_or_a_database_as_a_parameter():
    # The tenant arrives with the session, resolved from the token. There is no
    # argument a caller could tamper with to reach another hospital.
    for statement in (
        access.VISIT_SQL,
        access.PATIENT_SQL,
        access.PRESCRIPTION_SQL,
        access.INVENTORY_SQL,
    ):
        sql = str(statement).lower()
        for forbidden in (":tenant", ":database", ":db", ":dsn", ":schema"):
            assert forbidden not in sql


def test_an_unrecorded_allergy_history_is_distinguished_from_an_empty_one():
    assert access.parse_allergies(None) is None
    assert access.parse_allergies("") is None
    assert access.parse_allergies("   ") is None
    assert access.parse_allergies("Penicillin, Latex") == ["penicillin", "latex"]


# No language model anywhere in the clinical path


VENDOR_MARKERS = (
    "groq",
    "openai",
    "anthropic",
    "llm",
    "completion",
    "chat_completion",
    "system_prompt",
)

MODULES_UNDER_TEST = sorted(
    path for path in CDS_PACKAGE.glob("*.py") if path.name != "__init__.py"
)


@pytest.mark.parametrize("module", MODULES_UNDER_TEST, ids=lambda p: p.name)
def test_no_cds_module_mentions_a_model_vendor(module):
    source = module.read_text(encoding="utf-8").lower()
    # Strip comments and docstrings: this file's own prose explains why a model
    # is absent, and that prose must not fail its own test.
    tree = ast.parse(module.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            source = source.replace(node.value.lower(), "")
    source = "\n".join(
        line.split("#")[0] for line in source.splitlines()
    )

    for marker in VENDOR_MARKERS:
        assert marker not in source, f"{module.name} references {marker}"


@pytest.mark.parametrize("module", MODULES_UNDER_TEST, ids=lambda p: p.name)
def test_no_cds_module_imports_an_outbound_http_client(module):
    tree = ast.parse(module.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    # The offline normalizer is offline. Nothing in the clinical path may open a
    # socket to a vendor, a terminology service, or anything else.
    for forbidden in ("httpx", "requests", "aiohttp", "urllib", "socket", "openai", "groq"):
        assert forbidden not in imported, f"{module.name} imports {forbidden}"


def test_sql_echo_is_off_so_bound_parameters_never_reach_the_log():
    # Echo logs every statement with its bound parameters, which here means
    # visit and patient identifiers and the patient's recorded allergy text.
    # The other services enable it in dev; a clinical service must not.
    from app.db import tenant

    source = Path(tenant.__file__).read_text(encoding="utf-8")
    assert "echo=False" in source
    assert 'echo=settings.environment == "dev"' not in source
