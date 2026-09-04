"""Server-side validation of a push-to-talk capture.

The point of these tests is that the server decides what a capture is. What the
browser claims in its Content-Type header is treated as a claim to be checked
against the bytes, and the duration is read out of the container rather than
accepted from the client, so neither can be used to slip an oversized or
unsupported recording past the limits.
"""

import random

import pytest

from app.assistant.audio import (
    CODEC_AAC,
    CODEC_OPUS,
    CODEC_PCM,
    CODEC_VORBIS,
    CONTAINER_MP4,
    CONTAINER_OGG,
    CONTAINER_WAV,
    CONTAINER_WEBM,
    DURATION_FROM_CLUSTERS,
    DURATION_FROM_CONTAINER,
    DURATION_UNKNOWN,
    AudioRejection,
    AudioValidationError,
    normalize_mime_type,
    probe_audio,
    sniff_container,
    validate_audio,
)
from tests.audio_fixtures import mp4, ogg_opus, ogg_vorbis, wav, webm


class TestContainerIsIdentifiedFromTheBytes:
    @pytest.mark.parametrize(
        "data,expected",
        [
            (webm(), CONTAINER_WEBM),
            (ogg_opus(), CONTAINER_OGG),
            (mp4(), CONTAINER_MP4),
            (wav(), CONTAINER_WAV),
        ],
        # Named, because the parameter is a capture and pytest would otherwise
        # build the test id out of its bytes. WAV is real PCM rather than a
        # short stub, so that id runs to roughly a hundred thousand characters -
        # and pytest puts the id of the running test into PYTEST_CURRENT_TEST,
        # which on Windows cannot hold more than 32767. The test then errors in
        # setup, before its body runs, on every Windows machine and on no Linux
        # one. Naming the cases fixes it and reads better in the output too.
        ids=["webm", "ogg", "mp4", "wav"],
    )
    def test_each_supported_container_is_recognised(self, data, expected):
        assert sniff_container(data) == expected

    def test_unrecognised_bytes_are_not_guessed_at(self):
        assert sniff_container(b"this is not audio at all, not even close") is None

    def test_a_capture_too_short_to_hold_a_header_is_not_identified(self):
        assert sniff_container(b"OggS") is None


class TestCodecComesFromTheContainerNotTheHeader:
    def test_webm_opus(self):
        assert probe_audio(webm()).codec == CODEC_OPUS

    def test_webm_vorbis(self):
        assert probe_audio(webm(codec_id=b"A_VORBIS")).codec == CODEC_VORBIS

    def test_ogg_opus(self):
        probe = probe_audio(ogg_opus())
        assert probe.codec == CODEC_OPUS
        # Opus granule positions are always counted at 48 kHz.
        assert probe.sample_rate_hz == 48000

    def test_ogg_vorbis(self):
        probe = probe_audio(ogg_vorbis(sample_rate=44100))
        assert probe.codec == CODEC_VORBIS
        assert probe.sample_rate_hz == 44100

    def test_mp4_aac(self):
        probe = probe_audio(mp4())
        assert probe.codec == CODEC_AAC
        assert probe.sample_rate_hz == 44100
        assert probe.channels == 1

    def test_wav_pcm(self):
        probe = probe_audio(wav(sample_rate=16000))
        assert probe.codec == CODEC_PCM
        assert probe.sample_rate_hz == 16000

    def test_a_video_track_is_not_accepted_as_audio(self):
        with pytest.raises(AudioValidationError) as exc:
            probe_audio(webm(codec_id=b"V_VP8"))
        assert exc.value.rejection is AudioRejection.UNSUPPORTED_CODEC

    def test_an_mp4_without_a_sound_track_is_refused(self):
        with pytest.raises(AudioValidationError) as exc:
            probe_audio(mp4(handler=b"vide"))
        assert exc.value.rejection is AudioRejection.UNSUPPORTED_CODEC


class TestDurationIsDerivedFromTheContainer:
    def test_webm_with_an_explicit_duration_element(self):
        probe = probe_audio(webm(duration_ms=4500))
        assert probe.duration_ms == 4500
        assert probe.duration_source == DURATION_FROM_CONTAINER

    def test_webm_live_capture_falls_back_to_cluster_timecodes(self):
        # Chrome writes no Duration element for a live recording. The length has
        # to come from the clusters or it cannot be enforced at all.
        probe = probe_audio(webm(cluster_timecodes=(0, 1000, 2000, 3000)))
        assert probe.duration_ms == 3000
        assert probe.duration_source == DURATION_FROM_CLUSTERS

    def test_webm_with_neither_reports_unknown_rather_than_guessing(self):
        probe = probe_audio(webm())
        assert probe.duration_ms is None
        assert probe.duration_source == DURATION_UNKNOWN

    def test_ogg_duration_excludes_the_opus_pre_skip(self):
        probe = probe_audio(ogg_opus(duration_ms=5000, pre_skip=312))
        assert probe.duration_ms == 5000

    def test_mp4_duration_uses_the_movie_timescale(self):
        assert probe_audio(mp4(duration_ms=7000, timescale=600)).duration_ms == 7000

    def test_wav_duration_uses_the_byte_rate(self):
        assert probe_audio(wav(duration_ms=2500)).duration_ms == 2500


