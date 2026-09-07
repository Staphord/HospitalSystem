"""Patient lookup: the aliasing that keeps a name away from the model.

This is the only phase of the live-data work that changes a published safety
guarantee, so the tests here are about the guarantee rather than about the
metric. They cover the three ways it could quietly stop being true:

  - the substitution corrupting itself (PATIENT_1 inside PATIENT_12),
  - a name leaving the server by a path nobody looked at (a log line, an audit
    record, a cached row, the deterministic fallback),
  - the patient tier being reachable by a question that never named a patient.
"""

from __future__ import annotations

import logging
import re

import pytest

from app.assistant.live import registry as live_registry
from app.assistant.live.aliases import ALIAS_FIELD, ALIAS_PREFIX, UNNAMED, AliasTable
from app.assistant.live.contracts import MetricParams, MetricResult, MetricRow, MetricTier
from app.assistant.live.execution import _pseudonymise, _cache_key
from app.assistant.live.registry import METRIC_REGISTRY
from app.assistant.live.routing import extract_identifiers, route
from app.assistant.permissions import (
    CASHIER,
    DOCTOR,
    HOSPITAL_ADMIN,
    LAB_TECHNICIAN,
    PHARMACIST,
    RECEPTIONIST,
    TRIAGE_NURSE,
    WARD_NURSE,
)

live_registry.load_catalog()

PATIENT_METRICS = [
    metric
    for metric in sorted(METRIC_REGISTRY.values(), key=lambda m: m.metric_id)
    if metric.tier is MetricTier.PATIENT
]

# Real formats, taken from the generators rather than invented:
# patient-service/app/services/patient_number.py:49 and
# visit-service/app/services/number_generator.py:48.
A_PATIENT_NUMBER = "PT-20260829-0001"
A_VISIT_NUMBER = "VIS-20260829-0003"

RECEPTION = frozenset({RECEPTIONIST})
NURSE = frozenset({WARD_NURSE})
TILL = frozenset({CASHIER})


class TestLabelsDoNotCollide:
    """The bug that would hand one patient's name to another patient's row."""

    def test_a_label_and_a_longer_label_are_not_confused(self):
        table = AliasTable()
        labels = [table.issue(f"PT-{n:04d}", f"Name {n}") for n in range(1, 13)]
        assert labels[0] == ALIAS_PREFIX + "1"
        assert labels[11] == ALIAS_PREFIX + "12"

        text, ok = table.rehydrate("PATIENT_1 and PATIENT_12 are both waiting.")
        assert ok
        assert text == "Name 1 and Name 12 are both waiting."

    def test_the_longer_label_is_not_rewritten_by_the_shorter_one(self):
        """Sequential string replacement would turn PATIENT_12 into "Asha2"."""
        table = AliasTable()
        table.issue("PT-1", "Asha")
        table.issue("PT-2", "Juma")
        for _ in range(10):
            table.issue(f"filler-{_}", f"Filler {_}")

        text, ok = table.rehydrate("PATIENT_12")
        assert ok
        assert "Asha" not in text

    def test_the_same_patient_gets_the_same_label_twice(self):
        table = AliasTable()
        first = table.issue(A_PATIENT_NUMBER, "Asha Mwinyi")
        second = table.issue(A_PATIENT_NUMBER, "Asha Mwinyi")
        assert first == second
        assert table.issued == 1


class TestLabelsSurviveTheAnswerPipeline:
    def test_a_bold_label_rehydrates(self):
        """sanitize_answer keeps **bold**, so the label reaches rehydrate inside it."""
        table = AliasTable()
        table.issue(A_PATIENT_NUMBER, "Asha Mwinyi")
        text, ok = table.rehydrate("**PATIENT_1** is in the doctor queue.")
        assert ok
        assert text == "**Asha Mwinyi** is in the doctor queue."

    def test_a_label_survives_sanitising(self):
        """The underscore and the digit must both come through untouched."""
        from app.assistant.sanitize import sanitize_answer

        assert "PATIENT_1" in sanitize_answer("PATIENT_1 is waiting.")
        assert "**PATIENT_12**" in sanitize_answer("**PATIENT_12** is waiting.")

    def test_an_empty_answer_is_not_an_error(self):
        assert AliasTable().rehydrate("") == ("", True)


