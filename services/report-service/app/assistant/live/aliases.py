from __future__ import annotations

import re

from app.assistant.sanitize import sanitize_untrusted_content

# Patient lookup is the only place the assistant reads a row that is about one
# person, and this module is what keeps the published guarantee true anyway:
# the model is never shown a patient's name.
#
# A row about a patient carries an opaque label - PATIENT_1, PATIENT_2 - issued
# here for the life of one request. The model answers about the label; the
# server puts the real name back afterwards, once the answer has been sanitised
# and its figures checked.
#
# The label is sequential rather than a database id on purpose. A UUID is itself
# a re-identifier: it is stable across requests, so a vendor holding two
# transcripts could tell that both were about the same person even without ever
# seeing a name. A number that restarts at 1 every request carries no
# information outside that request.
#
# Nothing here is persisted, cached, or logged. The table is created inside the
# request that uses it, referenced by nothing else, and collected with it.

ALIAS_PREFIX = "PATIENT_"

# The field a pseudonymised row carries in place of the columns it was built
# from. execution.py substitutes it before any prompt text is assembled.
ALIAS_FIELD = "patient"

# One label, matched whole. \b on both sides is what stops PATIENT_1 matching
# the front of PATIENT_12: the substitution is done in a single pass over the
# answer with the captured number looked up, never by replacing label strings
# one after another. Sequential replacement would rewrite PATIENT_1 inside
# PATIENT_12 and hand one patient's name to another patient's row.
_LABEL = re.compile(r"\b" + ALIAS_PREFIX + r"(\d+)\b")

# A name is a name, not a paragraph. The ceiling is the projection's, not a
# judgement about how long a name may be.
MAX_NAME_CHARS = 200

# What is written when the recorded name sanitises away to nothing. full_name is
# NOT NULL in the tenant schema, so this is a defensive branch rather than an
# expected one; it must still never leave the label in the answer, because a
# label is meaningless to the reader.
UNNAMED = "the patient"


class AliasTable:
    """Per-request mapping between a patient's real identity and a label.

    Created by the request that needs it and discarded with it. It is deliberately
    not a cache, not a singleton, and not something a caller can look a name up in
    by patient number: the only way out of it is rehydrate(), which replaces
    labels the model was actually shown.
    """

    __slots__ = ("_label_by_real_id", "_name_by_number", "_next")

    def __init__(self) -> None:
        self._label_by_real_id: dict[str, str] = {}
        self._name_by_number: dict[int, str] = {}
        self._next = 1

    def __repr__(self) -> str:
        """Deliberately contentless.

        The default dataclass-style repr would put every real name into any log
        line, traceback, or debugger frame that happened to render the table.
        """
        return f"<AliasTable issued={len(self._name_by_number)}>"

    @property
    def issued(self) -> int:
        """How many labels this request has handed out."""
        return len(self._name_by_number)

    def issue(self, real_id: str, display_name: str) -> str:
        """Return the label standing for one patient in this request.

        Keyed on the real id, so two rows about the same patient - a queue row
        and an admission row, say - share one label rather than reading as two
        people.
        """
        key = str(real_id or "")
        existing = self._label_by_real_id.get(key)
        if existing is not None:
            return existing

        number = self._next
        self._next += 1
        label = ALIAS_PREFIX + str(number)
        self._label_by_real_id[key] = label
        self._name_by_number[number] = str(display_name or "")
        return label

    def rehydrate(self, answer: str) -> tuple[str, bool]:
        """Put the real names back. Returns (text, ok).

        ok is False when the answer names a label this request never issued.
        That rejects the whole answer rather than stripping the bad label and
        keeping the rest, for the reason cds/differential.build_considerations
        drops a whole consideration rather than editing it: rewriting means the
        server deciding what the model meant, and an invented patient label is
        exactly the case where that guess must not be made.

        Each substituted name goes through the same sanitiser as retrieved
        content on the way in. The answer has already been sanitised by this
        point, so a name carrying markup would otherwise reintroduce markup into
        text that was checked before the name was in it.
        """
        if not answer:
            return "", True

        invented = False

        def swap(match: re.Match[str]) -> str:
            nonlocal invented
            number = int(match.group(1))
            name = self._name_by_number.get(number)
            if name is None:
                invented = True
                return match.group(0)
            return sanitize_untrusted_content(name, MAX_NAME_CHARS) or UNNAMED

        rehydrated = _LABEL.sub(swap, answer)
        if invented:
            return answer, False
        return rehydrated, True