class TestSizeLimits:
    def test_an_empty_body_is_refused(self):
        with pytest.raises(AudioValidationError) as exc:
            validate_audio(b"", "audio/webm")
        assert exc.value.rejection is AudioRejection.EMPTY

    def test_an_oversized_capture_is_refused_before_anything_is_parsed(self):
        with pytest.raises(AudioValidationError) as exc:
            validate_audio(b"\x00" * (5 * 1024 * 1024 + 1), "audio/webm")
        assert exc.value.rejection is AudioRejection.TOO_LARGE

    def test_a_truncated_capture_is_refused(self):
        with pytest.raises(AudioValidationError) as exc:
            validate_audio(b"\x1a\x45\xdf\xa3" + b"\x00" * 20, "audio/webm")
        assert exc.value.rejection is AudioRejection.TOO_SHORT

    def test_the_size_limit_is_configurable_downwards(self):
        with pytest.raises(AudioValidationError) as exc:
            validate_audio(webm(duration_ms=1000), "audio/webm", max_bytes=64)
        assert exc.value.rejection is AudioRejection.TOO_LARGE


class TestDeclaredTypeMustMatchTheContent:
    @pytest.mark.parametrize(
        "declared", ["audio/webm", "audio/webm;codecs=opus", "AUDIO/WEBM", " audio/webm "]
    )
    def test_a_correct_declaration_with_parameters_is_accepted(self, declared):
        assert validate_audio(webm(duration_ms=1000), declared).container == CONTAINER_WEBM

    def test_an_unlisted_type_is_refused(self):
        with pytest.raises(AudioValidationError) as exc:
            validate_audio(webm(duration_ms=1000), "application/octet-stream")
        assert exc.value.rejection is AudioRejection.UNSUPPORTED_MIME

    def test_a_missing_type_is_refused(self):
        with pytest.raises(AudioValidationError) as exc:
            validate_audio(webm(duration_ms=1000), None)
        assert exc.value.rejection is AudioRejection.UNSUPPORTED_MIME

    def test_audio_relabelled_as_another_allowed_type_is_refused(self):
        # A capture cannot be smuggled through by claiming to be a type that
        # happens to be on the allowlist.
        with pytest.raises(AudioValidationError) as exc:
            validate_audio(wav(duration_ms=1000), "audio/webm")
        assert exc.value.rejection is AudioRejection.MIME_MISMATCH

    def test_normalize_mime_type_drops_parameters(self):
        assert normalize_mime_type("audio/webm;codecs=opus") == "audio/webm"
        assert normalize_mime_type(None) == ""


class TestDurationLimit:
    def test_an_over_long_capture_is_refused(self):
        with pytest.raises(AudioValidationError) as exc:
            validate_audio(webm(duration_ms=61_000), "audio/webm")
        assert exc.value.rejection is AudioRejection.TOO_LONG

    def test_a_capture_at_the_limit_is_accepted(self):
        assert validate_audio(webm(duration_ms=60_000), "audio/webm").duration_ms == 60_000

    def test_an_over_long_live_capture_is_caught_through_its_clusters(self):
        with pytest.raises(AudioValidationError) as exc:
            validate_audio(webm(cluster_timecodes=(0, 30_000, 90_000)), "audio/webm")
        assert exc.value.rejection is AudioRejection.TOO_LONG

    def test_a_capture_of_unknown_length_is_bounded_by_the_byte_limit_instead(self):
        # It is accepted here and reported as unknown, so the caller can see
        # that the length was never verified rather than assume it was.
        probe = validate_audio(webm(), "audio/webm")
        assert probe.duration_ms is None
        assert probe.duration_source == DURATION_UNKNOWN


class TestMalformedInputIsRefusedNotCrashed:
    @pytest.mark.parametrize(
        "data",
        [
            b"\x1a\x45\xdf\xa3" + b"\xff" * 1024,
            b"\x1a\x45\xdf\xa3" + b"\x01" * 1024,
            b"OggS" + b"\x00" * 1024,
            b"\x00\x00\x00\x18ftyp" + b"\xff" * 1024,
            b"RIFF" + b"\xff" * 4 + b"WAVE" + b"\xff" * 1024,
        ],
        # Named for the same reason as above. These payloads are only a kilobyte
        # so they stay under the Windows ceiling today, but the id is still four
        # thousand characters of escaped bytes in every failure message.
        ids=["webm-ff", "webm-01", "ogg", "mp4", "wav"],
    )
    def test_a_corrupt_container_raises_only_a_validation_error(self, data):
        with pytest.raises(AudioValidationError):
            validate_audio(data, "audio/" + (sniff_container(data) or "webm"))

    def test_random_bytes_never_escape_as_an_unexpected_exception(self):
        rng = random.Random(20260827)
        for _ in range(200):
            size = rng.randint(256, 2048)
            body = bytes(rng.getrandbits(8) for _ in range(size))
            for prefix in (
                b"",
                b"\x1a\x45\xdf\xa3",
                b"OggS",
                b"\x00\x00\x00\x18ftyp",
                b"RIFF\x00\x00\x00\x00WAVE",
            ):
                data = prefix + body
                try:
                    validate_audio(data, "audio/webm")
                except AudioValidationError:
                    pass

    def test_a_deeply_nested_container_cannot_exhaust_the_parser(self):
        # An attacker-controlled file must not be able to turn probing into
        # unbounded work.
        nested = b"\x1a\x45\xdf\xa3\x84\x42\x82\x85webm"
        nested += b"\x18\x53\x80\x67\xff" * 64
        nested += b"\x00" * 512
        try:
            validate_audio(nested, "audio/webm")
        except AudioValidationError:
            pass
