from __future__ import annotations

import re

from app.assistant.live.aliases import ALIAS_FIELD
from app.assistant.live.contracts import MetricResult
from app.assistant.live.execution import format_value
from app.assistant.live.routing import PATIENT_NUMBER_PATTERN, VISIT_NUMBER_PATTERN
from app.assistant.sanitize import sanitize_untrusted_content

# Turning figures into prompt text, and refusing an answer that invents one.
#
# The largest hallucination risk once real numbers are in play is not a wrong
# fact but a computed one: a model given a total and an available count will
# happily volunteer an occupancy percentage nobody asked it to work out, and it
# will sound exactly as authoritative as the figures it was given. The prompt
# rules in service.SYSTEM_INSTRUCTIONS tell it not to; this module checks.

MAX_FIGURE_CHARS_PER_VALUE = 120

# A figure heading that is an identifier rather than a name.
#
# It decides whether the block also has to explain the opaque label. When the
# heading is a number the caller typed, it must: nothing else in the block says
# who PATIENT_1 is. When the heading is the name they typed, it must not - told
# both, the model writes "Fatuma Mwita (PATIENT_1)", which rehydrates to
# "Fatuma Mwita (Fatuma Mwita)".
_IDENTIFIER_SUBJECT = re.compile(
    "^(" + PATIENT_NUMBER_PATTERN + "|" + VISIT_NUMBER_PATTERN + ")$",
    re.IGNORECASE,
)

# A number as it appears in prose, including decimals and thousands separators.
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

# Text removed from an answer before its numbers are counted.
#
# A reading time is rendered into the prompt, so the model may legitimately
# repeat it. Allowing its digits as figures would be far too generous: an answer
# read at 14:32 would silently accept "14" and "32" as supplied figures, which
# is exactly how a subtracted bed count slips through. The stamp is removed
# instead, so it is repeatable without widening what counts as a figure.
_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?(?:\s*UTC)?"
    r"|\b\d{1,2}:\d{2}(?::\d{2})?(?:\s*UTC)?"
)

# A numbered list marker is formatting, not an assertion. sanitize_answer keeps
# numbered lists, so "1." at the start of a step would otherwise be read as a
# figure the server never supplied and reject a perfectly good answer.
_LIST_MARKER = re.compile(r"^[ \t]*\d+[.)](?=\s)", re.MULTILINE)

# Dash characters a model may use in place of an ASCII hyphen, mapped back
# before anything is matched.
#
# This is not cosmetic. Models routinely typeset a date as "2026‐08‐31" with
# U+2010 HYPHEN rather than "-", and NFKC normalisation does not fold it, so the
# timestamp pattern above missed it and "2026" was read as an invented figure.
# The effect was that a correct answer citing its reading time - which the
# system prompt asks for on every figure - was rejected and replaced by the
# plain fallback almost every time.
_DASHES = str.maketrans({c: "-" for c in "‐‑‒–—−"})

# Nothing is allowed unconditionally. Numbers that legitimately appear in prose
# come from the content pack or the question, and both are already allowed in
# full by validate_figures, so a blanket allowance for small integers would only
# create a hole: "8 beds are free" when 8 was never read is exactly the kind of
# confident invention this guard exists to catch.
_ALWAYS_ALLOWED: frozenset[str] = frozenset()


def _normalise_number(token: str) -> str:
    """Reduce a number to a comparable form, dropping separators and zeros.

    "1,240" and "1240" are the same figure, and so are "12.0" and "12". Without
    this the validator would reject the model for formatting a supplied figure
    conventionally, which is not what it is there to catch.
    """
    cleaned = token.replace(",", "").strip()
    if "." in cleaned:
        cleaned = cleaned.rstrip("0").rstrip(".")
    return cleaned or "0"


def _alias_in(result: MetricResult) -> str | None:
    """The opaque label a pseudonymised result carries, if it carries one.

    Read from the row rather than passed in, so this module needs no knowledge
    of how a label is issued - only that a patient-tier row arrives with one
    already substituted in.
    """
    for row in result.rows:
        value = row.values.get(ALIAS_FIELD)
        if value:
            return str(value)
    return None


