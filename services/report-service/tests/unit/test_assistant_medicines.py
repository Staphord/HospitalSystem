"""The medicines reference, and the gates around it.

A doctor asking "can these two be given together to a pregnant woman" is the
question this capability exists for, and it is also the question with the most
room to do harm. These tests are organised around the four things that keep the
second from happening:

1. The pack is internally consistent - every rule reaches real medicines, every
   suggestion names one, nothing points at a class nothing carries.
2. The right questions reach it, and only those. A stock question stays with the
   operational assistant; a clinical question from a receptionist is refused as
   it always was.
3. The model cannot introduce pharmacology. Every number in its answer is
   checked back against the reference extract, and an answer that reassures is
   thrown away.
4. Every gate fails closed - flag off, wrong role, super admin, read-only
   session - and the fall-through is the refusal that existed before.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from app.assistant import service as svc
from app.assistant.audit import AssistantOutcome
from app.assistant.capabilities import describe_capabilities
from app.assistant.contracts import AssistantAnswerStatus, AssistantChatRequest
from app.assistant.flags import AssistantCapability
from app.assistant.medicines import reference as medicines
from app.assistant.medicines.answering import MEDICINES_INSTRUCTIONS, footer
from shared.medicines.models import Population, PregnancyStance, Severity
from shared.medicines.pack import INTERACTION_RULES, MONOGRAPHS
from app.assistant.permissions import (
    CASHIER,
    DOCTOR,
    HOSPITAL_ADMIN,
    LAB_TECHNICIAN,
    PHARMACIST,
    RADIOGRAPHER,
    RECEPTIONIST,
    TRIAGE_NURSE,
    WARD_NURSE,
    is_role_allowed,
)
from app.assistant.provider import (
    AssistantProviderError,
    ProviderErrorCode,
    ProviderResponse,
)
from app.assistant.retrieval import build_retrieval_context
from app.assistant.service import answer_question, build_caller
from app.assistant.suggestions import build_suggestions

TENANT = "hosp-test"
REQUEST_ID = "req-med-1"


@dataclass
class Ctx:
    user_sub: str = "user-1"
    tenant_id: str | None = TENANT
    roles: tuple = (DOCTOR,)
    is_super_admin: bool = False
    scope: str = "full"


class StubProvider:
    name = "stub"

    def __init__(self, text="The reference says so.", error=None):
        self.text = text
        self.error = error
        self.requests = []

    def describe(self):
        return {"provider": "stub", "model_version": "stub-1"}

    async def complete(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return ProviderResponse(text=self.text, model_version="stub-1")


@pytest.fixture(autouse=True)
def chat_on(monkeypatch):
    monkeypatch.setattr(
        svc.settings, "assistant_operational_chat_enabled", True, raising=False
    )


@pytest.fixture
def medicines_on(monkeypatch):
    """Switch the medicines capability on for one test.

    It ships off, like every clinical capability here, so the tests that assert
    the off behaviour simply do not take this fixture.
    """
    monkeypatch.setattr(
        svc.settings, "assistant_medication_check_enabled", True, raising=False
    )


@pytest.fixture
def model_fallback_on(monkeypatch):
    """Allow medicines outside the pack to be answered from model knowledge.

    Its own flag, separate from the capability, so a hospital can run the
    reference and nothing but the reference. Off by default, which is why the
    tests asserting the reference-only behaviour simply do not take it.
    """
    monkeypatch.setattr(
        svc.settings, "assistant_medicines_model_fallback_enabled", True, raising=False
    )


@pytest.fixture
def stub(monkeypatch):
    provider = StubProvider()
    monkeypatch.setattr(svc, "get_provider", lambda: provider)
    return provider


def ask(question, roles=(DOCTOR,), **ctx_kwargs):
    return asyncio.run(
        answer_question(
            REQUEST_ID,
            build_caller(Ctx(roles=roles, **ctx_kwargs)),
            AssistantChatRequest(question=question),
        )
    )


# ---------------------------------------------------------------------------
# 1. The pack itself
# ---------------------------------------------------------------------------


class TestThePackIsInternallyConsistent:
    def test_every_drug_id_is_unique(self):
        ids = [monograph.drug_id for monograph in MONOGRAPHS]
        assert len(ids) == len(set(ids))

    def test_every_rule_id_is_unique(self):
        ids = [rule.rule_id for rule in INTERACTION_RULES]
        assert len(ids) == len(set(ids))

    def test_no_name_resolves_to_two_different_medicines(self):
        """A name that reaches two monographs would make the answer a coin toss."""
        seen: dict[str, str] = {}
        for monograph in MONOGRAPHS:
            for name in monograph.names:
                assert name not in seen or seen[name] == monograph.drug_id, (
                    f"{name!r} resolves to both {seen.get(name)} and {monograph.drug_id}"
                )
                seen[name] = monograph.drug_id

    @pytest.mark.parametrize("rule", INTERACTION_RULES, ids=lambda r: r.rule_id)
    def test_every_rule_names_medicines_or_classes_the_pack_carries(self, rule):
        """A rule pointing at nothing is a rule that silently never fires.

        This is the failure this test exists for: renaming a class constant, or
        removing the last medicine in a class, detaches its rules without any
        error anywhere. The interaction simply stops being reported, and an
        interaction that stops being reported is indistinguishable from one that
        does not exist.
        """
        drug_ids = {monograph.drug_id for monograph in MONOGRAPHS}
        classes = {c for monograph in MONOGRAPHS for c in monograph.drug_classes}

        for drug, klass in ((rule.drug_a, rule.class_a), (rule.drug_b, rule.class_b)):
            if drug:
                assert drug in drug_ids, f"{rule.rule_id} names unknown medicine {drug}"
            else:
                assert klass in classes, f"{rule.rule_id} names unused class {klass}"

    @pytest.mark.parametrize("rule", INTERACTION_RULES, ids=lambda r: r.rule_id)
    def test_every_rule_says_what_happens_and_what_to_do(self, rule):
        """A risk with no management leaves the prescriber where they started."""
        assert rule.effect.strip()
        assert rule.management.strip()

    @pytest.mark.parametrize("m", MONOGRAPHS, ids=lambda m: m.drug_id)
    def test_every_monograph_states_a_dose_and_a_source(self, m):
        assert m.adult_dose.strip()
        assert m.source.strip()

    @pytest.mark.parametrize("m", MONOGRAPHS, ids=lambda m: m.drug_id)
    def test_a_stance_of_contraindicated_or_caution_carries_its_reasoning(self, m):
        """A verdict with no reasoning is the letter category this pack rejects."""
        if m.pregnancy_stance in (
            PregnancyStance.CONTRAINDICATED,
            PregnancyStance.CAUTION,
        ):
            assert m.pregnancy.strip(), f"{m.drug_id} states a stance with no text"

    @pytest.mark.parametrize("m", MONOGRAPHS, ids=lambda m: m.drug_id)
    def test_no_monograph_calls_anything_safe(self, m):
        """The word is the problem, wherever it appears.

        A monograph that says "safe in pregnancy" would put the phrase into the
        prompt, and the model would quite reasonably repeat it back.
        """
        for text in (m.pregnancy, m.breastfeeding, m.renal, m.hepatic, m.used_for):
            assert not medicines.has_forbidden_reassurance(text or ""), m.drug_id


class TestTheRulesReachTheRightPairs:
    def _pair(self, first: str, second: str):
        found = [m for m in MONOGRAPHS if m.drug_id in (first, second)]
        assert len(found) == 2, (first, second)
        return medicines.interactions_between(found)

    def test_warfarin_with_any_nsaid_is_avoid(self):
        for nsaid in ("ibuprofen", "diclofenac"):
            hits = self._pair("warfarin", nsaid)
            assert hits, f"no rule for warfarin with {nsaid}"
            assert hits[0][2].severity is Severity.AVOID

    def test_a_class_rule_reaches_a_pair_nobody_enumerated(self):
        """The point of class rules: diclofenac with losartan was never written."""
        hits = self._pair("diclofenac", "losartan")
        assert [hit[2].rule_id for hit in hits] == ["arb-nsaid"]

    def test_two_nsaids_are_flagged_as_duplication(self):
        hits = self._pair("ibuprofen", "diclofenac")
        assert hits[0][2].rule_id == "nsaid-nsaid"

    def test_a_medicine_is_never_interacted_with_itself(self):
        ibuprofen = [m for m in MONOGRAPHS if m.drug_id == "ibuprofen"]
        assert medicines.interactions_between(ibuprofen + ibuprofen) == []

    def test_the_worst_interaction_leads(self):
        """Warfarin with aspirin hits two rules; avoid must come before serious."""
        hits = self._pair("warfarin", "aspirin")
        assert len(hits) >= 2
        assert hits[0][2].severity is Severity.AVOID

    def test_pairs_with_nothing_recorded_return_nothing(self):
        assert self._pair("paracetamol", "amoxicillin") == []


# ---------------------------------------------------------------------------
# 2. What reaches the medicines path
# ---------------------------------------------------------------------------


class TestFindingWhatTheQuestionNames:
    def test_it_finds_both_medicines_in_the_order_asked(self):
        found = medicines.find_medicines(
            "Can ibuprofen 400mg and enalapril 5mg be used together?"
        )
        assert [m.drug_id for m in found] == ["ibuprofen", "enalapril"]

    def test_a_brand_name_reaches_the_generic_monograph(self):
        assert [m.drug_id for m in medicines.find_medicines("is panadol ok")] == [
            "paracetamol"
        ]

    def test_spelling_with_or_without_a_hyphen_reaches_the_same_entry(self):
        for spelling in ("co-trimoxazole", "co trimoxazole", "cotrimoxazole", "septrin"):
            assert [m.drug_id for m in medicines.find_medicines(spelling)] == [
                "cotrimoxazole"
            ], spelling

    def test_naming_one_medicine_twice_does_not_duplicate_it(self):
        found = medicines.find_medicines("paracetamol and panadol together")
        assert [m.drug_id for m in found] == ["paracetamol"]

    def test_a_name_inside_another_word_is_not_a_match(self):
        assert medicines.find_medicines("the ironing room") == []

    def test_a_medicine_the_pack_does_not_carry_is_named_back(self):
        question = "can warfarin 5mg and amiodarone 200mg be combined"
        found = medicines.find_medicines(question)
        assert medicines.unresolved_names(question, found) == ["amiodarone"]

    def test_the_words_of_the_question_are_never_reported_as_medicines(self):
        question = "what painkiller can I prescribe for a pregnant patient"
        assert medicines.unresolved_names(question, []) == []


class TestSpottingAMedicineThePackDoesNotCarry:
    """The half of the question that would otherwise be answered silently.

    Detection used to need a dose beside the name, so "can warfarin and
    amiodarone be used together" reported nothing unknown and came back
    confidently answered about warfarin alone - not an answer, not a refusal,
    but an answer to half the question with no sign that the other half had been
    dropped.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "amiodarone", "digoxin", "lisinopril", "valsartan", "clopidogrel",
            "spironolactone", "levothyroxine", "sertraline", "haloperidol",
            "valproate", "levetiracetam", "tenofovir", "lamivudine",
            "efavirenz", "pyrazinamide", "vancomycin", "meropenem",
            "cloxacillin", "nitrofurantoin", "albendazole", "praziquantel",
            "ivermectin", "chloroquine", "oxytocin", "misoprostol",
            "hydralazine", "labetalol", "propranolol", "metoclopramide",
            "promethazine", "ceftazidime", "cefixime", "insulin", "lithium",
        ],
    )
    def test_a_medicine_outside_the_pack_is_recognised_as_one(self, name):
        """Recognised as a medicine, not resolved to one. The pack carries
        nothing about these, and the point is to say so rather than to answer
        around them."""
        assert medicines.unresolved_names(f"what is the dose of {name}", []) == [name]

    def test_it_is_recognised_beside_a_medicine_the_pack_does_carry(self):
        question = "Can warfarin and amiodarone be used together?"
        found = medicines.find_medicines(question)
        assert [m.drug_id for m in found] == ["warfarin"]
        assert medicines.unresolved_names(question, found) == ["amiodarone"]

    @pytest.mark.parametrize(
        "question",
        [
            "what painkiller can I prescribe",
            "is this the right protocol for a pregnant patient",
            "how do I determine the dose",
            "can these medicines be used together",
            "certain patients need a separate dose",
            "which antibiotic should I use",
        ],
    )
    def test_ordinary_clinical_english_is_not_reported_as_a_medicine(self, question):
        """A false positive tells a clinician their own words are an
        unrecognised medicine, which reads as the assistant being broken."""
        assert medicines.unresolved_names(question, medicines.find_medicines(question)) == []

    def test_a_medicine_the_pack_carries_is_never_reported_as_unknown(self):
        question = "warfarin with metronidazole"
        found = medicines.find_medicines(question)
        assert medicines.unresolved_names(question, found) == []


