from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.assistant.flags import AssistantCapability, is_capability_enabled
from app.assistant.language import contains_swahili
from app.assistant.live import registry as live_registry
from app.assistant.live.registry import permitted_metrics
from app.assistant.permissions import is_role_allowed
from app.assistant.retrieval import RetrievalContext, visible_entries

# What this staff member can ask, in their own words.
#
# Two things depend on this list, and both used to be handled badly:
#
#   - "What can you help me with?" was answered "I do not have information about
#     that", because no content entry happens to be *about* the assistant's own
#     scope. The one question every new user asks first was the one question it
#     could not answer.
#   - A refusal used to end "please speak to the relevant department", or - from
#     the model - "ask your department's IT support or the system administrator".
#     That is not help. It is the assistant handing the problem back to somebody
#     who cannot fix it, for a question another part of the same assistant could
#     often have answered.
#
# So a refusal now says what this person *can* ask instead, and the list is built
# from the same two gates that decide every answer: the content entries they may
# read, and the metrics their roles pass. It cannot promise something they would
# then be refused, and it cannot go stale when a role's access changes.

# One area of the system, and the two things that can back it.
#
# Areas are declared rather than derived from ids alone so the wording is
# something a nurse would recognise: "Wards, beds and admissions" rather than
# "beds.*, admissions.*". An area appears only when the caller can reach at least
# one of the things behind it, and the half that appears - the how-to half, the
# figures half, or both - is decided the same way.


@dataclass(frozen=True)
class CapabilityArea:
    name: str
    # Entry-id and metric-id prefixes that back this area.
    entry_prefixes: tuple[str, ...] = ()
    metric_prefixes: tuple[str, ...] = ()
    # A capability that backs this area on its own, for the areas that are not
    # content and are not figures. The medicines reference is the first: it is
    # neither an entry nor a metric, it has its own operator flag and its own
    # two-role gate, and an area that appears here when that gate does not pass
    # would be offering a clinician-only answer to a cashier.
    requires_capability: AssistantCapability | None = None
    # What to say about each half. The figures half is only ever shown when live
    # data is switched on and the caller's roles reach one of its metrics.
    how_to: str = ""
    figures: str = ""
    # The Swahili wording is written out rather than translated at runtime. A
    # half-translated list - Swahili headings over English descriptions - reads
    # worse than either language on its own, which is what the first version of
    # this did. Screen and menu names stay in English inside the Swahili, the
    # same rule the answers themselves follow.
    swahili_name: str = ""
    swahili_how_to: str = ""
    swahili_figures: str = ""

    def entries_for(self, entry_ids: frozenset[str]) -> bool:
        return any(e.startswith(self.entry_prefixes) for e in entry_ids) if (
            self.entry_prefixes
        ) else False

    def metrics_for(self, metric_ids: frozenset[str]) -> bool:
        return any(m.startswith(self.metric_prefixes) for m in metric_ids) if (
            self.metric_prefixes
        ) else False


