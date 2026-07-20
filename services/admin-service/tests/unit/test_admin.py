"""Unit tests for admin-service business rules."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.user import User
from app.services.admin import count_active_hospital_admins, ensure_not_last_admin
from app.services.roles import SYSTEM_ROLES, keycloak_roles_for, validate_assignable_role


def test_keycloak_roles_for_clinical():
    assert keycloak_roles_for("receptionist") == ["receptionist", "hospital_user"]
    assert keycloak_roles_for("lab_technician") == ["lab_technician", "hospital_user"]
    assert keycloak_roles_for("hospital_admin") == ["hospital_admin", "hospital_user"]


def test_system_roles_include_srs_set():
    for role in (
        "receptionist",
        "triage_nurse",
        "lab_technician",
        "radiographer",
        "pharmacist",
        "cashier",
    ):
        assert role in SYSTEM_ROLES


def test_validate_assignable_role_rejects_super_admin():
    with pytest.raises(HTTPException) as exc:
        validate_assignable_role("super_admin")
    assert exc.value.status_code == 400


def test_count_active_hospital_admins():
    db = MagicMock()
    q = MagicMock()
    db.query.return_value = q
    q.filter.return_value = q
    q.count.return_value = 2
    assert count_active_hospital_admins(db, "hosp-1") == 2


def test_ensure_not_last_admin_blocks_demote():
    db = MagicMock()
    user = User(
        keycloak_sub="sub-1",
        role="hospital_admin",
        hospital_id="hosp-1",
        is_active=True,
    )
    with patch("app.services.admin.count_active_hospital_admins", return_value=0):
        with pytest.raises(HTTPException) as exc:
            ensure_not_last_admin(db, "hosp-1", user, demoting=True)
        assert exc.value.status_code == 400


def test_ensure_not_last_admin_allows_when_others_exist():
    db = MagicMock()
    user = User(
        keycloak_sub="sub-1",
        role="hospital_admin",
        hospital_id="hosp-1",
        is_active=True,
    )
    with patch("app.services.admin.count_active_hospital_admins", return_value=1):
        ensure_not_last_admin(db, "hosp-1", user, demoting=True)
