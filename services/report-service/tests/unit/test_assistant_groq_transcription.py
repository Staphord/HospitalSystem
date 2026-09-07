"""The Groq speech-to-text transport.

This is the only module allowed to send audio to a vendor, so these tests hold
the properties that matter at that boundary: the credential never escapes, the
vendor's failures become the small normalised set, no vendor payload reaches a
caller or a log, and the request is built so the engine transcribes what was
said rather than guessing at what it might have been.
"""

import httpx
import pytest

from app.assistant import groq_transcription as gt_mod
from app.assistant.groq_transcription import (
    GroqTranscriptionProvider,
    build_groq_transcription_provider,
)
from app.assistant.transcription import (
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionRequest,
)

API_KEY = "gsk_testkey1234567890abcdef"

REQUEST = TranscriptionRequest(
    audio=b"\x1a\x45\xdf\xa3" + b"\x00" * 1024,
    content_type="audio/webm",
    filename="capture.webm",
)


def make_provider(handler) -> GroqTranscriptionProvider:
    """Build a transport whose HTTP calls are served by `handler`."""
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    class PatchedClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    gt_mod.httpx.AsyncClient = PatchedClient
    return GroqTranscriptionProvider(
        API_KEY, "https://api.groq.test/openai/v1", "whisper-large-v3"
    )


@pytest.fixture(autouse=True)
def restore_httpx():
    original = httpx.AsyncClient
    yield
    gt_mod.httpx.AsyncClient = original


def ok_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "text": " Ninawezaje kusajili mgonjwa mpya? ",
            "language": "swahili",
            "duration": 4.2,
            "model": "whisper-large-v3",
        },
    )


