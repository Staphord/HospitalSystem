"""The assistant has one switch.

These tests used to assert the opposite: seven independently gated capabilities,
each shipping disabled behind its own environment variable. That was there to
stage a phased rollout, and it outlived it. The combination it allowed - the
assistant on but voice, history or medicines off - reached staff as a launcher
whose buttons answered 404 and a chat that forgot everything, neither of which
they could tell from a bug.

What replaced it: ASSISTANT_OPERATIONAL_CHAT_ENABLED is the only assistant
switch left in the environment, and it decides whether the assistant exists at
all. Who reaches which part of it is a role question, tested in
test_assistant_permissions.py, and is not affected by any of this.
"""

import pytest

from app.assistant.flags import (
    AssistantCapability,
    enabled_capabilities,
    is_assistant_enabled,
    is_capability_enabled,
)
from app.core.config import settings


@pytest.fixture
def assistant_on(monkeypatch):
    monkeypatch.setattr(settings, "assistant_operational_chat_enabled", True)


class TestTheAssistantShipsDisabled:
    def test_the_switch_is_off_by_default(self):
        assert settings.assistant_operational_chat_enabled is False
        assert is_assistant_enabled() is False

    @pytest.mark.parametrize("capability", list(AssistantCapability))
    def test_every_capability_ships_disabled(self, capability):
        assert is_capability_enabled(capability) is False

    def test_no_capability_is_enabled_by_default(self):
        assert enabled_capabilities() == []


class TestTheSwitchGovernsEveryCapability:
    @pytest.mark.parametrize("capability", list(AssistantCapability))
    def test_the_switch_on_enables_every_capability(self, capability, assistant_on):
        assert is_capability_enabled(capability) is True

    def test_everything_is_listed_once_it_is_on(self, assistant_on):
        assert set(enabled_capabilities()) == set(AssistantCapability)

    def test_it_can_be_switched_off_again(self, monkeypatch):
        monkeypatch.setattr(settings, "assistant_operational_chat_enabled", True)
        assert is_capability_enabled(AssistantCapability.VOICE) is True

        monkeypatch.setattr(settings, "assistant_operational_chat_enabled", False)
        assert is_capability_enabled(AssistantCapability.VOICE) is False

    def test_no_capability_can_be_reached_while_the_switch_is_off(self):
        # The point of the single switch: nothing is reachable behind an
        # operator's back, whatever else is configured.
        assert not any(is_capability_enabled(c) for c in AssistantCapability)


class TestFlagsFailClosed:
    def test_unknown_capability_is_disabled(self, assistant_on):
        # Refused even with the assistant on, so a typo disables rather than
        # enables.
        assert is_capability_enabled("not_a_capability") is False

    def test_none_capability_is_disabled(self, assistant_on):
        assert is_capability_enabled(None) is False

    def test_a_missing_setting_is_disabled(self, monkeypatch):
        monkeypatch.delattr(
            type(settings), "assistant_operational_chat_enabled", raising=False
        )
        monkeypatch.setattr(
            "app.assistant.flags.settings", object(), raising=True
        )
        assert is_assistant_enabled() is False
        assert is_capability_enabled(AssistantCapability.OPERATIONAL_CHAT) is False
