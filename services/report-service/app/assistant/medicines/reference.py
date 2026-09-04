"""Reading the medicines pack: what a question names, and what the pack says.

Everything here is deterministic and reads only the repo-shipped pack. No
database session is opened, no patient record is touched, and nothing a model
returned is ever fed back in. The model's only involvement with this module is
downstream of it: it is handed the block this file renders, and its answer is
handed back to `validate_doses` before anybody sees it.
"""

from __future__ import annotations

import re

from shared.medicines.matching import (
    MAX_MEDICINES_PER_LOOKUP as MAX_MEDICINES_PER_QUESTION,
    find_medicines,
    interactions_between,
    normalise as _normalise,
    pack_version,
    worst_severity as severity_headline,
)
from shared.medicines.models import (
    InteractionRule,
    Monograph,
    Population,
    PregnancyStance,
    Severity,
)
from shared.medicines.pack import MONOGRAPHS

__all__ = [
    "MAX_MEDICINES_PER_QUESTION",
    "alternatives_named",
    "detect_populations",
    "find_medicines",
    "has_forbidden_reassurance",
    "interactions_between",
    "is_medicines_question",
    "pack_version",
    "render_block",
    "render_fallback",
    "severity_headline",
    "unresolved_names",
    "validate_doses",
]


# ---------------------------------------------------------------------------
# Finding what the question is about
# ---------------------------------------------------------------------------

# A number with a unit, e.g. "500 mg", "5mg", "10 ml", "80 micrograms".
_DOSE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|microgram|micrograms|g|kg|ml|l|iu|units?|mmol|%)\b"
    r"(?:\s*/\s*(?:kg|day|dose|24\s*hours))?",
    re.IGNORECASE,
)

# Words that make a question clinical on their own.
#
# These are strong enough to route a question here even when it names no
# medicine at all, so that a clinician who asks "do these two interact" is asked
# which two rather than being refused. Nothing here is a word somebody reaches
# for while asking how to use the software.
_CLINICAL_VOCABULARY = (
    "interact", "interacts", "interaction", "interactions", "contraindicated",
    "contraindication", "compatible", "overdose", "side effect", "side effects",
    "adverse", "safe to take", "breastfeeding", "breast feeding", "lactating",
    "painkiller", "painkillers", "antibiotic", "antibiotics",
    # Asking whether things go together is asking about medicines. Nobody uses
    # these words to ask how to work a screen, and "can these two be used
    # together" has to reach the reference even when neither has been named yet.
    "together", "combination", "combine", "combined",
    # Swahili. The shared expansion map in language.py translates "dawa" to
    # pharmacy for retrieval, which is the operational sense; here it is the
    # clinical one, so the terms are listed in their own right.
    "madhara", "mwingiliano", "kipimo cha dawa", "pamoja",
)

# Words that appear in medicine questions but are not on their own a reason to
# take the question off the operational path.
#
# "How do I prescribe in the system" is a workflow question that happens to
# contain "prescribe", and the operational assistant answers it well. So these
# route here only alongside a medicine the reference actually carries - at which
# point the question is unmistakably about that medicine.
_WEAK_MEDICINES_VOCABULARY = (
    "medicine", "medicines", "medication", "medications", "drug", "drugs",
    "dose", "dosage", "doses", "dosing", "prescribe", "prescribing",
    "allergy", "allergic", "dawa", "kipimo",
)

_MEDICINES_VOCABULARY = _CLINICAL_VOCABULARY + _WEAK_MEDICINES_VOCABULARY

# Questions that mention a medicine but are about running the software: stock,
# dispensing, prescriptions on a screen. Those belong to the operational
# assistant, which already answers them, and must not be pulled in here.
_OPERATIONAL_VOCABULARY = (
    "stock", "inventory", "shelf", "reorder", "expiry", "expired", "batch",
    "dispense", "dispensing", "dispensed", "screen", "page", "button", "menu",
    "tab", "system", "record", "enter", "log in", "sign in", "report",
    "reports", "queue", "invoice", "bill", "billing", "hisa", "ghala",
)


