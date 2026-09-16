"""The no-arithmetic guard.

Once real numbers are in play, the dangerous failure is not a wrong fact but a
computed one: a model given a total and an available count will volunteer an
occupancy percentage nobody asked for, and it will sound exactly as
authoritative as the figures it was actually given. The prompt tells it not to.
These tests cover the part that does not rely on the model complying.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.assistant.live.contracts import MetricResult, MetricRow
from app.assistant.live.execution import format_value
from app.assistant.live.figures import (
    render_block,
    render_fallback,
    supplied_figures,
    validate_figures,
)

READ_AT = datetime(2026, 8, 31, 14, 32, tzinfo=timezone.utc)


def result(**values) -> MetricResult:
    """One metric result whose figures are exactly the values given."""
    return MetricResult(
        metric_id="beds.availability",
        label="Bed availability by ward",
        rows=(MetricRow(values=dict(values)),),
        read_at=READ_AT,
        figures=frozenset(format_value(v) for v in values.values()),
    )


BEDS = result(ward_name="Maternity", total_beds=20, available_beds=6)


class TestASuppliedFigureIsAccepted:
    def test_a_figure_stated_as_given(self):
        ok, offending = validate_figures("There are 6 free beds in Maternity.", [BEDS])
        assert ok, offending

    def test_several_supplied_figures(self):
        ok, _ = validate_figures("Maternity has 20 beds, 6 of them free.", [BEDS])
        assert ok

    def test_a_supplied_figure_written_with_a_thousands_separator(self):
        """1,240 and 1240 are the same figure; formatting is not an invention."""
        big = result(total_beds=1240)
        ok, _ = validate_figures("There are 1,240 beds.", [big])
        assert ok

    def test_a_supplied_decimal_written_without_its_trailing_zero(self):
        stay = result(average_length_of_stay_days=Decimal("4.0"))
        ok, _ = validate_figures("The average stay is 4 days.", [stay])
        assert ok

    def test_a_number_from_the_question_is_allowed(self):
        ok, _ = validate_figures(
            "You asked about the last 7 days; there are 6 free beds.",
            [BEDS],
            question="how many beds were free over the last 7 days",
        )
        assert ok

    def test_a_number_from_the_content_pack_is_allowed(self):
        ok, _ = validate_figures(
            "Follow the 4 steps on the Ward screen. 6 beds are free.",
            [BEDS],
            content_block="Detail: there are 4 steps to admit a patient.",
        )
        assert ok

    def test_the_reading_time_may_be_repeated(self):
        ok, offending = validate_figures(
            "As at 2026-08-31 14:32 there were 6 free beds.", [BEDS]
        )
        assert ok, offending

    def test_a_numbered_list_marker_is_formatting_not_a_figure(self):
        """sanitize_answer keeps numbered lists, so steps must not read as figures."""
        answer = "Check the ward board:\n1. Open Ward.\n2. Read the count: 6 free."
        ok, offending = validate_figures(answer, [BEDS])
        assert ok, offending


class TestAComputedFigureIsRejected:
    def test_an_occupancy_percentage_is_refused(self):
        """20 total and 6 free must not become "70% occupied"."""
        ok, offending = validate_figures("Maternity is 70% occupied.", [BEDS])
        assert not ok
        assert offending == "70"

    def test_a_subtraction_is_refused(self):
        """20 minus 6 is 14, and 14 was never supplied."""
        ok, offending = validate_figures("That leaves 14 beds occupied.", [BEDS])
        assert not ok
        assert offending == "14"

    def test_a_total_across_wards_is_refused(self):
        two_wards = MetricResult(
            metric_id="beds.availability",
            label="Bed availability by ward",
            rows=(
                MetricRow(values={"ward_name": "Maternity", "available_beds": 6}),
                MetricRow(values={"ward_name": "Surgical", "available_beds": 11}),
            ),
            read_at=READ_AT,
            figures=frozenset({"6", "11"}),
        )
        ok, offending = validate_figures("There are 17 free beds in total.", [two_wards])
        assert not ok
        assert offending == "17"

    def test_an_invented_figure_is_refused(self):
        ok, offending = validate_figures("There are 42 free beds.", [BEDS])
        assert not ok
        assert offending == "42"


class TestTheGuardIsSafeToCallUnconditionally:
    def test_no_results_means_nothing_to_contradict(self):
        """With no figures supplied this guard is not the right one to fire."""
        ok, _ = validate_figures("Open Reception, then Register patient.", [])
        assert ok

    def test_an_empty_answer_passes(self):
        ok, _ = validate_figures("", [BEDS])
        assert ok

    def test_an_answer_with_no_numbers_passes(self):
        ok, _ = validate_figures("Ask the ward manager.", [BEDS])
        assert ok


class TestSuppliedFigures:
    def test_it_collects_every_supplied_number(self):
        assert supplied_figures([BEDS]) >= {"20", "6"}

    def test_a_failed_result_supplies_nothing(self):
        failed = MetricResult(metric_id="x", label="X", failed=True)
        assert supplied_figures([failed]) == set()


class TestRendering:
    def test_the_prompt_block_names_the_figure_and_its_reading_time(self):
        block = render_block([BEDS])
        assert "Bed availability by ward" in block
        assert "2026-08-31 14:32 UTC" in block
        assert "6" in block

    def test_a_failed_result_is_not_rendered(self):
        failed = MetricResult(metric_id="x", label="Should not appear", failed=True)
        assert "Should not appear" not in render_block([failed])

    def test_an_empty_result_is_not_rendered(self):
        empty = MetricResult(metric_id="x", label="Should not appear", read_at=READ_AT)
        assert "Should not appear" not in render_block([empty])

    def test_the_fallback_states_that_nothing_was_calculated(self):
        text = render_fallback([BEDS])
        assert "6" in text
        assert "Nothing has been calculated" in text

    def test_every_number_in_the_fallback_is_one_that_was_supplied(self):
        """The fallback replaces a rejected answer, so it must itself pass the guard."""
        text = render_fallback([BEDS])
        ok, offending = validate_figures(text, [BEDS])
        assert ok, f"the fallback introduced an unsupplied number: {offending}"

    def test_the_fallback_is_empty_when_there_is_nothing_to_report(self):
        assert render_fallback([]) == ""


class TestValueFormatting:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, "not recorded"),
            (True, "yes"),
            (False, "no"),
            (12, "12"),
            (12.0, "12"),
            (12.5, "12.5"),
            (Decimal("10"), "10"),
            (Decimal("4.5"), "4.5"),
        ],
    )
    def test_values_render_as_a_reader_would_expect(self, value, expected):
        assert format_value(value) == expected

    def test_an_integral_decimal_never_renders_in_exponent_form(self):
        """Decimal.normalize turns 10 into 1E+1, which would read as nonsense."""
        assert "E" not in format_value(Decimal("10"))


class TestRealModelOutputIsNotRejectedByAccident:
    """False rejections matter as much as false acceptances.

    Every rejection replaces a well-written answer with the plain fallback, so a
    guard that fires on correct output makes the whole feature look broken.
    """

    def test_a_typographic_hyphen_in_a_date_does_not_look_like_a_figure(self):
        """Groq returned exactly this answer, and 2026 was read as invented.

        The figures are the triage queue's own, so 1 is genuinely supplied and
        the only number in question is the year inside the reading time.
        """
        triage = result(queue_type="triage", waiting_now=1, being_seen_now=0)
        answer = (
            "There is **1 patient** waiting for triage "
            "(recorded at 2026\u201008\u201031 17:26 UTC)."
        )
        ok, offending = validate_figures(answer, [triage])
        assert ok, f"rejected a correct answer on {offending!r}"

    @pytest.mark.parametrize("dash", ["-", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014"])
    def test_every_dash_a_model_might_type_is_handled(self, dash):
        answer = f"Read at 2026{dash}08{dash}31 14:32 UTC. There are 6 free beds."
        ok, offending = validate_figures(answer, [BEDS])
        assert ok, f"rejected on {offending!r} with dash {dash!r}"

    def test_a_computed_figure_is_still_refused_alongside_a_date(self):
        """Folding dashes must not weaken the guard it exists to serve."""
        answer = "As at 2026\u201008\u201031 the ward is 70% occupied."
        ok, offending = validate_figures(answer, [BEDS])
        assert not ok
        assert offending == "70"


class TestDigitsInsideNamesAreNotInventedFigures:
    """A name is not a claim about a quantity.

    Drug names carry their dose: "Oxytocin 10IU", "Amoxicillin 500mg". Counting
    only the numeric columns rejected an answer that merely named the drug it was
    asked about, so nearly every stock answer fell back to the plain listing.
    """

    def _stock(self):
        return MetricResult(
            metric_id="stock.below_reorder",
            label="Drugs at or below reorder level",
            rows=(
                MetricRow(
                    values={
                        "drug_name": "Oxytocin 10IU",
                        "unit": "ampoules",
                        "quantity_in_stock": 0,
                        "reorder_level": 20,
                    }
                ),
            ),
            read_at=READ_AT,
            figures=frozenset({"0", "20"}),
        )

    def test_naming_a_drug_with_a_dose_is_allowed(self):
        ok, offending = validate_figures(
            "Oxytocin 10IU is out of stock, against a reorder level of 20.",
            [self._stock()],
        )
        assert ok, f"rejected on {offending!r} - that came from the drug name"

    def test_a_dose_in_milligrams_is_allowed(self):
        stock = MetricResult(
            metric_id="stock.for_drug",
            label="Stock of one drug",
            rows=(MetricRow(values={"drug_name": "Amoxicillin 500mg", "quantity_in_stock": 240}),),
            read_at=READ_AT,
            figures=frozenset({"240"}),
        )
        ok, offending = validate_figures(
            "There are 240 capsules of Amoxicillin 500mg in stock.", [stock]
        )
        assert ok, f"rejected on {offending!r}"

    def test_an_invented_quantity_is_still_refused(self):
        """Widening for names must not weaken the guard it exists to serve."""
        ok, offending = validate_figures(
            "Oxytocin 10IU is out of stock; order 500 more.", [self._stock()]
        )
        assert not ok
        assert offending == "500"