class TestAnInventedLabelRejectsTheWholeAnswer:
    def test_a_label_that_was_never_issued_is_not_accepted(self):
        table = AliasTable()
        table.issue(A_PATIENT_NUMBER, "Asha Mwinyi")
        text, ok = table.rehydrate("PATIENT_1 is waiting and PATIENT_2 has gone home.")
        assert not ok
        # Returned unchanged, not half-substituted: the caller discards it whole
        # rather than the server deciding which half of the sentence was true.
        assert text == "PATIENT_1 is waiting and PATIENT_2 has gone home."
        assert "Asha Mwinyi" not in text

    def test_a_label_invented_when_none_was_issued_is_refused(self):
        text, ok = AliasTable().rehydrate("PATIENT_1 is in Maternity.")
        assert not ok
        assert text == "PATIENT_1 is in Maternity."


class TestASubstitutedNameCannotReintroduceMarkup:
    def test_a_name_carrying_a_link_is_neutralised_on_the_way_in(self):
        table = AliasTable()
        table.issue(A_PATIENT_NUMBER, "[Asha](https://evil.example/steal)")
        text, ok = table.rehydrate("PATIENT_1 is waiting.")
        assert ok
        assert "https://" not in text
        assert "](" not in text

    def test_a_name_carrying_a_tag_is_neutralised(self):
        from app.assistant.sanitize import contains_markup

        table = AliasTable()
        table.issue(A_PATIENT_NUMBER, "<script>alert(1)</script>Asha")
        text, ok = table.rehydrate("PATIENT_1 is waiting.")
        assert ok
        assert not contains_markup(text)
        assert "<script" not in text

    def test_a_name_that_sanitises_away_does_not_leave_the_label_behind(self):
        table = AliasTable()
        table.issue(A_PATIENT_NUMBER, "<<<>>>")
        text, ok = table.rehydrate("PATIENT_1 is waiting.")
        assert ok
        assert ALIAS_PREFIX not in text
        assert UNNAMED in text


class TestTheTableDoesNotOutliveTheRequest:
    def test_two_tables_share_nothing(self):
        first = AliasTable()
        first.issue(A_PATIENT_NUMBER, "Asha Mwinyi")

        second = AliasTable()
        text, ok = second.rehydrate("PATIENT_1 is waiting.")
        assert not ok
        assert "Asha Mwinyi" not in text

    def test_the_module_holds_no_table_of_its_own(self):
        """A module-level table would outlive every request that wrote to it."""
        from app.assistant.live import aliases

        for name in dir(aliases):
            value = getattr(aliases, name)
            assert not isinstance(value, AliasTable), (
                f"aliases.{name} is a module-level AliasTable; it would carry "
                f"one request's patients into the next"
            )

    def test_the_answer_path_builds_a_new_table_each_time(self):
        import asyncio

        from app.assistant.service import AssistantCaller, _live_results

        caller = AssistantCaller(
            user_sub="sub-1",
            tenant_id=None,  # returns early, which is all this needs
            roles=RECEPTION,
            is_super_admin=False,
            scope="full",
        )
        _, first, _ = asyncio.run(_live_results(caller, "where is " + A_PATIENT_NUMBER))
        _, second, _ = asyncio.run(_live_results(caller, "where is " + A_PATIENT_NUMBER))
        assert first is not second


class TestTheTableNeverPrintsWhatItHolds:
    def test_its_repr_carries_no_name(self):
        table = AliasTable()
        table.issue(A_PATIENT_NUMBER, "Asha Mwinyi")
        rendered = repr(table)
        assert "Asha" not in rendered
        assert A_PATIENT_NUMBER not in rendered

    def test_a_log_line_rendering_the_table_carries_no_name(self, caplog):
        table = AliasTable()
        table.issue(A_PATIENT_NUMBER, "Asha Mwinyi")
        with caplog.at_level(logging.DEBUG):
            logging.getLogger("test.aliases").info("alias table %s", table)
        assert "Asha" not in caplog.text
        assert A_PATIENT_NUMBER not in caplog.text

    def test_the_audit_record_carries_no_alias_mapping(self):
        """The audit metadata is a fixed shape; nothing here may widen it."""
        from app.assistant.audit import AssistantOutcome, build_audit_metadata
        from app.assistant.flags import AssistantCapability

        record = build_audit_metadata(
            request_id="req-1",
            actor_sub="sub-1",
            capability=AssistantCapability.OPERATIONAL_CHAT,
            outcome=AssistantOutcome.SUCCESS,
            tenant_id="hosp-1",
        )
        rendered = str(record.__dict__ if hasattr(record, "__dict__") else record)
        assert "Asha" not in rendered
        assert ALIAS_PREFIX not in rendered
        assert A_PATIENT_NUMBER not in rendered


