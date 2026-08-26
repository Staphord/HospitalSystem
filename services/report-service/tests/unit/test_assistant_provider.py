import inspect

import pytest

from app.assistant import provider as provider_mod
from app.assistant.provider import (
    AssistantProvider,
    AssistantProviderError,
    NullProvider,
    ProviderErrorCode,
    ProviderRequest,
    configured_provider_name,
    describe_configured_provider,
    get_provider,
    is_provider_configured,
)
from app.core.config import settings


class TestProviderBoundary:
    def test_provider_is_the_fail_closed_null_provider_without_a_credential(
        self, monkeypatch
    ):
        # Phase 1 asserted that get_provider() always returns NullProvider,
        # because no transport existed yet. Phase 2 registers the Groq
        # transport there, so the guarantee actually worth protecting is
        # narrower and more important: no credential means no provider, and
        # therefore never a fabricated answer.
        #
        # The assertion is now pinned to an explicit configuration instead of
        # reading whatever the machine's .env happens to hold. As written
        # before, it passed in CI and failed on any developer machine with a
        # key configured.
        #
        # Change authorised by the user on 2026-08-26 and agreed with
        # kakaAllord, owner of commit 0289498.
        monkeypatch.setattr(settings, "assistant_groq_api_key", None, raising=False)
        assert isinstance(get_provider(), NullProvider)

    def test_provider_is_the_fail_closed_null_provider_for_an_unknown_vendor(
        self, monkeypatch
    ):
        monkeypatch.setattr(settings, "assistant_provider", "unknown_vendor", raising=False)
        monkeypatch.setattr(settings, "assistant_groq_api_key", "gsk_x", raising=False)
        assert isinstance(get_provider(), NullProvider)

    def test_null_provider_satisfies_the_provider_interface(self):
        assert isinstance(NullProvider(), AssistantProvider)

    @pytest.mark.asyncio
    async def test_null_provider_never_fabricates_an_answer(self):
        with pytest.raises(AssistantProviderError) as exc:
            await NullProvider().complete(
                ProviderRequest(instructions="be helpful", content="a question")
            )
        assert exc.value.code == ProviderErrorCode.NOT_CONFIGURED

    def test_provider_error_message_is_short_and_safe(self):
        err = AssistantProviderError(ProviderErrorCode.TIMEOUT, "The assistant timed out.")
        assert err.code == ProviderErrorCode.TIMEOUT
        assert "traceback" not in str(err).lower()
        assert len(str(err)) < 200


class TestGroqIsTheRecordedVendor:
    def test_configured_vendor_is_groq(self):
        assert configured_provider_name() == "groq"

    def test_vendor_is_not_configured_without_a_server_side_credential(self, monkeypatch):
        monkeypatch.setattr(settings, "assistant_groq_api_key", None)
        assert is_provider_configured() is False

    def test_vendor_is_configured_once_a_credential_is_present(self, monkeypatch):
        monkeypatch.setattr(settings, "assistant_groq_api_key", "test-key-value")
        assert is_provider_configured() is True

    def test_an_unrecognised_vendor_is_never_treated_as_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "assistant_provider", "some-other-vendor")
        monkeypatch.setattr(settings, "assistant_groq_api_key", "test-key-value")
        assert is_provider_configured() is False


class TestCredentialIsNeverExposed:
    def test_description_reports_presence_but_not_the_credential(self, monkeypatch):
        monkeypatch.setattr(settings, "assistant_groq_api_key", "super-secret-key-value")
        described = describe_configured_provider()

        assert described["credential_present"] == "true"
        assert "super-secret-key-value" not in repr(described)
        for value in described.values():
            assert "super-secret-key-value" not in value

    def test_null_provider_description_carries_no_credential(self):
        described = NullProvider().describe()
        assert set(described) == {"provider", "model_version"}


class TestPhaseOneMakesNoModelCall:
    def test_no_vendor_sdk_or_http_client_is_imported(self):
        source = inspect.getsource(provider_mod)
        for forbidden in ("import httpx", "import requests", "from groq", "import groq"):
            assert forbidden not in source

    def test_module_exposes_no_transport_implementation_yet(self):
        assert not hasattr(provider_mod, "GroqProvider")
