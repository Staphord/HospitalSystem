import pytest

from app.assistant import flags as flags_mod
from app.assistant.flags import (
    AssistantCapability,
    enabled_capabilities,
    is_capability_enabled,
)
from app.core.config import settings


class TestAssistantFlagDefaults:
    @pytest.mark.parametrize("capability", list(AssistantCapability))
    def test_every_capability_ships_disabled(self, capability):
        assert is_capability_enabled(capability) is False

    def test_no_capability_is_enabled_by_default(self):
        assert enabled_capabilities() == []

    def test_clinical_and_realtime_defaults_are_off_in_settings(self):
        assert settings.assistant_operational_chat_enabled is False
        assert settings.assistant_voice_enabled is False
        assert settings.assistant_medication_check_enabled is False
        assert settings.assistant_differential_support_enabled is False
        assert settings.assistant_realtime_voice_enabled is False


class TestAssistantFlagIndependence:
    @pytest.mark.parametrize(
        "capability,attribute",
        [
            (AssistantCapability.OPERATIONAL_CHAT, "assistant_operational_chat_enabled"),
            (AssistantCapability.VOICE, "assistant_voice_enabled"),
            (AssistantCapability.MEDICATION_CHECK, "assistant_medication_check_enabled"),
            (
                AssistantCapability.DIFFERENTIAL_SUPPORT,
                "assistant_differential_support_enabled",
            ),
            (AssistantCapability.REALTIME_VOICE, "assistant_realtime_voice_enabled"),
        ],
    )
    def test_enabling_one_capability_leaves_the_others_off(
        self, capability, attribute, monkeypatch
    ):
        monkeypatch.setattr(settings, attribute, True, raising=False)

        assert is_capability_enabled(capability) is True
        for other in AssistantCapability:
            if other is not capability:
                assert is_capability_enabled(other) is False

    def test_each_capability_can_be_switched_off_again(self, monkeypatch):
        monkeypatch.setattr(settings, "assistant_operational_chat_enabled", True)
        assert is_capability_enabled(AssistantCapability.OPERATIONAL_CHAT) is True
        monkeypatch.setattr(settings, "assistant_operational_chat_enabled", False)
        assert is_capability_enabled(AssistantCapability.OPERATIONAL_CHAT) is False


class TestAssistantFlagsFailClosed:
    def test_unknown_capability_is_disabled(self):
        assert is_capability_enabled("not_a_capability") is False

    def test_missing_setting_is_disabled(self, monkeypatch):
        monkeypatch.setitem(
            flags_mod._FLAG_ATTRIBUTES,
            AssistantCapability.OPERATIONAL_CHAT,
            "setting_that_does_not_exist",
        )
        assert is_capability_enabled(AssistantCapability.OPERATIONAL_CHAT) is False

    def test_none_capability_is_disabled(self):
        assert is_capability_enabled(None) is False