CAPABILITY_AREAS: tuple[CapabilityArea, ...] = (
    CapabilityArea(
        name="Registration and visits",
        swahili_name="Usajili na ziara",
        entry_prefixes=("workflow.reception.",),
        metric_prefixes=("visits.",),
        how_to="how to register a patient and work the visit queue",
        figures="how many visits were registered and what stage they have reached",
        swahili_how_to="jinsi ya kusajili mgonjwa na kushughulikia foleni ya ziara",
        swahili_figures="ziara ngapi zimesajiliwa na zimefika hatua gani",
    ),
    CapabilityArea(
        name="Triage",
        swahili_name="Uchunguzi wa awali (triage)",
        entry_prefixes=("workflow.triage.",),
        how_to="how to assess a patient in triage and where past assessments are",
        swahili_how_to="jinsi ya kumpima mgonjwa katika Triage na wapi kuona vipimo vya nyuma",
    ),
    CapabilityArea(
        name="Consultations",
        swahili_name="Mashauriano",
        entry_prefixes=("workflow.consultation.",),
        how_to="how to open a consultation encounter and record it",
        swahili_how_to="jinsi ya kufungua na kuandika mashauriano",
    ),
    CapabilityArea(
        name="Queues and waiting times",
        swahili_name="Foleni na muda wa kusubiri",
        metric_prefixes=("queue.",),
        figures="how many patients are waiting in each queue, and the average wait",
        swahili_figures="wagonjwa wangapi wanasubiri kila foleni, na wastani wa muda wa kusubiri",
    ),
    CapabilityArea(
        name="Wards, beds and admissions",
        swahili_name="Wodi, vitanda na kulazwa",
        entry_prefixes=("workflow.ward.",),
        metric_prefixes=("beds.", "admissions."),
        how_to="how to manage ward beds and admitted patients",
        figures="how many beds are free, who is admitted, and the average length of stay",
        swahili_how_to="jinsi ya kusimamia vitanda vya wodi na wagonjwa waliolazwa",
        swahili_figures="vitanda vingapi viko wazi, nani amelazwa, na wastani wa siku za kulazwa",
    ),
    CapabilityArea(
        name="One patient, by number",
        swahili_name="Mgonjwa mmoja, kwa namba",
        metric_prefixes=("patient.",),
        # This area exists because the list is what the model believes it can do.
        # With patient lookup working and unlisted, a receptionist asking "where
        # is PT-20260829-0003" was answered "I do not have that information" and
        # offered this very list - the figure was in the prompt and the model
        # declined it as out of scope. The capability list outranks the figures,
        # so a figure that is not described here cannot be relied on to be used.
        #
        # The wording says "give their patient or visit number" because that is a
        # condition of the answer, not a nicety: without one the question never
        # reaches the patient tier at all.
        figures=(
            "where one patient is now - their visit stage, queue, ward and bed, "
            "and what is outstanding on their bill - when you give their patient "
            "number or visit number"
        ),
        swahili_figures=(
            "mgonjwa mmoja yuko wapi sasa - hatua ya ziara, foleni, wodi na "
            "kitanda, na deni lililobaki - ukitoa namba yake ya mgonjwa au ya ziara"
        ),
    ),
    CapabilityArea(
        name="Laboratory",
        swahili_name="Maabara",
        entry_prefixes=("workflow.laboratory.",),
        metric_prefixes=("lab.",),
        how_to="how to process a laboratory request",
        figures=(
            "the outstanding request backlog, critical results awaiting "
            "verification, and turnaround times"
        ),
        swahili_how_to="jinsi ya kushughulikia ombi la maabara",
        swahili_figures=(
            "vipimo vilivyobaki, matokeo ya hatari yanayosubiri kuthibitishwa, "
            "na muda wa majibu"
        ),
    ),
    CapabilityArea(
        name="Imaging",
        swahili_name="Mionzi",
        entry_prefixes=("workflow.radiology.",),
        how_to="how to process an imaging request",
        swahili_how_to="jinsi ya kushughulikia ombi la mionzi",
    ),
    CapabilityArea(
        name="Pharmacy and stock",
        swahili_name="Famasi na hisa za dawa",
        entry_prefixes=("workflow.pharmacy.",),
        metric_prefixes=("stock.",),
        how_to="how to dispense a prescription",
        figures="which drugs are low or out of stock, and what is on the shelf",
        swahili_how_to="jinsi ya kutoa dawa kwa agizo",
        swahili_figures="dawa zipi zimepungua au zimeisha, na kilichopo ghalani",
    ),
    CapabilityArea(
        name="Billing and payments",
        swahili_name="Bili na malipo",
        entry_prefixes=("workflow.billing.",),
        metric_prefixes=("billing.",),
        how_to="how to take a payment against a bill",
        figures="how much has been collected and how much is still outstanding",
        swahili_how_to="jinsi ya kupokea malipo kwenye bili",
        swahili_figures="kiasi gani kimekusanywa na kiasi gani bado hakijalipwa",
    ),
    CapabilityArea(
        name="Medicines",
        swahili_name="Dawa",
        requires_capability=AssistantCapability.MEDICATION_CHECK,
        how_to=(
            "what the hospital medicines reference records about a medicine - the "
            "usual adult dose, what it interacts with, and what it says about "
            "pregnancy, breastfeeding, and impaired kidney or liver function"
        ),
        swahili_how_to=(
            "yale yaliyoandikwa kwenye marejeo ya dawa ya hospitali - kipimo cha "
            "mtu mzima, mwingiliano na dawa nyingine, na matumizi wakati wa "
            "ujauzito, kunyonyesha, au matatizo ya figo na ini"
        ),
    ),
    CapabilityArea(
        name="Reports",
        swahili_name="Ripoti",
        entry_prefixes=("report.",),
        how_to="which reports exist, who may run them, and where to find each one",
        swahili_how_to="ripoti zipi zipo, nani anaweza kuziendesha, na wapi kuzipata",
    ),
    CapabilityArea(
        name="Staff administration",
        swahili_name="Utawala wa wafanyakazi",
        entry_prefixes=("workflow.admin.",),
        how_to="how to add a staff member",
        swahili_how_to="jinsi ya kuongeza mfanyakazi",
    ),
    CapabilityArea(
        name="Your account and hospital policy",
        swahili_name="Akaunti yako na sera za hospitali",
        entry_prefixes=("help.", "policy."),
        how_to=(
            "changing your password, why a screen says you are not authorised, "
            "and what the hospital's data and account rules say"
        ),
        swahili_how_to=(
            "kubadilisha nywila yako, kwa nini skrini inasema hauruhusiwi, na "
            "sera za hospitali kuhusu taarifa na akaunti"
        ),
    ),
)


