"""What the assistant says it can do, and what it says when it cannot.

Three complaints drove this file, all of them fair:

  - "What can you help me with?" was answered "I do not have information about
    that." No content entry is *about* the assistant's own scope in the words
    people use, so the first question every new user asks scored zero against
    every entry and came back as a refusal.

  - A refusal was a dead end. It ended "please speak to the relevant department",
    or, from the model, "ask your department's IT support or the system
    administrator" - handing the problem to somebody who cannot fix it, for a
    question a neighbouring part of the same assistant could often have answered.

  - Nobody was told what they *could* ask.

The list is built from the two gates that decide every other answer: the content
entries this caller may read, and the metrics their roles pass. That is what
makes it impossible for it to offer something they would then be refused, and
what stops it going stale when a role's access changes.
"""

from __future__ import annotations

import re

import pytest

from app.assistant.capabilities import (
    CAPABILITY_AREAS,
    capability_answer,
    describe_capabilities,
    is_capability_question,
    prompt_block,
    refusal_with_capabilities,
)
from app.assistant.live import registry as live_registry
from app.assistant.live.registry import METRIC_REGISTRY
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
from app.assistant.retrieval import build_retrieval_context, visible_entries
from app.core.config import settings

live_registry.load_catalog()

TENANT = "hosp-test"

EVERY_ROLE = [
    HOSPITAL_ADMIN, RECEPTIONIST, TRIAGE_NURSE, WARD_NURSE, DOCTOR,
    LAB_TECHNICIAN, RADIOGRAPHER, PHARMACIST, CASHIER,
]

# Every way of sending somebody away that used to appear in an answer, or that
# the prompt used to invite. None of these may survive anywhere the user reads.
FORBIDDEN_REFERRALS = [
    "it support",
    "it department",
    "system administrator",
    "systems administrator",
    "sysadmin",
    "helpdesk",
    "help desk",
    "technical support",
    "relevant department",
    "ask your manager",
    "your supervisor",
]


def _context(role: str):
    return build_retrieval_context(TENANT, [role])


@pytest.fixture
def live_data_on(monkeypatch):
    monkeypatch.setattr(settings, "assistant_live_data_enabled", True, raising=False)


class TestItRecognisesTheQuestion:
    @pytest.mark.parametrize(
        "question",
        [
            "What can you do?",
            "What can I do?",
            "what can you help me with",
            "What can you help me with?",
            "What can I ask you?",
            "How can you help?",
            "What are your capabilities?",
            "what else can you do",
            "Who are you?",
            "What is this assistant for?",
            "Unaweza kunisaidia nini?",
            "Unasaidia nini?",
        ],
    )
    def test_a_question_about_scope_is_recognised(self, question):
        assert is_capability_question(question) is True

    @pytest.mark.parametrize(
        "question",
        [
            "How many beds are free in Maternity?",
            "How do I register a new patient?",
            "What is the average lab turnaround time today?",
            "How much have we collected today?",
            "Do these two drugs interact?",
            "",
        ],
    )
    def test_an_ordinary_question_is_not(self, question):
        assert is_capability_question(question) is False


