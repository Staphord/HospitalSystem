"""Deterministic red flags for clinical differential support.

A language model plays no part in this module and cannot reach it. Every flag is
produced by a rule in the pack below, matching recorded symptom text against a
fixed pattern, and every flag carries the rule id and pack version that produced
it. A flag that cannot be traced to a rule cannot exist, because the RedFlag
contract refuses to be constructed without both.

Two deliberate limits on what these rules are:

**They are not an emergency alarm.** The phase rules are explicit that this
workflow must not create emergency directives, and the wording of every rule
reflects that: a flag states what was recorded and that it warrants clinician
assessment. It never tells anyone to call anybody, admit anybody, or start
anything.

**They are not exhaustive.** This is a small, conservative pack for one approved
department. Its absence of a flag means only that none of these rules matched;
it never means a presentation is benign, and the response contract carries that
limitation on every result.

Recorded symptom text is untrusted data. It is matched against patterns and is
never interpreted as an instruction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.cds.contracts import RedFlag
from app.core.config import settings


@dataclass(frozen=True)
class RedFlagRule:
    """One deterministic rule.

    `all_of` must every match for the rule to fire; `any_of`, when present, must
    also contribute at least one match. That combination is what keeps the pack
    specific: "chest pain" alone is not a flag, "chest pain with breathlessness"
    is.
    """

    rule_id: str
    label: str
    detail: str
    all_of: tuple[str, ...]
    any_of: tuple[str, ...] = ()


# Wording note: every `detail` states an observation and a review requirement.
# None of them issues an instruction, a destination, or a treatment.
RULES: tuple[RedFlagRule, ...] = (
    RedFlagRule(
        rule_id="RF-001",
        label="Chest pain with breathlessness",
        detail=(
            "Chest pain was recorded together with breathlessness. This combination "
            "warrants clinician assessment before the encounter proceeds."
        ),
        all_of=(r"chest\s*pain",),
        any_of=(r"short(ness)?\s*of\s*breath", r"breathless", r"dyspn(o)?ea", r"cannot breathe"),
    ),
    RedFlagRule(
        rule_id="RF-002",
        label="Chest pain radiating to arm, jaw, or back",
        detail=(
            "Chest pain was recorded with radiation to the arm, jaw, or back. This "
            "pattern warrants clinician assessment before the encounter proceeds."
        ),
        all_of=(r"chest\s*pain",),
        any_of=(r"radiat\w*", r"(left|right)\s*arm", r"\bjaw\b", r"into (the )?back"),
    ),
    RedFlagRule(
        rule_id="RF-003",
        label="Sudden severe headache",
        detail=(
            "A headache described as sudden, worst-ever, or thunderclap was recorded. "
            "This description warrants clinician assessment before the encounter proceeds."
        ),
        all_of=(r"headache",),
        any_of=(r"thunderclap", r"worst[- ]?ever", r"sudden(ly)?\s+severe", r"abrupt onset"),
    ),
    RedFlagRule(
        rule_id="RF-004",
        label="Headache with neck stiffness or fever",
        detail=(
            "A headache was recorded together with neck stiffness, fever, or light "
            "sensitivity. This combination warrants clinician assessment before the "
            "encounter proceeds."
        ),
        all_of=(r"headache",),
        any_of=(r"neck\s*stiff\w*", r"stiff\s*neck", r"photophobia", r"light sensitiv\w*", r"fever"),
    ),
    RedFlagRule(
        rule_id="RF-005",
        label="Sudden focal neurological symptoms",
        detail=(
            "Focal neurological symptoms such as one-sided weakness, facial droop, or "
            "slurred speech were recorded. These warrant clinician assessment before "
            "the encounter proceeds."
        ),
        all_of=(),
        any_of=(
            r"facial\s*droop",
            r"face\s*droop\w*",
            r"one[- ]?sided\s*weak\w*",
            r"weakness\s*(on|of)\s*one\s*side",
            r"hemipares\w*",
            r"slurred\s*speech",
            r"unable to speak",
        ),
    ),
    RedFlagRule(
        rule_id="RF-006",
        label="Breathlessness at rest",
        detail=(
            "Breathlessness at rest was recorded. This warrants clinician assessment "
            "before the encounter proceeds."
        ),
        all_of=(r"(short(ness)?\s*of\s*breath|breathless|dyspn(o)?ea)",),
        any_of=(r"at\s*rest", r"resting", r"even (when|while) (sitting|lying|resting)"),
    ),
    RedFlagRule(
        rule_id="RF-007",
        label="Severe abdominal pain with guarding or rigidity",
        detail=(
            "Abdominal pain was recorded with guarding, rigidity, or rebound "
            "tenderness. This combination warrants clinician assessment before the "
            "encounter proceeds."
        ),
        all_of=(r"abdo(minal|men)\w*\s*pain|stomach\s*pain|belly\s*pain",),
        any_of=(r"guard\w*", r"rigid\w*", r"rebound", r"board[- ]?like"),
    ),
    RedFlagRule(
        rule_id="RF-008",
        label="Visible bleeding in vomit or stool",
        detail=(
            "Blood in vomit or stool was recorded. This warrants clinician assessment "
            "before the encounter proceeds."
        ),
        all_of=(),
        any_of=(
            r"h(a)?ematemesis",
            r"mel(a)?ena",
            r"blood\s*in\s*(the\s*)?(vomit|stool|faeces|feces)",
            r"vomiting\s*blood",
            r"coffee[- ]?ground",
            r"black\s*tarry\s*stool",
        ),
    ),
    RedFlagRule(
        rule_id="RF-009",
        label="Altered consciousness or new confusion",
        detail=(
            "Altered consciousness, new confusion, or a fainting episode was recorded. "
            "This warrants clinician assessment before the encounter proceeds."
        ),
        all_of=(),
        any_of=(
            r"unconscious\w*",
            r"loss\s*of\s*consciousness",
            r"altered\s*(mental\s*status|consciousness)",
            r"new\w*\s*confus\w*",
            r"disorient\w*",
            r"syncop\w*",
            r"fainted",
            r"unrespons\w*",
        ),
    ),
    RedFlagRule(
        rule_id="RF-010",
        label="Fever with a non-blanching rash",
        detail=(
            "Fever was recorded together with a rash described as non-blanching or "
            "purpuric. This combination warrants clinician assessment before the "
            "encounter proceeds."
        ),
        all_of=(r"fever|pyrexia",),
        any_of=(r"non[- ]?blanch\w*", r"purpur\w*", r"petechia\w*"),
    ),
)


def _compiled(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


_COMPILED: dict[str, tuple[tuple[re.Pattern[str], ...], tuple[re.Pattern[str], ...]]] = {
    rule.rule_id: (_compiled(rule.all_of), _compiled(rule.any_of)) for rule in RULES
}


def ruleset_version() -> str:
    """The version of the pack answering right now, for the audit trail."""
    return str(getattr(settings, "cds_redflag_ruleset_version", "builtin"))


def evaluate_red_flags(texts: list[str]) -> list[RedFlag]:
    """Match the recorded symptom text against the pack.

    `texts` is everything the clinician recorded: the chief complaint, each
    symptom name and location, and the free-text notes. They are joined into one
    haystack because a presentation is frequently split across fields - "chest
    pain" in the complaint and "short of breath" in a symptom - and a rule that
    only ever looked at one field would miss it.
    """
    haystack = " \n ".join(t for t in texts if t).strip()
    if not haystack:
        return []

    version = ruleset_version()
    flags: list[RedFlag] = []

    for rule in RULES:
        all_patterns, any_patterns = _COMPILED[rule.rule_id]

        matched: list[str] = []
        if not all(p.search(haystack) for p in all_patterns):
            continue
        matched.extend(
            m.group(0) for p in all_patterns if (m := p.search(haystack)) is not None
        )

        if any_patterns:
            any_hits = [m.group(0) for p in any_patterns if (m := p.search(haystack)) is not None]
            if not any_hits:
                continue
            matched.extend(any_hits)

        flags.append(
            RedFlag(
                rule_id=rule.rule_id,
                ruleset_version=version,
                label=rule.label,
                detail=rule.detail,
                # What actually matched, so a clinician can see why it fired and
                # dismiss it immediately when the match was incidental.
                matched_on=sorted({m.strip().lower() for m in matched if m.strip()})[:10],
            )
        )

    return flags