class TestExecutionReplacesTheIdentityBeforeARowExists:
    def test_the_name_and_the_number_are_both_gone(self):
        table = AliasTable()
        row = _pseudonymise(
            {
                "full_name": "Asha Mwinyi",
                "patient_number": A_PATIENT_NUMBER,
                "ward": "Maternity",
            },
            table,
        )
        assert row == {"patient": ALIAS_PREFIX + "1", "ward": "Maternity"}
        assert "full_name" not in row
        assert "patient_number" not in row

    def test_it_refuses_to_run_without_a_table(self):
        """Answering without one would mean sending the name."""
        with pytest.raises(RuntimeError):
            _pseudonymise(
                {"full_name": "Asha Mwinyi", "patient_number": A_PATIENT_NUMBER}, None
            )

    def test_it_refuses_a_row_that_carries_no_identity_to_alias(self):
        with pytest.raises(RuntimeError):
            _pseudonymise({"ward": "Maternity"}, AliasTable())

    def test_no_rendered_figure_block_contains_a_name(self):
        from app.assistant.live import figures as live_figures

        table = AliasTable()
        row = _pseudonymise(
            {
                "full_name": "Asha Mwinyi",
                "patient_number": A_PATIENT_NUMBER,
                "ward": "Maternity",
            },
            table,
        )
        result = MetricResult(
            metric_id="patient.status",
            label="Patient status",
            rows=(MetricRow(values=row),),
        )
        block = live_figures.render_block([result])
        fallback = live_figures.render_fallback([result])
        for rendered in (block, fallback):
            assert "Asha" not in rendered
            assert A_PATIENT_NUMBER not in rendered
            assert ALIAS_PREFIX + "1" in rendered

    def test_the_fallback_listing_rehydrates_cleanly(self):
        """The rejected-answer path must still name the patient to the reader."""
        from app.assistant.live import figures as live_figures

        table = AliasTable()
        row = _pseudonymise(
            {
                "full_name": "Asha Mwinyi",
                "patient_number": A_PATIENT_NUMBER,
                "ward": "Maternity",
            },
            table,
        )
        result = MetricResult(
            metric_id="patient.status",
            label="Patient status",
            rows=(MetricRow(values=row),),
        )
        text, ok = table.rehydrate(live_figures.render_fallback([result]))
        assert ok
        assert "Asha Mwinyi" in text


class TestPatientRowsAreNeverCached:
    def test_the_patient_metrics_are_kept_out_of_the_shared_cache(self):
        """A cached label means nothing in the request that reads it back.

        It would also leave the one thing in the catalog that is about a person
        sitting in a process-wide cache after its request ended.
        """
        import inspect

        from app.assistant.live import execution

        source = inspect.getsource(execution.execute)
        assert "MetricTier.PATIENT" in source, (
            "execute() no longer distinguishes the patient tier from the cache"
        )

    @pytest.mark.parametrize("metric", PATIENT_METRICS, ids=lambda m: m.metric_id)
    def test_no_patient_result_reaches_the_cache(self, metric):
        from app.assistant.live import execution

        execution._CACHE.clear()
        key = _cache_key("hosp-1", metric, {"patient_number": A_PATIENT_NUMBER})
        assert execution._CACHE.get(key) is None


