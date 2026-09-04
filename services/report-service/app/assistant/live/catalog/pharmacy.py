from __future__ import annotations

from app.assistant.live.contracts import MetricTier
from app.assistant.live.registry import MetricDefinition, register
from app.assistant.permissions import DOCTOR, HOSPITAL_ADMIN, PHARMACIST

# Drug stock levels.
#
# drug_inventory holds product data, not patient data: a drug name here is the
# name of a product on a shelf, and nothing on this page joins to a prescription,
# a dispensing record, or a patient. That is what makes stock answerable without
# any of the aliasing the patient tier will need.
#
# Two details are taken from pharmacy-service rather than invented, because both
# would fail silently if guessed:
#
#   - Low stock is `quantity_in_stock <= reorder_level`, inclusive. That is the
#     definition in services/pharmacy-service/app/services/inventory.py (is_low_stock,
#     and the same comparison in the list filter). Using `<` would quietly omit
#     every drug sitting exactly on its reorder level, which is precisely the set
#     a pharmacist is asking about.
#   - Every inventory read filters is_active, as pharmacy-service does. A
#     withdrawn product is not a shortage.

_STOCK_ROLES = frozenset({PHARMACIST, HOSPITAL_ADMIN})

# A doctor may ask whether a drug is available, because that decides whether a
# prescription can be filled today. Reorder levels and shortage lists are
# procurement work and stay with the pharmacy and the administrator.
_AVAILABILITY_ROLES = frozenset({PHARMACIST, HOSPITAL_ADMIN, DOCTOR})

_STOCK_TRIGGERS = frozenset(
    {
        "stock", "stocks", "inventory", "supply", "supplies", "drug", "drugs",
        "medicine", "medicines", "medication", "medications", "reorder",
        "reordering", "low", "short", "shortage", "running", "out", "empty",
        "restock", "restocked", "pharmacy", "dispensary", "quantity",
        # Swahili: dawa -> pharmacy/prescription is already in the shared map.
        "dawa", "madawa", "hisa", "ghala",
    }
)


STOCK_BELOW_REORDER = MetricDefinition(
    metric_id="stock.below_reorder",
    label="Drugs at or below reorder level",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_STOCK_ROLES,
    triggers=_STOCK_TRIGGERS,
    sql="""
        SELECT drug_name AS drug_name,
               unit AS unit,
               quantity_in_stock AS quantity_in_stock,
               reorder_level AS reorder_level
        FROM drug_inventory
        WHERE is_active
          AND quantity_in_stock <= reorder_level
        ORDER BY quantity_in_stock, drug_name
        LIMIT 25
    """,
    params=frozenset(),
    exposed_fields=frozenset(
        {"drug_name", "unit", "quantity_in_stock", "reorder_level"}
    ),
    numeric_fields=frozenset({"quantity_in_stock", "reorder_level"}),
    max_rows=25,
    example_question="Which drugs are at or below their reorder level?",
    swahili_example_question="Dawa zipi zimefika kiwango cha kuagiza tena?",
)


STOCK_OUT_OF_STOCK = MetricDefinition(
    metric_id="stock.out_of_stock",
    label="Drugs out of stock",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_STOCK_ROLES,
    triggers=frozenset(
        {
            "out", "empty", "none", "zero", "finished", "unavailable",
            "stock", "drug", "drugs", "medicine", "medicines", "dawa",
            "imeisha", "hakuna",
        }
    ),
    sql="""
        SELECT drug_name AS drug_name,
               unit AS unit,
               reorder_level AS reorder_level
        FROM drug_inventory
        WHERE is_active
          AND quantity_in_stock = 0
        ORDER BY drug_name
        LIMIT 25
    """,
    params=frozenset(),
    exposed_fields=frozenset({"drug_name", "unit", "reorder_level"}),
    numeric_fields=frozenset({"reorder_level"}),
    max_rows=25,
    example_question="Which medicines are out of stock?",
    swahili_example_question="Dawa zipi zimeisha?",
)


STOCK_SUMMARY = MetricDefinition(
    metric_id="stock.summary",
    label="Stock position overall",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_STOCK_ROLES,
    triggers=frozenset(
        {
            "stock", "inventory", "overall", "summary", "position", "how",
            "many", "count", "drugs", "medicines", "supplies", "dawa",
        }
    ),
    sql="""
        SELECT COUNT(*) AS drugs_tracked,
               COUNT(*) FILTER (WHERE quantity_in_stock <= reorder_level)
                   AS drugs_at_or_below_reorder,
               COUNT(*) FILTER (WHERE quantity_in_stock = 0) AS drugs_out_of_stock
        FROM drug_inventory
        WHERE is_active
    """,
    params=frozenset(),
    exposed_fields=frozenset(
        {"drugs_tracked", "drugs_at_or_below_reorder", "drugs_out_of_stock"}
    ),
    numeric_fields=frozenset(
        {"drugs_tracked", "drugs_at_or_below_reorder", "drugs_out_of_stock"}
    ),
    max_rows=1,
    example_question="What is our overall stock position?",
    swahili_example_question="Muhtasari wa hisa za dawa ukoje kwa ujumla?",
)


STOCK_FOR_DRUG = MetricDefinition(
    metric_id="stock.for_drug",
    label="Stock of one drug",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_AVAILABILITY_ROLES,
    triggers=frozenset(
        {
            "stock", "have", "any", "available", "left", "remaining", "supply",
            "drug", "medicine", "medication", "quantity", "dawa", "tuna",
        }
    ),
    # The bound name is not free text from the question: routing resolves it
    # against the drug names that already exist in this tenant, so the value here
    # is always one the inventory actually holds. That is why an exact match is
    # right and a LIKE would only add ways to match the wrong product.
    sql="""
        SELECT drug_name AS drug_name,
               unit AS unit,
               quantity_in_stock AS quantity_in_stock,
               reorder_level AS reorder_level,
               last_restocked_at AS last_restocked_at
        FROM drug_inventory
        WHERE is_active
          AND drug_name = :drug_name
        ORDER BY drug_name
        LIMIT 5
    """,
    params=frozenset({"drug_name"}),
    exposed_fields=frozenset(
        {
            "drug_name",
            "unit",
            "quantity_in_stock",
            "reorder_level",
            "last_restocked_at",
        }
    ),
    numeric_fields=frozenset({"quantity_in_stock", "reorder_level"}),
    max_rows=5,
)


register(
    STOCK_BELOW_REORDER,
    STOCK_OUT_OF_STOCK,
    STOCK_SUMMARY,
    STOCK_FOR_DRUG,
)