def render_block(results: list[MetricResult]) -> str:
    """Render live figures as inert, labelled prompt data.

    Every value passes through the same sanitiser as retrieved content, so a
    ward or drug name carrying instruction-shaped text is neutralised on the way
    into the prompt exactly as a content entry would be.
    """
    blocks: list[str] = []
    for result in results:
        if result.failed or result.is_empty:
            continue
        read_at = result.read_at.strftime("%Y-%m-%d %H:%M UTC") if result.read_at else "unknown"
        heading = sanitize_untrusted_content(result.label, 200)
        if result.subject:
            # Name the patient the row is about, in the heading, by the
            # identifier the staff member typed. Without this the model cannot
            # connect a row labelled PATIENT_1 to the number in the question, and
            # refuses a figure it has in full - which is what it did, repeatedly,
            # until this line existed. See MetricResult.subject.
            heading += " for " + sanitize_untrusted_content(result.subject, 60)
        lines = [
            "Figure: " + heading,
            "Read at: " + read_at,
        ]
        alias = _alias_in(result)
        if alias and _IDENTIFIER_SUBJECT.match(result.subject or ""):
            # And say what the label is, or the model reports the number back
            # and never uses the label - so the server has nothing to put the
            # patient's real name into when it rehydrates the answer.
            # Worded as an instruction to use the label, not as an explanation
            # of it. Told the label "stands in for their name", the model wrote
            # "Patient PT-20260829-0001 (shown as PATIENT_1) is..." - which
            # rehydrates to "(shown as Amina Mwita)", a phrase that reads like a
            # disclaimer about the person's own name.
            lines.append("Call this patient " + alias + " in your answer.")
        for row in result.rows:
            pairs = [
                sanitize_untrusted_content(str(name).replace("_", " "), 80)
                + ": "
                + sanitize_untrusted_content(
                    format_value(value), MAX_FIGURE_CHARS_PER_VALUE
                )
                for name, value in sorted(row.values.items())
            ]
            lines.append("- " + ", ".join(pairs))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def supplied_figures(results: list[MetricResult]) -> set[str]:
    """Every number the server put in front of the model, normalised.

    This scans every cell, not only the numeric columns, because a name can
    carry digits: "Oxytocin 10IU", "Amoxicillin 500mg", "Metformin 850mg",
    "Ward 3". Counting only the numeric columns rejected an answer that simply
    named the drug it was asked about, which made almost every stock answer fall
    back to the plain listing.

    The test this guard exists for is unchanged: a number the model was never
    shown is still refused. A number printed in the block was shown to it.
    """
    figures: set[str] = set()
    for result in results:
        for figure in result.figures:
            for token in _NUMBER.findall(figure):
                figures.add(_normalise_number(token))
        for row in result.rows:
            for value in row.values.values():
                for token in _NUMBER.findall(format_value(value)):
                    figures.add(_normalise_number(token))
        # The label and the subject are rendered into the block too, so a figure
        # named "Ward 3 occupancy" does not make its own title unquotable, and a
        # patient figure headed "for PT-20260829-0003" does not make the
        # identifier the staff member typed an invented number.
        for heading in (result.label or "", result.subject or ""):
            for token in _NUMBER.findall(heading):
                figures.add(_normalise_number(token))
    return figures


def validate_figures(
    answer: str,
    results: list[MetricResult],
    question: str = "",
    content_block: str = "",
) -> tuple[bool, str | None]:
    """Check that every number in an answer is one the server supplied.

    Returns (ok, offending_number). A number that appears in neither the
    supplied figures, the staff question, nor the retrieved content means the
    model calculated something, and the answer is rejected whole rather than
    edited. Rewriting would mean the server deciding which half of a computed
    sentence to keep, which is the judgement this design withholds from it -
    the same stance as cds/differential.build_considerations.
    """
    # With no figures supplied the model was shown none, so there is nothing
    # here to contradict and every number in the answer came from the content
    # pack, which this function is not the guard for. Returning early keeps the
    # function safe to call unconditionally.
    if not answer or not results:
        return True, None

    allowed = set(_ALWAYS_ALLOWED)
    allowed |= supplied_figures(results)
    for source in (question or "", content_block or ""):
        for token in _NUMBER.findall(source):
            allowed.add(_normalise_number(token))

    # Reading times and list markers are not assertions about the hospital, so
    # they are removed rather than allowed. Removing them keeps the set of
    # acceptable figures as narrow as the readings themselves. Dashes are folded
    # to ASCII first, or a typographic hyphen inside a date hides it from the
    # timestamp pattern and its year is then counted as an invented figure.
    scanned = answer.translate(_DASHES)
    scanned = _TIMESTAMP.sub(" ", scanned)
    scanned = _LIST_MARKER.sub(" ", scanned)

    for token in _NUMBER.findall(scanned):
        if _normalise_number(token) not in allowed:
            return False, token
    return True, None


def render_fallback(results: list[MetricResult]) -> str:
    """Compose the answer deterministically, with no model involvement.

    Used when the model's answer was rejected. It is plainer than a written
    reply, but every number in it came straight from the database, which is the
    property that matters when the alternative is a confident invented one.
    """
    lines: list[str] = []
    for result in results:
        if result.failed or result.is_empty:
            continue
        read_at = result.read_at.strftime("%H:%M UTC") if result.read_at else "unknown time"
        # The subject belongs here too. Without it the fallback listing for a
        # patient lookup says "Patient status" over a row identified only by a
        # label, which tells the reader nothing about who it is.
        heading = result.label + (" for " + result.subject if result.subject else "")
        lines.append(heading + " (read at " + read_at + "):")
        for row in result.rows:
            pairs = [
                str(name).replace("_", " ") + " " + format_value(value)
                for name, value in sorted(row.values.items())
            ]
            lines.append("- " + ", ".join(pairs))
        lines.append("")

    if not lines:
        return ""
    lines.append("These are the recorded figures. Nothing has been calculated from them.")
    return "\n".join(lines).strip()