# Words that appear in medicine questions and are not medicines. Without this,
# "can these tablets be used together" reports "tablets" as an unknown medicine.
_NOT_A_MEDICINE = frozenset(
    {
        "about", "adult", "adults", "after", "again", "against", "allergic",
        "allergy", "along", "already", "alternative", "always", "amount",
        "another", "answer", "before", "being", "below", "between", "breast",
        "breastfeeding", "capsule", "capsules", "check", "child", "children",
        "combination", "combine", "combined", "contraindicated", "could",
        "daily", "dangerous", "different", "dosage", "dose", "doses", "drink",
        "drug", "drugs", "during", "effect", "effects", "elderly", "every",
        "female", "first", "given", "giving", "hours", "hourly", "impaired",
        "infusion", "inject", "injection", "instead", "interact", "interaction",
        "interactions", "kidney", "large", "liver", "male", "measure",
        "medication", "medications", "medicine", "medicines", "might", "month",
        "months", "morning", "mother", "nightly", "nursing", "old", "older",
        "other", "patient", "patients", "pregnancy", "pregnant", "prescribe",
        "prescribed", "prescription", "problem", "renal", "safe", "safely",
        "should", "since", "started", "starting", "still", "syrup", "table",
        "tablet", "tablets", "taken", "taking", "there", "these", "third",
        "those", "three", "times", "together", "treat", "treated", "treatment",
        "trimester", "twice", "under", "using", "weeks", "which", "while",
        "with", "without", "woman", "women", "would", "years", "young",
    }
    # Every word that makes a question a medicines question is, by definition,
    # not itself a medicine. Folding the vocabulary in here rather than
    # repeating it keeps the two from drifting: "painkiller" was in one list and
    # not the other, so asking "what painkiller can I prescribe" reported
    # "painkiller" back as a medicine the reference does not carry.
    | {word for term in _MEDICINES_VOCABULARY for word in term.split()}
    | {word for term in _OPERATIONAL_VOCABULARY for word in term.split()}
)


# The stems medicine names are built from. A name ending in one of these is
# almost certainly a medicine, whether or not this pack carries it, and that is
# a far better signal than whether somebody typed a dose beside it.
#
# Ordered longest first so the specific stem is reported rather than a shorter
# one inside it. The list is deliberately conservative: a stem short or common
# enough to match ordinary English is left out, because the cost of a false
# positive is telling a clinician their perfectly good word is an unrecognised
# medicine.
_MEDICINE_STEMS = (
    "sulfamethoxazole", "hydrochlorothiazide", "glitazone", "prazole", "triptan",
    "barbital", "sartan", "statin", "cillin", "mycin", "micin", "cycline",
    "floxacin", "oxacin", "azepam", "zepam", "profen", "codone", "peridol",
    "thiazide", "semide", "pramine", "oxetine", "dipine", "formin", "caine",
    "olol", "azole", "parin", "tidine", "zosin", "penem", "amide", "azine",
    "ectin", "grel", "pril", "vir", "mab", "tinib", "toin", "quine", "cept",
    "arone", "idone", "afil", "setron", "vastatin", "conazole", "trexate",
    "gliptin", "tocin", "prostol", "oxin", "done", "pam", "ine", "ide", "ate",
    "one", "ol", "racetam", "virenz", "fulvin", "quantel", "ixime", "idime",
)

# Stems that identify a medicine from the front rather than the back. No English
# word begins "cef" or "ceph", so a whole class of antibiotics is recognised
# from two syllables.
_MEDICINE_PREFIXES = ("cef", "ceph")

# Names built like nothing else, which no rule of thumb will reach.
_IRREGULAR_MEDICINE_NAMES = frozenset({"insulin", "lithium"})