class TestDetectingWhoTheQuestionIsAbout:
    @pytest.mark.parametrize(
        "question,expected",
        [
            ("is this ok in a pregnant woman", Population.PREGNANCY),
            ("she is breastfeeding", Population.BREASTFEEDING),
            ("the patient has kidney impairment", Population.RENAL),
            ("in liver disease", Population.HEPATIC),
            ("for a child of 6", Population.CHILD),
            ("in an elderly patient", Population.ELDERLY),
            ("kwa mjamzito", Population.PREGNANCY),
        ],
    )
    def test_it_reads_the_group_from_the_clinicians_words(self, question, expected):
        assert expected in medicines.detect_populations(question)

    def test_a_question_about_nobody_in_particular_detects_nothing(self):
        assert medicines.detect_populations("what is the dose of amoxicillin") == []

    def test_pregnancy_leads_the_extract_when_it_was_asked_about(self):
        found = medicines.find_medicines("ibuprofen in pregnancy")
        block = medicines.render_block(found, [], [Population.PREGNANCY])
        assert "In pregnancy" in block

    def test_a_contraindication_in_pregnancy_appears_even_unasked(self):
        """Nobody should have to know to ask. If the reference says do not use
        this in pregnancy, that belongs in the extract either way."""
        found = medicines.find_medicines("what is the dose of warfarin")
        block = medicines.render_block(found, [], [])
        assert "In pregnancy" in block


