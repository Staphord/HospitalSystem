"""Starting questions, and the guarantee that each one actually works.

The panel used to hardcode three questions in the browser:

    'How do I register a new patient?'
    'What reports can I run?'
    'Where do I find a patient visit history?'

Two of the three matched no content entry at all, so anybody who tried one was
told "I do not have information about that". The third is answered by
workflow.reception.register-patient, which is gated to receptionist and
hospital_admin - so a doctor, a pharmacist, a cashier or a lab technician was
invited to ask a question that could not be answered for them. A suggestion that
fails is worse than no suggestion, because it teaches people the assistant does
not work.

The tests below are the reason that cannot happen again. Every example question
declared on a content entry must actually retrieve that entry, and every example
question declared on a metric must actually route to that metric, for each role
permitted to see it. A suggestion that has drifted away from its answer fails the
build rather than the user.
"""

from __future__ import annotations

import pytest

from app.assistant.content.entries import OPERATIONAL_CONTENT
from app.assistant.content.models import ContentKind
from app.assistant.live import registry as live_registry
from app.assistant.live.contracts import MetricTier
from app.assistant.live.registry import METRIC_REGISTRY
from app.assistant.live.routing import route
from app.assistant.permissions import (
    CASHIER,
    DOCTOR,
    HOSPITAL_ADMIN,
    LAB_TECHNICIAN,
    PHARMACIST,
    RADIOGRAPHER,
    RECEPTIONIST,
    SUPER_ADMIN,
    TRIAGE_NURSE,
    WARD_NURSE,
)
from app.assistant.retrieval import build_retrieval_context, retrieve
from app.assistant.suggestions import (
    DEFAULT_SUGGESTION_LIMIT,
    FOLLOW_UP_LIMIT,
    build_follow_ups,
    build_suggestions,
)
from app.core.config import settings

live_registry.load_catalog()

TENANT = "hosp-test"

EVERY_ROLE = [
    HOSPITAL_ADMIN, RECEPTIONIST, TRIAGE_NURSE, WARD_NURSE, DOCTOR,
    LAB_TECHNICIAN, RADIOGRAPHER, PHARMACIST, CASHIER,
]

ENTRIES_WITH_A_QUESTION = [e for e in OPERATIONAL_CONTENT if e.example_question]
METRICS_WITH_A_QUESTION = [
    m for m in METRIC_REGISTRY.values() if m.example_question
]

# The three questions the browser used to hardcode. Kept as a regression fixture
# so the specific failure that prompted this work stays covered.
THE_OLD_HARDCODED_QUESTIONS = [
    "How do I register a new patient?",
    "What reports can I run?",
    "Where do I find a patient visit history?",
]


def _context(role: str):
    return build_retrieval_context(TENANT, [role])


@pytest.fixture
def live_data_on(monkeypatch):
    """Switch live data on for one test.

    It is off by default in this deployment, and a suggestion for a figure must
    not be offered when the figures themselves are not there - the panel would
    invite a question the server would then decline. TestSuggestionsFailClosed
    checks the off case; the tests taking this fixture check the on case.
    """
    monkeypatch.setattr(settings, "assistant_live_data_enabled", True, raising=False)


class TestEveryContentSuggestionIsAnswerable:
    @pytest.mark.parametrize(
        "entry", ENTRIES_WITH_A_QUESTION, ids=lambda e: e.entry_id
    )
    def test_its_example_question_retrieves_its_own_entry(self, entry):
        """The suggestion must find the entry it was written for.

        Not merely "some entry": a question that retrieves a different entry is
        a question whose answer is about something else.
        """
        for role in sorted(entry.roles):
            context = _context(role)
            assert context is not None
            found = retrieve(context, entry.example_question, limit=5)
            assert entry.entry_id in [e.entry_id for e in found], (
                f"{entry.entry_id} suggests {entry.example_question!r}, which "
                f"retrieves {[e.entry_id for e in found]} for a {role} - not "
                f"itself. Offering it would answer a different question, or none."
            )

    @pytest.mark.parametrize(
        "entry", ENTRIES_WITH_A_QUESTION, ids=lambda e: e.entry_id
    )
    def test_its_example_question_is_a_question(self, entry):
        assert entry.example_question.strip().endswith("?"), (
            f"{entry.entry_id} suggests something that is not a question"
        )
        assert len(entry.example_question) <= 200

    def test_every_entry_carries_one(self):
        """An entry with no example question can never be suggested.

        That is allowed, but it should be a decision rather than an oversight,
        so the count is pinned: adding an entry without one fails here.
        """
        without = [e.entry_id for e in OPERATIONAL_CONTENT if not e.example_question]
        assert without == [], (
            "these entries can never be suggested to anyone: " + ", ".join(without)
        )


