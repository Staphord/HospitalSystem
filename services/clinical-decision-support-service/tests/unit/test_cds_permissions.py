"""Who may reach a clinical result.

The load-bearing assertion in this file is that administrative privilege is not
clinical privilege. A hospital admin can create the doctor's account and still
must not read the doctor's clinical results.
"""

import pytest

from app.cds.flags import CdsCapability
from app.cds.permissions import (
    DIFFERENTIAL_SUPPORT_ROLES,
    clinical_role_of,
    is_role_allowed,
    normalize_role,
    normalize_roles,
)

DIFFERENTIAL = CdsCapability.DIFFERENTIAL_SUPPORT


def test_a_doctor_is_allowed():
    assert is_role_allowed(DIFFERENTIAL, ["doctor"]) is True


@pytest.mark.parametrize(
    "role",
    [
        "pharmacist",
        "hospital_admin",
        "receptionist",
        "triage_nurse",
        "ward_nurse",
        "lab_technician",
        "radiographer",
        "cashier",
        "hospital_user",
    ],
)
def test_every_other_role_is_denied(role):
    """Narrower than it might look, deliberately.

    Differential support is a clinician's reasoning aid for their own encounter,
    so a pharmacist is denied even though they are clinical staff.
    """
    assert is_role_allowed(DIFFERENTIAL, [role]) is False


def test_hospital_admin_is_denied_even_alongside_other_roles():
    # Seniority is not competence. An admin who also holds receptionist still
    # has no clinical role and must not reach a clinical result.
    assert is_role_allowed(DIFFERENTIAL, ["hospital_admin", "receptionist"]) is False


def test_super_admin_is_denied_even_when_it_also_holds_doctor():
    # A platform super admin administers tenants and must never read tenant
    # clinical data, whatever else the token happens to carry.
    assert is_role_allowed(DIFFERENTIAL, ["super_admin", "doctor"]) is False
    assert is_role_allowed(DIFFERENTIAL, ["doctor"], is_super_admin=True) is False


def test_no_roles_at_all_is_denied():
    assert is_role_allowed(DIFFERENTIAL, None) is False
    assert is_role_allowed(DIFFERENTIAL, []) is False


def test_unknown_capability_is_denied():
    assert is_role_allowed("not_a_capability", ["doctor"]) is False


def test_role_slugs_are_normalized_the_same_way_the_frontend_writes_them():
    assert normalize_role("  Triage-Nurse ") == "triage_nurse"
    assert normalize_role("WARD NURSE") == "ward_nurse"
    assert normalize_role(None) == ""
    assert normalize_roles(["Doctor", "  "]) == {"doctor"}


def test_clinical_role_recorded_for_audit_is_the_clinical_one():
    assert clinical_role_of(["hospital_admin", "doctor"]) == "doctor"
    assert clinical_role_of(["receptionist"]) == ""


def test_the_allowed_role_set_is_exactly_one_role():
    # A test that fails whenever the set widens. Adding a role here is a
    # clinical governance decision, not a code change.
    assert set(DIFFERENTIAL_SUPPORT_ROLES) == {"doctor"}