class TestWhichQuestionsAreMedicinesQuestions:
    @pytest.mark.parametrize(
        "question",
        [
            "can ibuprofen and enalapril be used together",
            "what is the dose of amoxicillin",
            "does warfarin interact with metronidazole",
            "is metronidazole used in pregnancy",
            "what are the side effects of gentamicin",
            "je, dawa hizi zinaweza kutumika pamoja",
        ],
    )
    def test_a_clinical_medicine_question_routes_here(self, question):
        assert medicines.is_medicines_question(question) is True

    @pytest.mark.parametrize(
        "question",
        [
            "which drugs are low in stock",
            "how do I dispense a prescription",
            "is paracetamol out of stock",
            "where do I find the pharmacy inventory",
            "how do I record a dose in the system",
        ],
    )
    def test_an_operational_question_stays_with_the_operational_assistant(
        self, question
    ):
        """These are answered well today by the content pack and the stock
        metrics. Pulling them into the clinical path would replace a working
        answer with a monograph nobody asked for."""
        assert medicines.is_medicines_question(question) is False

    @pytest.mark.parametrize(
        "question",
        ["how do I register a patient", "how many beds are free", "what reports can I run"],
    )
    def test_an_unrelated_question_does_not_route_here(self, question):
        assert medicines.is_medicines_question(question) is False

    @pytest.mark.parametrize(
        "question",
        [
            "can these medicines be used together",
            "je, dawa hizi zinaweza kutumika pamoja",
            "is this combination a problem",
        ],
    )
    def test_asking_whether_things_go_together_routes_here_unnamed(self, question):
        """Nobody asks whether two screens go together. These reach the
        reference even before a medicine has been named, so the reply can ask
        which two rather than refuse the topic."""
        assert medicines.is_medicines_question(question) is True

    @pytest.mark.parametrize(
        "question",
        [
            "how do I prescribe in the system",
            "where do I add a medication to the prescription",
            "how do I change a dose on the screen",
        ],
    )
    def test_a_workflow_question_that_mentions_medicines_stays_operational(
        self, question
    ):
        """The words "prescribe", "medication" and "dose" appear in workflow
        questions the operational assistant already answers well. A capability
        that is added must not take answers away from the one that was there
        first, so those words alone do not route a question here."""
        assert medicines.is_medicines_question(question) is False

    def test_a_word_containing_system_is_not_read_as_the_software(self):
        """"Systemic" contains "system". Matching on substrings rather than
        whole words sent a real clinical question to the operational path."""
        assert medicines.is_medicines_question(
            "what is the dose of fluconazole in systemic infection"
        ) is True


