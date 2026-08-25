from __future__ import annotations

from collections.abc import Iterable

from app.assistant.flags import AssistantCapability

# Canonical tenant role slugs, aligned with the frontend role helper
# (frontend-hospital/src/lib/roles.ts). There is no generic "nurse" role in this
# system; nursing access is expressed as triage_nurse and ward_nurse.
SUPER_ADMIN = "super_admin"
HOSPITAL_ADMIN = "hospital_admin"
RECEPTIONIST = "receptionist"
TRIAGE_NURSE = "triage_nurse"
WARD_NURSE = "ward_nurse"
DOCTOR = "doctor"
LAB_TECHNICIAN = "lab_technician"
RADIOGRAPHER = "radiographer"
PHARMACIST = "pharmacist"
CASHIER = "cashier"

TENANT_STAFF_ROLES: frozenset[str] = frozenset(
    {
        HOSPITAL_ADMIN,
        RECEPTIONIST,
        TRIAGE_NURSE,
        WARD_NURSE,
        DOCTOR,
        LAB_TECHNICIAN,
        RADIOGRAPHER,
        PHARMACIST,
        CASHIER,
    }
)

# Capabilities that expose clinical judgement rather than operational content.
# Administrative seniority must never imply clinical access, so hospital_admin is
# deliberately absent from every clinical capability below.
CLINICAL_CAPABILITIES: frozenset[AssistantCapability] = frozenset(
    {
        AssistantCapability.MEDICATION_CHECK,
        AssistantCapability.DIFFERENTIAL_SUPPORT,
    }
)

CAPABILITY_ROLES: dict[AssistantCapability, frozenset[str]] = {
    AssistantCapability.OPERATIONAL_CHAT: TENANT_STAFF_ROLES,
    AssistantCapability.VOICE: TENANT_STAFF_ROLES,
    AssistantCapability.MEDICATION_CHECK: frozenset({DOCTOR, PHARMACIST}),
    AssistantCapability.DIFFERENTIAL_SUPPORT: frozenset({DOCTOR}),
    AssistantCapability.REALTIME_VOICE: TENANT_STAFF_ROLES,
}


def normalize_role(role: str | None) -> str:
    """Normalize a role string from a token or profile to its canonical slug."""
    if not role or not isinstance(role, str):
        return ""
    normalized = role.strip().lower()
    for separator in (" ", "-"):
        normalized = normalized.replace(separator, "_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized


def normalize_roles(roles: Iterable[str] | None) -> set[str]:
    """Normalize a collection of role strings, dropping empty values."""
    if not roles:
        return set()
    return {slug for slug in (normalize_role(role) for role in roles) if slug}


def is_role_allowed(
    capability: AssistantCapability | str,
    roles: Iterable[str] | None,
    is_super_admin: bool = False,
) -> bool:
    """Return whether the caller's roles permit an assistant capability.

    Fail-closed. A platform super admin is denied every assistant capability:
    super admins administer tenants and must never read tenant clinical or
    operational content. Roles are taken from the verified token only; a role
    supplied in a request body, a prompt, or a model response is never accepted.
    """
    try:
        capability = AssistantCapability(capability)
    except ValueError:
        return False

    normalized = normalize_roles(roles)

    if is_super_admin or SUPER_ADMIN in normalized:
        return False

    allowed = CAPABILITY_ROLES.get(capability)
    if not allowed:
        return False

    return bool(normalized & allowed)


def is_clinical_capability(capability: AssistantCapability | str) -> bool:
    """Return whether a capability carries clinical risk and clinical gating."""
    try:
        capability = AssistantCapability(capability)
    except ValueError:
        return False
    return capability in CLINICAL_CAPABILITIES
