"""The speech-to-text seam.

The seam exists so the assistant is not welded to one speech vendor: swapping a
hosted engine for a self-hosted one must be a configuration change plus one new
transport, not a rewrite. These tests hold that boundary in place and hold the
fail-closed behaviour that stops a missing credential turning into invented
words.
"""

import inspect

import pytest

from app.assistant import transcription as tr_mod
from app.assistant.transcription import (
    GROQ,
    NULL,
    SUPPORTED_LANGUAGES,
    NullTranscriptionProvider,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionRequest,
    describe_configured_transcription,
    get_transcription_provider,
    is_silence_artifact,
    MAX_DETECTED_LANGUAGE_CHARS,
    is_transcription_configured,
    normalize_detected_language,
    normalize_language,
)

REQUEST = TranscriptionRequest(
    audio=b"\x00" * 1024, content_type="audio/webm", filename="capture.webm"
)


class TestTheSeamCarriesNoVendor:
    def test_the_seam_module_imports_no_vendor_sdk(self):
        source = inspect.getsource(tr_mod)
        for forbidden in ("import groq", "from groq", "import httpx", "openai"):
            assert forbidden not in source

    def test_the_seam_never_reads_a_credential_value(self):
        # Presence is all this module may know. The value belongs to the
        # transport and nowhere else.
        source = inspect.getsource(tr_mod)
        assert "Authorization" not in source
        assert "Bearer" not in source

    def test_describe_reports_presence_never_the_credential(self, monkeypatch):
        monkeypatch.setattr(
            tr_mod.settings, "assistant_groq_api_key", "gsk_secret_value", raising=False
        )
        monkeypatch.setattr(tr_mod.settings, "assistant_provider", GROQ, raising=False)

        described = describe_configured_transcription()
        assert described["credential_present"] == "true"
        assert "gsk_secret_value" not in str(described)


class TestFailsClosedWithoutACredential:
    @pytest.fixture(autouse=True)
    def no_credential(self, monkeypatch):
        monkeypatch.setattr(
            tr_mod.settings, "assistant_groq_api_key", None, raising=False
        )
        monkeypatch.setattr(tr_mod.settings, "assistant_provider", GROQ, raising=False)

    def test_no_credential_means_not_configured(self):
        assert is_transcription_configured() is False

    def test_the_resolved_engine_is_the_null_engine(self):
        assert isinstance(get_transcription_provider(), NullTranscriptionProvider)
        assert get_transcription_provider().name == NULL

    @pytest.mark.asyncio
    async def test_the_null_engine_refuses_rather_than_inventing_words(self):
        with pytest.raises(TranscriptionError) as exc:
            await NullTranscriptionProvider().transcribe(REQUEST)
        assert exc.value.code == TranscriptionErrorCode.NOT_CONFIGURED
        # The refusal must read as unavailability, never as a transcript.
        assert "not available" in exc.value.message.lower()

    def test_an_unknown_vendor_is_not_configured(self, monkeypatch):
        monkeypatch.setattr(
            tr_mod.settings, "assistant_groq_api_key", "gsk_x", raising=False
        )
        monkeypatch.setattr(
            tr_mod.settings, "assistant_provider", "some-other-vendor", raising=False
        )
        assert is_transcription_configured() is False
        assert isinstance(get_transcription_provider(), NullTranscriptionProvider)


class TestLanguageHint:
    @pytest.mark.parametrize("code", ["en", "sw", "EN", " sw "])
    def test_supported_languages_are_accepted(self, code):
        assert normalize_language(code) in SUPPORTED_LANGUAGES

    @pytest.mark.parametrize(
        "value", [None, "", "fr", "klingon", "en-US-hacky", "../../etc/passwd", 42]
    )
    def test_anything_else_falls_back_to_auto_detection(self, value):
        # Falling back to None lets the engine detect the language. Telling it
        # something untrue would be worse than telling it nothing.
        assert normalize_language(value) is None

    def test_only_english_and_swahili_are_supported(self):
        assert SUPPORTED_LANGUAGES == frozenset({"en", "sw"})


class TestSilenceIsNotTurnedIntoSpeech:
    @pytest.mark.parametrize(
        "artifact",
        [
            "",
            "   ",
            "Thank you.",
            "thank you",
            "Thanks for watching!",
            "you",
            "Bye.",
            "Subtitles by the Amara.org community",
            "Asante sana",
            "Please subscribe",
        ],
    )
    def test_known_silence_hallucinations_are_reported_as_no_speech(self, artifact):
        assert is_silence_artifact(artifact) is True

    @pytest.mark.parametrize(
        "speech",
        [
            "How do I register a new patient?",
            "Ninawezaje kusajili mgonjwa mpya?",
            "Thank you for the report, where do I find the discharge summary?",
            "Fungua Reception kisha bonyeza Register patient",
        ],
    )
    def test_real_speech_is_never_discarded_as_an_artifact(self, speech):
        assert is_silence_artifact(speech) is False

    def test_a_stock_phrase_inside_a_real_sentence_is_kept(self):
        # Only a transcript consisting solely of an artifact is discarded.
        # Discarding any transcript that merely contains one would delete real
        # speech.
        assert is_silence_artifact("Thank you, where is the ward handover?") is False


class TestTheDetectedLanguageLabelIsShaped:
    """The vendor picks its own wording, and it will not always fit.

    Groq answers "Swahili (macrolanguage)" when asked for Swahili. That is 23
    characters against a 16 character response field, and it reached the
    contract unshaped, which turned a working transcription into a 500. Found in
    live gateway testing, not by a stub.
    """

    def test_the_groq_swahili_label_is_reduced_to_a_plain_name(self):
        assert normalize_detected_language("Swahili (macrolanguage)") == "swahili"

    def test_the_result_always_fits_the_response_contract(self):
        for label in (
            "Swahili (macrolanguage)",
            "English",
            "Norwegian Bokmal (individual language)",
            "a" * 200,
            "Chinese (Simplified, Mandarin, macrolanguage variant)",
        ):
            normalized = normalize_detected_language(label)
            assert normalized is not None
            assert len(normalized) <= MAX_DETECTED_LANGUAGE_CHARS

    @pytest.mark.parametrize("value", [None, "", "   ", "()", "12345", 42])
    def test_an_unusable_label_becomes_none(self, value):
        assert normalize_detected_language(value) is None

    def test_punctuation_and_markup_are_stripped(self):
        # The label is vendor output, so it is treated as untrusted like any
        # other vendor string rather than passed through to the browser.
        assert "<" not in (normalize_detected_language("<b>English</b>") or "")
        assert normalize_detected_language("English!!!") == "english"