# ---------------------------------------------------------------------------
# 3. The model cannot introduce pharmacology
# ---------------------------------------------------------------------------


class TestTheAnswerIsCheckedAgainstTheReference:
    def test_a_number_the_reference_supplied_passes(self):
        block = "Usual adult dose: 500 mg by mouth every 8 hours."
        ok, offending = medicines.validate_doses("Give 500 mg every 8 hours.", block)
        assert ok is True and offending is None

    def test_a_number_the_reference_never_supplied_fails(self):
        block = "Usual adult dose: 500 mg by mouth every 8 hours."
        ok, offending = medicines.validate_doses("Give 900 mg every 8 hours.", block)
        # Reported with its unit, because that is what makes the rejection
        # readable in a log: "900" alone says nothing about what was claimed.
        assert ok is False and offending == "900 mg"

    def test_an_invented_frequency_fails_too(self):
        """A dose is a number and an interval. Getting the interval wrong is
        every bit as capable of harm as getting the milligrams wrong."""
        block = "Usual adult dose: 500 mg by mouth every 8 hours."
        ok, offending = medicines.validate_doses("500 mg every 36 hours.", block)
        assert ok is False and offending == "36 hours"

    def test_a_number_the_clinician_themselves_wrote_is_allowed(self):
        block = "Usual adult dose: 500 mg by mouth every 8 hours."
        ok, _ = medicines.validate_doses(
            "You asked about 750 mg.", block, question="is 750 mg too much"
        )
        assert ok is True

    def test_list_numbering_is_not_mistaken_for_a_dose(self):
        block = "Usual adult dose: 500 mg by mouth every 8 hours."
        ok, _ = medicines.validate_doses("1. Give 500 mg\n2. Review in 8 hours", block)
        assert ok is True

    def test_an_invented_maximum_fails(self):
        """Found by probing: "max 3 g daily" passed against a reference that
        says 1 g, because 3 was in the small-integer allowance. Small numbers
        are where frequencies and ceilings live, which makes them the last
        numbers that should have been waved through."""
        block = "Usual adult dose: 500 mg every 8 hours. Maximum: 1 g every 8 hours."
        ok, offending = medicines.validate_doses("500 mg every 8 hours, max 3 g daily.", block)
        assert ok is False and offending == "3 g"

    @pytest.mark.parametrize(
        "answer,offending",
        [
            ("Give 500 micrograms every 8 hours.", "500 micrograms"),
            ("Give 500 g every 8 hours.", "500 g"),
            ("Give 500 ml every 8 hours.", "500 ml"),
        ],
    )
    def test_a_unit_substitution_fails_even_with_the_right_number(
        self, answer, offending
    ):
        """A thousandfold error wearing the reference's own figure.

        Checking the number alone accepted "500 micrograms" against a reference
        that says 500 mg, because the number matched. The unit is half of the
        dose, so both halves are checked.
        """
        block = "Usual adult dose: 500 mg by mouth every 8 hours."
        ok, got = medicines.validate_doses(answer, block)
        assert ok is False and got == offending

    def test_a_frequency_inside_a_range_the_reference_gave_is_allowed(self):
        """"Every 4 to 6 hours" states two acceptable frequencies and an answer
        may quote either, so the range widens what is allowed rather than
        pinning the answer to one end of it."""
        block = "Usual adult dose: 500 mg to 1 g by mouth every 4 to 6 hours."
        ok, _ = medicines.validate_doses("500 mg every 4 hours.", block)
        assert ok is True

    def test_counting_words_in_prose_are_not_treated_as_doses(self):
        """A number with no unit is not a dose claim. Rejecting "both of these
        2 medicines" would send good answers to the fallback for writing
        ordinary English."""
        block = "Usual adult dose: 500 mg by mouth every 8 hours."
        ok, _ = medicines.validate_doses("Both of these 2 medicines are NSAIDs.", block)
        assert ok is True

    @pytest.mark.parametrize("m", MONOGRAPHS, ids=lambda m: m.drug_id)
    def test_every_monograph_can_be_quoted_back_without_being_rejected(self, m):
        """The other half of the guard: a validator that rejects faithful
        answers is safe and useless, because every answer becomes the fallback
        and the model may as well not be there. Every dose in the pack must
        survive being quoted."""
        block = medicines.render_block([m], [], list(Population))
        quoted = " ".join(
            part
            for part in (
                m.adult_dose,
                m.max_adult_dose,
                m.pregnancy,
                m.breastfeeding,
                m.renal,
                m.hepatic,
                m.monitoring,
                " ".join(m.cautions),
            )
            if part
        )
        ok, offending = medicines.validate_doses(quoted, block)
        assert ok is True, f"{m.drug_id} rejected on {offending!r}"

    @pytest.mark.parametrize(
        "answer",
        [
            "This combination is safe.",
            "They are safe to take together.",
            "There is no risk in giving both.",
            "It is a safe combination.",
        ],
    )
    def test_an_answer_that_reassures_is_caught(self, answer):
        assert medicines.has_forbidden_reassurance(answer) is True

    def test_stating_what_is_recorded_is_not_reassurance(self):
        answer = (
            "The reference lists no interaction between them. That is not the "
            "same as an established absence of one."
        )
        assert medicines.has_forbidden_reassurance(answer) is False


