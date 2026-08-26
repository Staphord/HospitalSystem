from __future__ import annotations

import re
import unicodedata

# Model output is never trusted and never rendered as HTML. This module reduces
# whatever the provider returned to a tightly controlled Markdown subset:
# paragraphs, hyphen bullets, numbered list items, and bold emphasis.
#
# Everything capable of carrying an action or a destination is removed rather
# than escaped: HTML tags, HTML entities, images, links, autolinks, bare URLs,
# and code fences. A hospital answer never needs them, and each of them is a
# route by which text injected into retrieved content could try to phish a
# member of staff or exfiltrate what they typed.

MAX_ANSWER_CHARS = 8000

_HTML_TAG = re.compile(r"<[^>]*>")
_HTML_ENTITY = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});")
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_AUTOLINK = re.compile(r"<(?:https?|mailto|data|javascript):[^>]*>", re.IGNORECASE)
_BARE_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
_DANGEROUS_SCHEME = re.compile(r"\b(?:javascript|data|vbscript|file):\S*", re.IGNORECASE)
_CODE_FENCE = re.compile(r"```+")
_BLANK_RUN = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+$", re.MULTILINE)


def _strip_control_characters(text: str) -> str:
    """Drop control and format characters, keeping newlines and tabs.

    Format characters (category Cf) include the bidirectional overrides that can
    make displayed text read differently from its actual content.
    """
    return "".join(
        ch
        for ch in text
        if ch in ("\n", "\t") or not unicodedata.category(ch).startswith("C")
    )


def sanitize_answer(text: str, max_chars: int = MAX_ANSWER_CHARS) -> str:
    """Reduce model output to safe, renderable text.

    The result is safe to render as plain text or as tightly controlled
    Markdown. It never contains HTML, and it never contains a link the reader
    could follow out of the application.
    """
    if not text or not isinstance(text, str):
        return ""

    cleaned = unicodedata.normalize("NFKC", text)
    cleaned = _strip_control_characters(cleaned)

    # Order matters: remove images before links, since an image is a link shape.
    cleaned = _MD_IMAGE.sub(r"\1", cleaned)
    cleaned = _MD_LINK.sub(r"\1", cleaned)
    cleaned = _AUTOLINK.sub(" ", cleaned)
    cleaned = _HTML_TAG.sub(" ", cleaned)
    cleaned = _HTML_ENTITY.sub(" ", cleaned)
    cleaned = _DANGEROUS_SCHEME.sub(" ", cleaned)
    cleaned = _BARE_URL.sub(" ", cleaned)
    cleaned = _CODE_FENCE.sub(" ", cleaned)

    cleaned = _TRAILING_SPACE.sub("", cleaned)
    cleaned = _BLANK_RUN.sub("\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = cleaned.strip()

    if len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 3].rstrip() + "..."

    return cleaned


def contains_markup(text: str) -> bool:
    """Return whether text still carries markup that must never be rendered."""
    if not text:
        return False
    return bool(
        _HTML_TAG.search(text)
        or _HTML_ENTITY.search(text)
        or _MD_LINK.search(text)
        or _BARE_URL.search(text)
        or _DANGEROUS_SCHEME.search(text)
    )


def sanitize_untrusted_content(text: str, max_chars: int) -> str:
    """Neutralise retrieved content before it is placed in a prompt.

    Retrieved text is data, never instructions. Delimiters that could be used to
    close the data block and start issuing directions are removed here, in
    addition to the markup handling applied to model output.
    """
    cleaned = sanitize_answer(text, max_chars=max_chars)
    # Remove sequences a payload could use to imitate the prompt's own framing.
    cleaned = re.sub(r"-{3,}", " ", cleaned)
    cleaned = re.sub(r"={3,}", " ", cleaned)
    cleaned = re.sub(r"[<>{}]", " ", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()