# Questions that are asking what this assistant is for.
#
# Matched on the server rather than left to retrieval, because no content entry
# is *about* the assistant's scope in the words people actually use, so "what can
# you help me with" scored zero against every entry and came back as a refusal.
_WHAT_CAN_YOU_DO = re.compile(
    r"\b("
    r"what\s+(can|could)\s+(you|i|we)\s+(do|ask|say|help|use)"
    r"|what\s+(do|are)\s+you\s+(do|for|able)"
    r"|what\s+(else\s+)?can\s+you"
    r"|what\s+can\s+i\s+ask\s+you"
    r"|how\s+can\s+you\s+help"
    r"|what\s+are\s+your\s+(capabilities|features|abilities)"
    r"|what\s+(is|are)\s+(this|the)\s+assistant\s+(for|able)"
    r"|who\s+are\s+you"
    r"|help\s+me\s+with\s+what"
    r"|unaweza\s+(kunisaidia|kufanya|kusaidia)\s+(nini|na\s+nini)"
    r"|naweza\s+ku(kuuliza|uliza)\s+nini"
    r"|unasaidia\s+nini"
    r"|wewe\s+ni\s+nani"
    r")\b",
    re.IGNORECASE,
)


def is_capability_question(question: str) -> bool:
    """Return whether the staff member is asking what the assistant is for."""
    if not question or not isinstance(question, str):
        return False
    return _WHAT_CAN_YOU_DO.search(question) is not None


def _reachable(
    context: RetrievalContext | None,
    roles: frozenset[str],
    is_super_admin: bool = False,
) -> tuple[frozenset[str], frozenset[str]]:
    """The entry ids and metric ids this caller can actually reach.

    Both gates are the real ones, not copies: visible_entries applies approval,
    effective date, tenant, role and department; permitted_metrics applies each
    metric's own allowed_roles, and the live-data flag is checked first. So this
    list can never name something the caller would then be refused.
    """
    entry_ids: frozenset[str] = frozenset()
    if context is not None:
        entry_ids = frozenset(e.entry_id for e in visible_entries(context))

    metric_ids: frozenset[str] = frozenset()
    if is_capability_enabled(AssistantCapability.LIVE_DATA) and is_role_allowed(
        AssistantCapability.LIVE_DATA, roles, is_super_admin=is_super_admin
    ):
        live_registry.load_catalog()
        metric_ids = frozenset(
            m.metric_id
            for m in permitted_metrics(roles, is_super_admin=is_super_admin)
        )
    return entry_ids, metric_ids