# Words that end like a medicine name and are not one. Without these, ordinary
# English in a clinical question - "protocol", "routine", "determine" - is
# reported back to the clinician as a medicine the reference does not carry.
_NOT_A_MEDICINE_NAME = frozenset(
    {
        "alcohol", "alone", "anyone", "baseline", "before", "candidate",
        "certain", "chocolate", "combine", "concentrate", "control", "deadline",
        "decline", "define", "determine", "discipline", "done", "duplicate",
        "elsewhere", "engine", "everyone", "examine", "female", "guideline",
        "headline", "imagine", "immediate", "indicate", "machine", "medicine",
        "moderate", "morphine", "nicotine", "offline", "online", "outline",
        "override", "phone", "protocol", "provide", "regulate", "routine",
        "separate", "someone", "state", "sunshine", "telephone", "template",
        "timeline", "update", "vaccine", "whole",
    }
)

# Words that join two medicines in a question: "warfarin and amiodarone".
_JOINERS = frozenset({"and", "with", "plus", "or", "alongside", "na", "pamoja"})


def _looks_like_a_medicine_name(word: str) -> bool:
    """Whether a word is built the way medicine names are built."""
    if word in _IRREGULAR_MEDICINE_NAMES:
        return True
    if len(word) < 6 or word in _NOT_A_MEDICINE_NAME:
        return False
    return word.endswith(_MEDICINE_STEMS) or word.startswith(_MEDICINE_PREFIXES)


