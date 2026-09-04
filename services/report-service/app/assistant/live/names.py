from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import text

from app.assistant.live.catalog.patients import LOOKUP_TRIGGERS
from app.assistant.retrieval import _tokenize
from app.db.tenant import tenant_session

logger = logging.getLogger("assistant.live")

# Finding one patient from a name typed into the chat.
#
# Staff do not carry patient numbers in their heads. "Where is Amina Mwita?" is
# the question they actually ask, and until now it reached nothing at all,
# because the patient tier only ever resolved a PT- or VIS- identifier.
#
# This resolves a name to a patient *number*, and then the ordinary
# patient.status metric runs on that number. Nothing else changes: the same
# query, the same column allowlist, the same role gate, and the same aliasing,
# so a name-shaped question and a number-shaped question are answered by exactly
# the same code with exactly the same guarantees.
#
# Two things about it are deliberate and worth reading before changing it.
#
# **A tie is never broken.** "Best match wins" is the obvious design and it is
# the wrong one here. Two patients called Amina are common; picking the
# higher-scoring row and reporting a bed number would be a confident answer
# about the wrong person, which in a ward is not a cosmetic failure. When more
# than one patient matches equally the caller is asked for the number instead.
#
# **A tie is never itemised.** The reply says how many matched, never who. A
# list of names would turn a guessed first name into a way of harvesting the
# patient list, which is a worse hole than the one this closes.

# How many candidate rows to consider. Small on purpose: this is a lookup, not a
# search, and a question matching dozens of people is not a question this can
# answer whatever the ceiling is.
MAX_CANDIDATES = 8

# The shortest run of letters that may be treated as part of a name. Two-letter
# tokens match far too much, and every real given name in the tenant is longer.
MIN_NAME_TOKEN = 3

# Words that are never part of a patient's name, however the question is phrased.
# The retrieval stopwords already cover "where", "is", "patient" and the rest;
# these are the ordinary English and Swahili that the stopword list does not.
_NOT_A_NAME: frozenset[str] = frozenset(
    {
        "the", "for", "and", "her", "his", "him", "she", "they", "them", "their",
        "which", "whose", "whom", "many", "much", "more", "most", "some", "all",
        "was", "were", "has", "have", "had", "does", "did", "will", "would",
        "could", "should", "still", "right", "there", "here", "into", "than",
        "please", "tell", "find", "look", "check", "know", "about", "any",
        "one", "this", "that", "these", "those", "give", "show", "list", "see",
        "new", "register", "registered", "record", "records", "details",
        # Time words. resolve_window already reads these; they are never a name.
        "today", "leo", "now", "sasa", "yesterday", "jana", "week", "month",
        # Titles and forms of address, English and Swahili.
        "mgonjwa", "wagonjwa", "bwana", "bibi", "mama", "baba", "ndugu",
        "mister", "mrs", "doctor", "nurse", "sister", "daktari", "muuguzi",
    }
)

# Words that say the question is about where a *person* is.
#
# This is the gate that keeps name resolution off the common path, and it is a
# positive signal on purpose. Candidate name words alone are not enough: "how do
# I register a new patient" leaves "register" and "new" behind, and firing on
# that opened a database session for a question the content pack answers by
# itself, which broke the standing guarantee that a how-do-I question costs no
# query. Blacklisting "register" and "new" would have fixed that one question and
# not the next one; requiring a positive signal fixes the class.
#
# Matched as whole words against the lower-cased question rather than against
# _tokenize's output, because most of these ("where", "who") are stopwords that
# _tokenize deliberately throws away.
_PERSON_QUESTION_WORDS: frozenset[str] = frozenset(
    {
        "where", "who", "status", "located", "location", "admitted", "admission",
        "ward", "bed", "queue", "waiting", "owe", "owes", "balance",
        "outstanding", "discharged",
        # Swahili: wapi/yuko -> where/is, hali -> status, kitanda -> bed,
        # wodi -> ward, foleni -> queue, deni -> debt, amelazwa -> is admitted.
        "wapi", "yuko", "hali", "kitanda", "wodi", "foleni", "deni", "amelazwa",
    }
)

# Runs of letters, which is all a whole-word test needs here. Deliberately a
# character class and no escape sequence: an earlier version of this module
# carried a word-boundary escape that had been corrupted into a control
# character, so the pattern silently matched nothing and every name question was
# quietly refused. A character class cannot fail that way.
_WORDS = re.compile("[a-z]+")


class NameOutcome(str, Enum):
    """What resolving a name from a question came to."""

    # Nothing in the question looked like a name, so no query was run at all.
    NOT_ASKED = "not_asked"
    # Exactly one patient matched best. patient_number is set.
    RESOLVED = "resolved"
    # Several matched equally well. The caller is asked for a number instead.
    AMBIGUOUS = "ambiguous"
    # A name was asked for and nobody matched it.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class NameMatch:
    """The result of resolving a name. Carries no name back to the caller.

    `patient_number` is the only identifying value here, and only when exactly
    one patient matched. `matches` is a count, never a list: the number of people
    who share a name is not itself identifying, and the names are what must not
    travel back to somebody who only guessed one.
    """

    outcome: NameOutcome
    patient_number: str | None = None
    matches: int = 0

    @property
    def is_resolved(self) -> bool:
        return self.outcome is NameOutcome.RESOLVED and bool(self.patient_number)


def looks_like_a_person_question(question: str) -> bool:
    """Whether this asks where somebody is, rather than how to do something."""
    if not question:
        return False
    return bool(set(_WORDS.findall(question.lower())) & _PERSON_QUESTION_WORDS)