class TestTheListIsBuiltFromWhatTheCallerCanActuallyReach:
    @pytest.mark.parametrize("role", EVERY_ROLE)
    def test_every_role_is_told_something(self, role):
        lines = describe_capabilities(_context(role), frozenset({role}))
        assert lines, f"a {role} asking what the assistant does is told nothing"

    @pytest.mark.parametrize("role", EVERY_ROLE)
    def test_it_never_names_an_area_the_role_cannot_reach(self, role, live_data_on):
        """The guarantee. An area appears only when something behind it is
        reachable, so the list cannot promise what would then be refused."""
        context = _context(role)
        offered = describe_capabilities(context, frozenset({role}))
        # visible_entries, not retrieve: retrieve caps its result at
        # MAX_ENTRIES_PER_RETRIEVAL, so using it here would make the oracle
        # narrower than the thing it is checking.
        entry_ids = {e.entry_id for e in visible_entries(context)}
        metric_ids = {
            m.metric_id
            for m in METRIC_REGISTRY.values()
            if m.is_permitted(frozenset({role}))
        }
        for area in CAPABILITY_AREAS:
            named = any(line.startswith(area.name) for line in offered)
            if not named:
                continue
            reachable = any(
                e.startswith(area.entry_prefixes) for e in entry_ids
            ) or any(m.startswith(area.metric_prefixes) for m in metric_ids)
            assert reachable, (
                f"a {role} is told they can use {area.name}, but they can reach "
                f"nothing behind it"
            )

    def test_a_cashier_is_told_about_billing_and_not_about_the_laboratory(
        self, live_data_on
    ):
        lines = " ".join(describe_capabilities(_context(CASHIER), frozenset({CASHIER})))
        assert "Billing" in lines
        assert "Laboratory" not in lines
        assert "Pharmacy" not in lines

    def test_a_lab_technician_is_told_about_the_laboratory_and_not_about_money(
        self, live_data_on
    ):
        lines = " ".join(
            describe_capabilities(_context(LAB_TECHNICIAN), frozenset({LAB_TECHNICIAN}))
        )
        assert "Laboratory" in lines
        assert "Billing" not in lines

    def test_a_pharmacist_is_told_about_stock_and_not_about_money(self, live_data_on):
        lines = " ".join(
            describe_capabilities(_context(PHARMACIST), frozenset({PHARMACIST}))
        )
        assert "Pharmacy" in lines
        assert "Billing" not in lines

    def test_only_an_administrator_is_told_about_reports(self):
        for role in EVERY_ROLE:
            lines = " ".join(describe_capabilities(_context(role), frozenset({role})))
            if role == HOSPITAL_ADMIN:
                assert "Reports" in lines
            else:
                assert "Reports" not in lines, f"a {role} was offered the report catalogue"

    def test_no_figure_is_promised_while_live_data_is_switched_off(self):
        """Off means the figures are not there, so promising one would invite a
        question the server would then decline."""
        lines = " ".join(describe_capabilities(_context(CASHIER), frozenset({CASHIER})))
        assert "how much has been collected" not in lines

    def test_the_figures_half_appears_once_live_data_is_on(self, live_data_on):
        lines = " ".join(describe_capabilities(_context(CASHIER), frozenset({CASHIER})))
        assert "how much has been collected" in lines

    @pytest.mark.parametrize("role", EVERY_ROLE)
    def test_it_is_stable_across_calls(self, role):
        first = describe_capabilities(_context(role), frozenset({role}))
        second = describe_capabilities(_context(role), frozenset({role}))
        assert first == second


class TestItFailsClosed:
    def test_a_super_admin_is_told_nothing(self):
        assert describe_capabilities(
            _context(HOSPITAL_ADMIN), frozenset({HOSPITAL_ADMIN}), is_super_admin=True
        ) == []
        assert describe_capabilities(
            _context(SUPER_ADMIN), frozenset({SUPER_ADMIN})
        ) == []

    def test_a_caller_with_no_roles_is_told_nothing(self):
        assert describe_capabilities(None, frozenset()) == []

    def test_the_refusal_is_still_a_refusal_when_there_is_nothing_to_offer(self):
        """It must not print an empty numbered list."""
        answer = refusal_with_capabilities(None, frozenset())
        assert "can't help" in answer.lower()
        assert "1." not in answer

    def test_the_capability_answer_is_empty_rather_than_a_bare_heading(self):
        assert capability_answer(None, frozenset()) == ""


