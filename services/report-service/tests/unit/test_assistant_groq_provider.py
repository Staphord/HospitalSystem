import inspect

import httpx
import pytest

from app.assistant import groq_provider as gp_mod
from app.assistant import provider as provider_mod
from app.assistant.groq_provider import GroqProvider, build_groq_provider
from app.assistant.provider import (
    AssistantProviderError,
    NullProvider,
    ProviderErrorCode,
    ProviderRequest,
    get_provider,
)

API_KEY = "gsk_testkey1234567890abcdef"


def make_provider(handler) -> GroqProvider:
    """Build a provider whose HTTP calls are served by `handler`."""
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    class PatchedClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    gp_mod.httpx.AsyncClient = PatchedClient
    return GroqProvider(API_KEY, "https://api.groq.test/openai/v1", "test-model")


@pytest.fixture(autouse=True)
def restore_httpx():
    original = httpx.AsyncClient
    yield
    gp_mod.httpx.AsyncClient = original


def ok_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "test-model-v2",
            "choices": [
                {"message": {"content": "Open Reception, then Queue."},
                 "finish_reason": "stop"}
            ],
        },
    )


REQUEST = ProviderRequest(instructions="be helpful", content="how do I do X")


class TestSuccessfulCompletion:
    @pytest.mark.asyncio
    async def test_returns_normalised_response(self):
        provider = make_provider(ok_response)
        result = await provider.complete(REQUEST)
        assert result.text == "Open Reception, then Queue."
        assert result.model_version == "test-model-v2"
        assert result.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_credential_is_sent_as_a_bearer_header(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization")
            return ok_response(request)

        provider = make_provider(handler)
        await provider.complete(REQUEST)
        assert seen["auth"] == f"Bearer {API_KEY}"

    def test_describe_never_reveals_the_credential(self):
        provider = GroqProvider(API_KEY, "https://api.groq.test", "test-model")
        described = provider.describe()
        assert API_KEY not in str(described)
        assert described == {"provider": "groq", "model_version": "test-model"}


class TestErrorNormalisation:
    @pytest.mark.asyncio
    async def test_timeout_maps_to_timeout_code(self):
        def handler(request):
            raise httpx.TimeoutException("timed out")

        provider = make_provider(handler)
        with pytest.raises(AssistantProviderError) as exc:
            await provider.complete(REQUEST)
        assert exc.value.code == ProviderErrorCode.TIMEOUT

    @pytest.mark.asyncio
    async def test_transport_error_maps_to_unavailable(self):
        def handler(request):
            raise httpx.ConnectError("no route to host")

        provider = make_provider(handler)
        with pytest.raises(AssistantProviderError) as exc:
            await provider.complete(REQUEST)
        assert exc.value.code == ProviderErrorCode.UNAVAILABLE

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503])
    async def test_error_statuses_map_to_unavailable(self, status):
        def handler(request):
            return httpx.Response(status, json={"error": {"message": "leaky detail"}})

        provider = make_provider(handler)
        with pytest.raises(AssistantProviderError) as exc:
            await provider.complete(REQUEST)
        assert exc.value.code == ProviderErrorCode.UNAVAILABLE
        # The vendor body may echo the prompt, so none of it may surface.
        assert "leaky detail" not in exc.value.message

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            {"choices": []},
            {"choices": [{"message": {}}]},
            {"nothing": "useful"},
            {"choices": [{"message": {"content": "   "}}]},
            {"choices": [{"message": {"content": None}}]},
        ],
    )
    async def test_malformed_output_maps_to_invalid_output(self, payload):
        def handler(request):
            return httpx.Response(200, json=payload)

        provider = make_provider(handler)
        with pytest.raises(AssistantProviderError) as exc:
            await provider.complete(REQUEST)
        assert exc.value.code == ProviderErrorCode.INVALID_OUTPUT

    @pytest.mark.asyncio
    async def test_non_json_body_maps_to_invalid_output(self):
        def handler(request):
            return httpx.Response(200, text="<html>not json</html>")

        provider = make_provider(handler)
        with pytest.raises(AssistantProviderError) as exc:
            await provider.complete(REQUEST)
        assert exc.value.code == ProviderErrorCode.INVALID_OUTPUT

    @pytest.mark.asyncio
    async def test_error_messages_never_carry_the_credential(self):
        def handler(request):
            return httpx.Response(500, json={"error": API_KEY})

        provider = make_provider(handler)
        with pytest.raises(AssistantProviderError) as exc:
            await provider.complete(REQUEST)
        assert API_KEY not in str(exc.value)


class TestProviderSelection:
    def test_without_a_credential_the_null_provider_is_used(self, monkeypatch):
        monkeypatch.setattr(
            provider_mod.settings, "assistant_groq_api_key", None, raising=False
        )
        assert isinstance(get_provider(), NullProvider)

    def test_with_a_credential_the_groq_transport_is_used(self, monkeypatch):
        monkeypatch.setattr(
            provider_mod.settings, "assistant_provider", "groq", raising=False
        )
        monkeypatch.setattr(
            provider_mod.settings, "assistant_groq_api_key", API_KEY, raising=False
        )
        monkeypatch.setattr(
            gp_mod.settings, "assistant_groq_api_key", API_KEY, raising=False
        )
        assert isinstance(get_provider(), GroqProvider)

    def test_an_unknown_vendor_falls_back_to_the_null_provider(self, monkeypatch):
        monkeypatch.setattr(
            provider_mod.settings, "assistant_provider", "someone_else", raising=False
        )
        assert isinstance(get_provider(), NullProvider)

    def test_build_returns_none_without_a_credential(self, monkeypatch):
        monkeypatch.setattr(
            gp_mod.settings, "assistant_groq_api_key", None, raising=False
        )
        assert build_groq_provider() is None

    def test_null_provider_never_fabricates_an_answer(self):
        import asyncio

        with pytest.raises(AssistantProviderError):
            asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
                NullProvider().complete(REQUEST)
            )


class TestVendorIsolation:
    def test_only_the_transport_module_imports_the_vendor(self):
        for module in (
            "app.assistant.service",
            "app.assistant.tools",
            "app.assistant.router",
            "app.assistant.retrieval",
        ):
            imported = __import__(module, fromlist=["*"])
            source = inspect.getsource(imported)
            assert "groq" not in source.lower() or "groq_provider" in source

    def test_the_seam_does_not_import_a_vendor_at_module_level(self):
        source = inspect.getsource(provider_mod)
        header = source.split("def get_provider")[0]
        assert "groq_provider" not in header
