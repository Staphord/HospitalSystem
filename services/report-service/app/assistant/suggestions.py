from __future__ import annotations

from dataclasses import dataclass

from app.assistant.content.models import ContentKind
from app.assistant.language import expand_query
from app.assistant.flags import AssistantCapability, is_capability_enabled
from app.assistant.live import registry as live_registry
from app.assistant.live.registry import permitted_metrics
from app.assistant.permissions import is_role_allowed
from app.assistant.retrieval import RetrievalContext, _tokenize, visible_entries

# What to offer somebody who has opened the assistant and typed nothing.
#
# The panel used to carry three questions hardcoded in the browser. Two of them
# ("What reports can I run?", "Where do I find a patient visit history?") matched
# no content entry at all, so every user who tried one was told the assistant did
# not have that information. The third only worked for reception, because the
# entry answering it is gated to receptionist and hospital_admin - so a doctor,
# a pharmacist or a cashier was invited to ask a question that could not be
# answered for them. A suggestion that fails is worse than no suggestion: it
# teaches people the assistant does not work.
#
# Suggestions are therefore built here, on the server, from the two things that
# can actually answer a question:
#
#   - content entries the caller is permitted to see, and
#   - live metrics the caller's roles pass, when live data is switched on.
#
# Both carry their example question beside them rather than in a list of their
# own, so a suggestion cannot outlive what answers it, and cannot be offered to
# somebody who may not reach it. test_assistant_suggestions.py asserts that every
# example question actually retrieves its entry or routes to its metric, which
# makes each suggestion a tested claim rather than a hopeful string.

DEFAULT_SUGGESTION_LIMIT = 6

# Content kinds in the order a new user benefits from them. Workflow first: the
# commonest reason to open the assistant is not knowing where a screen is.
# Report catalog entries are all administrator-only, so for most callers this
# ordering simply skips them.
_KIND_ORDER: tuple[ContentKind, ...] = (
    ContentKind.WORKFLOW,
    ContentKind.REPORT_CATALOG,
    ContentKind.HELP,
    ContentKind.POLICY,
)


@dataclass(frozen=True)
class Suggestion:
    """One question this caller can actually get an answer to."""

    question: str
    # "live_metric" or "content". The panel groups by this so a figure reads as
    # a figure and a how-do-I reads as a how-do-I.
    kind: str
    # The same question in Swahili, where one is declared. Offered instead of
    # `question` when the reply is in Swahili.
    swahili_question: str = ""

    def text(self, swahili: bool = False) -> str:
        return self.swahili_question if (swahili and self.swahili_question) else self.question


def _relevance(suggestion: Suggestion, terms: set[str]) -> int:
    """Overlap between the words somebody used and the words of a suggestion.

    Used only to order a list that is already filtered to what the caller may
    reach, so it can reorder an offer but never widen one. It is what turns
    "here is everything I can do" into "did you mean one of these", which is the
    difference between a menu and an answer.
    """
    if not terms:
        return 0
    words = _tokenize(expand_query(suggestion.question))
    if suggestion.swahili_question:
        words |= _tokenize(expand_query(suggestion.swahili_question))
    return len(terms & words)


def _content_suggestions(context: RetrievalContext) -> list[Suggestion]:
    """Questions drawn from the entries this caller may read.

    visible_entries applies every access filter - approval, effective date,
    tenant, role, department - before anything here is considered, so this
    cannot offer a question whose answer the caller would then be refused.
    """
    by_kind: dict[ContentKind, list[Suggestion]] = {kind: [] for kind in _KIND_ORDER}
    for entry in visible_entries(context):
        if not entry.example_question:
            continue
        if entry.kind in by_kind:
            by_kind[entry.kind].append(
                Suggestion(
                    question=entry.example_question,
                    kind="content",
                    swahili_question=entry.swahili_example_question or "",
                )
            )

    ordered: list[Suggestion] = []
    for kind in _KIND_ORDER:
        # visible_entries already sorts by entry_id, so the order within a kind
        # is stable and the same caller is shown the same list twice running.
        ordered.extend(by_kind[kind])
    return ordered


def _live_suggestions(
    roles: frozenset[str], is_super_admin: bool = False
) -> list[Suggestion]:
    """Questions drawn from the metrics this caller's roles pass.

    Gated twice, exactly as an answer is: the live data capability first, then
    the metric's own allowed_roles. Holding the capability does not mean reaching
    every figure, so a pharmacist is not offered a question about takings.
    """
    if not is_capability_enabled(AssistantCapability.LIVE_DATA):
        return []
    if not is_role_allowed(
        AssistantCapability.LIVE_DATA, roles, is_super_admin=is_super_admin
    ):
        return []

    live_registry.load_catalog()
    return [
        Suggestion(
            question=definition.example_question,
            kind="live_metric",
            swahili_question=definition.swahili_example_question,
        )
        for definition in permitted_metrics(roles, is_super_admin=is_super_admin)
        if definition.example_question
    ]


# Starting questions for the medicines reference.
#
# Held to the same standard as every other suggestion here: each one is asserted
# by test_assistant_medicines.py to route to the medicines path and to name a
# medicine the pack actually carries, so a suggestion cannot drift away from the
# reference that answers it. They are offered only to a caller who holds the
# medication capability, which is the same gate that answers them, so nobody is
# invited to ask something they would then be refused.
_MEDICINE_SUGGESTIONS: tuple[Suggestion, ...] = (
    Suggestion(
        question="Can ibuprofen and enalapril be given together?",
        kind="medicine",
        swahili_question="Je, ibuprofen na enalapril zinaweza kutumika pamoja?",
    ),
    Suggestion(
        question="What does the reference say about metronidazole in pregnancy?",
        kind="medicine",
        swahili_question="Marejeo yanasema nini kuhusu metronidazole wakati wa ujauzito?",
    ),
    Suggestion(
        question="What is the usual adult dose of amoxicillin?",
        kind="medicine",
        swahili_question="Kipimo cha kawaida cha amoxicillin kwa mtu mzima ni kipi?",
    ),
    Suggestion(
        question="Does warfarin interact with metronidazole?",
        kind="medicine",
        swahili_question="Je, warfarin ina mwingiliano na metronidazole?",
    ),
)


