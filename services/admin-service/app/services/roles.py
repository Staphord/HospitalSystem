"""Canonical hospital roles and Keycloak role stacks (SRS §1.5 / FR-53/54)."""

from __future__ import annotations

# Roles that must always exist and cannot be deleted/renamed via admin API.
SYSTEM_ROLES: frozenset[str] = frozenset(
    {
        "hospital_admin",
        "hospital_user",
        "receptionist",
        "triage_nurse",
        "nurse",
        "clinician",
        "doctor",
        "lab_technician",
        "radiographer",
        "pharmacist",
        "cashier",
        "patient",
    }
)

# Forbidden to assign via hospital admin APIs.
FORBIDDEN_ASSIGNABLE_ROLES: frozenset[str] = frozenset({"super_admin"})

# Default module access matrix seed (role_name -> modules).
DEFAULT_ROLE_MODULES: dict[str, list[str]] = {
    "hospital_admin": [
        "admin",
        "reception",
        "triage",
        "consultation",
        "laboratory",
        "radiology",
        "pharmacy",
        "billing",
        "ward",
        "reports",
    ],
    "hospital_user": [],
    "receptionist": ["reception"],
    "triage_nurse": ["triage"],
    "nurse": ["triage", "ward"],
    "clinician": ["consultation"],
    "doctor": ["consultation", "ward", "laboratory", "radiology"],
    "lab_technician": ["laboratory"],
    "radiographer": ["radiology"],
    "pharmacist": ["pharmacy"],
    "cashier": ["billing"],
    "patient": [],
}

DEFAULT_ROLE_ACTIONS: dict[str, list[str]] = {
    "hospital_admin": ["create", "read", "update", "delete", "configure", "report", "backup"],
    "hospital_user": ["read"],
    "receptionist": ["create", "read", "update"],
    "triage_nurse": ["create", "read", "update"],
    "nurse": ["create", "read", "update"],
    "clinician": ["create", "read", "update"],
    "doctor": ["create", "read", "update"],
    "lab_technician": ["create", "read", "update"],
    "radiographer": ["create", "read", "update"],
    "pharmacist": ["create", "read", "update"],
    "cashier": ["create", "read", "update"],
    "patient": ["read"],
}


def keycloak_roles_for(primary_role: str) -> list[str]:
    """Return Keycloak realm roles to attach for a primary role."""
    if primary_role == "hospital_admin":
        return ["hospital_admin", "hospital_user"]
    if primary_role == "hospital_user":
        return ["hospital_user"]
    if primary_role == "patient":
        return ["patient", "hospital_user"]
    # Clinical and custom roles always include hospital_user base.
    return [primary_role, "hospital_user"]


def validate_assignable_role(role: str) -> None:
    from fastapi import HTTPException, status

    if role in FORBIDDEN_ASSIGNABLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Role '{role}' cannot be assigned by hospital admin",
        )