class TestTheAnswerReadsLikeAnAnswer:
    @pytest.mark.parametrize("role", EVERY_ROLE)
    def test_it_is_a_numbered_list(self, role, live_data_on):
        answer = capability_answer(_context(role), frozenset({role}))
        assert "1." in answer and "2." in answer
        numbers = [int(n) for n in re.findall(r"^(\d+)\.", answer, re.MULTILINE)]
        assert numbers == list(range(1, len(numbers) + 1)), (
            "the list is not numbered consecutively from 1"
        )

    @pytest.mark.parametrize("role", EVERY_ROLE)
    def test_a_refusal_says_what_it_cannot_do_and_then_offers_real_questions(
        self, role, live_data_on
    ):
        """Real questions, not a description of the categories it covers.

        A list of areas reads as a fixed menu however carefully it was filtered -
        the honest reaction to the first version was "have you hardcoded the
        questions?". These are the same tested questions the panel suggests.
        """
        answer = refusal_with_capabilities(_context(role), frozenset({role}))
        assert answer.lower().startswith("i can't help with that")
        assert "You could ask me:" in answer
        offered = [line[2:] for line in answer.splitlines() if line.startswith("- ")]
        assert offered, "the refusal offered no questions at all"
        for question in offered:
            assert question.endswith("?"), (
                f"a refusal offered {question!r}, which is not a question"
            )

    @pytest.mark.parametrize("role", EVERY_ROLE)
    def test_a_refusal_offers_questions_this_role_can_actually_get_answered(
        self, role, live_data_on
    ):
        """Every offered question must be one of this caller's own tested ones."""
        from app.assistant.suggestions import build_suggestions

        reachable = {
            s.question
            for s in build_suggestions(
                _context(role), frozenset({role}), limit=20
            )
        }
        answer = refusal_with_capabilities(_context(role), frozenset({role}))
        for line in answer.splitlines():
            if line.startswith("- "):
                assert line[2:] in reachable, (
                    f"a {role} was offered {line[2:]!r}, which is not on their "
                    f"own list of answerable questions"
                )

    def test_a_refusal_puts_the_nearest_question_first(self, live_data_on):
        """Ranked against what was actually asked, so the fallback responds
        rather than reciting."""
        answer = refusal_with_capabilities(
            _context(TRIAGE_NURSE),
            frozenset({TRIAGE_NURSE}),
            question="how do I change my password",
        )
        first = next(line[2:] for line in answer.splitlines() if line.startswith("- "))
        assert first == "How do I change my password?"

    def test_it_answers_a_swahili_question_in_swahili(self, live_data_on):
        answer = capability_answer(
            _context(CASHIER), frozenset({CASHIER}), swahili=True
        )
        assert "Ninaweza kukusaidia" in answer
        assert "Bili na malipo" in answer

    def test_a_swahili_refusal_is_in_swahili(self, live_data_on):
        answer = refusal_with_capabilities(
            _context(DOCTOR), frozenset({DOCTOR}), swahili=True
        )
        assert "Samahani" in answer
        assert "Unaweza kuniuliza:" in answer

    def test_a_swahili_refusal_offers_swahili_questions(self, live_data_on):
        """Not English questions inside a Swahili answer."""
        answer = refusal_with_capabilities(
            _context(TRIAGE_NURSE), frozenset({TRIAGE_NURSE}), swahili=True
        )
        offered = [line[2:] for line in answer.splitlines() if line.startswith("- ")]
        assert offered
        for question in offered:
            assert not question.lower().startswith(("how do i", "how many", "what is")), (
                f"a Swahili refusal offered the English question {question!r}"
            )


