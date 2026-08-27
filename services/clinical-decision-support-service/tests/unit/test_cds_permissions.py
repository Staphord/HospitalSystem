"""Who may reach a clinical result.

The load-bearing assertion in this file is that administrative privilege is not
clinical privilege. A hospital admin can create the pharmacist's account and
still must not read the pharmacist's alerts.
"""

import pytest

from app.cds.flags import CdsCapability
from app.cds.permissions import (
    MEDICATION_CHECK_ROLES,
    clinical_role_of,
    is_role_allowed,
    may_override,
    normalize_role,
    normalize_roles,
)

MEDICATION_CHECK = CdsCapability.MEDICATION_CHECK


@pytest.mark.parametrize("role", ["doctor", "pharmacist"])
def test_clinical_roles_are_allowed(role):
    assert is_role_allowed(MEDICATION_CHECK, [role]) is True


@pytest.mark.parametrize(
    "role",
    [
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
def test_non_clinical_roles_are_denied(role):
    assert is_role_allowed(MEDICATION_CHECK, [role]) is False


def test_hospital_admin_is_denied_even_alongside_other_roles():
    # Seniority is not competence. An admin who also holds receptionist still
    # has no clinical role and must not reach a medication result.
    assert is_role_allowed(MEDICATION_CHECK, ["hospital_admin", "receptionist"]) is False


def test_super_admin_is_denied_even_when_it_also_holds_doctor():
    # A platform super admin administers tenants and must never read tenant
    # clinical data, whatever else the token happens to carry.
    assert is_role_allowed(MEDICATION_CHECK, ["super_admin", "doctor"]) is False
    assert is_role_allowed(MEDICATION_CHECK, ["doctor"], is_super_admin=True) is False


def test_differential_support_is_reachable_by_nobody_in_this_phase():
    for role in ("doctor", "pharmacist", "hospital_admin"):
        assert is_role_allowed(CdsCapability.DIFFERENTIAL_SUPPORT, [role]) is False


def test_no_roles_at_all_is_denied():
    assert is_role_allowed(MEDICATION_CHECK, None) is False
    assert is_role_allowed(MEDICATION_CHECK, []) is False


def test_unknown_capability_is_denied():
    assert is_role_allowed("not_a_capability", ["doctor"]) is False


def test_role_slugs_are_normalized_the_same_way_the_frontend_writes_them():
    assert normalize_role("  Triage-Nurse ") == "triage_nurse"
    assert normalize_role("WARD NURSE") == "ward_nurse"
    assert normalize_role(None) == ""
    assert normalize_roles(["Doctor", "  "]) == {"doctor"}


def test_override_is_limited_to_clinical_roles():
    assert may_override(["doctor"]) is True
    assert may_override(["pharmacist"]) is True
    assert may_override(["hospital_admin"]) is False
    assert may_override(["doctor"], is_super_admin=True) is False


def test_clinical_role_recorded_for_audit_is_the_clinical_one():
    assert clinical_role_of(["hospital_admin", "doctor"]) == "doctor"
    assert clinical_role_of(["pharmacist"]) == "pharmacist"
    assert clinical_role_of(["receptionist"]) == ""


def test_the_allowed_role_set_is_exactly_these_two():
    # A test that fails whenever the set widens. Adding a role here is a
    # clinical governance decision, not a code change.
    assert set(MEDICATION_CHECK_ROLES) == {"doctor", "pharmacist"}