class TestEveryLiveSuggestionIsAnswerable:
    @pytest.mark.parametrize(
        "metric", METRICS_WITH_A_QUESTION, ids=lambda m: m.metric_id
    )
    def test_its_example_question_routes_to_its_own_metric(self, metric):
        for role in sorted(metric.allowed_roles):
            routed = route(metric.example_question, roles=frozenset({role}))
            assert metric.metric_id in [r.definition.metric_id for r in routed], (
                f"{metric.metric_id} suggests {metric.example_question!r}, which "
                f"routes to {[r.definition.metric_id for r in routed]} for a "
                f"{role} - not to itself"
            )

    @pytest.mark.parametrize(
        "metric", METRICS_WITH_A_QUESTION, ids=lambda m: m.metric_id
    )
    def test_its_example_question_is_a_question(self, metric):
        assert metric.example_question.strip().endswith("?")
        assert len(metric.example_question) <= 200

    def test_a_metric_filtering_on_a_name_suggests_nothing(self):
        """Whether such a question works depends on this hospital's own data.

        "How much Amoxicillin do we have?" is answerable only where Amoxicillin
        is on the shelf. A suggestion that works in one tenant and fails in the
        next is worse than no suggestion, so these metrics stay unsuggested and
        their unfiltered siblings cover the intent.
        """
        for metric in METRIC_REGISTRY.values():
            if metric.params & {"ward_name", "drug_name"}:
                assert not metric.example_question, (
                    f"{metric.metric_id} filters on a name this hospital may not "
                    f"hold, so its suggestion cannot be relied on"
                )

    @pytest.mark.parametrize(
        "metric", METRICS_WITH_A_QUESTION, ids=lambda m: m.metric_id
    )
    def test_it_returns_a_row_even_when_there_is_nothing_to_report(self, metric):
        """A suggested figure must answer zero, never answer nothing.

        No rows means the figure is dropped from the prompt entirely, and the
        staff member is told the assistant does not have that information - when
        the true answer is that nothing has happened yet today. That is exactly
        how a starting question turns into evidence the assistant is broken.

        A metric guarantees a row one of two ways: it aggregates with no GROUP BY,
        so it always returns exactly one row; or it groups off a one-row anchor on
        the left of a LEFT JOIN, so there is always at least one group.
        """
        sql = " ".join(metric.sql.split())
        if "GROUP BY" not in sql.upper():
            return
        assert "FROM (SELECT 1)" in sql and "LEFT JOIN" in sql, (
            f"{metric.metric_id} is suggested to users but groups without an "
            f"anchor, so on a quiet day it returns no rows and the question it "
            f"suggests is answered 'I do not have that information'"
        )

    def test_every_other_metric_carries_one(self):
        # A patient-tier metric is excused for a stronger reason than the
        # ward-filtered and drug-filtered ones: its question would have to carry
        # a real patient number, and putting one person's number in front of
        # every user of the panel is the exposure the patient tier exists to
        # prevent. test_assistant_live_aliases.py asserts they stay empty.
        without = sorted(
            metric_id
            for metric_id, metric in METRIC_REGISTRY.items()
            if not metric.example_question
            and not (metric.params & {"ward_name", "drug_name"})
            and metric.tier is not MetricTier.PATIENT
        )
        assert without == [], (
            "these figures can never be suggested to anyone: " + ", ".join(without)
        )