class TestNobodyIsSentToIt:
    """The complaint, made into a test.

    "Stop saying contact your system administrator or IT whatsoever."
    """

    @pytest.mark.parametrize("role", EVERY_ROLE)
    @pytest.mark.parametrize("phrase", FORBIDDEN_REFERRALS)
    def test_no_refusal_sends_anyone_away(self, role, phrase, live_data_on):
        answer = refusal_with_capabilities(_context(role), frozenset({role}))
        assert phrase not in answer.lower(), (
            f"a {role} is told to {phrase!r} instead of what they can ask"
        )

    @pytest.mark.parametrize("phrase", FORBIDDEN_REFERRALS)
    def test_the_capability_answer_sends_nobody_away(self, phrase):
        answer = capability_answer(_context(DOCTOR), frozenset({DOCTOR}))
        assert phrase not in answer.lower()

    def test_the_prompt_forbids_the_model_from_doing_it_too(self):
        """The deterministic paths are only half of it: the model writes most
        refusals, and it was the one saying "ask your department's IT support"."""
        from app.assistant.service import SYSTEM_INSTRUCTIONS

        rules = SYSTEM_INSTRUCTIONS.lower()
        for phrase in ("contact it", "it department", "system administrator", "helpdesk"):
            assert phrase in rules, (
                "the prompt does not name " + phrase + " as something never to say"
            )
        assert "never tell the staff member to contact" in rules

    def test_the_prompt_no_longer_asks_the_model_to_suggest_who_could_help(self):
        from app.assistant.service import SYSTEM_INSTRUCTIONS

        assert "suggest who\n  in the hospital could help" not in SYSTEM_INSTRUCTIONS
        assert "offer the\n  numbered list" in SYSTEM_INSTRUCTIONS


class TestTheModelIsGivenTheSameList:
    @pytest.mark.parametrize("role", EVERY_ROLE)
    def test_the_prompt_block_carries_this_caller_s_areas(self, role, live_data_on):
        block = prompt_block(_context(role), frozenset({role}))
        assert block
        for line in describe_capabilities(_context(role), frozenset({role})):
            assert line in block

    def test_a_super_admin_gets_no_prompt_block(self):
        assert prompt_block(
            _context(HOSPITAL_ADMIN), frozenset({HOSPITAL_ADMIN}), is_super_admin=True
        ) == ""

    def test_the_block_names_no_metric_id_or_entry_id(self):
        """The model is told what the person can ask about, never the machinery."""
        block = prompt_block(_context(HOSPITAL_ADMIN), frozenset({HOSPITAL_ADMIN}))
        for metric_id in METRIC_REGISTRY:
            assert metric_id not in block
        assert "workflow." not in block and "policy." not in block


class TestTheChatFlowUsesIt:
    def test_the_unsupported_branch_no_longer_carries_the_old_wording(self):
        import inspect

        from app.assistant import service

        source = inspect.getsource(service)
        # The phrase still appears in the prompt, as something the model is told
        # never to say, and in the comment explaining why. What must be gone is
        # the answer that used to be built from it.
        assert "I do not have information about that. This assistant covers" not in source
        assert "refusal_with_capabilities(" in source

    def test_a_capability_question_never_reaches_the_model(self):
        """It is composed on the server, so it cannot be hallucinated and cannot
        promise anything the caller may not reach."""
        import inspect

        from app.assistant import service

        source = inspect.getsource(service.answer_question)
        answered_at = source.index("is_capability_question(question)")
        provider_at = source.index("get_provider()")
        assert answered_at < provider_at, (
            "the capability answer is composed after the provider is selected, so "
            "it is not short-circuiting the model"
        )

    @pytest.mark.parametrize("role", EVERY_ROLE)
    def test_a_swahili_list_is_swahili_all_the_way_down(self, role, live_data_on):
        """Not Swahili headings over English descriptions.

        The first version translated only the area names, so a Swahili speaker
        got "Usajili na ziara - how to register a patient", which reads worse
        than either language on its own.
        """
        for line in describe_capabilities(
            _context(role), frozenset({role}), swahili=True
        ):
            for english in (
                " how to ", " how many ", "which drugs", "who may run them",
                " and what ", " the average ",
            ):
                assert english not in line.lower(), (
                    f"a Swahili list still carries English wording: {line!r}"
                )

    def test_every_area_that_declares_english_declares_swahili(self):
        for area in CAPABILITY_AREAS:
            if area.how_to:
                assert area.swahili_how_to, f"{area.name} has no Swahili how-to"
            if area.figures:
                assert area.swahili_figures, f"{area.name} has no Swahili figures"
            assert area.swahili_name, f"{area.name} has no Swahili name"