def _operational_vocabulary() -> frozenset[str]:
    """Every word that routes a figure, and so is never part of a name.

    Built from the registry rather than written out, so it grows with the
    catalog. The alternative - a hand-kept blacklist - was tried first and was
    already wrong: "beds" is a trigger on beds.availability but the singular
    "bed" was the only form listed, so "How many beds are free?" offered "beds"
    and "free" as candidate names. A word good enough to select a figure is
    operational vocabulary by definition.
    """
    from app.assistant.live.registry import METRIC_REGISTRY, load_catalog

    load_catalog()
    vocabulary: set[str] = set(LOOKUP_TRIGGERS)
    for definition in METRIC_REGISTRY.values():
        vocabulary |= definition.triggers
    return frozenset(vocabulary)


def candidate_name_terms(question: str, known_wards: list[str] | None = None) -> list[str]:
    """The words in a question that could plausibly be part of a patient's name.

    Deliberately crude, and safe because of what happens next: these are matched
    against whole words of `full_name` in the database, so a word that is not
    somebody's name simply matches nobody. The filtering here exists to keep the
    query cheap and to stop an operational word - "bed", "queue", "maternity" -
    resolving to a patient who happens to share it.
    """
    if not question:
        return []

    # The raw question only. Expanding it first was tempting - it is what
    # routing does - but expansion *adds* English translations of Swahili terms,
    # and every added word is another chance to match some unrelated patient's
    # name and score a hit for it. Swahili is handled by filtering instead:
    # _tokenize already drops SWAHILI_STOPWORDS, and the courtesy words and
    # operational terms are named above.
    terms = _tokenize(question)

    ward_words: set[str] = set()
    for ward in known_wards or []:
        ward_words |= _tokenize(ward)

    operational = _operational_vocabulary()

    def keep(term: str) -> bool:
        return (
            len(term) >= MIN_NAME_TOKEN
            and term.isalpha()
            and term not in _NOT_A_NAME
            and term not in _PERSON_QUESTION_WORDS
            and term not in operational
            and term not in ward_words
        )

    # In the order they were written, not sorted. The order is what makes
    # name_as_typed below read like a name rather than like an index entry:
    # "Peter Kimaro", not "Kimaro Peter".
    seen: set[str] = set()
    ordered: list[str] = []
    for token in _WORDS.findall(question.lower()):
        if token in terms and token not in seen and keep(token):
            seen.add(token)
            ordered.append(token)
    return ordered


def name_as_typed(question: str, known_wards: list[str] | None = None) -> str:
    """The name the staff member wrote, tidied for display. Never a stored name.

    Used as the heading of a patient figure that was found by name, and built
    only from the question. That distinction is the whole point: the model is
    shown what the caller typed, which it has seen already, and never the name
    the database holds - that stays behind the alias and is put back by the
    server after the answer.

    It has to be there. Headed with the patient *number* the lookup resolved to,
    a figure answers a question the caller never asked: they typed "Peter
    Kimaro" and the figure said "for PT-20260829-0028", so the model could not
    tell that the two referred to the same person and refused a row it had in
    full.
    """
    return " ".join(
        term.capitalize() for term in candidate_name_terms(question, known_wards)
    )


# Whole-word matching against the recorded name, scored by how many of the
# question's candidate words a patient's name actually contains.
#
# Whole words rather than a substring: "ana" must not match "Amina", and a
# LIKE over a name column is both slower and much looser. The terms are bound as
# an array - never concatenated - so this is parameterised like every other
# query in the catalog.
#
# is_active is checked here for the same reason patient.status checks it: a
# deactivated record must not be reachable by any route.
_RESOLVE_SQL = """
    SELECT p.patient_number AS patient_number,
           (
               SELECT COUNT(DISTINCT w)
               FROM unnest(string_to_array(lower(p.full_name), ' ')) AS w
               WHERE w = ANY(:terms)
           ) AS hits
    FROM patients p
    WHERE p.is_active
      AND EXISTS (
          SELECT 1
          FROM unnest(string_to_array(lower(p.full_name), ' ')) AS w
          WHERE w = ANY(:terms)
      )
    ORDER BY hits DESC, p.patient_number
    LIMIT :ceiling
"""


async def resolve_patient_by_name(
    tenant_id: str, question: str, known_wards: list[str] | None = None
) -> NameMatch:
    """Resolve a patient number from a name in the question. Never raises.

    A failure of any kind - an unreachable database, a timeout - is NOT_ASKED,
    which degrades to the behaviour before names were understood at all: the
    patient tier simply does not run.
    """
    if not looks_like_a_person_question(question):
        return NameMatch(NameOutcome.NOT_ASKED)

    terms = candidate_name_terms(question, known_wards=known_wards)
    if not tenant_id or not terms:
        return NameMatch(NameOutcome.NOT_ASKED)

    try:
        # Imported inside the call so that resolving a name costs nothing at
        # import time and this module stays independent of execution's own
        # import graph.
        from app.assistant.live.execution import _prepare_readonly

        async with tenant_session(tenant_id) as session:
            await _prepare_readonly(session)
            result = await session.execute(
                text(_RESOLVE_SQL), {"terms": terms, "ceiling": MAX_CANDIDATES}
            )
            rows = result.fetchall()
    except Exception:
        logger.warning("could not resolve a patient name, answering without it")
        return NameMatch(NameOutcome.NOT_ASKED)

    if not rows:
        return NameMatch(NameOutcome.UNKNOWN)

    best = max(int(row.hits) for row in rows)
    winners = [row for row in rows if int(row.hits) == best]

    # The tie is not broken. See the note at the top of this module: one of two
    # patients called Amina is not an answer, it is a coin toss with a bed
    # number attached.
    if len(winners) > 1:
        return NameMatch(NameOutcome.AMBIGUOUS, matches=len(winners))

    return NameMatch(
        NameOutcome.RESOLVED, patient_number=str(winners[0].patient_number), matches=1
    )
