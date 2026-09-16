"""Finding one patient by name, and refusing to guess which one.

Looking a patient up by name is the question staff actually ask - nobody carries
patient numbers in their head - but it is also the first route into the patient
tier that the caller does not have to already know an identifier to use. So the
tests here are mostly about what it refuses to do:

  - it never breaks a tie between people who share a name,
  - it never says who the people who shared it were,
  - it never runs for a role that could not read the answer,
  - and a number written in the question always beats a name.
"""

from __future__ import annotations

import pytest

from app.assistant.live import registry as live_registry
from app.assistant.live.contracts import MetricTier
from app.assistant.live.names import (
    MIN_NAME_TOKEN,
    NameMatch,
    NameOutcome,
    candidate_name_terms,
    looks_like_a_person_question,
    name_as_typed,
)
from app.assistant.live.registry import reaches_patient_tier
from app.assistant.live.routing import route
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
)
from app.assistant.service import _ambiguous_name_answer

live_registry.load_catalog()

RECEPTION = frozenset({RECEPTIONIST})
A_PATIENT_NUMBER = "PT-20260829-0001"


class TestWhatCountsAsANameInAQuestion:
    def test_a_plain_name_is_picked_out(self):
        assert candidate_name_terms("Where is Amina Mwita?") == ["amina", "mwita"]

    def test_the_operational_words_are_not_a_name(self):
        """A question about a bed must not resolve to a patient called Bed."""
        for question in (
            "How many beds are free?",
            "What is the status of the doctor queue?",
            "Which patients are admitted to the ward?",
            "How much is outstanding on unpaid bills?",
        ):
            for term in candidate_name_terms(question):
                assert term not in {
                    "bed", "beds", "ward", "queue", "status", "admitted",
                    "outstanding", "balance", "doctor",
                }, f"{term!r} from {question!r} would be treated as a name"

    def test_a_ward_name_is_not_a_patient_name(self):
        terms = candidate_name_terms(
            "Where is Maternity Annex", known_wards=["Maternity Annex"]
        )
        assert "maternity" not in terms and "annex" not in terms

    def test_short_tokens_are_ignored(self):
        for term in candidate_name_terms("Where is Jo Li now?"):
            assert len(term) >= MIN_NAME_TOKEN

    def test_a_number_is_not_a_name(self):
        assert candidate_name_terms("Where is PT-20260829-0001") == []

    def test_a_question_with_nothing_name_shaped_asks_nothing(self):
        assert candidate_name_terms("How many beds are free?") == []
        assert candidate_name_terms("") == []

    def test_swahili_courtesy_words_are_not_names(self):
        for term in candidate_name_terms("Yuko wapi mgonjwa Amina Mwita sasa?"):
            assert term not in {"mgonjwa", "wagonjwa", "sasa", "wapi", "yuko"}


class TestOnlyAPersonQuestionCausesALookup:
    """The guard that keeps this off the common path.

    Candidate name words on their own are not a signal. "how do I register a new
    patient" leaves "register" and "new" behind, and firing on that opened a
    database session for a question the content pack answers by itself - which
    broke the standing guarantee that a how-do-I question costs no query.
    """

    @pytest.mark.parametrize(
        "question",
        [
            "Where is Amina Mwita?",
            "Where is Amina Mwita now?",
            "What is the status of Joseph Kimaro?",
            "Which ward is Amina Mwita in?",
            "How much does Joseph Kimaro owe?",
            "Yuko wapi mgonjwa Amina Mwita?",
            "Amina Mwita yuko wodi gani?",
        ],
    )
    def test_a_question_about_a_person_qualifies(self, question):
        assert looks_like_a_person_question(question)
        assert candidate_name_terms(question)

    @pytest.mark.parametrize(
        "question",
        [
            "how do I register a new patient",
            "How do I process a laboratory request?",
            "How do I take a payment against a bill?",
            "Ninawezaje kusajili mgonjwa mpya?",
            "What reports can I run?",
            "How do I change my password?",
        ],
    )
    def test_a_how_do_i_question_does_not(self, question):
        assert not looks_like_a_person_question(question)

    def test_an_empty_question_does_not(self):
        assert not looks_like_a_person_question("")

    @pytest.mark.asyncio
    async def test_the_resolver_refuses_a_how_do_i_question_before_any_query(self):
        """It must not even reach the database. tenant_id is real; the gate is not."""
        from app.assistant.live.names import resolve_patient_by_name

        match = await resolve_patient_by_name(
            "tenant-that-does-not-exist", "how do I register a new patient"
        )
        assert match.outcome is NameOutcome.NOT_ASKED


