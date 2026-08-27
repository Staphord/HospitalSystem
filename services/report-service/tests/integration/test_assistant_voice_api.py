"""Endpoint-level tests for push-to-talk voice.

These exercise the real FastAPI app, the real router, the real gates, and the
real response contracts. Only two things are substituted: the token dependency,
because Keycloak is not reachable from a unit test, and the speech engine,
because a test must never make a paid outbound call or send audio to a vendor.
"""

from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from app.assistant import service as svc
from app.assistant.transcription import (
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionResult,
)
from app.core.tenant_auth import get_current_tenant
from app.main import app
from tests.audio_fixtures import mp4, ogg_opus, wav, webm

VOICE_URL = "/api/v1/reports/assistant/voice/transcribe"
CAPTURE = webm(duration_ms=4000)
SPOKEN = "Ninawezaje kusajili mgonjwa mpya?"


@dataclass
class FakeTenantContext:
    tenant_id: str | None = "hosp-aaaa1111"
    user_sub: str = "user-1"
    preferred_username: str | None = "jdoe"
    email: str | None = None
    roles: list = field(default_factory=lambda: ["receptionist"])
    is_super_admin: bool = False
    scope: str = "full"
    raw_token: dict = field(default_factory=dict)


class StubTranscriber:
    name = "stub"

    def __init__(self, text=SPOKEN, error=None, duration=4.0):
        self.text = text
        self.error = error
        self.duration = duration
        self.calls = []

    def describe(self):
        return {"provider": "stub", "model_version": "stub-whisper"}

    async def transcribe(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return TranscriptionResult(
            text=self.text,
            language="swahili",
            duration_seconds=self.duration,
            model_version="stub-whisper",
        )


@pytest.fixture
def as_user(monkeypatch):
    """Sign in as a chosen role and tenant, with voice switched on."""
    monkeypatch.setattr(svc.settings, "assistant_voice_enabled", True, raising=False)

    def _sign_in(**kwargs):
        ctx = FakeTenantContext(**kwargs)
        app.dependency_overrides[get_current_tenant] = lambda: ctx
        return ctx

    yield _sign_in
    app.dependency_overrides.clear()


@pytest.fixture
def transcriber(monkeypatch):
    stub = StubTranscriber()
    monkeypatch.setattr(svc, "get_transcription_provider", lambda: stub)
    return stub


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Give each test its own rate-limit budget.

    The endpoint really is rate limited and every test here shares one client
    identity, so without this the limiter would leak between tests and fail them
    for the wrong reason. The limit itself is exercised deliberately below.
    """
    from app.core.limiter import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def client():
    return TestClient(app)


def post(client, audio=CAPTURE, content_type="audio/webm", params=None):
    return client.post(
        VOICE_URL, content=audio, headers={"Content-Type": content_type}, params=params
    )


class TestAuthentication:
    def test_a_request_without_a_token_is_refused(self, client, monkeypatch):
        monkeypatch.setattr(svc.settings, "assistant_voice_enabled", True, raising=False)
        app.dependency_overrides.clear()
        assert post(client).status_code == 401


class TestTheCapabilityFlagIsTheKillSwitch:
    def test_with_voice_off_the_endpoint_is_not_there(
        self, client, monkeypatch, transcriber
    ):
        monkeypatch.setattr(
            svc.settings, "assistant_voice_enabled", False, raising=False
        )
        app.dependency_overrides[get_current_tenant] = lambda: FakeTenantContext()
        try:
            response = post(client)
            assert response.status_code == 404
            assert response.json()["code"] == "CAPABILITY_DISABLED"
            assert transcriber.calls == []
        finally:
            app.dependency_overrides.clear()

    def test_chat_being_on_does_not_switch_voice_on(
        self, client, monkeypatch, transcriber
    ):
        # Independent flags: enabling operational chat must not enable voice.
        monkeypatch.setattr(
            svc.settings, "assistant_operational_chat_enabled", True, raising=False
        )
        monkeypatch.setattr(
            svc.settings, "assistant_voice_enabled", False, raising=False
        )
        app.dependency_overrides[get_current_tenant] = lambda: FakeTenantContext()
        try:
            assert post(client).status_code == 404
        finally:
            app.dependency_overrides.clear()


class TestAuthorization:
    def test_a_staff_role_is_admitted(self, client, as_user, transcriber):
        as_user(roles=["ward_nurse"])
        assert post(client).status_code == 200

    def test_a_platform_super_admin_is_refused(self, client, as_user, transcriber):
        as_user(roles=["doctor"], is_super_admin=True)
        response = post(client)
        assert response.status_code == 403
        assert response.json()["code"] == "PERMISSION_DENIED"
        assert transcriber.calls == []

    def test_a_role_outside_the_matrix_is_refused(self, client, as_user, transcriber):
        as_user(roles=["hospital_user"])
        assert post(client).status_code == 403
        assert transcriber.calls == []

    def test_a_read_only_impersonation_session_is_refused(
        self, client, as_user, transcriber
    ):
        as_user(roles=["doctor"], scope="readonly")
        assert post(client).status_code == 403
        assert transcriber.calls == []


class TestCaptureValidationOverHttp:
    def test_a_valid_capture_returns_a_transcript_for_confirmation(
        self, client, as_user, transcriber
    ):
        as_user()
        response = post(client)
        body = response.json()

        assert response.status_code == 200
        assert body["status"] == "transcribed"
        assert body["transcript"] == SPOKEN
        assert body["requires_confirmation"] is True
        assert body["request_id"]
        assert body["metadata"]["audio_retained"] is False
        assert body["metadata"]["transcript_confirmed_by_user"] is False

    @pytest.mark.parametrize(
        "audio,content_type",
        [
            (webm(duration_ms=3000), "audio/webm;codecs=opus"),
            (ogg_opus(duration_ms=3000), "audio/ogg"),
            (mp4(duration_ms=3000), "audio/mp4"),
            (wav(duration_ms=3000), "audio/wav"),
        ],
    )
    def test_every_browser_capture_format_is_accepted(
        self, client, as_user, transcriber, audio, content_type
    ):
        as_user()
        assert post(client, audio=audio, content_type=content_type).status_code == 200

    def test_an_oversized_upload_is_refused(self, client, as_user, transcriber):
        as_user()
        response = post(client, audio=b"\x00" * (5 * 1024 * 1024 + 1))
        assert response.status_code == 413
        assert response.json()["code"] == "REQUEST_TOO_LARGE"
        assert transcriber.calls == []

    def test_an_over_long_capture_is_refused(self, client, as_user, transcriber):
        as_user()
        response = post(client, audio=webm(duration_ms=61_000))
        assert response.status_code == 400
        assert response.json()["code"] == "AUDIO_TOO_LONG"
        assert transcriber.calls == []

    def test_an_unsupported_content_type_is_refused(self, client, as_user, transcriber):
        as_user()
        response = post(client, audio=b"%PDF-1.4" + b"\x00" * 4096,
                        content_type="application/pdf")
        assert response.status_code == 415
        assert response.json()["code"] == "UNSUPPORTED_AUDIO_FORMAT"
        assert transcriber.calls == []

    def test_a_capture_relabelled_as_another_allowed_type_is_refused(
        self, client, as_user, transcriber
    ):
        as_user()
        response = post(client, audio=wav(duration_ms=1000), content_type="audio/webm")
        assert response.status_code == 415
        assert transcriber.calls == []

    def test_a_malformed_capture_is_refused(self, client, as_user, transcriber):
        as_user()
        response = post(client, audio=b"\x1a\x45\xdf\xa3" + b"\x7f" * 2048)
        assert response.status_code in (400, 415)
        assert transcriber.calls == []

    def test_an_empty_body_is_refused(self, client, as_user, transcriber):
        as_user()
        response = post(client, audio=b"")
        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_AUDIO"
        assert transcriber.calls == []


class TestLanguageHint:
    def test_a_supported_hint_is_forwarded(self, client, as_user, transcriber):
        as_user()
        post(client, params={"language": "sw"})
        assert transcriber.calls[0].language == "sw"

    @pytest.mark.parametrize("hint", ["fr", "de", "xx"])
    def test_a_well_formed_but_unsupported_hint_falls_back_to_detection(
        self, client, as_user, transcriber, hint
    ):
        # Telling the engine an untrue language would be worse than telling it
        # nothing, so an unsupported code becomes auto-detection.
        as_user()
        assert post(client, params={"language": hint}).status_code == 200
        assert transcriber.calls[0].language is None

    def test_an_over_long_hint_is_rejected_by_validation(
        self, client, as_user, transcriber
    ):
        as_user()
        response = post(client, params={"language": "x" * 64})
        assert response.status_code == 422


class TestNoSpeech:
    def test_silence_returns_no_speech_rather_than_invented_words(
        self, client, as_user, monkeypatch
    ):
        monkeypatch.setattr(
            svc, "get_transcription_provider", lambda: StubTranscriber(text="Thank you.")
        )
        as_user()
        body = post(client).json()
        assert body["status"] == "no_speech_detected"
        assert body["transcript"] == ""


class TestEngineFailuresAreSafeOverHttp:
    @pytest.mark.parametrize(
        "code,status",
        [
            (TranscriptionErrorCode.TIMEOUT, 504),
            (TranscriptionErrorCode.UNAVAILABLE, 503),
            (TranscriptionErrorCode.NOT_CONFIGURED, 503),
            (TranscriptionErrorCode.INVALID_OUTPUT, 502),
        ],
    )
    def test_each_failure_maps_to_a_safe_status(
        self, client, as_user, monkeypatch, code, status
    ):
        monkeypatch.setattr(
            svc,
            "get_transcription_provider",
            lambda: StubTranscriber(error=TranscriptionError(code, "safe message")),
        )
        as_user()
        response = post(client)
        assert response.status_code == status

    def test_no_response_body_ever_carries_a_credential_or_a_vendor_detail(
        self, client, as_user, monkeypatch
    ):
        monkeypatch.setattr(
            svc,
            "get_transcription_provider",
            lambda: StubTranscriber(
                error=TranscriptionError(
                    TranscriptionErrorCode.UNAVAILABLE, "Voice input is not available."
                )
            ),
        )
        as_user()
        body = post(client).text
        for forbidden in ("gsk_", "groq", "whisper", "Bearer", "Traceback"):
            assert forbidden.lower() not in body.lower()


class TestTheEndpointStaysInsideTheExistingGatewayRoute:
    def test_the_voice_path_lives_under_the_reports_prefix(self):
        # The gateway already routes /api/v1/reports to this service, so no new
        # gateway route and no per-service frontend base URL is needed.
        assert VOICE_URL.startswith("/api/v1/reports/")

    def test_the_route_is_registered_exactly_once(self):
        paths = app.openapi()["paths"]
        assert VOICE_URL in paths
        assert set(paths[VOICE_URL]) == {"post"}


class TestRateLimiting:
    def test_voice_is_rate_limited_more_tightly_than_chat(
        self, client, as_user, transcriber
    ):
        as_user()
        statuses = [post(client).status_code for _ in range(12)]
        assert 429 in statuses, "voice transcription is not rate limited"
