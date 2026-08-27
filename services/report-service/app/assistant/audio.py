from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import Enum

# Server-side audio validation for push-to-talk capture.
#
# Nothing about an upload is taken on trust. The browser's Content-Type is a
# claim to be checked, not a fact: this module reads the bytes themselves,
# identifies the container from its magic number, confirms the audio codec from
# the container's own headers, and derives the duration from the container
# rather than from anything the client said. A capture that cannot be identified
# is refused before a single byte reaches a transcription vendor.
#
# No audio is written to disk here, and no audio byte is ever logged.

MAX_AUDIO_BYTES = 5 * 1024 * 1024
MAX_AUDIO_DURATION_MS = 60_000

# Below this a capture cannot contain a usable container header at all, so it is
# a truncated or empty recording rather than speech.
MIN_AUDIO_BYTES = 256

# Containers the browser's MediaRecorder actually produces, and nothing else.
CONTAINER_WEBM = "webm"
CONTAINER_OGG = "ogg"
CONTAINER_MP4 = "mp4"
CONTAINER_WAV = "wav"

CODEC_OPUS = "opus"
CODEC_VORBIS = "vorbis"
CODEC_AAC = "aac"
CODEC_PCM = "pcm"

ALLOWED_CODECS: frozenset[str] = frozenset(
    {CODEC_OPUS, CODEC_VORBIS, CODEC_AAC, CODEC_PCM}
)

# Declared MIME type -> container it must actually turn out to be. A capture
# whose bytes disagree with its declared type is refused rather than sniffed
# into whatever it looks like, so a mislabelled or disguised upload cannot slip
# through by being "helpfully" reinterpreted.
ALLOWED_MIME_TYPES: dict[str, str] = {
    "audio/webm": CONTAINER_WEBM,
    "audio/ogg": CONTAINER_OGG,
    "audio/mp4": CONTAINER_MP4,
    "audio/m4a": CONTAINER_MP4,
    "audio/x-m4a": CONTAINER_MP4,
    "audio/wav": CONTAINER_WAV,
    "audio/wave": CONTAINER_WAV,
    "audio/x-wav": CONTAINER_WAV,
}

# Where a duration came from, recorded so an unbounded capture is never quietly
# treated as if its length had been verified.
DURATION_FROM_CONTAINER = "container"
DURATION_FROM_CLUSTERS = "clusters"
DURATION_UNKNOWN = "unknown"


class AudioRejection(str, Enum):
    """Why a capture was refused. Stable codes; no vendor or parser detail."""

    EMPTY = "empty_audio"
    TOO_LARGE = "audio_too_large"
    TOO_SHORT = "audio_too_short"
    TOO_LONG = "audio_too_long"
    UNSUPPORTED_MIME = "unsupported_mime_type"
    MIME_MISMATCH = "declared_type_does_not_match_content"
    UNREADABLE = "unreadable_audio"
    UNSUPPORTED_CODEC = "unsupported_codec"


class AudioValidationError(Exception):
    """A capture that must be refused before any provider is contacted."""

    def __init__(self, rejection: AudioRejection, message: str) -> None:
        super().__init__(message)
        self.rejection = rejection
        self.message = message


@dataclass(frozen=True)
class AudioProbe:
    """What the server itself determined about a capture."""

    container: str
    codec: str
    byte_size: int
    duration_ms: int | None
    duration_source: str
    sample_rate_hz: int | None
    channels: int | None


def normalize_mime_type(declared: str | None) -> str:
    """Reduce a Content-Type header to its bare type, lowercased.

    An `audio/webm;codecs=opus` and an `audio/webm` are the same container; the
    codec parameter is a claim and is ignored, because the codec is read from
    the container's own header further down.
    """
    if not declared or not isinstance(declared, str):
        return ""
    return declared.split(";", 1)[0].strip().lower()


# EBML / WebM