class TestTheHeadingUsesTheNameThatWasTyped:
    """A figure found by name must be headed by the name, not by the number.

    The caller typed "Peter Kimaro". Heading the row "for PT-20260829-0028"
    answers a question they never asked, and the model would not accept that the
    two referred to the same person - it refused a row it had in full.
    """

    def test_it_reads_back_in_the_order_it_was_written(self):
        assert name_as_typed("Where is Peter Kimaro?") == "Peter Kimaro"

    def test_it_drops_the_question_words_around_the_name(self):
        assert name_as_typed("What is the status of Fatuma Mwita now?") == "Fatuma Mwita"

    def test_a_question_with_no_name_yields_nothing(self):
        assert name_as_typed("How many beds are free?") == ""

    def test_a_name_heading_does_not_also_explain_the_label(self):
        """Told both, the model writes the name twice over.

        With the heading "for Fatuma Mwita" and a line saying to call the
        patient PATIENT_1, the answer came back "Fatuma Mwita (PATIENT_1)",
        which rehydrates to "Fatuma Mwita (Fatuma Mwita)".
        """
        from app.assistant.live import figures as live_figures
        from app.assistant.live.aliases import ALIAS_PREFIX, AliasTable
        from app.assistant.live.contracts import MetricResult, MetricRow
        from app.assistant.live.execution import _pseudonymise

        def block_for(subject):
            row = _pseudonymise(
                {
                    "full_name": "Fatuma Mwita",
                    "patient_number": "PT-20260829-0027",
                    "ward": "Maternity",
                },
                AliasTable(),
            )
            return live_figures.render_block(
                [
                    MetricResult(
                        metric_id="patient.status",
                        label="Patient status",
                        rows=(MetricRow(values=row),),
                        subject=subject,
                    )
                ]
            )

        by_name = block_for("Fatuma Mwita")
        assert "for Fatuma Mwita" in by_name
        assert "Call this patient" not in by_name

        by_number = block_for("PT-20260829-0027")
        assert "for PT-20260829-0027" in by_number
        assert "Call this patient " + ALIAS_PREFIX + "1" in by_number

    def test_it_is_built_only_from_the_question(self):
        """It must never reach for the stored name; that stays behind the alias."""
        import inspect

        from app.assistant.live import names

        source = inspect.getsource(names.name_as_typed)
        assert "full_name" not in source
        assert "candidate_name_terms" in source


class TestATieIsNeverBroken:
    """The reason "best match wins" is not what this does.

    Two patients called Amina is ordinary. Reporting a bed number for the
    higher-scoring row would be a confident answer about the wrong person, and
    in a ward that is not a cosmetic failure.
    """

    def test_an_ambiguous_match_resolves_to_no_patient(self):
        match = NameMatch(NameOutcome.AMBIGUOUS, matches=3)
        assert match.patient_number is None
        assert not match.is_resolved

    def test_an_unknown_name_resolves_to_no_patient(self):
        match = NameMatch(NameOutcome.UNKNOWN)
        assert match.patient_number is None
        assert not match.is_resolved

    def test_a_single_match_resolves(self):
        match = NameMatch(NameOutcome.RESOLVED, patient_number=A_PATIENT_NUMBER, matches=1)
        assert match.is_resolved
        assert match.patient_number == A_PATIENT_NUMBER

    def test_a_resolved_match_with_no_number_is_not_resolved(self):
        """Belt and braces: the flag never outranks the value."""
        assert not NameMatch(NameOutcome.RESOLVED, patient_number=None).is_resolved


class TestTheAmbiguousReplyNamesNobody:
    def test_it_gives_a_count_and_asks_for_the_number(self):
        answer = _ambiguous_name_answer(3)
        assert "3 patients" in answer
        assert "patient number" in answer

    def test_it_lists_no_names(self):
        """Listing them would turn a guessed first name into a patient list."""
        answer = _ambiguous_name_answer(4)
        for name in ("Amina", "Mwita", "Joseph", "Kimaro"):
            assert name not in answer

    def test_it_never_claims_fewer_than_two(self):
        """Ambiguity means at least two; a bad count must not read as one."""
        assert "2 patients" in _ambiguous_name_answer(0)
        assert "2 patients" in _ambiguous_name_answer(1)

    def test_it_answers_in_swahili_when_the_question_was(self):
        answer = _ambiguous_name_answer(3, swahili=True)
        assert "Wagonjwa 3" in answer
        assert "namba ya mgonjwa" in answer

    def test_the_example_it_gives_is_a_real_format(self):
        """An example in a format nothing generates would send staff in circles."""
        from app.assistant.live.routing import _PATIENT_NUMBER, _VISIT_NUMBER

        answer = _ambiguous_name_answer(2)
        assert _PATIENT_NUMBER.search(answer)
        assert _VISIT_NUMBER.search(answer)


