"""Drug stock metrics.

Two things here would fail silently if got wrong, so both are pinned by tests:

  - "Low stock" is inclusive. pharmacy-service defines it as
    `quantity_in_stock <= reorder_level` (is_low_stock in
    services/pharmacy-service/app/services/inventory.py). A strict `<` would
    omit every drug sitting exactly on its reorder level - the precise set a
    pharmacist is asking about - and would look like a shorter, healthier list
    rather than an error.
  - Every read filters is_active, as pharmacy-service does. A withdrawn product
    showing up as a shortage would send someone ordering something deliberately
    discontinued.
"""

from __future__ import annotations

import pytest

from app.assistant.live import registry as live_registry
from app.assistant.live.contracts import MetricTier
from app.assistant.live.registry import METRIC_REGISTRY
from app.assistant.live.routing import route
from app.assistant.permissions import (
    CASHIER,
    DOCTOR,
    HOSPITAL_ADMIN,
    PHARMACIST,
    TRIAGE_NURSE,
)

live_registry.load_catalog()

PHARM = frozenset({PHARMACIST})
STOCK_METRICS = [m for m in METRIC_REGISTRY.values() if m.metric_id.startswith("stock.")]


class TestTheStockDefinitionMatchesPharmacyService:
    def test_low_stock_is_inclusive(self):
        """A drug sitting exactly on its reorder level is low stock."""
        sql = METRIC_REGISTRY["stock.below_reorder"].sql
        assert "quantity_in_stock <= reorder_level" in sql
        assert "quantity_in_stock < reorder_level" not in sql

    def test_the_summary_uses_the_same_inclusive_test(self):
        """Two definitions of low stock in one system is a defect waiting to happen."""
        sql = METRIC_REGISTRY["stock.summary"].sql
        assert "quantity_in_stock <= reorder_level" in sql

    @pytest.mark.parametrize("metric", STOCK_METRICS, ids=lambda m: m.metric_id)
    def test_every_stock_metric_ignores_withdrawn_products(self, metric):
        assert "is_active" in metric.sql, (
            f"{metric.metric_id} would count withdrawn products as real stock"
        )

    def test_out_of_stock_means_exactly_zero(self):
        assert "quantity_in_stock = 0" in METRIC_REGISTRY["stock.out_of_stock"].sql


class TestStockCarriesNoPatientData:
    @pytest.mark.parametrize("metric", STOCK_METRICS, ids=lambda m: m.metric_id)
    def test_it_never_joins_to_a_patient_or_a_prescription(self, metric):
        """A drug name is product data only while it is not tied to a person."""
        sql = metric.sql.lower()
        for table in ("prescription", "dispensing", "patient", "visit", "consultation"):
            assert table not in sql, (
                f"{metric.metric_id} touches {table}, which turns a product name "
                f"into information about a person"
            )

    @pytest.mark.parametrize("metric", STOCK_METRICS, ids=lambda m: m.metric_id)
    def test_it_reads_only_the_inventory_table(self, metric):
        assert "drug_inventory" in metric.sql

    def test_no_stock_metric_is_patient_tier(self):
        for metric in STOCK_METRICS:
            assert metric.tier is MetricTier.AGGREGATE


class TestStockPermissions:
    def test_a_pharmacist_reaches_every_stock_metric(self):
        for metric in STOCK_METRICS:
            assert metric.is_permitted(PHARM), f"{metric.metric_id} refused a pharmacist"

    def test_a_hospital_admin_reaches_every_stock_metric(self):
        for metric in STOCK_METRICS:
            assert metric.is_permitted(frozenset({HOSPITAL_ADMIN}))

    def test_a_doctor_may_ask_whether_a_drug_is_available(self):
        """Whether a drug is in stock decides whether a prescription can be filled."""
        assert METRIC_REGISTRY["stock.for_drug"].is_permitted(frozenset({DOCTOR}))

    def test_a_doctor_does_not_get_the_procurement_lists(self):
        """Reorder levels and shortage lists are pharmacy and administration work."""
        for metric_id in ("stock.below_reorder", "stock.out_of_stock", "stock.summary"):
            assert not METRIC_REGISTRY[metric_id].is_permitted(frozenset({DOCTOR}))

    @pytest.mark.parametrize("role", [CASHIER, TRIAGE_NURSE])
    def test_unrelated_roles_reach_no_stock_metric(self, role):
        for metric in STOCK_METRICS:
            assert not metric.is_permitted(frozenset({role})), (
                f"{metric.metric_id} was reachable by {role}"
            )

    def test_a_super_admin_reaches_no_stock_metric(self):
        for metric in STOCK_METRICS:
            assert not metric.is_permitted(PHARM, is_super_admin=True)