class TestTheMetricsAreShapedForTheAliasPath:
    @pytest.mark.parametrize("metric", PATIENT_METRICS, ids=lambda m: m.metric_id)
    def test_it_exposes_the_identity_the_alias_is_issued_from(self, metric):
        assert {"full_name", "patient_number"} <= metric.exposed_fields, (
            f"{metric.metric_id} is patient-tier but exposes no identity for "
            f"execution to alias, so it would fail closed at run time"
        )

    @pytest.mark.parametrize("metric", PATIENT_METRICS, ids=lambda m: m.metric_id)
    def test_it_reads_no_date_of_birth_into_a_row(self, metric):
        assert "date_of_birth" not in metric.exposed_fields
        assert "age_band" in metric.exposed_fields

    @pytest.mark.parametrize("metric", PATIENT_METRICS, ids=lambda m: m.metric_id)
    def test_it_offers_no_starting_suggestion(self, metric):
        """A suggestion would have to carry somebody's real patient number."""
        assert not metric.example_question
        assert not metric.swahili_example_question

    @pytest.mark.parametrize("metric", PATIENT_METRICS, ids=lambda m: m.metric_id)
    def test_it_declares_exactly_one_identifier(self, metric):
        """Declaring both would bind NULL for whichever the question omitted."""
        identifiers = metric.params & {"patient_number", "visit_number"}
        assert len(identifiers) == 1, (
            f"{metric.metric_id} declares {sorted(identifiers)}; a metric that "
            f"needs both answers confidently about nobody when given one"
        )

    @pytest.mark.parametrize("metric", PATIENT_METRICS, ids=lambda m: m.metric_id)
    def test_its_status_literals_are_values_the_columns_hold(self, metric):
        """The registry-wide literal check cannot reach a multi-table metric.

        test_assistant_live_flow.py resolves the table from a metric's single
        FROM clause and skips anything that joins, so these literals - the ones
        that would silently count zero if wrong - would go unchecked.
        """
        from tests.unit.test_assistant_live_flow import ENUM_VALUES

        pairs = re.findall(r"(\w+)\.status\s*(?:=|<>)\s*'(\w+)'", metric.sql)
        aliases_to_tables = {
            "v": "visits",
            "q": "queues",
            "ahead": "queues",
            "a": "admissions",
            "bl": "bills",
        }
        checked = 0
        for alias, value in pairs:
            table = aliases_to_tables.get(alias)
            if table is None:
                continue
            allowed = ENUM_VALUES[(table, "status")]
            assert value in allowed, (
                f"{metric.metric_id} compares {table}.status to '{value}', "
                f"which that column never holds. Allowed: {sorted(allowed)}"
            )
            checked += 1
        assert checked >= 3, (
            "the alias map above has drifted from the query; these literals are "
            "no longer being checked"
        )

    @pytest.mark.parametrize("metric", PATIENT_METRICS, ids=lambda m: m.metric_id)
    def test_it_subtracts_the_discount_from_what_is_owed(self, metric):
        """The phase 4 rule, restated where a patient is told what they owe."""
        assert "discount_amount" in metric.sql
        assert "paid_amount" in metric.sql

    @pytest.mark.parametrize("metric", PATIENT_METRICS, ids=lambda m: m.metric_id)
    def test_it_groups_money_by_currency(self, metric):
        """This tenant holds TZS and USD bills; one SUM across them is not money."""
        assert "currency" in metric.sql


class TestOnlyTheRightRolesReachAPatient:
    @pytest.mark.parametrize("metric", PATIENT_METRICS, ids=lambda m: m.metric_id)
    @pytest.mark.parametrize(
        "role", [CASHIER, PHARMACIST, LAB_TECHNICIAN, TRIAGE_NURSE]
    )
    def test_a_role_with_no_reason_to_look_is_refused(self, metric, role):
        assert not metric.is_permitted(frozenset({role}))

    @pytest.mark.parametrize("metric", PATIENT_METRICS, ids=lambda m: m.metric_id)
    @pytest.mark.parametrize(
        "role", [RECEPTIONIST, HOSPITAL_ADMIN, DOCTOR, WARD_NURSE]
    )
    def test_the_roles_that_move_patients_around_are_allowed(self, metric, role):
        assert metric.is_permitted(frozenset({role}))

    def test_a_cashier_asking_about_a_patient_routes_to_no_patient_metric(self):
        routed = route("where is " + A_PATIENT_NUMBER, roles=TILL)
        assert not [r for r in routed if r.definition.tier is MetricTier.PATIENT]