class TestSuggestionsAreRoleAware:
    """The failure the whole endpoint exists to prevent."""

    @pytest.mark.parametrize("role", EVERY_ROLE)
    def test_every_role_is_offered_something(self, role):
        built = build_suggestions(_context(role), frozenset({role}))
        assert built, f"a {role} opening the assistant is offered nothing at all"

    @pytest.mark.parametrize("role", EVERY_ROLE)
    def test_no_role_is_offered_more_than_the_panel_shows(self, role):
        built = build_suggestions(_context(role), frozenset({role}))
        assert len(built) <= DEFAULT_SUGGESTION_LIMIT

    @pytest.mark.parametrize("role", EVERY_ROLE)
    def test_every_question_offered_is_one_this_role_can_reach(self, role, live_data_on):
        """The guarantee, checked end to end rather than by construction.

        For each suggestion, either a content entry this role may read answers
        it, or a metric this role's allowed_roles admits routes to it.
        """
        roles = frozenset({role})
        context = _context(role)
        for suggestion in build_suggestions(context, roles):
            if suggestion.kind == "content":
                found = retrieve(context, suggestion.question, limit=5)
                assert found, (
                    f"a {role} is offered {suggestion.question!r}, which retrieves "
                    f"no content they may read"
                )
            else:
                routed = route(suggestion.question, roles=roles)
                assert routed, (
                    f"a {role} is offered {suggestion.question!r}, which routes to "
                    f"no figure they may read"
                )

    @pytest.mark.parametrize("role", EVERY_ROLE)
    def test_it_never_offers_the_old_hardcoded_questions_to_a_role_they_fail_for(
        self, role
    ):
        """The precise regression: two matched nothing, one was reception-only."""
        offered = {
            s.question for s in build_suggestions(_context(role), frozenset({role}))
        }
        context = _context(role)
        for question in THE_OLD_HARDCODED_QUESTIONS:
            if question in offered:
                assert retrieve(context, question, limit=5), (
                    f"a {role} is still offered {question!r}, which answers nothing "
                    f"for them"
                )

    def test_a_reception_only_question_is_not_offered_to_a_doctor(self):
        offered = {
            s.question
            for s in build_suggestions(_context(DOCTOR), frozenset({DOCTOR}))
        }
        assert "How do I register a new patient?" not in offered

    def test_a_money_question_is_not_offered_to_a_pharmacist(self, live_data_on):
        offered = {
            s.question
            for s in build_suggestions(_context(PHARMACIST), frozenset({PHARMACIST}))
        }
        for question in offered:
            assert "collected" not in question.lower()
            assert "unpaid" not in question.lower()

    def test_a_cashier_is_offered_a_money_question(self, live_data_on):
        """Interleaving exists so a short list does not crowd out the figures."""
        offered = {
            s.question
            for s in build_suggestions(_context(CASHIER), frozenset({CASHIER}))
        }
        assert any("collected" in q.lower() or "unpaid" in q.lower() for q in offered)

    def test_a_lab_technician_is_offered_a_lab_question(self, live_data_on):
        offered = {
            s.question
            for s in build_suggestions(
                _context(LAB_TECHNICIAN), frozenset({LAB_TECHNICIAN})
            )
        }
        assert any("lab" in q.lower() for q in offered)

    @pytest.mark.parametrize("role", EVERY_ROLE)
    def test_it_is_stable_across_calls(self, role):
        """The same caller must not see the list reshuffle on every open."""
        first = build_suggestions(_context(role), frozenset({role}))
        second = build_suggestions(_context(role), frozenset({role}))
        assert [s.question for s in first] == [s.question for s in second]

    @pytest.mark.parametrize("role", EVERY_ROLE)
    def test_it_offers_no_duplicates(self, role):
        questions = [s.question for s in build_suggestions(_context(role), frozenset({role}))]
        assert len(questions) == len(set(questions))


class TestSuggestionsFailClosed:
    def test_a_super_admin_is_offered_nothing(self):
        """Super admins administer tenants and never read tenant content."""
        assert build_suggestions(
            _context(HOSPITAL_ADMIN), frozenset({HOSPITAL_ADMIN}), is_super_admin=True
        ) == []
        assert build_suggestions(_context(SUPER_ADMIN), frozenset({SUPER_ADMIN})) == []

    def test_a_caller_with_no_roles_is_offered_nothing(self):
        assert build_suggestions(None, frozenset()) == []

    def test_a_caller_with_no_tenant_is_offered_no_content(self):
        """No retrieval context means no content entry may be read.

        Figures are gated separately, on role, so this asserts only that nothing
        from the content pack leaks out without a tenant.
        """
        built = build_suggestions(None, frozenset({RECEPTIONIST}))
        assert all(s.kind == "live_metric" for s in built)

    def test_no_figure_is_suggested_while_live_data_is_switched_off(self):
        """Off means the figures are not there, so offering one would invite a
        question the server would then decline."""
        for role in EVERY_ROLE:
            built = build_suggestions(_context(role), frozenset({role}))
            assert all(s.kind == "content" for s in built)

    def test_a_zero_limit_returns_nothing(self):
        assert build_suggestions(
            _context(DOCTOR), frozenset({DOCTOR}), limit=0
        ) == []

    def test_the_limit_is_bounded_however_large_it_is_asked_for(self):
        built = build_suggestions(_context(HOSPITAL_ADMIN), frozenset({HOSPITAL_ADMIN}), limit=999)
        assert len(built) <= 20