class TestStockRouting:
    def test_a_reorder_question_routes_to_stock(self):
        routed = route("which drugs are below reorder level", roles=PHARM)
        assert any(r.definition.metric_id.startswith("stock.") for r in routed)

    def test_an_out_of_stock_question_routes_to_stock(self):
        routed = route("what medicines are we out of", roles=PHARM)
        assert any(r.definition.metric_id.startswith("stock.") for r in routed)

    def test_a_named_drug_binds_only_when_the_hospital_holds_it(self):
        routed = route(
            "do we have any Amoxicillin 500mg left",
            roles=PHARM,
            known_drugs=["Amoxicillin 500mg", "Paracetamol 500mg"],
        )
        named = [r for r in routed if "drug_name" in r.definition.params]
        assert named, "a real drug name did not reach the per-drug metric"
        assert named[0].params.drug_name == "Amoxicillin 500mg"

    def test_an_unknown_drug_never_binds(self):
        """A drug this hospital does not stock must not reach the query at all."""
        routed = route(
            "do we have any Unobtainium left",
            roles=PHARM,
            known_drugs=["Amoxicillin 500mg"],
        )
        assert all("drug_name" not in r.definition.params for r in routed)

    def test_a_nurse_asking_about_stock_gets_nothing(self):
        assert route("which drugs are low on stock", roles=frozenset({TRIAGE_NURSE})) == []

    def test_a_swahili_medicine_question_routes(self):
        """dawa -> pharmacy/prescription comes from the shared vocabulary map."""
        assert route("dawa gani zimeisha", roles=PHARM)


class TestAQuestionThatOnlyMatchesAFilteredMetric:
    """The cheap first routing pass must not beg its own question.

    _live_results routes twice: once to decide whether reading the tenant's ward
    and drug names is worth a query, then again with those names. A metric that
    filters on a drug cannot match before the names are loaded, so if the first
    pass applied that skip, "do we have any amoxicillin left" - which matches no
    other metric - would look like a question about nothing and the names would
    never be read. It answered from the help pack instead of the shelf.
    """

    def test_it_matches_nothing_without_the_names_and_without_the_flag(self):
        assert route("do we have any Amoxicillin 500mg left", roles=PHARM) == []

    def test_the_first_pass_still_sees_it(self):
        routed = route(
            "do we have any Amoxicillin 500mg left",
            roles=PHARM,
            assume_named_values=True,
        )
        assert any(r.definition.metric_id == "stock.for_drug" for r in routed), (
            "the provisional pass missed a question only a filtered metric answers, "
            "so the drug names would never be loaded"
        )

    def test_the_second_pass_binds_the_drug_once_the_names_are_known(self):
        routed = route(
            "do we have any Amoxicillin 500mg left",
            roles=PHARM,
            known_drugs=["Amoxicillin 500mg"],
        )
        named = [r for r in routed if r.definition.metric_id == "stock.for_drug"]
        assert named and named[0].params.drug_name == "Amoxicillin 500mg"

    def test_the_flag_does_not_make_an_unrelated_question_match(self):
        """It relaxes the parameter check only, never the trigger scoring."""
        assert route(
            "how do I register a new patient", roles=PHARM, assume_named_values=True
        ) == []