def _beside_a_known_medicine(words: list[str], index: int, known: set[str]) -> bool:
    """Whether this word stands where the second medicine in a pair stands.

    "Warfarin and amiodarone" gives amiodarone away by position alone, which
    catches the names whose stems this file does not know.
    """
    for step in (-2, 2):
        neighbour = index + step
        joiner = index + (step // 2)
        if 0 <= neighbour < len(words) and 0 <= joiner < len(words):
            if words[joiner] in _JOINERS and words[neighbour] in known:
                return True
    return False


def unresolved_names(text: str, found: list[Monograph]) -> list[str]:
    """Medicines the clinician named that the pack does not carry.

    Only ever used to say "the reference has no entry for X", and - where the
    model fallback is switched on - to decide what to ask the model about. It
    never guesses what X might be and never answers about a near neighbour:
    answering about a different medicine from the one asked about is the single
    worst thing a medicines reference can do.

    A word counts as a medicine somebody asked about if it carries a dose, if it
    is built like a medicine name, or if it stands beside one the pack carries.
    Requiring a dose - which is all this used to do - meant "can warfarin and
    amiodarone be used together" reported nothing unknown at all, and the
    question came back confidently answered about half of itself.
    """
    haystack = _normalise(text)
    known: set[str] = set()
    for monograph in MONOGRAPHS:
        for name in monograph.names:
            known.update(_normalise(name).split())

    words = haystack.split()
    dosed = _dosed_words(haystack)

    named: list[str] = []
    for index, raw in enumerate(words):
        word = raw.strip(",;:()?.!")
        if not word or len(word) < 5:
            continue
        if word in known or word in _NOT_A_MEDICINE:
            continue
        if word in named:
            continue
        if (
            word in dosed
            or _looks_like_a_medicine_name(word)
            or _beside_a_known_medicine(words, index, known)
        ):
            named.append(word)

    return named[:MAX_MEDICINES_PER_QUESTION]


def _dosed_words(haystack: str) -> set[str]:
    """Words immediately before or after a dose, e.g. "lisinopril 10 mg"."""
    dosed: set[str] = set()
    for match in _DOSE.finditer(haystack):
        before = haystack[: match.start()].split()
        after = haystack[match.end():].split()
        if before:
            dosed.add(before[-1].strip(",;:()"))
        if after:
            dosed.add(after[0].strip(",;:()"))
    return dosed


# ---------------------------------------------------------------------------
# Which population the question is about
# ---------------------------------------------------------------------------

_POPULATION_TERMS: tuple[tuple[Population, tuple[str, ...]], ...] = (
    (
        Population.PREGNANCY,
        (
            "pregnant", "pregnancy", "expecting", "antenatal", "trimester",
            "gestation", "in utero", "obstetric", "mjamzito", "ujauzito",
            "mimba", "ana mimba",
        ),
    ),
    (
        Population.BREASTFEEDING,
        (
            "breastfeeding", "breast feeding", "breast-feeding", "lactating",
            "lactation", "nursing mother", "kunyonyesha", "ananyonyesha",
        ),
    ),
    (
        Population.RENAL,
        (
            "renal", "kidney", "kidneys", "dialysis", "egfr", "creatinine",
            "figo", "ugonjwa wa figo",
        ),
    ),
    (Population.HEPATIC, ("liver", "hepatic", "cirrhosis", "jaundice", "ini")),
    (
        Population.CHILD,
        (
            "child", "children", "infant", "baby", "neonate", "newborn",
            "paediatric", "pediatric", "mtoto", "watoto",
        ),
    ),
    (Population.ELDERLY, ("elderly", "older patient", "mzee", "geriatric")),
)


def detect_populations(text: str) -> list[Population]:
    """Which groups the clinician said the question is about.

    Read from their words only. This capability opens no database and reads no
    patient record, so pregnancy is surfaced because somebody wrote "pregnant",
    never because the assistant went looking for a patient's status.
    """
    haystack = _normalise(text)
    if not haystack:
        return []
    detected: list[Population] = []
    for population, terms in _POPULATION_TERMS:
        if any(re.search(r"\b" + re.escape(_normalise(term)) + r"\b", haystack) for term in terms):
            detected.append(population)
    return detected


# ---------------------------------------------------------------------------
# Is this a medicines question at all
#
# The two vocabularies this reads are declared further up, because the
# not-a-medicine list is built from them.
# ---------------------------------------------------------------------------


def _mentions(haystack: str, terms: tuple[str, ...]) -> bool:
    """Whether any term appears as a whole word.

    Whole words rather than substrings, because "systemic infection" contains
    "system" and would otherwise be read as a question about the software.
    """
    return any(
        re.search(r"\b" + re.escape(term) + r"\b", haystack) for term in terms
    )


def is_medicines_question(text: str) -> bool:
    """Whether this question should be answered from the medicines reference.

    Three gates, in order, and each of them narrows rather than widens:

    * A question about stock, dispensing or a screen stays with the operational
      assistant, however many medicines it names. It answers those well today,
      and a new capability must not take answers away from an old one.
    * A question naming a medicine the reference carries comes here.
    * A question naming none still comes here if it is unmistakably clinical, so
      "do these two interact" is answered with "which two" rather than with a
      refusal. Words like "prescribe" or "dose" are not enough on their own:
      "how do I prescribe in the system" is a workflow question.
    """
    haystack = _normalise(text)
    if not haystack:
        return False

    if _mentions(haystack, _OPERATIONAL_VOCABULARY):
        return False

    if find_medicines(haystack):
        return True

    return _mentions(haystack, _CLINICAL_VOCABULARY)


# ---------------------------------------------------------------------------
# What the pack says, rendered for a reader
# ---------------------------------------------------------------------------


def _population_lines(monograph: Monograph, populations: list[Population]) -> list[str]:
    """The monograph fields the question actually asked about.

    A pregnancy question gets the pregnancy line first and in full. A question
    that named no population still gets the pregnancy stance where the reference
    says contraindicated, because "do not use this in pregnancy" is not a detail
    to be surfaced only when asked.
    """
    lines: list[str] = []
    asked = set(populations)

    if Population.PREGNANCY in asked or monograph.pregnancy_stance is PregnancyStance.CONTRAINDICATED:
        if monograph.pregnancy:
            lines.append(
                "In pregnancy ("
                + monograph.pregnancy_stance.value.replace("_", " ")
                + "): "
                + monograph.pregnancy
            )
        elif monograph.pregnancy_stance is not PregnancyStance.NOT_STATED:
            lines.append("In pregnancy: " + monograph.pregnancy_stance.value.replace("_", " "))
        else:
            lines.append("In pregnancy: the reference carries no statement for this medicine.")

    if Population.BREASTFEEDING in asked:
        lines.append(
            "While breastfeeding: "
            + (monograph.breastfeeding or "the reference carries no statement for this medicine.")
        )
    if Population.RENAL in asked:
        lines.append(
            "In kidney impairment: "
            + (monograph.renal or "the reference carries no statement for this medicine.")
        )
    if Population.HEPATIC in asked:
        lines.append(
            "In liver impairment: "
            + (monograph.hepatic or "the reference carries no statement for this medicine.")
        )
    if Population.CHILD in asked:
        lines.append(
            "In children: this reference carries adult doses only. Use the paediatric "
            "dosing chart, which doses by weight."
        )
    if Population.ELDERLY in asked:
        lines.append(
            "In older patients: start at the lower end of the dose range and review "
            "kidney function, which falls with age even where creatinine looks normal."
        )
    return lines


def render_monograph(monograph: Monograph, populations: list[Population]) -> str:
    """One medicine, rendered as inert reference text."""
    lines = [
        "Medicine: " + monograph.generic_name,
        "Class: " + monograph.class_label,
        "Used for: " + monograph.used_for,
        "Usual adult dose: " + monograph.adult_dose,
    ]
    if monograph.max_adult_dose:
        lines.append("Maximum adult dose: " + monograph.max_adult_dose)
    lines.extend(_population_lines(monograph, populations))
    if monograph.monitoring:
        lines.append("Monitoring: " + monograph.monitoring)
    for caution in monograph.cautions:
        lines.append("Caution: " + caution)
    return "\n".join(lines)


def render_interaction(
    first: Monograph, second: Monograph, rule: InteractionRule
) -> str:
    return "\n".join(
        [
            first.generic_name + " with " + second.generic_name + ": " + rule.severity.headline,
            "What happens: " + rule.effect,
            "What to do: " + rule.management,
        ]
    )


# How many alternatives one extract may carry. An answer that lists six
# substitutes is not an answer, and the clinician asked about the medicines they
# named.
MAX_ALTERNATIVES = 4


def alternatives_named(
    monographs: list[Monograph],
    interactions: list[tuple[Monograph, Monograph, InteractionRule]],
) -> list[Monograph]:
    """Medicines the reference itself puts forward instead of the ones asked about.

    Read only from the fields where the reference recommends a substitute - the
    population statements on a monograph, and the management line of a rule -
    so this picks up "change to methyldopa or nifedipine" and "use paracetamol
    for pain instead" without dragging in every medicine mentioned in passing.

    Without this the extract names a medicine and says nothing about it, and an
    answer that fills the gap with a dose gets rejected by validate_doses - the
    guard doing its job on a question the extract should have answered itself.
    """
    already = {monograph.drug_id for monograph in monographs}

    recommendations: list[str] = []
    for monograph in monographs:
        recommendations.extend(
            [
                monograph.pregnancy,
                monograph.breastfeeding,
                monograph.renal,
                monograph.hepatic,
            ]
        )
    for _, _, rule in interactions:
        recommendations.append(rule.management)

    found: list[Monograph] = []
    for text in recommendations:
        for candidate in find_medicines(text or ""):
            if candidate.drug_id in already:
                continue
            already.add(candidate.drug_id)
            found.append(candidate)
            if len(found) == MAX_ALTERNATIVES:
                return found
    return found


def render_alternative(monograph: Monograph) -> str:
    """An alternative, in the two lines that make it usable: what and how much."""
    lines = [
        monograph.generic_name
        + " ("
        + monograph.class_label
        + ") - usual adult dose: "
        + monograph.adult_dose
    ]
    if monograph.pregnancy:
        lines.append("  In pregnancy: " + monograph.pregnancy)
    return "\n".join(lines)


def render_block(
    monographs: list[Monograph],
    interactions: list[tuple[Monograph, Monograph, InteractionRule]],
    populations: list[Population],
) -> str:
    """The reference material for one question, as data for the prompt.

    Everything the model is allowed to say about medicines is in here. It is
    labelled as data, and the instructions tell the model that text inside it is
    never an instruction, but the real protection is that this is the only
    pharmacology it has: there is no tool it can call and no field of the answer
    the server does not check.
    """
    if not monographs and not interactions:
        return ""

    sections: list[str] = []
    for monograph in monographs:
        sections.append(render_monograph(monograph, populations))

    if interactions:
        rendered = [render_interaction(*hit) for hit in interactions]
        sections.append(
            "Interactions between the medicines named, as classified by this "
            "reference:\n\n" + "\n\n".join(rendered)
        )
    elif len(monographs) > 1:
        sections.append(
            "Interactions between the medicines named: this reference lists no "
            "interaction between them. That means nothing is recorded here, not "
            "that the combination has been established as safe."
        )

    alternatives = alternatives_named(monographs, interactions)
    if alternatives:
        sections.append(
            "Medicines named above as alternatives. Their doses are given so "
            "you can state one if you suggest the alternative; they were not "
            "asked about, and no interaction has been checked for them:\n\n"
            + "\n\n".join(render_alternative(m) for m in alternatives)
        )

    return "\n\n".join(sections)


def render_fallback(
    monographs: list[Monograph],
    interactions: list[tuple[Monograph, Monograph, InteractionRule]],
    populations: list[Population],
) -> str:
    """The answer composed by the server, with no model involvement.

    Used when the model's answer was rejected. It reads as a reference extract
    rather than as a reply, which is the point: every word of it came from the
    pack, and that is what matters when the alternative is a fluent answer with
    an invented number in it.
    """
    if not monographs and not interactions:
        return ""

    parts: list[str] = []
    if interactions:
        parts.append("From the hospital medicines reference:")
        for first, second, rule in interactions:
            parts.append(
                "- "
                + first.generic_name
                + " with "
                + second.generic_name
                + " - "
                + rule.severity.headline
                + ". "
                + rule.effect
                + " "
                + rule.management
            )

    for monograph in monographs:
        parts.append("")
        parts.append(monograph.generic_name + " (" + monograph.class_label + ")")
        parts.append("- Usual adult dose: " + monograph.adult_dose)
        if monograph.max_adult_dose:
            parts.append("- Maximum adult dose: " + monograph.max_adult_dose)
        for line in _population_lines(monograph, populations):
            parts.append("- " + line)
        for caution in monograph.cautions:
            parts.append("- " + caution)

    alternatives = alternatives_named(monographs, interactions)
    if alternatives:
        parts.append("")
        parts.append("Alternatives named above, with their usual adult doses:")
        for alternative in alternatives:
            parts.append(
                "- " + alternative.generic_name + ": " + alternative.adult_dose
            )

    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Checking the model's answer
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_LIST_MARKER = re.compile(r"(?m)^\s*\d+[.)]\s")
_DASHES = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-"})

# What a number has to be attached to for it to be a claim about a dose.
#
# Units of amount and units of time both count, because a dose is an amount and
# an interval and getting either wrong is a prescribing error: "500 mg every 4
# hours" against a reference that says every 8 is a doubled daily dose, written
# in numbers the reference does contain.
_UNITS = {
    "mg": "mg", "mcg": "microgram", "microgram": "microgram",
    "micrograms": "microgram", "g": "g", "gram": "g", "grams": "g",
    "kg": "kg", "ml": "ml", "l": "l", "litre": "l", "litres": "l",
    "iu": "iu", "unit": "unit", "units": "unit", "mmol": "mmol", "%": "%",
    "tablet": "tablet", "tablets": "tablet", "capsule": "capsule",
    "capsules": "capsule", "dose": "dose", "doses": "dose", "drop": "drop",
    "drops": "drop", "puff": "puff", "puffs": "puff",
    "hour": "hour", "hours": "hour", "hourly": "hour", "minute": "minute",
    "minutes": "minute", "day": "day", "days": "day", "daily": "day",
    "week": "week", "weeks": "week", "month": "month", "months": "month",
    "time": "time", "times": "time",
}

_UNIT_PATTERN = "|".join(
    sorted((re.escape(unit) for unit in _UNITS), key=len, reverse=True)
)
_MEASURE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(" + _UNIT_PATTERN + r")\b", re.IGNORECASE
)
# "every 4 to 6 hours" states two acceptable frequencies, and an answer is
# entitled to quote either. Read on the reference side only, so it can widen
# what the answer may say but never what the reference said.
_RANGE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:to|-|and)\s*(\d+(?:[.,]\d+)?)\s*("
    + _UNIT_PATTERN
    + r")\b",
    re.IGNORECASE,
)