class TestTheEndpoint:
    def test_it_is_registered_and_reads_nothing_from_the_request(self):
        """Roles and tenant come from the verified token, never a parameter."""
        import inspect

        from app.assistant.router import assistant_suggestions

        accepted = set(inspect.signature(assistant_suggestions).parameters)
        for forbidden in ("roles", "role", "tenant_id", "department", "user_sub"):
            assert forbidden not in accepted, (
                f"the suggestions endpoint accepts {forbidden}, which would let a "
                f"browser ask what another role may see"
            )

    def test_the_response_names_no_entry_or_metric(self):
        """The browser is told the question, never what backs it."""
        from app.assistant.contracts import AssistantSuggestion

        fields = set(AssistantSuggestion.model_fields)
        assert fields == {"question", "kind"}


class TestTheHelpEntryAnswersTheObviousQuestion:
    """"What can you help me with?" returned nothing at all, for everyone."""

    @pytest.mark.parametrize("role", EVERY_ROLE)
    def test_asking_what_the_assistant_does_retrieves_the_scope_policy(self, role):
        found = retrieve(_context(role), "What can this assistant help me with?", limit=5)
        assert "policy.assistant.scope" in [e.entry_id for e in found]

    def test_the_scope_policy_is_visible_to_every_member_of_staff(self):
        entry = next(
            e for e in OPERATIONAL_CONTENT if e.entry_id == "policy.assistant.scope"
        )
        assert entry.kind is ContentKind.POLICY
        for role in EVERY_ROLE:
            assert role in entry.roles


class TestTheSwahiliQuestionsWorkToo:
    """The Swahili half of the corpus, held to exactly the same standard.

    This exists because Swahili retrieval was quietly broken in a way no test
    caught: "Ninawezaje kubadilisha nywila yangu?" - how do I change my password,
    an entry every role can read - matched no content at all, because neither
    "kubadilisha" nor "nywila" was in the vocabulary map. The assistant printed
    "I can help you change your password" and then could not.

    Every Swahili example question below must reach the same entry or metric its
    English twin does. That makes this the regression suite for Swahili as much
    as it is a check on the suggestions.
    """

    @pytest.mark.parametrize(
        "entry", ENTRIES_WITH_A_QUESTION, ids=lambda e: e.entry_id
    )
    def test_the_swahili_question_retrieves_the_same_entry(self, entry):
        assert entry.swahili_example_question, (
            entry.entry_id + " has no Swahili question, so a Swahili speaker "
            "would be offered English inside a Swahili answer"
        )
        for role in sorted(entry.roles):
            found = retrieve(
                _context(role), entry.swahili_example_question, limit=5
            )
            assert entry.entry_id in [e.entry_id for e in found], (
                f"{entry.entry_id} suggests {entry.swahili_example_question!r} in "
                f"Swahili, which retrieves {[e.entry_id for e in found]} for a "
                f"{role} - not itself"
            )

    @pytest.mark.parametrize(
        "metric", METRICS_WITH_A_QUESTION, ids=lambda m: m.metric_id
    )
    def test_the_swahili_question_routes_to_the_same_metric(self, metric):
        assert metric.swahili_example_question, (
            metric.metric_id + " has no Swahili question"
        )
        for role in sorted(metric.allowed_roles):
            routed = route(
                metric.swahili_example_question, roles=frozenset({role})
            )
            assert metric.metric_id in [r.definition.metric_id for r in routed], (
                f"{metric.metric_id} suggests {metric.swahili_example_question!r} "
                f"in Swahili, which routes to "
                f"{[r.definition.metric_id for r in routed]} for a {role}"
            )


class TestASuggestionListRespondsToWhatWasAsked:
    """A fixed list is what makes a fallback feel canned.

    Ranking by overlap with the question turns "here is everything I can do" into
    "did you mean one of these". It only reorders a list already filtered to what
    the caller may reach, so it can never widen an offer.
    """

    def test_a_password_question_puts_the_password_suggestion_first(self):
        offered = build_suggestions(
            _context(TRIAGE_NURSE),
            frozenset({TRIAGE_NURSE}),
            question="how do I change my password",
        )
        assert offered[0].question == "How do I change my password?"

    def test_a_swahili_password_question_ranks_it_first_too(self):
        offered = build_suggestions(
            _context(TRIAGE_NURSE),
            frozenset({TRIAGE_NURSE}),
            question="Ninawezaje kubadilisha nywila yangu?",
        )
        assert offered[0].question == "How do I change my password?"

    def test_a_bed_question_puts_a_bed_suggestion_first(self, live_data_on):
        offered = build_suggestions(
            _context(WARD_NURSE),
            frozenset({WARD_NURSE}),
            question="how many beds do we have free",
        )
        assert "bed" in offered[0].question.lower()

    def test_ranking_never_offers_something_the_role_cannot_reach(self, live_data_on):
        """Reordering must not widen. A pharmacist asking about money still gets
        no money question, however closely the words match."""
        offered = build_suggestions(
            _context(PHARMACIST),
            frozenset({PHARMACIST}),
            question="how much have we collected in payments today",
        )
        for suggestion in offered:
            assert "collected" not in suggestion.question.lower()
            assert "outstanding" not in suggestion.question.lower()

    def test_with_no_question_the_order_is_the_stable_default(self):
        plain = build_suggestions(_context(CASHIER), frozenset({CASHIER}))
        again = build_suggestions(_context(CASHIER), frozenset({CASHIER}), question="")
        assert [s.question for s in plain] == [s.question for s in again]

    def test_a_swahili_reply_offers_swahili_questions(self):
        offered = build_suggestions(_context(TRIAGE_NURSE), frozenset({TRIAGE_NURSE}))
        for suggestion in offered:
            text = suggestion.text(swahili=True)
            assert text == suggestion.swahili_question or not suggestion.swahili_question