class TestSuccessfulTranscription:
    @pytest.mark.asyncio
    async def test_returns_a_normalised_result(self):
        provider = make_provider(ok_response)
        result = await provider.transcribe(REQUEST)

        assert result.text.strip() == "Ninawezaje kusajili mgonjwa mpya?"
        assert result.language == "swahili"
        assert result.duration_seconds == pytest.approx(4.2)
        assert result.model_version == "whisper-large-v3"

    @pytest.mark.asyncio
    async def test_it_posts_to_the_transcriptions_endpoint(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            return ok_response(request)

        await make_provider(handler).transcribe(REQUEST)
        assert seen["method"] == "POST"
        assert seen["url"].endswith("/audio/transcriptions")

    @pytest.mark.asyncio
    async def test_decoding_is_deterministic_and_unprompted(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.content
            return ok_response(request)

        await make_provider(handler).transcribe(REQUEST)
        body = seen["body"]

        # Temperature zero: a guessing engine is how words nobody said end up in
        # a transcript.
        assert b'name="temperature"' in body
        assert b"\r\n\r\n0\r\n" in body

        # No vocabulary prompt. Priming the engine with a medication or symptom
        # list is a direct route to a transcript naming a drug that was never
        # spoken.
        assert b'name="prompt"' not in body

    @pytest.mark.asyncio
    async def test_a_language_hint_is_forwarded_when_given(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.content
            return ok_response(request)

        request = TranscriptionRequest(
            audio=REQUEST.audio,
            content_type="audio/webm",
            filename="capture.webm",
            language="sw",
        )
        await make_provider(handler).transcribe(request)
        assert b'name="language"' in seen["body"]
        assert b"\r\n\r\nsw\r\n" in seen["body"]

    @pytest.mark.asyncio
    async def test_no_language_field_is_sent_when_none_is_given(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.content
            return ok_response(request)

        await make_provider(handler).transcribe(REQUEST)
        assert b'name="language"' not in seen["body"]


class TestTheCredentialStaysOnTheServer:
    @pytest.mark.asyncio
    async def test_the_key_is_sent_only_as_a_bearer_header(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = request.content
            return ok_response(request)

        await make_provider(handler).transcribe(REQUEST)
        assert seen["auth"] == f"Bearer {API_KEY}"
        # Never in the body, where it could be echoed back by the vendor.
        assert API_KEY.encode() not in seen["body"]

    @pytest.mark.asyncio
    async def test_the_key_never_appears_in_a_result(self):
        provider = make_provider(ok_response)
        result = await provider.transcribe(REQUEST)
        assert API_KEY not in str(result)

    def test_describe_carries_no_credential(self):
        provider = make_provider(ok_response)
        assert API_KEY not in str(provider.describe())
        assert provider.describe()["model_version"] == "whisper-large-v3"


class TestVendorFailuresAreNormalised:
    @pytest.mark.asyncio
    async def test_a_timeout_becomes_a_timeout_code(self):
        def handler(request):
            raise httpx.TimeoutException("timed out", request=request)

        with pytest.raises(TranscriptionError) as exc:
            await make_provider(handler).transcribe(REQUEST)
        assert exc.value.code == TranscriptionErrorCode.TIMEOUT

    @pytest.mark.asyncio
    async def test_a_transport_error_becomes_unavailable(self):
        def handler(request):
            raise httpx.ConnectError("no route", request=request)

        with pytest.raises(TranscriptionError) as exc:
            await make_provider(handler).transcribe(REQUEST)
        assert exc.value.code == TranscriptionErrorCode.UNAVAILABLE

    @pytest.mark.parametrize("status", [400, 401, 413, 429, 500, 503])
    @pytest.mark.asyncio
    async def test_a_rejected_request_never_leaks_the_vendor_body(self, status):
        secret_body = {"error": {"message": "invalid api key gsk_leaked", "code": "x"}}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json=secret_body)

        with pytest.raises(TranscriptionError) as exc:
            await make_provider(handler).transcribe(REQUEST)

        assert exc.value.code == TranscriptionErrorCode.UNAVAILABLE
        assert "gsk_leaked" not in exc.value.message
        assert "invalid api key" not in exc.value.message.lower()

    @pytest.mark.asyncio
    async def test_a_non_json_body_becomes_invalid_output(self):
        def handler(request):
            return httpx.Response(200, content=b"<html>not json</html>")

        with pytest.raises(TranscriptionError) as exc:
            await make_provider(handler).transcribe(REQUEST)
        assert exc.value.code == TranscriptionErrorCode.INVALID_OUTPUT

    @pytest.mark.asyncio
    async def test_a_missing_text_field_becomes_invalid_output(self):
        def handler(request):
            return httpx.Response(200, json={"language": "en", "duration": 1.0})

        with pytest.raises(TranscriptionError) as exc:
            await make_provider(handler).transcribe(REQUEST)
        assert exc.value.code == TranscriptionErrorCode.INVALID_OUTPUT

    @pytest.mark.asyncio
    async def test_an_empty_transcript_is_returned_rather_than_invented(self):
        # An empty string is a legitimate answer for silence. The service layer
        # turns it into "no speech detected"; the transport must not fill it in.
        def handler(request):
            return httpx.Response(200, json={"text": "", "duration": 0.4})

        result = await make_provider(handler).transcribe(REQUEST)
        assert result.text == ""


class TestBuildingFromConfiguration:
    def test_no_credential_yields_no_transport(self, monkeypatch):
        monkeypatch.setattr(
            gt_mod.settings, "assistant_groq_api_key", None, raising=False
        )
        assert build_groq_transcription_provider() is None

    def test_a_credential_yields_a_transport_on_the_configured_model(self, monkeypatch):
        monkeypatch.setattr(
            gt_mod.settings, "assistant_groq_api_key", API_KEY, raising=False
        )
        monkeypatch.setattr(
            gt_mod.settings,
            "assistant_transcription_model",
            "whisper-large-v3",
            raising=False,
        )
        provider = build_groq_transcription_provider()
        assert provider is not None
        assert provider.describe()["model_version"] == "whisper-large-v3"