def _normalise_number(token: str) -> str:
    cleaned = token.replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        return cleaned
    return str(int(value)) if value.is_integer() else str(value)


def _measures(text: str, include_ranges: bool = False) -> set[str]:
    """Every "number unit" claim in a piece of text, normalised.

    Units are folded to one spelling each ("micrograms" and "mcg" are the same
    claim, "mg" and "microgram" are emphatically not), so a unit substitution
    cannot pass as a quotation of the reference.
    """
    scanned = (text or "").translate(_DASHES)
    found = {
        _normalise_number(number) + " " + _UNITS[unit.lower()]
        for number, unit in _MEASURE.findall(scanned)
    }
    if include_ranges:
        for low, high, unit in _RANGE.findall(scanned):
            found.add(_normalise_number(low) + " " + _UNITS[unit.lower()])
            found.add(_normalise_number(high) + " " + _UNITS[unit.lower()])
    return found


def validate_doses(answer: str, block: str, question: str = "") -> tuple[bool, str | None]:
    """Check that every number in the answer is one the reference supplied.

    Returns (ok, offending_number). A dose, a frequency or a ceiling that
    appears in neither the reference block nor the clinician's own question
    means the model produced a number from its own memory of pharmacology,
    which is exactly what this design exists to prevent. The answer is rejected
    whole rather than edited: choosing which half of "500 mg every 6 hours" to
    keep is a clinical judgement, and the server does not make clinical
    judgements.

    Numbers are checked together with their units, not on their own. Checking
    the number alone let "500 micrograms" pass against a reference that says
    500 mg - a thousandfold error wearing the reference's own figure. A number
    with no unit attached ("the 2 medicines") is checked more loosely, against
    the numbers the reference and the question actually contain plus the small
    integers that appear in ordinary prose.
    """
    if not answer:
        return True, None

    scanned = answer.translate(_DASHES)
    scanned = _LIST_MARKER.sub(" ", scanned)

    supplied = (block or "") + "\n" + (question or "")

    # Doses and frequencies: the number and its unit must both have been given.
    allowed_measures = _measures(supplied, include_ranges=True)
    for number, unit in _MEASURE.findall(scanned):
        measure = _normalise_number(number) + " " + _UNITS[unit.lower()]
        if measure not in allowed_measures:
            return False, (number + " " + unit).strip()

    # Bare numbers: no unit, so no dose claim. Small integers are how prose
    # counts things, and rejecting them would send perfectly good answers to the
    # fallback for saying "both of these".
    allowed_numbers = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"}
    allowed_numbers.update(_normalise_number(t) for t in _NUMBER.findall(supplied))

    for match in _NUMBER.finditer(scanned):
        # Skip a number that belongs to a measure; it was checked above.
        trailing = scanned[match.end(): match.end() + 24]
        if re.match(r"\s*(" + _UNIT_PATTERN + r")\b", trailing, re.IGNORECASE):
            continue
        if _normalise_number(match.group()) not in allowed_numbers:
            return False, match.group()
    return True, None


def has_forbidden_reassurance(answer: str) -> bool:
    """Whether the answer tells a prescriber that something is safe.

    A reference says what is recorded and what to do about it. "This is safe"
    is a clinical judgement about one patient, made by whoever is holding the
    prescription pad, and an assistant that offers it invites the prescriber to
    stop thinking. Phrases that merely quote the reference back - "safe in
    pregnancy" appears in no monograph here for that reason - are caught the
    same way, so the wording stays out of answers entirely.
    """
    lowered = " ".join((answer or "").lower().split())
    return any(
        phrase in lowered
        for phrase in (
            "is safe",
            "are safe",
            "it is safe",
            "they are safe",
            "perfectly safe",
            "completely safe",
            "no risk",
            "there is no danger",
            "safe to combine",
            "safe combination",
        )
    )