class TestOnlyThePermittedRolesCauseALookup:
    """A role that could not read the answer must not cause the query either."""

    @pytest.mark.parametrize(
        "role", [RECEPTIONIST, HOSPITAL_ADMIN, DOCTOR, WARD_NURSE]
    )
    def test_a_permitted_role_reaches_the_tier(self, role):
        assert reaches_patient_tier(frozenset({role}))

    @pytest.mark.parametrize(
        "role", [CASHIER, PHARMACIST, LAB_TECHNICIAN, TRIAGE_NURSE, RADIOGRAPHER]
    )
    def test_a_forbidden_role_does_not(self, role):
        assert not reaches_patient_tier(frozenset({role}))

    def test_a_super_admin_does_not(self):
        assert not reaches_patient_tier(
            frozenset({RECEPTIONIST}), is_super_admin=True
        )

    def test_no_roles_at_all_does_not(self):
        assert not reaches_patient_tier(frozenset())


class TestAResolvedNameRunsTheOrdinaryMetric:
    """Nothing new answers a name question; the numbered path answers it."""

    def test_a_resolved_number_reaches_the_patient_tier(self):
        routed = route(
            "Where is Amina Mwita?",
            roles=RECEPTION,
            resolved_patient_number=A_PATIENT_NUMBER,
        )
        assert any(r.definition.metric_id == "patient.status" for r in routed)

    def test_it_binds_the_resolved_number(self):
        routed = route(
            "Where is Amina Mwita?",
            roles=RECEPTION,
            resolved_patient_number=A_PATIENT_NUMBER,
        )
        patient = [r for r in routed if r.definition.tier is MetricTier.PATIENT][0]
        assert patient.params.patient_number == A_PATIENT_NUMBER

    def test_without_a_resolved_number_a_name_reaches_nothing(self):
        routed = route("Where is Amina Mwita?", roles=RECEPTION)
        assert not [r for r in routed if r.definition.tier is MetricTier.PATIENT]

    def test_a_written_number_always_beats_a_resolved_name(self):
        """The caller who typed an identifier has said exactly who they mean."""
        routed = route(
            "Where is PT-20260829-0003?",
            roles=RECEPTION,
            resolved_patient_number="PT-20260829-0001",
        )
        patient = [r for r in routed if r.definition.tier is MetricTier.PATIENT][0]
        assert patient.params.patient_number == "PT-20260829-0003"

    def test_a_resolved_name_does_not_reach_the_visit_metric(self):
        """It resolved a patient number, so the visit-keyed metric must not run."""
        routed = route(
            "Where is Amina Mwita?",
            roles=RECEPTION,
            resolved_patient_number=A_PATIENT_NUMBER,
        )
        assert "patient.status_by_visit" not in {r.definition.metric_id for r in routed}

    def test_a_forbidden_role_is_still_refused_with_a_resolved_number(self):
        """Resolution happens before routing; the role gate is still the gate."""
        routed = route(
            "Where is Amina Mwita?",
            roles=frozenset({CASHIER}),
            resolved_patient_number=A_PATIENT_NUMBER,
        )
        assert not [r for r in routed if r.definition.tier is MetricTier.PATIENT]


class TestTheResolverQueryIsSafe:
    def test_it_binds_its_terms_rather_than_building_sql(self):
        from app.assistant.live.names import _RESOLVE_SQL

        assert ":terms" in _RESOLVE_SQL
        assert ":ceiling" in _RESOLVE_SQL
        # No f-string, no concatenation of anything question-derived.
        assert "%s" not in _RESOLVE_SQL and "format(" not in _RESOLVE_SQL

    def test_it_reads_no_forbidden_column(self):
        from app.assistant.live.names import _RESOLVE_SQL
        from app.assistant.live.registry import FORBIDDEN_SQL_COLUMNS

        lowered = _RESOLVE_SQL.lower()
        for column in FORBIDDEN_SQL_COLUMNS:
            assert column not in lowered

    def test_it_excludes_inactive_patients(self):
        """A deactivated record must not be reachable by any route."""
        from app.assistant.live.names import _RESOLVE_SQL

        assert "is_active" in _RESOLVE_SQL

    def test_it_returns_no_name_to_the_caller(self):
        """full_name is matched against, never returned.

        Asserted on the output columns rather than on the text before FROM,
        because the scoring subquery legitimately reads full_name inside the
        select list. What matters is what comes back out.
        """
        import re

        from app.assistant.live.names import _RESOLVE_SQL

        returned = set(re.findall(r"\bAS\s+([a-z_]+)", _RESOLVE_SQL))
        # `w` is the unnest alias inside the subqueries, not an output column.
        assert returned - {"w"} == {"patient_number", "hits"}

    def test_it_bounds_how_many_it_will_consider(self):
        from app.assistant.live.names import MAX_CANDIDATES, _RESOLVE_SQL

        assert "LIMIT :ceiling" in _RESOLVE_SQL
        assert 0 < MAX_CANDIDATES <= 25

    def test_the_match_carries_a_count_and_not_a_list(self):
        """A NameMatch has no field that could hold somebody's name."""
        import dataclasses

        fields = {f.name for f in dataclasses.fields(NameMatch)}
        assert fields == {"outcome", "patient_number", "matches"}