def _medicine_suggestions(
    roles: frozenset[str], is_super_admin: bool = False
) -> list[Suggestion]:
    """Medicine questions, for the roles that may have them answered.

    Gated exactly as the answer is: the operator flag first, then the two-role
    matrix. A hospital that has not switched the medicines reference on offers
    none of these, so the panel never advertises a capability the deployment
    does not have.
    """
    if not is_capability_enabled(AssistantCapability.MEDICATION_CHECK):
        return []
    if not is_role_allowed(
        AssistantCapability.MEDICATION_CHECK, roles, is_super_admin=is_super_admin
    ):
        return []
    return list(_MEDICINE_SUGGESTIONS)


def build_suggestions(
    context: RetrievalContext | None,
    roles: frozenset[str],
    is_super_admin: bool = False,
    limit: int = DEFAULT_SUGGESTION_LIMIT,
    question: str = "",
) -> list[Suggestion]:
    """Build the starting questions for one caller. Fail-closed.

    A caller with no usable retrieval context - no tenant, or no recognised role
    - is offered nothing, which is the same stance every other assistant surface
    takes. A platform super admin is offered nothing either: they administer
    tenants and never read tenant content.

    Live figures and how-do-I questions are interleaved rather than concatenated.
    Concatenating would fill a six-item list with whichever source happened to be
    longer, and a cashier would see six workflow questions and never learn that
    the assistant can tell them what is in the till.
    """
    bounded = max(0, min(int(limit), 20))
    if bounded == 0 or is_super_admin or not roles:
        return []

    live = _live_suggestions(roles, is_super_admin=is_super_admin)
    content = _content_suggestions(context) if context is not None else []
    medicine = _medicine_suggestions(roles, is_super_admin=is_super_admin)
    if not live and not content and not medicine:
        return []

    picked: list[Suggestion] = []
    seen: set[str] = set()
    # Medicines lead for the clinicians who have them, because they are the one
    # thing on this list that no other screen in the hospital system will tell
    # them. Ranking against a question, below, can still reorder them.
    for suggestion in medicine:
        seen.add(suggestion.question.lower())
        picked.append(suggestion)
    for pair in zip(live, content):
        for suggestion in pair:
            if suggestion.question.lower() in seen:
                continue
            seen.add(suggestion.question.lower())
            picked.append(suggestion)
    # Whichever list was longer supplies the remainder, so a short content pack
    # does not cost the caller their figures and vice versa.
    for suggestion in live[len(content):] + content[len(live):]:
        if suggestion.question.lower() in seen:
            continue
        seen.add(suggestion.question.lower())
        picked.append(suggestion)

    # With a question in hand, order by how close each suggestion is to what was
    # actually asked. Offering a fixed list to somebody who has just asked
    # something specific is what makes a fallback feel canned; putting the
    # nearest thing first is what makes it feel like an answer. The ranking only
    # reorders a list already filtered to what this caller may reach.
    if question:
        terms = _tokenize(expand_query(question))
        picked.sort(key=lambda s: -_relevance(s, terms))

    return picked[:bounded]


# What to offer after an answer, as opposed to before the first question.
#
# A reply used to end the conversation. The panel offered starting questions
# only while the thread was empty, so the moment somebody asked one thing they
# were back to a blank box and had to guess what else the assistant knew - the
# same guessing the starting questions exist to remove. Follow-ups are those
# same vetted questions, ranked against what was just asked, so the offer after
# an answer is as safe as the offer before one: every question here is still a
# question this caller can actually get an answer to.
FOLLOW_UP_LIMIT = 3

# Enough candidates for the ranking to have something to choose from before the
# already-asked ones are removed. build_suggestions caps at 20 regardless.
_FOLLOW_UP_POOL = 20


def _same_question(text: str) -> str:
    """A question reduced to what makes it the same question.

    Used only to keep the panel from offering back something the staff member
    has already asked in this thread; it never widens what is offered.
    """
    return " ".join((text or "").lower().split()).strip("?!. ")


def build_follow_ups(
    context: RetrievalContext | None,
    roles: frozenset[str],
    *,
    question: str,
    asked: tuple[str, ...] = (),
    is_super_admin: bool = False,
    limit: int = FOLLOW_UP_LIMIT,
    swahili: bool = False,
) -> list[str]:
    """Questions to carry the conversation on from the answer just given.

    Ordered by closeness to what was asked, so the offer reads as "and next"
    rather than as a menu reprinted after every reply. Anything already asked in
    the thread is dropped: repeating somebody's own question back to them is the
    clearest possible sign that nothing is listening.
    """
    bounded = max(0, min(int(limit), FOLLOW_UP_LIMIT))
    if bounded == 0:
        return []

    seen = {_same_question(question)}
    seen.update(_same_question(previous) for previous in asked)

    follow_ups: list[str] = []
    for suggestion in build_suggestions(
        context,
        roles,
        is_super_admin=is_super_admin,
        limit=_FOLLOW_UP_POOL,
        question=question,
    ):
        text = suggestion.text(swahili)
        key = _same_question(text)
        if not key or key in seen:
            continue
        seen.add(key)
        follow_ups.append(text)
        if len(follow_ups) == bounded:
            break

    return follow_ups
