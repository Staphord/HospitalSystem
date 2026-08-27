"""Synthetic audio containers for testing server-side capture validation.

These build real, structurally valid container headers byte by byte, so the
validation path is exercised against the same shapes a browser produces rather
than against a mock.

Nothing here is a recording of a person. No human speech, and therefore no
patient data and no consent question, enters the repository: the containers
carry silent or arbitrary payload bytes, because what is under test is the
container, the codec identification, and the duration arithmetic, never the
audio itself. Recognition quality for English, Swahili, and code-mixed speech
is covered by the documented manual QA script instead, which is where real
voices belong.
"""

from __future__ import annotations

import struct

# EBML / WebM


def _vint(value: int) -> bytes:
    """Encode an EBML size as a variable-length integer, shortest form."""
    for length in range(1, 9):
        capacity = (1 << (7 * length)) - 1
        # The all-ones pattern means "unknown size", so it is never used here.
        if value < capacity:
            marker = 1 << (8 - length)
            raw = value | (marker << (8 * (length - 1)))
            return raw.to_bytes(length, "big")
    raise ValueError("size too large for a vint")


def _element(element_id: bytes, payload: bytes) -> bytes:
    return element_id + _vint(len(payload)) + payload


def _uint(value: int) -> bytes:
    length = max(1, (value.bit_length() + 7) // 8)
    return value.to_bytes(length, "big")


EBML_HEADER = b"\x1a\x45\xdf\xa3"
SEGMENT = b"\x18\x53\x80\x67"
INFO = b"\x15\x49\xa9\x66"
TIMECODE_SCALE = b"\x2a\xd7\xb1"
DURATION = b"\x44\x89"
TRACKS = b"\x16\x54\xae\x6b"
TRACK_ENTRY = b"\xae"
CODEC_ID = b"\x86"
AUDIO = b"\xe1"
SAMPLING_FREQUENCY = b"\xb5"
CHANNELS = b"\x9f"
CLUSTER = b"\x1f\x43\xb6\x75"
CLUSTER_TIMECODE = b"\xe7"


def webm(
    duration_ms: int | None = None,
    cluster_timecodes: tuple[int, ...] = (),
    codec_id: bytes = b"A_OPUS",
    sample_rate: float = 48000.0,
    padding: int = 512,
) -> bytes:
    """Build a WebM/Matroska capture.

    Passing `duration_ms` writes an explicit Duration element, the way a
    finalised file carries one. Passing only `cluster_timecodes` reproduces what
    Chrome's MediaRecorder actually produces for a live capture: no Duration at
    all, with the length recoverable only from the cluster timecodes.
    """
    info_children = _element(TIMECODE_SCALE, _uint(1_000_000))
    if duration_ms is not None:
        info_children += _element(DURATION, struct.pack(">d", float(duration_ms)))
    info = _element(INFO, info_children)

    audio = _element(AUDIO, _element(SAMPLING_FREQUENCY, struct.pack(">f", sample_rate)) + _element(CHANNELS, _uint(1)))
    track_entry = _element(TRACK_ENTRY, _element(CODEC_ID, codec_id) + audio)
    tracks = _element(TRACKS, track_entry)

    clusters = b""
    for timecode in cluster_timecodes:
        clusters += _element(CLUSTER, _element(CLUSTER_TIMECODE, _uint(timecode)))

    # Padding is a real Void element inside the segment, which is how a writer
    # actually reserves space. Trailing bytes after the segment would not be
    # valid EBML, and would be testing the fixture rather than the parser.
    void = _element(b"\xec", b"\x00" * padding) if padding else b""
    segment = _element(SEGMENT, info + tracks + clusters + void)
    header = _element(EBML_HEADER, b"\x42\x82\x85webm")
    return header + segment


# Ogg


def _ogg_page(
    payload: bytes, granule: int, sequence: int, serial: int = 0x1234ABCD
) -> bytes:
    segments = []
    remaining = len(payload)
    while remaining >= 255:
        segments.append(255)
        remaining -= 255
    segments.append(remaining)

    return (
        b"OggS"
        + bytes([0])
        + bytes([0])
        + struct.pack("<q", granule)
        + struct.pack("<I", serial)
        + struct.pack("<I", sequence)
        + struct.pack("<I", 0)
        + bytes([len(segments)])
        + bytes(segments)
        + payload
    )


def ogg_opus(
    duration_ms: int = 4000, pre_skip: int = 312, channels: int = 1
) -> bytes:
    """Build an Ogg/Opus capture, the shape Firefox produces."""
    opus_head = (
        b"OpusHead"
        + bytes([1])
        + bytes([channels])
        + struct.pack("<H", pre_skip)
        + struct.pack("<I", 48000)
        + struct.pack("<h", 0)
        + bytes([0])
    )
    granule = int(duration_ms * 48000 / 1000) + pre_skip

    first = _ogg_page(opus_head, granule=0, sequence=0)
    tags = _ogg_page(b"OpusTags" + struct.pack("<I", 0) + struct.pack("<I", 0), granule=0, sequence=1)
    last = _ogg_page(b"\x00" * 256, granule=granule, sequence=2)
    return first + tags + last


def ogg_vorbis(duration_ms: int = 4000, sample_rate: int = 44100) -> bytes:
    """Build an Ogg/Vorbis capture."""
    ident = (
        b"\x01vorbis"
        + struct.pack("<I", 0)
        + bytes([1])
        + struct.pack("<I", sample_rate)
        + b"\x00" * 16
    )
    granule = int(duration_ms * sample_rate / 1000)
    first = _ogg_page(ident, granule=0, sequence=0)
    last = _ogg_page(b"\x00" * 256, granule=granule, sequence=1)
    return first + last


# MP4


def _box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + box_type + payload


def mp4(
    duration_ms: int = 4000,
    timescale: int = 1000,
    audio_format: bytes = b"mp4a",
    sample_rate: int = 44100,
    channels: int = 1,
    handler: bytes = b"soun",
) -> bytes:
    """Build an MP4/M4A capture, the shape Safari produces."""
    ftyp = _box(b"ftyp", b"M4A " + struct.pack(">I", 0) + b"M4A mp42isom")

    mvhd = _box(
        b"mvhd",
        bytes([0, 0, 0, 0])
        + struct.pack(">I", 0)
        + struct.pack(">I", 0)
        + struct.pack(">I", timescale)
        + struct.pack(">I", int(duration_ms * timescale / 1000))
        + b"\x00" * 80,
    )

    hdlr = _box(
        b"hdlr",
        bytes([0, 0, 0, 0]) + struct.pack(">I", 0) + handler + b"\x00" * 12,
    )

    sample_entry = (
        audio_format
        + b"\x00" * 6
        + struct.pack(">H", 1)
        + struct.pack(">H", 0)
        + struct.pack(">H", 0)
        + struct.pack(">I", 0)
        + struct.pack(">H", channels)
        + struct.pack(">H", 16)
        + struct.pack(">H", 0)
        + struct.pack(">H", 0)
        + struct.pack(">I", sample_rate << 16)
    )
    sample_entry = struct.pack(">I", len(sample_entry) + 4) + sample_entry
    stsd = _box(b"stsd", bytes([0, 0, 0, 0]) + struct.pack(">I", 1) + sample_entry)

    stbl = _box(b"stbl", stsd)
    minf = _box(b"minf", stbl)
    mdia = _box(b"mdia", hdlr + minf)
    trak = _box(b"trak", mdia)
    moov = _box(b"moov", mvhd + trak)
    return ftyp + moov + b"\x00" * 512


# WAV


def wav(duration_ms: int = 4000, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """Build a RIFF/WAVE PCM capture."""
    bits = 16
    byte_rate = sample_rate * channels * bits // 8
    data_size = int(byte_rate * duration_ms / 1000)

    fmt = struct.pack(
        "<HHIIHH",
        1,
        channels,
        sample_rate,
        byte_rate,
        channels * bits // 8,
        bits,
    )
    body = (
        b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", data_size)
        + b"\x00" * data_size
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body