class TestAlternativesTheReferenceItselfNames:
    """When the reference says "change to methyldopa", it has to say what that is.

    Probing realistic answers turned this up: asked about enalapril in
    pregnancy, the extract said to change to methyldopa or nifedipine and then
    said nothing about either, so a model that helpfully added the dose had its
    whole answer rejected by the dose guard. The model was right to want the
    dose; the extract was wrong to name a medicine and go quiet.
    """

    def _for(self, question):
        found = medicines.find_medicines(question)
        return medicines.alternatives_named(
            found, medicines.interactions_between(found)
        )

    def test_a_pregnancy_alternative_is_carried_with_its_dose(self):
        offered = {m.drug_id for m in self._for("enalapril in pregnancy")}
        assert {"methyldopa", "nifedipine"} <= offered

    def test_an_interaction_management_alternative_is_carried(self):
        offered = {m.drug_id for m in self._for("simvastatin with clarithromycin")}
        assert "azithromycin" in offered

    def test_the_extract_states_their_doses(self):
        question = "can ibuprofen and enalapril be given in pregnancy"
        found = medicines.find_medicines(question)
        block = medicines.render_block(
            found,
            medicines.interactions_between(found),
            medicines.detect_populations(question),
        )
        assert "Methyldopa" in block
        assert "250 mg by mouth two to three times a day" in block

    def test_an_answer_may_then_quote_that_dose(self):
        """The point of the whole change: this answer used to be thrown away."""
        question = "can ibuprofen 400mg and enalapril 5mg be given to a pregnant woman"
        found = medicines.find_medicines(question)
        block = medicines.render_block(
            found,
            medicines.interactions_between(found),
            medicines.detect_populations(question),
        )
        answer = (
            "Stop the enalapril and change to methyldopa 250 mg by mouth two to "
            "three times a day. Use paracetamol 500 mg to 1 g every 4 to 6 hours "
            "for the pain."
        )
        ok, offending = medicines.validate_doses(answer, block, question)
        assert ok is True, offending

    def test_an_alternative_is_never_treated_as_co_prescribed(self):
        """It is a suggestion, not a second prescription, so no interaction is
        computed for it and the extract says so."""
        question = "enalapril in pregnancy"
        found = medicines.find_medicines(question)
        interactions = medicines.interactions_between(found)
        assert interactions == []
        block = medicines.render_block(found, interactions, [Population.PREGNANCY])
        assert "no interaction has been checked for them" in block

    def test_a_question_with_no_alternatives_named_carries_none(self):
        assert self._for("what is the dose of amoxicillin") == []