_SEGMENT = 0x18538067
_INFO = 0x1549A966
_TIMECODE_SCALE = 0x2AD7B1
_DURATION = 0x4489
_TRACKS = 0x1654AE6B
_TRACK_ENTRY = 0xAE
_CODEC_ID = 0x86
_AUDIO = 0xE1
_SAMPLING_FREQUENCY = 0xB5
_CHANNELS = 0x9F
_CLUSTER = 0x1F43B675
_CLUSTER_TIMECODE = 0xE7

_EBML_MASTERS: frozenset[int] = frozenset(
    {_SEGMENT, _INFO, _TRACKS, _TRACK_ENTRY, _AUDIO, _CLUSTER}
)

# Hard ceiling on parser work, so a hostile or corrupt file cannot turn probing
# into an expensive operation.
_MAX_EBML_ELEMENTS = 200_000
_MAX_EBML_DEPTH = 8


def _read_vint(data: bytes, pos: int, keep_marker: bool) -> tuple[int, int, bool]:
    """Read one EBML variable-size integer. Returns (value, next_pos, unknown)."""
    if pos >= len(data):
        raise AudioValidationError(AudioRejection.UNREADABLE, "truncated audio")

    first = data[pos]
    if first == 0:
        raise AudioValidationError(AudioRejection.UNREADABLE, "unreadable audio")

    length = 1
    mask = 0x80
    while not first & mask:
        mask >>= 1
        length += 1
        if length > 8:
            raise AudioValidationError(AudioRejection.UNREADABLE, "unreadable audio")

    if pos + length > len(data):
        raise AudioValidationError(AudioRejection.UNREADABLE, "truncated audio")

    value = first if keep_marker else first & (mask - 1)
    for offset in range(1, length):
        value = (value << 8) | data[pos + offset]

    unknown = False
    if not keep_marker:
        unknown = value == (1 << (7 * length)) - 1

    return value, pos + length, unknown


def _ebml_uint(chunk: bytes) -> int:
    value = 0
    for byte in chunk:
        value = (value << 8) | byte
    return value


def _ebml_float(chunk: bytes) -> float:
    if len(chunk) == 4:
        return struct.unpack(">f", chunk)[0]
    if len(chunk) == 8:
        return struct.unpack(">d", chunk)[0]
    if len(chunk) == 0:
        return 0.0
    raise AudioValidationError(AudioRejection.UNREADABLE, "unreadable audio")


def _webm_codec(codec_id: str) -> str | None:
    codec_id = (codec_id or "").strip().upper()
    if codec_id.startswith("A_OPUS"):
        return CODEC_OPUS
    if codec_id.startswith("A_VORBIS"):
        return CODEC_VORBIS
    if codec_id.startswith("A_AAC"):
        return CODEC_AAC
    if codec_id.startswith("A_PCM"):
        return CODEC_PCM
    return None


