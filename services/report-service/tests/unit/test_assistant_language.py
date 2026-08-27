"""Swahili retrieval support.

The operational content pack is written in English and retrieval matches on
keywords, so without this layer a Swahili question scores zero against every
entry and the assistant answers "I do not have information about that" to a
staff member who asked a perfectly ordinary question.

The safety property that matters here is that translation only ever affects
ranking. Access filtering by tenant, role, department, approval state, and
effective date happens first and is untouched, so no Swahili term can surface an
entry the caller is not permitted to read.
"""

from datetime import date

import pytest

from app.assistant.language import (
    SWAHILI_STOPWORDS,
    SWAHILI_TO_ENGLISH,
    contains_swahili,
    expand_query,
)
from app.assistant.retrieval import RetrievalContext, retrieve, visible_entries

TODAY = date(2026, 8, 27)


def context(*roles: str, tenant="hosp-aaaa1111", department=None) -> RetrievalContext:
    return RetrievalContext(
        tenant_id=tenant,
        roles=frozenset(roles),
        department=department,
        today=TODAY,
    )


class TestQueryExpansion:
    @pytest.mark.parametrize(
        "swahili,english",
        [
            ("nataka kusajili mgonjwa", "register"),
            ("dawa", "pharmacy"),
            ("ripoti za mapato", "revenue"),
            ("vitanda vya wodi", "ward"),
            ("malipo ya bili", "payment"),
            ("maabara", "laboratory"),
            ("foleni", "queue"),
            ("wafanyakazi", "staff"),
        ],
    )
    def test_swahili_terms_gain_their_english_equivalents(self, swahili, english):
        assert english in expand_query(swahili)

    def test_the_original_wording_is_preserved(self):
        expanded = expand_query("nataka kusajili mgonjwa")
        assert "kusajili" in expanded

    def test_an_english_query_is_left_alone(self):
        query = "how do I register a new patient"
        assert expand_query(query) == query

    def test_a_code_mixed_query_gains_from_both_languages(self):
        # This is how staff actually speak: Swahili grammar around English
        # screen names.
        expanded = expand_query("nifungue vipi ukurasa wa Reception")
        assert "Reception" in expanded
        assert "page" in expanded or "screen" in expanded

    def test_a_verb_prefix_is_stripped_only_when_it_yields_a_known_term(self):
        assert "register" in expand_query("kusajili")
        # An unrelated word starting with the same letters is not mangled.
        untouched = "kusomeka"
        assert expand_query(untouched) == untouched

    @pytest.mark.parametrize("value", ["", None, "   "])
    def test_empty_input_is_safe(self, value):
        assert expand_query(value) in ("", "   ")

    def test_expansion_never_produces_duplicates(self):
        expanded = expand_query("dawa dawa madawa")
        assert expanded.split().count("pharmacy") == 1


class TestSwahiliQuestionsFindEnglishContent:
    def test_a_receptionist_asking_in_swahili_finds_the_registration_workflow(self):
        found = retrieve(context("receptionist", department="reception"),
                         query="Ninawezaje kusajili mgonjwa mpya?")
        assert found, "a Swahili registration question found no content at all"
        assert any("register" in entry.title.lower() for entry in found)

    def test_the_same_question_in_english_finds_the_same_entry(self):
        ctx = context("receptionist", department="reception")
        swahili = retrieve(ctx, query="Ninawezaje kusajili mgonjwa mpya?")
        english = retrieve(ctx, query="How do I register a new patient?")
        assert {e.entry_id for e in swahili} & {e.entry_id for e in english}

    def test_a_pharmacist_asking_about_dawa_finds_pharmacy_content(self):
        found = retrieve(context("pharmacist", department="pharmacy"),
                         query="Nitatoaje dawa kwa mgonjwa?")
        assert any("pharmacy" in entry.entry_id for entry in found)

    def test_a_cashier_asking_about_malipo_finds_billing_content(self):
        found = retrieve(context("cashier", department="billing"),
                         query="Naingizaje malipo ya bili?")
        assert any("billing" in entry.entry_id for entry in found)


class TestTranslationCannotWidenAccess:
    def test_a_swahili_term_cannot_surface_content_the_role_may_not_read(self):
        # "wafanyakazi" maps to "staff", and the staff workflow is admin-only.
        # A receptionist asking about it must still get nothing.
        found = retrieve(context("receptionist", department="reception"),
                         query="Nitaongezaje wafanyakazi wapya?")
        assert not any(entry.entry_id == "workflow.admin.add-staff" for entry in found)

    def test_the_same_term_does_reach_an_administrator(self):
        found = retrieve(context("hospital_admin", department="administration"),
                         query="Nitaongezaje wafanyakazi wapya?")
        assert any(entry.entry_id == "workflow.admin.add-staff" for entry in found)

    def test_retrieval_never_returns_more_than_the_caller_may_see(self):
        ctx = context("receptionist", department="reception")
        permitted = {entry.entry_id for entry in visible_entries(ctx)}
        for query in ("dawa", "wodi", "wafanyakazi", "ripoti", "maabara", "vitanda"):
            found = {entry.entry_id for entry in retrieve(ctx, query=query)}
            assert found <= permitted, f"query {query!r} escaped the permitted set"

    def test_a_swahili_query_from_another_tenant_still_gets_nothing_of_theirs(self):
        # Tenant filtering runs before ranking, so expansion cannot reach across.
        ctx = context("receptionist", tenant="hosp-other999", department="reception")
        for entry in retrieve(ctx, query="kusajili mgonjwa"):
            assert not entry.tenants or "hosp-other999" in entry.tenants


class TestVocabularyHealth:
    def test_every_mapped_term_is_lowercase_and_non_empty(self):
        for swahili, english in SWAHILI_TO_ENGLISH.items():
            assert swahili == swahili.lower().strip()
            assert english, f"{swahili} maps to nothing"
            for word in english:
                assert word == word.lower().strip()

    def test_no_term_is_both_a_stopword_and_a_mapped_term(self):
        # A term that is both would be expanded and then immediately discarded.
        assert not (set(SWAHILI_TO_ENGLISH) & SWAHILI_STOPWORDS)

    def test_swahili_is_detected_for_diagnostics(self):
        assert contains_swahili("Ninawezaje kusajili mgonjwa?") is True
        assert contains_swahili("How do I register a patient?") is False
        assert contains_swahili("") is False