class TestWhatTheClinicianActuallyGets:
    def test_a_good_answer_reaches_the_clinician_with_the_reference_footer(
        self, medicines_on, stub
    ):
        stub.text = "Avoid both here. Change to methyldopa and use paracetamol."
        response, audit = ask(
            "Can ibuprofen and enalapril be used together in a pregnant woman?"
        )

        assert response.status is AssistantAnswerStatus.SUPPORTED
        assert audit.outcome is AssistantOutcome.SUCCESS
        assert audit.capability is AssistantCapability.MEDICATION_CHECK
        assert "Change to methyldopa" in response.answer
        assert footer() in response.answer

    def test_the_answer_is_stamped_with_the_reference_version(
        self, medicines_on, stub
    ):
        _, audit = ask("what is the dose of amoxicillin")
        assert audit.ruleset_version == medicines.pack_version()

    def test_the_model_is_given_the_reference_and_the_clinical_instructions(
        self, medicines_on, stub
    ):
        ask("Can ibuprofen and enalapril be given together in pregnancy?")

        assert len(stub.requests) == 1
        request = stub.requests[0]
        assert request.instructions == MEDICINES_INSTRUCTIONS
        # The pharmacology it is allowed to use, and nothing else.
        assert "Serious interaction" in request.content
        assert "Contraindicated" in request.content or "contraindicated" in request.content

    def test_an_invented_dose_costs_the_model_its_whole_answer(
        self, medicines_on, stub
    ):
        stub.text = "Give amoxicillin 900 mg every 8 hours."
        response, audit = ask("what is the adult dose of amoxicillin")

        assert audit.outcome is AssistantOutcome.NEEDS_REVIEW
        assert "900" not in response.answer
        # The clinician is not left with nothing: the reference extract stands
        # in for the rejected answer.
        assert "500 mg by mouth every 8 hours" in response.answer

    def test_an_answer_that_calls_a_combination_safe_is_replaced(
        self, medicines_on, stub
    ):
        stub.text = "Yes, this combination is safe."
        response, audit = ask("can paracetamol and amoxicillin be given together")

        assert audit.outcome is AssistantOutcome.NEEDS_REVIEW
        assert "safe" not in response.answer.lower()

    def test_a_provider_outage_still_answers_from_the_reference(self, monkeypatch):
        monkeypatch.setattr(
            svc.settings, "assistant_medication_check_enabled", True, raising=False
        )
        broken = StubProvider(
            error=AssistantProviderError(
                ProviderErrorCode.UNAVAILABLE, "provider is down"
            )
        )
        monkeypatch.setattr(svc, "get_provider", lambda: broken)

        response, audit = ask("what is the adult dose of amoxicillin")

        assert audit.outcome is AssistantOutcome.PROVIDER_ERROR
        assert response.status is AssistantAnswerStatus.SUPPORTED
        assert "500 mg by mouth every 8 hours" in response.answer

    def test_a_medicine_outside_the_reference_is_named_not_guessed_at(
        self, medicines_on, stub
    ):
        stub.text = "Warfarin needs INR monitoring."
        response, _ = ask("can warfarin 5mg and amiodarone 200mg be used together")

        assert "amiodarone" in response.answer
        assert "no entry for" in response.answer

    def test_a_question_naming_no_medicine_asks_for_one(self, medicines_on, stub):
        response, audit = ask("what painkiller can I prescribe")

        assert response.status is AssistantAnswerStatus.UNSUPPORTED
        assert "Name the medicine" in response.answer
        # Nothing was sent to the provider: there was nothing to organise.
        assert stub.requests == []

    def test_a_swahili_question_is_answered_in_swahili(self, medicines_on, stub):
        stub.text = "Hapana, enalapril haipaswi kutumika wakati wa ujauzito."
        response, _ = ask(
            "Je, ibuprofen na enalapril zinaweza kutumika pamoja kwa mjamzito?"
        )
        assert footer(swahili=True) in response.answer

    def test_no_operational_follow_ups_are_offered_under_a_clinical_answer(
        self, medicines_on, stub
    ):
        response, _ = ask("what is the dose of amoxicillin")
        assert response.follow_ups == []


# ---------------------------------------------------------------------------
# 4. Every gate fails closed
# ---------------------------------------------------------------------------