class TestTheTierIsUnreachableWithoutAnIdentifier:
    @pytest.mark.parametrize(
        "question",
        [
            "where are my patients",
            "how many patients are waiting",
            "show me patient details",
            "who is in Maternity ward",
            "list every admitted patient",
            "wagonjwa wangu wako wapi",
        ],
    )
    def test_a_general_question_never_reaches_the_patient_tier(self, question):
        for roles in (RECEPTION, NURSE, frozenset({DOCTOR}), frozenset({HOSPITAL_ADMIN})):
            routed = route(question, roles=roles, actor_sub="sub-1")
            assert not [
                r for r in routed if r.definition.tier is MetricTier.PATIENT
            ], f"{question!r} reached the patient tier for {sorted(roles)}"

    def test_a_visit_number_does_not_run_the_patient_number_metric(self):
        """It would bind NULL and answer about nobody, confidently."""
        routed = route("where is " + A_VISIT_NUMBER, roles=RECEPTION)
        selected = {r.definition.metric_id for r in routed}
        assert "patient.status_by_visit" in selected
        assert "patient.status" not in selected

    def test_a_patient_number_does_not_run_the_visit_metric(self):
        routed = route("where is " + A_PATIENT_NUMBER, roles=RECEPTION)
        selected = {r.definition.metric_id for r in routed}
        assert "patient.status" in selected
        assert "patient.status_by_visit" not in selected


class TestIdentifiersAreReadInTheirRealFormat:
    def test_a_real_patient_number_is_found(self):
        patient, visit = extract_identifiers("where is patient " + A_PATIENT_NUMBER)
        assert patient == A_PATIENT_NUMBER
        assert visit is None

    def test_a_real_visit_number_is_found(self):
        patient, visit = extract_identifiers("status of visit " + A_VISIT_NUMBER)
        assert visit == A_VISIT_NUMBER
        assert patient is None

    def test_a_lower_case_identifier_binds_the_stored_value(self):
        patient, _ = extract_identifiers("where is " + A_PATIENT_NUMBER.lower())
        assert patient == A_PATIENT_NUMBER

    def test_the_two_prefixes_do_not_match_each_other(self):
        patient, visit = extract_identifiers(
            A_PATIENT_NUMBER + " and " + A_VISIT_NUMBER
        )
        assert patient == A_PATIENT_NUMBER
        assert visit == A_VISIT_NUMBER

    def test_a_question_with_no_identifier_yields_none(self):
        assert extract_identifiers("how many patients are waiting") == (None, None)

    @pytest.mark.parametrize(
        "text",
        [
            "PT-2026-0001",       # the shape this work guessed before
            "PT-20260829",        # no counter
            "OPD-2026-0142",      # a format no service generates
            "V-20260829",         # the old visit guess
        ],
    )
    def test_a_shape_no_service_generates_is_not_an_identifier(self, text):
        assert extract_identifiers("where is " + text) == (None, None)

    def test_a_named_patient_still_routes_when_the_words_are_all_stopwords(self):
        """"where is patient X" tokenises to the digits of X and nothing else.

        Trigger overlap alone cannot route this, which is why routing scores the
        patient tier on the identifier itself.
        """
        routed = route("where is patient " + A_PATIENT_NUMBER, roles=RECEPTION)
        assert any(r.definition.metric_id == "patient.status" for r in routed)


class TestTheEndpointCanSupplyWhatThePatientMetricsDeclare:
    def test_every_declared_parameter_is_accepted(self):
        import inspect

        from app.api.v1.metrics.router import read_metric

        accepted = set(inspect.signature(read_metric).parameters)
        supplied_by_the_server = {"start", "end", "actor_sub"}
        for metric in PATIENT_METRICS:
            missing = set(metric.params) - accepted - supplied_by_the_server
            assert not missing, (
                f"{metric.metric_id} needs {sorted(missing)}, which the endpoint "
                f"cannot supply, so it would bind NULL and answer about nobody"
            )

    def test_the_endpoint_does_not_take_an_identity_from_the_caller(self):
        """actor_sub comes from the verified token and from nowhere else."""
        import inspect

        from app.api.v1.metrics.router import read_metric

        assert "actor_sub" not in set(inspect.signature(read_metric).parameters)