def _parse_webm(data: bytes) -> AudioProbe:
    """Derive codec, sample rate, and duration from a WebM/Matroska capture.

    Chrome's MediaRecorder writes a live stream with no Duration element, so the
    duration is recovered from the highest cluster timecode instead. That is
    accurate to within one cluster, which is far tighter than the limit being
    enforced. When neither is present the duration stays unknown and is reported
    as such rather than guessed.
    """
    timecode_scale = 1_000_000
    duration_ticks: float | None = None
    max_cluster_timecode: int | None = None
    codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None

    # (end_of_parent, depth) frames
    stack: list[tuple[int, int]] = [(len(data), 0)]
    pos = 0
    elements = 0

    while stack:
        end, depth = stack[-1]
        if pos >= end:
            stack.pop()
            continue

        elements += 1
        if elements > _MAX_EBML_ELEMENTS:
            break

        try:
            element_id, pos, _ = _read_vint(data, pos, keep_marker=True)
            size, pos, unknown_size = _read_vint(data, pos, keep_marker=False)
        except AudioValidationError:
            # A capture can end in a partly written cluster, and some writers
            # pad the tail. Stop walking rather than throwing away everything
            # already read: if the codec was never found the probe still fails
            # below, so this cannot turn an unidentifiable file into a valid one.
            break

        if unknown_size:
            # A live-written master element runs to the end of its parent.
            element_end = end
        else:
            element_end = min(pos + size, end)
            if pos + size > len(data):
                element_end = len(data)

        if element_id in _EBML_MASTERS and depth < _MAX_EBML_DEPTH:
            stack.append((element_end, depth + 1))
            continue

        chunk = data[pos:element_end]

        if element_id == _TIMECODE_SCALE:
            scale = _ebml_uint(chunk)
            if scale > 0:
                timecode_scale = scale
        elif element_id == _DURATION:
            duration_ticks = _ebml_float(chunk)
        elif element_id == _CLUSTER_TIMECODE:
            timecode = _ebml_uint(chunk)
            if max_cluster_timecode is None or timecode > max_cluster_timecode:
                max_cluster_timecode = timecode
        elif element_id == _CODEC_ID and codec is None:
            codec = _webm_codec(chunk.decode("ascii", "ignore"))
        elif element_id == _SAMPLING_FREQUENCY and sample_rate is None:
            rate = _ebml_float(chunk)
            if rate > 0:
                sample_rate = int(rate)
        elif element_id == _CHANNELS and channels is None:
            count = _ebml_uint(chunk)
            if count > 0:
                channels = count

        pos = element_end

    if codec is None:
        raise AudioValidationError(
            AudioRejection.UNSUPPORTED_CODEC, "unsupported audio codec"
        )

    duration_ms: int | None = None
    duration_source = DURATION_UNKNOWN
    if duration_ticks is not None and duration_ticks > 0:
        duration_ms = int(duration_ticks * timecode_scale / 1_000_000)
        duration_source = DURATION_FROM_CONTAINER
    elif max_cluster_timecode is not None and max_cluster_timecode > 0:
        duration_ms = int(max_cluster_timecode * timecode_scale / 1_000_000)
        duration_source = DURATION_FROM_CLUSTERS

    return AudioProbe(
        container=CONTAINER_WEBM,
        codec=codec,
        byte_size=len(data),
        duration_ms=duration_ms,
        duration_source=duration_source,
        sample_rate_hz=sample_rate,
        channels=channels,
    )


# Ogg


def _parse_ogg(data: bytes) -> AudioProbe:
    """Derive codec and duration from an Ogg capture.

    Duration comes from the granule position on the final page, which is the
    authoritative sample count for the stream, minus the Opus pre-skip.
    """
    if not data.startswith(b"OggS"):
        raise AudioValidationError(AudioRejection.UNREADABLE, "unreadable audio")

    segment_count = data[26] if len(data) > 26 else 0
    payload_start = 27 + segment_count
    header = data[payload_start : payload_start + 32]

    codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    pre_skip = 0

    if header.startswith(b"OpusHead"):
        codec = CODEC_OPUS
        # Opus granule positions are always counted at 48 kHz regardless of the
        # rate the audio was captured at.
        sample_rate = 48_000
        if len(header) >= 12:
            channels = header[9]
            pre_skip = struct.unpack("<H", header[10:12])[0]
    elif header.startswith(b"\x01vorbis"):
        codec = CODEC_VORBIS
        if len(header) >= 16:
            channels = header[11]
            sample_rate = struct.unpack("<I", header[12:16])[0]

    if codec is None or not sample_rate:
        raise AudioValidationError(
            AudioRejection.UNSUPPORTED_CODEC, "unsupported audio codec"
        )

    last_page = data.rfind(b"OggS")
    duration_ms: int | None = None
    duration_source = DURATION_UNKNOWN
    if last_page >= 0 and last_page + 14 <= len(data):
        granule = struct.unpack("<q", data[last_page + 6 : last_page + 14])[0]
        if granule > 0:
            samples = max(0, granule - pre_skip)
            duration_ms = int(samples * 1000 / sample_rate)
            duration_source = DURATION_FROM_CONTAINER

    return AudioProbe(
        container=CONTAINER_OGG,
        codec=codec,
        byte_size=len(data),
        duration_ms=duration_ms,
        duration_source=duration_source,
        sample_rate_hz=sample_rate,
        channels=channels,
    )


# MP4 / ISO base media