def describe_capabilities(
    context: RetrievalContext | None,
    roles: frozenset[str],
    is_super_admin: bool = False,
    swahili: bool = False,
) -> list[str]:
    """Return one plain line per area this caller can actually use.

    Ordered as CAPABILITY_AREAS is, so the same person sees the same list twice
    running. Empty for a caller who can reach nothing, which is a real answer:
    somebody with no usable role is told the truth rather than a menu of things
    that would be refused.
    """
    if is_super_admin or not roles:
        return []

    entry_ids, metric_ids = _reachable(
        context, roles, is_super_admin=is_super_admin
    )

    lines: list[str] = []
    for area in CAPABILITY_AREAS:
        halves: list[str] = []
        # An area backed by a capability rather than by content is offered on
        # exactly the gate that would answer it: the operator flag first, then
        # the role matrix. Nothing here is a second copy of either.
        if area.requires_capability is not None:
            if not is_capability_enabled(area.requires_capability):
                continue
            if not is_role_allowed(
                area.requires_capability, roles, is_super_admin=is_super_admin
            ):
                continue
            name = area.swahili_name if (swahili and area.swahili_name) else area.name
            described = (
                area.swahili_how_to
                if (swahili and area.swahili_how_to)
                else area.how_to
            )
            lines.append(name + " - " + described + ".")
            continue
        if area.how_to and area.entries_for(entry_ids):
            halves.append(
                area.swahili_how_to
                if (swahili and area.swahili_how_to)
                else area.how_to
            )
        if area.figures and area.metrics_for(metric_ids):
            halves.append(
                area.swahili_figures
                if (swahili and area.swahili_figures)
                else area.figures
            )
        if not halves:
            continue
        name = area.swahili_name if (swahili and area.swahili_name) else area.name
        joiner = ", na " if swahili else ", and "
        lines.append(name + " - " + joiner.join(halves) + ".")
    return lines


def capability_answer(
    context: RetrievalContext | None,
    roles: frozenset[str],
    is_super_admin: bool = False,
    swahili: bool = False,
    lead: str | None = None,
) -> str:
    """Compose the answer to "what can you help me with", as a numbered list.

    Returns "" when there is nothing to offer, so the caller can fall through to
    its own wording rather than printing an empty list.
    """
    lines = describe_capabilities(
        context, roles, is_super_admin=is_super_admin, swahili=swahili
    )
    if not lines:
        return ""

    if lead is None:
        lead = (
            "Ninaweza kukusaidia na haya:"
            if swahili
            else "Here is what I can help you with:"
        )
    closing = (
        "Uliza lolote kati ya haya kwa maneno yako mwenyewe."
        if swahili
        else "Ask about any of these in your own words."
    )
    numbered = [f"{index}. {line}" for index, line in enumerate(lines, start=1)]
    return lead + "\n\n" + "\n".join(numbered) + "\n\n" + closing


def refusal_with_capabilities(
    context: RetrievalContext | None,
    roles: frozenset[str],
    is_super_admin: bool = False,
    swahili: bool = False,
    question: str = "",
    limit: int = 4,
) -> str:
    """What to say when the assistant cannot answer.

    Never "ask IT", never "speak to the relevant department", never "contact your
    system administrator". Those send somebody away from a system that, more
    often than not, could have answered a neighbouring question.

    What it offers is real questions, not a description of the categories it
    covers. A list of areas reads as a fixed menu however carefully it was
    filtered - the first version of this printed one, and the honest reaction to
    it was "have you hardcoded the questions?". These are the same questions the
    panel suggests, each one already tested to reach the entry or the figure that
    answers it, ordered by how close they are to what was actually asked. So
    somebody who asked about a password is offered the password question first.
    """
    from app.assistant.suggestions import build_suggestions

    opening = (
        "Samahani, siwezi kukusaidia na hilo."
        if swahili
        else "I can't help with that."
    )
    offered = build_suggestions(
        context,
        roles,
        is_super_admin=is_super_admin,
        limit=limit,
        question=question,
    )
    if not offered:
        return opening

    lead = "Unaweza kuniuliza:" if swahili else "You could ask me:"
    lines = ["- " + item.text(swahili=swahili) for item in offered]
    return opening + " " + lead + "\n\n" + "\n".join(lines)


def prompt_block(
    context: RetrievalContext | None,
    roles: frozenset[str],
    is_super_admin: bool = False,
) -> str:
    """The same list, rendered for the model.

    Given to the model so that when *it* decides it cannot answer, it offers this
    list rather than inventing a referral. The model is shown only what the
    server already established this caller may reach, so it cannot widen anything
    by quoting the list back.
    """
    lines = describe_capabilities(context, roles, is_super_admin=is_super_admin)
    if not lines:
        return ""
    return (
        "What you can help this staff member with. If you cannot answer their "
        "question, say so plainly and then offer this list, numbered, in the "
        "language of your reply:\n"
        + "\n".join("- " + line for line in lines)
    )
