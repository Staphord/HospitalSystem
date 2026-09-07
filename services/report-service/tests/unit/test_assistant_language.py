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
    _lookup,
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


class TestTheReplyLanguageIsDecidedByTheServer:
    """An English question answered in Swahili reads as a broken assistant.

    SYSTEM_INSTRUCTIONS asks the model to reply in the language it was asked in,
    and it mostly does. "Mostly" showed up in QA as "How do I take a payment
    against a bill?" coming back as "Fungua Billing, chagua Bills...". The server
    already knows which language the question was in, so it says so in the prompt
    rather than hoping. These tests pin that the instruction is built, is
    unambiguous, and is presentation only.
    """

    @pytest.mark.parametrize(
        "question,expected",
        [
            ("How do I take a payment against a bill?", "English"),
            ("How much have we collected today?", "English"),
            ("Which medicines are out of stock?", "English"),
            ("Malipo ya leo ni kiasi gani?", "Swahili"),
            ("Ninawezaje kusajili mgonjwa mpya?", "Swahili"),
            ("Foleni ya daktari ina watu wangapi?", "Swahili"),
        ],
    )
    def test_it_resolves_the_language_of_a_real_question(self, question, expected):
        resolved = "Swahili" if contains_swahili(question) else "English"
        assert resolved == expected

    def test_the_detector_never_gates_anything(self):
        """It decides wording, never access.

        Being wrong about the language can only produce an answer in the wrong
        language. It must not appear in any access decision, or a question phrased
        in Swahili could reach content a question phrased in English could not.
        """
        import inspect

        from app.assistant import retrieval, service

        for module in (retrieval, service):
            source = inspect.getsource(module)
            for line in source.splitlines():
                if "contains_swahili" not in line or line.strip().startswith("#"):
                    continue
                # The capability answer also asks which language to compose in,
                # which is the same presentation decision made one layer up.
                allowed = ("reply_language", "swahili=", "import")
                assert any(token in line for token in allowed), (
                    "contains_swahili is used outside the reply-language decision "
                    "in " + module.__name__ + ": " + line.strip()
                )

    @pytest.mark.parametrize(
        "question",
        [
            "Ni kiasi gani bado hakijalipwa?",
            "Vipimo vingapi bado havijakamilika?",
            "Foleni ina watu wangapi sasa?",
            "Je, kuna dawa zilizoisha?",
        ],
    )
    def test_a_swahili_question_carrying_no_mapped_term_is_still_swahili(
        self, question
    ):
        """A question can be entirely Swahili and translate to nothing.

        "Ni kiasi gani bado hakijalipwa" - how much is still unpaid - has a verb
        conjugated past anything the vocabulary map recognises, so before the
        function-word list it read as English and was answered in English.
        """
        assert contains_swahili(question) is True

    @pytest.mark.parametrize(
        "question",
        [
            "How much have we collected today?",
            "How do I take a payment against a bill?",
            "Which medicines are out of stock?",
            "What is the average lab turnaround time today?",
            "Are there critical lab results awaiting verification?",
            "How many patients are waiting in each queue?",
            "Where is Leo's record?",
        ],
    )
    def test_an_english_question_is_never_mistaken_for_swahili(self, question):
        """Including one with a name that is also a Swahili word."""
        assert contains_swahili(question) is False

    def test_the_function_words_do_not_disturb_retrieval_or_routing(self):
        """They must not leak into the shared stopword set.

        retrieval merges SWAHILI_STOPWORDS into its own stopwords, and routing
        tokenises against the same function, so a word added there stops being a
        term at all - which would silently disable the billing trigger on "kiasi"
        and the laboratory trigger on "bado".
        """
        from app.assistant.language import _SWAHILI_FUNCTION_WORDS
        from app.assistant.retrieval import _tokenize

        assert not (_SWAHILI_FUNCTION_WORDS & SWAHILI_STOPWORDS)
        for term in ("kiasi", "bado"):
            assert term in _tokenize(term), (
                term + " was swallowed as a stopword and can no longer route"
            )


class TestTheQuestionsANurseActuallyTyped:
    """Reported from the ward, not invented here.

    A triage nurse asked these in Swahili and got a list of things the assistant
    could do instead of an answer - including, absurdly, for changing a password,
    which the very list it printed said it could help with.

    Two causes, both silent:

      - "kubadilisha" and "nywila" were simply not in the vocabulary map, so not
        one word of "Ninawezaje kubadilisha nywila yangu?" reached the content
        pack.
      - Swahili puts the object inside the verb. "kusajili" is to register;
        "kumsajili" is to register *them*. That single infixed letter defeated
        the prefix stripper, so "kumpima" missed "kupima" - which was in the map
        the whole time.
    """

    @pytest.mark.parametrize(
        "question,expected",
        [
            ("Ninawezaje kubadilisha nywila yangu?", "password"),
            ("Ninawezaje kumpima mgonjwa?", "triage"),
            ("Nifanyeje kumsajili mgonjwa mpya?", "register"),
            ("Nitajuaje kama mgonjwa ana dharura?", "emergency"),
            ("Naweza kuona vipimo vya nyuma vya mgonjwa wapi?", "laboratory"),
        ],
    )
    def test_it_now_reaches_the_english_the_content_pack_uses(self, question, expected):
        assert expected in expand_query(question).lower(), (
            f"{question!r} still expands to nothing the content pack can match"
        )

    @pytest.mark.parametrize(
        "infixed,plain",
        [
            ("kumsajili", "kusajili"),
            ("kumpima", "kupima"),
            ("kuwapima", "kupima"),
            ("kumuandikisha", "kuandikisha"),
        ],
    )
    def test_an_object_infix_resolves_to_the_same_terms_as_the_plain_verb(
        self, infixed, plain
    ):
        assert _lookup(infixed) == _lookup(plain) != ()

    def test_stripping_never_invents_a_match(self):
        """Only a result that is itself a known term is accepted, so an ordinary
        word cannot be mangled into a translation."""
        for word in ("kubernetes", "kumquat", "kuwait", "customer", "nurse"):
            assert _lookup(word) == (), f"{word} was mangled into a Swahili match"
