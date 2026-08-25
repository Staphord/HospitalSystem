import pytest

from app.assistant.flags import AssistantCapability
from app.assistant.permissions import (
    CLINICAL_CAPABILITIES,
    TENANT_STAFF_ROLES,
    is_clinical_capability,
    is_role_allowed,
    normalize_role,
    normalize_roles,
)


class TestRoleNormalization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Hospital Admin", "hospital_admin"),
            ("  DOCTOR ", "doctor"),
            ("triage-nurse", "triage_nurse"),
            ("Ward  Nurse", "ward_nurse"),
            ("lab_technician", "lab_technician"),
        ],
    )
    def test_role_strings_normalize_to_canonical_slugs(self, raw, expected):
        assert normalize_role(raw) == expected

    def test_empty_and_invalid_roles_normalize_to_nothing(self):
        assert normalize_role(None) == ""
        assert normalize_role("") == ""
        assert normalize_roles(None) == set()
        assert normalize_roles(["", None]) == set()

    def test_there_is_no_generic_nurse_role(self):
        assert "nurse" not in TENANT_STAFF_ROLES
        assert "triage_nurse" in TENANT_STAFF_ROLES
        assert "ward_nurse" in TENANT_STAFF_ROLES


class TestOperationalCapabilityAccess:
    @pytest.mark.parametrize(
        "role",
        [
            "hospital_admin",
            "doctor",
            "triage_nurse",
            "ward_nurse",
            "pharmacist",
            "receptionist",
            "lab_technician",
            "radiographer",
            "cashier",
        ],
    )
    def test_tenant_staff_may_use_operational_chat(self, role):
        assert is_role_allowed(AssistantCapability.OPERATIONAL_CHAT, [role]) is True

    def test_unknown_role_is_denied(self):
        assert is_role_allowed(AssistantCapability.OPERATIONAL_CHAT, ["intruder"]) is False

    def test_caller_with_no_roles_is_denied(self):
        assert is_role_allowed(AssistantCapability.OPERATIONAL_CHAT, []) is False
        assert is_role_allowed(AssistantCapability.OPERATIONAL_CHAT, None) is False

    def test_role_matching_survives_display_formatting(self):
        assert is_role_allowed(AssistantCapability.OPERATIONAL_CHAT, ["Ward Nurse"]) is True


class TestSuperAdminIsDeniedTenantAssistantAccess:
    @pytest.mark.parametrize("capability", list(AssistantCapability))
    def test_super_admin_role_is_denied_every_capability(self, capability):
        assert is_role_allowed(capability, ["super_admin"]) is False

    @pytest.mark.parametrize("capability", list(AssistantCapability))
    def test_super_admin_flag_is_denied_every_capability(self, capability):
        assert is_role_allowed(capability, ["doctor"], is_super_admin=True) is False

    def test_super_admin_cannot_escalate_by_also_holding_a_tenant_role(self):
        assert (
            is_role_allowed(
                AssistantCapability.OPERATIONAL_CHAT, ["super_admin", "doctor"]
            )
            is False
        )


class TestAdminAccessDoesNotImplyClinicalAccess:
    @pytest.mark.parametrize("capability", sorted(CLINICAL_CAPABILITIES, key=str))
    def test_hospital_admin_is_denied_clinical_capabilities(self, capability):
        assert is_role_allowed(capability, ["hospital_admin"]) is False

    def test_medication_check_is_limited_to_doctor_and_pharmacist(self):
        assert is_role_allowed(AssistantCapability.MEDICATION_CHECK, ["doctor"]) is True
        assert is_role_allowed(AssistantCapability.MEDICATION_CHECK, ["pharmacist"]) is True
        for denied in ("hospital_admin", "receptionist", "cashier", "ward_nurse"):
            assert is_role_allowed(AssistantCapability.MEDICATION_CHECK, [denied]) is False

    def test_differential_support_is_limited_to_doctors(self):
        assert (
            is_role_allowed(AssistantCapability.DIFFERENTIAL_SUPPORT, ["doctor"]) is True
        )
        for denied in ("pharmacist", "hospital_admin", "triage_nurse", "ward_nurse"):
            assert (
                is_role_allowed(AssistantCapability.DIFFERENTIAL_SUPPORT, [denied]) is False
            )

    def test_clinical_capabilities_are_labelled_as_clinical(self):
        assert is_clinical_capability(AssistantCapability.MEDICATION_CHECK) is True
        assert is_clinical_capability(AssistantCapability.DIFFERENTIAL_SUPPORT) is True
        assert is_clinical_capability(AssistantCapability.OPERATIONAL_CHAT) is False
        assert is_clinical_capability("not_a_capability") is False


class TestPermissionsFailClosed:
    def test_unknown_capability_is_denied(self):
        assert is_role_allowed("not_a_capability", ["doctor"]) is False

    def test_none_capability_is_denied(self):
        assert is_role_allowed(None, ["doctor"]) is False