class TestAnsweringForMedicinesThePackDoesNotCarry:
    """The model answers directly, and the server says whose answer it is.

    The reference cannot be extended fast enough to cover everything a hospital
    stocks, so "no entry for amiodarone" is a dead end for the person holding
    the chart. What this path gives up is the dose guard - there is no supplied
    text to check figures against - so everything here is about making sure the
    reader cannot mistake one kind of answer for the other.
    """

    def test_it_is_off_unless_the_hospital_turns_it_on(self, medicines_on, stub):
        """Warfarin is in the pack, so the verified path answers as it always
        did and amiodarone is named back as missing - no model knowledge, and
        the reference instructions, not the model-knowledge ones."""
        response, _ = ask("can warfarin and amiodarone be given together")

        assert "no entry for" in response.answer
        assert "NOT FROM THE HOSPITAL REFERENCE" not in response.answer
        assert stub.requests[0].instructions == MEDICINES_INSTRUCTIONS

    def test_with_it_on_the_model_is_asked_about_the_medicine(
        self, medicines_on, model_fallback_on, stub
    ):
        stub.text = "Amiodarone raises the INR substantially."
        response, _ = ask("can warfarin and amiodarone be given together")

        assert len(stub.requests) == 1
        assert "amiodarone" in stub.requests[0].content.lower()
        assert "Amiodarone raises the INR" in response.answer

    def test_the_answer_opens_with_what_it_is_not(
        self, medicines_on, model_fallback_on, stub
    ):
        """The banner leads. A caveat underneath is read after the reader has
        already decided what to do."""
        stub.text = "Amiodarone raises the INR substantially."
        response, _ = ask("can warfarin and amiodarone be given together")

        assert response.answer.startswith("NOT FROM THE HOSPITAL REFERENCE")
        assert "amiodarone" in response.answer
        assert "not been verified" in response.answer

    def test_it_does_not_carry_the_hospital_reference_footer(
        self, medicines_on, model_fallback_on, stub
    ):
        """The footer says an answer came from the reference. This one did not,
        and the two must never appear on the same answer."""
        stub.text = "Amiodarone raises the INR substantially."
        response, _ = ask("can warfarin and amiodarone be given together")

        assert footer() not in response.answer
        assert "was not the source for this answer" in response.answer

    def test_the_audit_records_that_nothing_checked_it(
        self, medicines_on, model_fallback_on, stub
    ):
        """So a hospital can always ask how many of its answers were
        unverified, and which."""
        stub.text = "Amiodarone raises the INR substantially."
        _, audit = ask("can warfarin and amiodarone be given together")

        assert audit.outcome is AssistantOutcome.NEEDS_REVIEW
        assert audit.ruleset_version.endswith("+model-knowledge")

    def test_the_reference_extract_still_goes_to_the_model_as_context(
        self, medicines_on, model_fallback_on, stub
    ):
        """Where the pack does know one of the medicines, the model is held to
        it rather than left to recall it."""
        stub.text = "Amiodarone raises the INR substantially."
        ask("can warfarin and amiodarone be given together")

        content = stub.requests[0].content
        assert "Warfarin" in content
        assert "INR" in content

    def test_an_answer_that_reassures_is_still_refused(
        self, medicines_on, model_fallback_on, stub
    ):
        """The one guard that survives without a reference to check against."""
        stub.text = "Yes, this combination is safe."
        response, audit = ask("can warfarin and amiodarone be given together")

        assert audit.outcome is AssistantOutcome.NEEDS_REVIEW
        assert "safe" not in response.answer.lower()
        assert "NOT FROM THE HOSPITAL REFERENCE" not in response.answer

    def test_a_question_naming_nothing_at_all_still_asks_for_a_name(
        self, medicines_on, model_fallback_on, stub
    ):
        """The fallback is for a medicine that was named. "Do they interact"
        names none, so the answer is still to ask which."""
        response, _ = ask("do they interact")

        assert "Name the medicine" in response.answer
        assert stub.requests == []

    def test_a_medicine_the_pack_carries_never_takes_this_path(
        self, medicines_on, model_fallback_on, stub
    ):
        """The verified path is not optional where the reference has an
        answer."""
        stub.text = "Warfarin with ibuprofen is one to avoid."
        response, audit = ask("can warfarin and ibuprofen be given together")

        assert "NOT FROM THE HOSPITAL REFERENCE" not in response.answer
        assert footer() in response.answer
        assert audit.ruleset_version == medicines.pack_version()