_MP4_CONTAINER_BOXES: frozenset[bytes] = frozenset(
    {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta"}
)
_MP4_AUDIO_FORMATS: dict[bytes, str] = {
    b"mp4a": CODEC_AAC,
    b"opus": CODEC_OPUS,
    b"Opus": CODEC_OPUS,
}
_MAX_MP4_BOXES = 50_000


def _parse_mp4(data: bytes) -> AudioProbe:
    """Derive codec, sample rate, and duration from an MP4/M4A capture."""
    duration_ms: int | None = None
    duration_source = DURATION_UNKNOWN
    codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    has_sound_track = False

    stack: list[tuple[int, int, int]] = [(0, len(data), 0)]
    boxes = 0

    while stack:
        pos, end, depth = stack.pop()
        while pos + 8 <= end:
            boxes += 1
            if boxes > _MAX_MP4_BOXES:
                break

            size = struct.unpack(">I", data[pos : pos + 4])[0]
            box_type = data[pos + 4 : pos + 8]
            body = pos + 8

            if size == 1:
                if body + 8 > end:
                    break
                size = struct.unpack(">Q", data[body : body + 8])[0]
                body += 8
            elif size == 0:
                size = end - pos

            box_end = pos + size
            if size < 8 or box_end > end:
                break

            if box_type in _MP4_CONTAINER_BOXES and depth < 8:
                stack.append((body, box_end, depth + 1))
            elif box_type == b"mvhd" and duration_ms is None:
                version = data[body] if body < box_end else 0
                if version == 1 and body + 32 <= box_end:
                    timescale = struct.unpack(">I", data[body + 20 : body + 24])[0]
                    raw = struct.unpack(">Q", data[body + 24 : body + 32])[0]
                elif version == 0 and body + 20 <= box_end:
                    timescale = struct.unpack(">I", data[body + 12 : body + 16])[0]
                    raw = struct.unpack(">I", data[body + 16 : body + 20])[0]
                else:
                    timescale, raw = 0, 0
                if timescale > 0 and raw > 0:
                    duration_ms = int(raw * 1000 / timescale)
                    duration_source = DURATION_FROM_CONTAINER
            elif box_type == b"hdlr" and body + 12 <= box_end:
                if data[body + 8 : body + 12] == b"soun":
                    has_sound_track = True
            elif box_type == b"stsd" and body + 16 <= box_end:
                entry_format = data[body + 12 : body + 16]
                resolved = _MP4_AUDIO_FORMATS.get(entry_format)
                if resolved and codec is None:
                    codec = resolved
                    # AudioSampleEntry: channel count and sample rate sit at a
                    # fixed offset inside the sample description entry.
                    entry = body + 8
                    if entry + 36 <= box_end:
                        channels = struct.unpack(">H", data[entry + 24 : entry + 26])[0]
                        sample_rate = (
                            struct.unpack(">I", data[entry + 32 : entry + 36])[0] >> 16
                        )

            pos = box_end

    if codec is None or not has_sound_track:
        raise AudioValidationError(
            AudioRejection.UNSUPPORTED_CODEC, "unsupported audio codec"
        )

    return AudioProbe(
        container=CONTAINER_MP4,
        codec=codec,
        byte_size=len(data),
        duration_ms=duration_ms,
        duration_source=duration_source,
        sample_rate_hz=sample_rate or None,
        channels=channels or None,
    )


# WAV


_WAV_PCM_FORMATS: frozenset[int] = frozenset({0x0001, 0x0003, 0xFFFE})


def _parse_wav(data: bytes) -> AudioProbe:
    """Derive codec, sample rate, and duration from a RIFF/WAVE capture."""
    pos = 12
    sample_rate: int | None = None
    channels: int | None = None
    byte_rate = 0
    data_size = 0
    audio_format = 0

    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        chunk_size = struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        body = pos + 8

        if chunk_id == b"fmt " and body + 16 <= len(data):
            audio_format = struct.unpack("<H", data[body : body + 2])[0]
            channels = struct.unpack("<H", data[body + 2 : body + 4])[0]
            sample_rate = struct.unpack("<I", data[body + 4 : body + 8])[0]
            byte_rate = struct.unpack("<I", data[body + 8 : body + 12])[0]
        elif chunk_id == b"data":
            data_size = min(chunk_size, len(data) - body)

        pos = body + chunk_size + (chunk_size & 1)

    if audio_format not in _WAV_PCM_FORMATS or not sample_rate:
        raise AudioValidationError(
            AudioRejection.UNSUPPORTED_CODEC, "unsupported audio codec"
        )

    duration_ms: int | None = None
    duration_source = DURATION_UNKNOWN
    if byte_rate > 0 and data_size > 0:
        duration_ms = int(data_size * 1000 / byte_rate)
        duration_source = DURATION_FROM_CONTAINER

    return AudioProbe(
        container=CONTAINER_WAV,
        codec=CODEC_PCM,
        byte_size=len(data),
        duration_ms=duration_ms,
        duration_source=duration_source,
        sample_rate_hz=sample_rate,
        channels=channels,
    )


def sniff_container(data: bytes) -> str | None:
    """Identify the container from its magic number alone."""
    if len(data) < 12:
        return None
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return CONTAINER_WEBM
    if data[:4] == b"OggS":
        return CONTAINER_OGG
    if data[4:8] == b"ftyp":
        return CONTAINER_MP4
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return CONTAINER_WAV
    return None


_PARSERS = {
    CONTAINER_WEBM: _parse_webm,
    CONTAINER_OGG: _parse_ogg,
    CONTAINER_MP4: _parse_mp4,
    CONTAINER_WAV: _parse_wav,
}


def probe_audio(data: bytes) -> AudioProbe:
    """Identify a capture from its own bytes. Raises AudioValidationError."""
    container = sniff_container(data)
    if container is None:
        raise AudioValidationError(AudioRejection.UNREADABLE, "unreadable audio")

    parser = _PARSERS[container]
    try:
        probe = parser(data)
    except AudioValidationError:
        raise
    except Exception as exc:
        # A malformed container must be refused, never crash the endpoint and
        # never surface a parser message to the caller.
        raise AudioValidationError(
            AudioRejection.UNREADABLE, "unreadable audio"
        ) from exc

    if probe.codec not in ALLOWED_CODECS:
        raise AudioValidationError(
            AudioRejection.UNSUPPORTED_CODEC, "unsupported audio codec"
        )
    return probe


def validate_audio(
    data: bytes,
    declared_content_type: str | None,
    max_bytes: int = MAX_AUDIO_BYTES,
    max_duration_ms: int = MAX_AUDIO_DURATION_MS,
    min_bytes: int = MIN_AUDIO_BYTES,
) -> AudioProbe:
    """Validate one capture completely, before any provider is contacted.

    Order matters. Size is checked first because it is the cheapest refusal and
    bounds every parse that follows. The declared type is then required to agree
    with what the bytes actually are, so a capture cannot be relabelled into an
    allowlisted type. Duration is derived from the container, never accepted
    from the client.
    """
    if not data:
        raise AudioValidationError(AudioRejection.EMPTY, "no audio was received")

    if len(data) > max_bytes:
        raise AudioValidationError(
            AudioRejection.TOO_LARGE, "that recording is too large"
        )

    if len(data) < min_bytes:
        raise AudioValidationError(
            AudioRejection.TOO_SHORT, "that recording is too short"
        )

    mime = normalize_mime_type(declared_content_type)
    expected = ALLOWED_MIME_TYPES.get(mime)
    if expected is None:
        raise AudioValidationError(
            AudioRejection.UNSUPPORTED_MIME, "that audio format is not supported"
        )

    probe = probe_audio(data)

    if probe.container != expected:
        raise AudioValidationError(
            AudioRejection.MIME_MISMATCH,
            "that recording does not match the format it claims to be",
        )

    if probe.duration_ms is not None and probe.duration_ms > max_duration_ms:
        raise AudioValidationError(AudioRejection.TOO_LONG, "that recording is too long")

    return probe
