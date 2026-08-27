from __future__ import annotations

from collections.abc import Iterable

from app.cds.flags import CdsCapability

# Canonical tenant role slugs, aligned with frontend-hospital/src/lib/roles.ts.
# There is no generic "nurse" role in this system.
SUPER_ADMIN = "super_admin"
HOSPITAL_ADMIN = "hospital_admin"
DOCTOR = "doctor"
PHARMACIST = "pharmacist"

# Roles allowed to run a medication check. Administrative seniority is not
# clinical competence, so hospital_admin is deliberately absent: an admin can
# create the pharmacist's account but must not read the pharmacist's clinical
# alerts. super_admin is denied everything here as well, because platform super
# admins administer tenants and must never read tenant clinical data.
#
# This is the authority for the /api/v1/cds endpoints. The assistant permission
# matrix in report-service governs a different question — whether the assistant
# surface offers the capability at all — and the two are deliberately separate
# so that switching the assistant on can never widen who may reach a clinical
# result.
MEDICATION_CHECK_ROLES: frozenset[str] = frozenset({DOCTOR, PHARMACIST})

# Overriding a blocking alert is a narrower act than seeing one. Kept as its own
# set so the two can diverge without touching the check path.
MEDICATION_OVERRIDE_ROLES: frozenset[str] = frozenset({DOCTOR, PHARMACIST})

CAPABILITY_ROLES: dict[CdsCapability, frozenset[str]] = {
    CdsCapability.MEDICATION_CHECK: MEDICATION_CHECK_ROLES,
    # Phase 7 decides this. An empty set means nobody, which is the right
    # default for a capability that does not exist yet.
    CdsCapability.DIFFERENTIAL_SUPPORT: frozenset(),
}


def normalize_role(role: str | None) -> str:
    """Normalize a role string from a verified token to its canonical slug."""
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
    capability: CdsCapability | str,
    roles: Iterable[str] | None,
    is_super_admin: bool = False,
) -> bool:
    """Return whether the caller's verified roles permit a CDS capability.

    Roles come from the token only. A role named in a request body, a prompt, a
    transcript, or a model response is never accepted here.
    """
    try:
        capability = CdsCapability(capability)
    except ValueError:
        return False

    normalized = normalize_roles(roles)
    if is_super_admin or SUPER_ADMIN in normalized:
        return False

    allowed = CAPABILITY_ROLES.get(capability, frozenset())
    return bool(normalized & allowed)


def may_override(roles: Iterable[str] | None, is_super_admin: bool = False) -> bool:
    """Return whether the caller may override a blocking medication alert."""
    normalized = normalize_roles(roles)
    if is_super_admin or SUPER_ADMIN in normalized:
        return False
    return bool(normalized & MEDICATION_OVERRIDE_ROLES)


def clinical_role_of(roles: Iterable[str] | None) -> str:
    """Return the caller's clinical role slug for the audit record.

    Records what the actor acted as, not everything they hold. Returns an empty
    string when no clinical role is present, which the caller should already
    have refused.
    """
    normalized = normalize_roles(roles)
    for candidate in (DOCTOR, PHARMACIST):
        if candidate in normalized:
            return candidate
    return ""