class TestWhatIsOfferedAfterAnAnswer:
    """A reply used to be the end of the conversation.

    Starting questions were offered only while the thread was empty, so asking
    one thing took them away and left a blank box. Follow-ups are the same vetted
    questions, ranked against what was just asked, so the offer after an answer
    is exactly as safe as the offer before one - and the thread's own questions
    are removed, because reading somebody's question back to them is the
    clearest possible sign that nothing is listening.
    """

    def test_it_never_offers_back_the_question_just_asked(self):
        asked = "How do I change my password?"
        offered = build_follow_ups(
            _context(TRIAGE_NURSE), frozenset({TRIAGE_NURSE}), question=asked
        )
        assert asked not in offered

    def test_it_never_offers_back_a_question_asked_earlier_in_the_thread(self):
        earlier = "How do I change my password?"
        offered = build_follow_ups(
            _context(TRIAGE_NURSE),
            frozenset({TRIAGE_NURSE}),
            question="where do I record observations",
            asked=(earlier,),
        )
        assert earlier not in offered

    def test_punctuation_and_case_do_not_smuggle_a_question_back_in(self):
        offered = build_follow_ups(
            _context(TRIAGE_NURSE),
            frozenset({TRIAGE_NURSE}),
            question="  how do I CHANGE my password  ",
        )
        assert "How do I change my password?" not in offered

    def test_it_offers_no_more_than_the_limit(self):
        offered = build_follow_ups(
            _context(HOSPITAL_ADMIN),
            frozenset({HOSPITAL_ADMIN}),
            question="what reports can I run",
        )
        assert len(offered) <= FOLLOW_UP_LIMIT

    def test_every_follow_up_is_a_question_this_caller_could_already_be_offered(self):
        """Following on must not widen. A follow-up is drawn from the same
        filtered list as a starting question, never from anything else."""
        reachable = {
            s.question
            for s in build_suggestions(
                _context(CASHIER), frozenset({CASHIER}), limit=20
            )
        }
        offered = build_follow_ups(
            _context(CASHIER), frozenset({CASHIER}), question="how do I take a payment"
        )
        assert offered
        for follow_up in offered:
            assert follow_up in reachable

    def test_a_role_that_cannot_reach_a_figure_is_not_offered_it(self, live_data_on):
        offered = build_follow_ups(
            _context(PHARMACIST),
            frozenset({PHARMACIST}),
            question="how much have we collected in payments today",
        )
        for follow_up in offered:
            assert "collected" not in follow_up.lower()
            assert "outstanding" not in follow_up.lower()

    def test_a_caller_with_no_roles_is_offered_nothing(self):
        assert build_follow_ups(None, frozenset(), question="anything at all") == []

    def test_a_super_admin_is_offered_nothing(self):
        assert (
            build_follow_ups(
                _context(SUPER_ADMIN),
                frozenset({SUPER_ADMIN}),
                question="what can you help me with",
                is_super_admin=True,
            )
            == []
        )

    def test_a_swahili_answer_offers_swahili_follow_ups(self):
        swahili = {
            s.text(swahili=True)
            for s in build_suggestions(
                _context(TRIAGE_NURSE), frozenset({TRIAGE_NURSE}), limit=20
            )
        }
        offered = build_follow_ups(
            _context(TRIAGE_NURSE),
            frozenset({TRIAGE_NURSE}),
            question="Ninawezaje kubadilisha nywila yangu?",
            swahili=True,
        )
        assert offered
        for follow_up in offered:
            assert follow_up in swahili