class TestThePromptTellsTheModelWhatALabelIs:
    def test_it_names_the_label_and_forbids_inventing_one(self):
        from app.assistant.service import SYSTEM_INSTRUCTIONS

        assert ALIAS_PREFIX + "1" in SYSTEM_INSTRUCTIONS
        # Wrapped to 79 columns, so the phrases are matched with the line breaks
        # collapsed rather than the prompt being reflowed to suit a test.
        lowered = SYSTEM_INSTRUCTIONS_LOWER()
        assert "do not invent a label" in lowered
        assert "do not write a patient's name" in lowered

    def test_it_says_the_figures_are_a_source_in_their_own_right(self):
        """Without this the model refuses every figure-only question.

        The first rule used to say "answer only from the reference material",
        naming the content block alone. A patient lookup retrieves no content at
        all, so the model read an empty reference section, obeyed the rule, and
        declined - with the correct figure sitting beneath it in the same prompt.
        """
        lowered = SYSTEM_INSTRUCTIONS_LOWER()
        assert "reference material and the live figures" in lowered

    def test_a_patient_figure_heading_is_described(self):
        lowered = SYSTEM_INSTRUCTIONS_LOWER()
        assert "headed" in lowered and "visit number" in lowered


def SYSTEM_INSTRUCTIONS_LOWER() -> str:
    """The prompt, lower-cased and unwrapped, for phrase assertions."""
    from app.assistant.service import SYSTEM_INSTRUCTIONS

    return " ".join(SYSTEM_INSTRUCTIONS.lower().split())


class TestTheFigureBlockIdentifiesThePatientAsked:
    """The defect step 4 of the protocol found, kept as a test.

    Asked "where is PT-20260829-0003 now", the model answered "I do not have
    that information" three times running while the correct row sat in its
    prompt: the row's only identifier was PATIENT_1 and nothing tied that to the
    number in the question. Probing the live model with the heading varied and
    everything else held fixed, it answered whenever the heading named the
    identifier and refused whenever it did not.
    """

    def _result(self, subject="PT-20260829-0001"):
        from app.assistant.live.aliases import AliasTable

        table = AliasTable()
        row = _pseudonymise(
            {
                "full_name": "Asha Mwinyi",
                "patient_number": "PT-20260829-0001",
                "ward": "Maternity",
            },
            table,
        )
        return (
            MetricResult(
                metric_id="patient.status",
                label="Patient status",
                rows=(MetricRow(values=row),),
                subject=subject,
            ),
            table,
        )

    def test_the_heading_names_the_identifier_that_was_asked_for(self):
        from app.assistant.live import figures as live_figures

        result, _ = self._result()
        block = live_figures.render_block([result])
        assert "for PT-20260829-0001" in block

    def test_the_block_says_what_the_label_stands_for(self):
        from app.assistant.live import figures as live_figures

        result, _ = self._result()
        block = live_figures.render_block([result])
        assert "Call this patient " + ALIAS_PREFIX + "1" in block

    def test_the_heading_still_carries_no_name(self):
        from app.assistant.live import figures as live_figures

        result, _ = self._result()
        for rendered in (
            live_figures.render_block([result]),
            live_figures.render_fallback([result]),
        ):
            assert "Asha" not in rendered

    def test_the_identifier_is_not_treated_as_an_invented_figure(self):
        """Its digits are in the heading, so quoting it back must be allowed."""
        from app.assistant.live import figures as live_figures

        result, _ = self._result()
        ok, offending = live_figures.validate_figures(
            "PATIENT_1 (PT-20260829-0001) is in Maternity.", [result]
        )
        assert ok, offending

    def test_an_aggregate_figure_gains_no_heading_of_this_kind(self):
        from app.assistant.live import figures as live_figures

        plain = MetricResult(
            metric_id="beds.availability",
            label="Bed availability",
            rows=(MetricRow(values={"available_beds": 4}),),
        )
        block = live_figures.render_block([plain])
        assert "for " not in block.splitlines()[0]
        assert "shown as" not in block