class TestTheGates:
    def test_only_doctors_and_pharmacists_hold_the_capability(self):
        for allowed in (DOCTOR, PHARMACIST):
            assert is_role_allowed(AssistantCapability.MEDICATION_CHECK, [allowed])
        for denied in (
            HOSPITAL_ADMIN,
            RECEPTIONIST,
            TRIAGE_NURSE,
            WARD_NURSE,
            LAB_TECHNICIAN,
            RADIOGRAPHER,
            CASHIER,
        ):
            assert not is_role_allowed(AssistantCapability.MEDICATION_CHECK, [denied])

    def test_with_the_flag_off_a_doctor_gets_the_operational_refusal(self, stub):
        """Fail-closed: the capability being off must be indistinguishable from
        it never having been built."""
        response, audit = ask("can ibuprofen and enalapril be given in pregnancy")

        assert audit.capability is AssistantCapability.OPERATIONAL_CHAT
        assert medicines.pack_version() not in response.answer

    def test_a_receptionist_asking_a_clinical_question_is_refused_as_before(
        self, medicines_on, stub
    ):
        response, audit = ask(
            "can ibuprofen and enalapril be given in pregnancy", roles=(RECEPTIONIST,)
        )

        assert audit.capability is AssistantCapability.OPERATIONAL_CHAT
        assert medicines.pack_version() not in response.answer

    def test_a_pharmacist_reaches_it(self, medicines_on, stub):
        stub.text = "The reference records a serious interaction."
        _, audit = ask(
            "does warfarin interact with metronidazole", roles=(PHARMACIST,)
        )
        assert audit.capability is AssistantCapability.MEDICATION_CHECK

    def test_a_super_admin_never_reaches_it(self, medicines_on, stub):
        _, audit = ask(
            "what is the dose of amoxicillin", roles=(DOCTOR,), is_super_admin=True
        )
        assert audit.capability is AssistantCapability.OPERATIONAL_CHAT

    def test_a_read_only_session_never_reaches_it(self, medicines_on, stub):
        _, audit = ask("what is the dose of amoxicillin", scope="readonly")
        assert audit.capability is not AssistantCapability.MEDICATION_CHECK

    def test_a_caller_with_no_tenant_never_reaches_it(self, medicines_on, stub):
        _, audit = ask("what is the dose of amoxicillin", tenant_id=None)
        assert audit.capability is not AssistantCapability.MEDICATION_CHECK

    def test_a_stock_question_from_a_pharmacist_stays_operational(
        self, medicines_on, stub
    ):
        _, audit = ask("which drugs are low in stock", roles=(PHARMACIST,))
        assert audit.capability is AssistantCapability.OPERATIONAL_CHAT


class TestWhatTheAssistantSaysItCanDo:
    def _context(self, role):
        return build_retrieval_context(TENANT, [role])

    def test_medicines_is_listed_for_a_doctor_once_it_is_on(self, medicines_on):
        lines = describe_capabilities(self._context(DOCTOR), frozenset({DOCTOR}))
        assert any(line.startswith("Medicines") for line in lines)

    def test_it_is_not_listed_while_the_capability_is_off(self):
        lines = describe_capabilities(self._context(DOCTOR), frozenset({DOCTOR}))
        assert not any(line.startswith("Medicines") for line in lines)

    def test_it_is_not_listed_for_a_role_that_cannot_use_it(self, medicines_on):
        lines = describe_capabilities(
            self._context(RECEPTIONIST), frozenset({RECEPTIONIST})
        )
        assert not any(line.startswith("Medicines") for line in lines)


class TestEveryMedicineSuggestionIsAnswerable:
    """The same guarantee the operational suggestions carry.

    A suggestion that fails teaches people the assistant does not work. These
    are offered to clinicians, so a suggestion that failed would teach a
    clinician that the medicines reference does not work, on the first question
    they ever asked it.
    """

    def _offered(self, role=DOCTOR):
        return [
            suggestion
            for suggestion in build_suggestions(
                build_retrieval_context(TENANT, [role]), frozenset({role})
            )
            if suggestion.kind == "medicine"
        ]

    def test_a_doctor_is_offered_medicine_questions(self, medicines_on):
        assert self._offered()

    def test_none_are_offered_while_the_capability_is_off(self):
        assert self._offered() == []

    def test_none_are_offered_to_a_role_that_cannot_use_them(self, medicines_on):
        assert self._offered(role=RECEPTIONIST) == []

    def test_every_offered_question_routes_to_the_medicines_path(self, medicines_on):
        for suggestion in self._offered():
            assert medicines.is_medicines_question(suggestion.question), (
                suggestion.question
            )

    def test_every_offered_question_names_a_medicine_the_pack_carries(
        self, medicines_on
    ):
        for suggestion in self._offered():
            assert medicines.find_medicines(suggestion.question), suggestion.question

    def test_every_swahili_version_names_the_same_medicines(self, medicines_on):
        """A Swahili suggestion that reaches a different monograph than its
        English twin would answer a different question depending on language."""
        for suggestion in self._offered():
            if not suggestion.swahili_question:
                continue
            english = {m.drug_id for m in medicines.find_medicines(suggestion.question)}
            swahili = {
                m.drug_id for m in medicines.find_medicines(suggestion.swahili_question)
            }
            assert english == swahili, suggestion.question


class TestTheReferenceReadsNothingItShouldNot:
    def test_the_medicines_modules_open_no_database_and_make_no_request(self):
        """The reference is a file. It must stay one.

        A future change that gave it a database session would put patient data
        one import away from a prompt, so the property is asserted rather than
        assumed.
        """
        import inspect

        from app.assistant.medicines import answering, reference
        from shared.medicines import matching, models, pack

        # Both halves: the shared pack that the dispensing gate also reads, and
        # the assistant's own layer over it.
        for module in (models, pack, matching, reference, answering):
            source = inspect.getsource(module)
            for forbidden in (
                "AsyncSession",
                "sqlalchemy",
                "httpx",
                "requests.",
                "tenant_session",
                "execute(",
            ):
                assert forbidden not in source, (
                    f"{module.__name__} references {forbidden}"
                )
