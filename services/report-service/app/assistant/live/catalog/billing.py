from __future__ import annotations

from app.assistant.live.contracts import MetricTier
from app.assistant.live.registry import MetricDefinition, register
from app.assistant.permissions import CASHIER, HOSPITAL_ADMIN

# Money taken and money owed.
#
# Four decisions here are load-bearing, and three of them would fail silently:
#
#   1. Outstanding balance is total_amount - discount_amount - paid_amount, all
#      three terms. billing-service computes exactly that
#      (app/api/v1/router.py:208, and again at :255-260 when a payment lands).
#      The naive total_amount - paid_amount ignores waivers and discounts and
#      therefore *overstates what a patient owes*. Of every figure in this
#      catalog that is the one it is least acceptable to get wrong, so a test
#      asserts the three-term form appears in the SQL.
#
#   2. bills.status is "open", "paid" or "partial". Not "partially_paid".
#      It is a plain varchar with no enum behind it, so a wrong literal would
#      count zero rather than raise. Bills are created "open"
#      (app/services/billing.py:74,171) and the status is changed *in the
#      router* on payment and on adjustment, not in the service layer, which is
#      why grepping only app/services/ finds "open" and nothing else.
#
#   3. Bills carry a currency and this tenant holds more than one. Summing
#      across currencies would produce a number that is not money in any
#      currency, so every total below is grouped by currency and labelled with
#      it. Nothing adds TZS to USD.
#
#   4. payments is queried directly rather than through admin-service's
#      revenue_summary. That report's department breakdown is fabricated from
#      hardcoded percentages (admin-service/app/services/reports.py:207-224);
#      its payment_method totals are real, and the query below has deliberately
#      the same shape as those so the two can be compared.
#
# Money is cashier and administrator work. Not a pharmacist, not a doctor, not a
# nurse: what a patient owes is not a clinical fact and does not travel with
# clinical seniority.
_BILLING_ROLES = frozenset({CASHIER, HOSPITAL_ADMIN})

# bill_items.description names the procedure a line was raised for, which is
# clinical. No metric here reads bill_items at all.

_MONEY_TRIGGERS = frozenset(
    {
        "billing", "bill", "bills", "billed", "invoice", "invoices",
        "payment", "payments", "paid", "pay", "cash", "money", "amount",
        "cashier", "receipt", "receipts", "revenue", "takings", "income",
        # Swahili: malipo/lipa/kulipa -> payment, bili/ankara -> bill and
        # mapato -> revenue are already in the shared map in language.py. The
        # direct forms are kept so a term the map does not cover still routes.
        #
        # "kiasi" is here because the map cannot help with it: "ni kiasi gani
        # bado hakijalipwa" - how much is still unpaid - carries no word the
        # map translates, since the verb is conjugated past recognition, and it
        # matched nothing at all until "kiasi" was a trigger in its own right.
        "malipo", "bili", "ankara", "mapato", "pesa", "fedha", "risiti", "kiasi",
    }
)


BILLING_COLLECTED = MetricDefinition(
    metric_id="billing.collected",
    label="Payments collected",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_BILLING_ROLES,
    triggers=_MONEY_TRIGGERS
    | frozenset(
        {
            "collected", "collect", "collection", "collections", "received",
            "much", "total", "jumla",
        }
    ),
    # Deliberately no bare date words. "today", "leo", "week" and the rest carry
    # no topic - resolve_window already reads them - so triggering on them would
    # let any question mentioning today pull a money figure into the prompt of
    # someone who happens to be permitted one. The money words above are what
    # make a question a question about money.
    #
    # The one-row anchor on the left of the join is what makes an empty till
    # answer "nothing collected" instead of nothing at all. A plain GROUP BY
    # returns no rows before the first payment of the day, and no rows means the
    # figure is dropped from the prompt entirely, so a cashier asking at eight in
    # the morning would be told the assistant does not have that information.
    # With the anchor there is always exactly one row per group and at least one
    # group, so zero is reported as zero.
    #
    # total_collected repeats the whole-window total on every row on purpose.
    # Without it a model shown "cash 90000, insurance 60000" answers "150000
    # altogether", validate_figures refuses a number the server never supplied,
    # and a correct answer is replaced by the fallback listing. Supplying the
    # total leaves nothing to add up.
    sql="""
        SELECT COALESCE(p.payment_method, 'none recorded') AS payment_method,
               COALESCE(SUM(p.amount_paid), 0) AS amount_collected,
               COUNT(p.payment_id) AS payments_recorded,
               COALESCE(SUM(SUM(p.amount_paid)) OVER (), 0) AS total_collected,
               SUM(COUNT(p.payment_id)) OVER () AS total_payments_recorded
        FROM (SELECT 1) AS every_window
        LEFT JOIN payments p
               ON p.created_at::date >= :start
              AND p.created_at::date <= :end
        GROUP BY p.payment_method
        ORDER BY 1
        LIMIT 12
    """,
    params=frozenset({"start", "end"}),
    exposed_fields=frozenset(
        {
            "payment_method",
            "amount_collected",
            "payments_recorded",
            "total_collected",
            "total_payments_recorded",
        }
    ),
    numeric_fields=frozenset(
        {
            "amount_collected",
            "payments_recorded",
            "total_collected",
            "total_payments_recorded",
        }
    ),
    max_rows=12,
    example_question="How much have we collected in payments today?",
    swahili_example_question="Malipo tuliyokusanya leo ni kiasi gani?",
)


BILLING_UNPAID = MetricDefinition(
    metric_id="billing.unpaid",
    label="Bills still outstanding",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_BILLING_ROLES,
    triggers=_MONEY_TRIGGERS
    | frozenset(
        {
            "unpaid", "outstanding", "owed", "owing", "owe", "balance",
            "balances", "debt", "debts", "arrears", "due", "settled",
            "unsettled", "deni", "madeni", "salio",
        }
    ),
    # Deliberately not date-filtered, for the same reason queue.waiting_by_type
    # is not: a debt is a live state, not an event. A bill raised last month and
    # still unpaid is exactly the one being asked about, and a "today" window
    # would report the hospital as owed nothing.
    #
    # `status <> 'paid'` rather than a list of unpaid statuses: it cannot
    # undercount what is owed if billing-service ever adds a fourth status,
    # and undercounting a debt is the worse direction to be wrong in.
    #
    # The anchor join gives the same useful zero as billing.collected: a
    # hospital with every bill settled answers "nothing outstanding".
    sql="""
        SELECT COALESCE(b.currency, 'none outstanding') AS currency,
               COUNT(b.bill_id) AS unpaid_bills,
               COUNT(b.bill_id) FILTER (WHERE b.status = 'open')
                   AS bills_not_yet_paid,
               COUNT(b.bill_id) FILTER (WHERE b.status = 'partial')
                   AS bills_part_paid,
               COALESCE(
                   SUM(b.total_amount - b.discount_amount - b.paid_amount), 0
               ) AS outstanding_total
        FROM (SELECT 1) AS every_currency
        LEFT JOIN bills b ON b.status <> 'paid'
        GROUP BY b.currency
        ORDER BY 1
        LIMIT 8
    """,
    params=frozenset(),
    exposed_fields=frozenset(
        {
            "currency",
            "unpaid_bills",
            "bills_not_yet_paid",
            "bills_part_paid",
            "outstanding_total",
        }
    ),
    numeric_fields=frozenset(
        {
            "unpaid_bills",
            "bills_not_yet_paid",
            "bills_part_paid",
            "outstanding_total",
        }
    ),
    max_rows=8,
    example_question="How much is still outstanding on unpaid bills?",
    swahili_example_question="Ni kiasi gani bado hakijalipwa kwenye bili?",
)


BILLING_BILL_STATUS_SUMMARY = MetricDefinition(
    metric_id="billing.bill_status_summary",
    label="Bills raised, by status",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_BILLING_ROLES,
    triggers=_MONEY_TRIGGERS
    | frozenset(
        {
            "status", "summary", "position", "open", "partial", "settled",
            "raised", "many", "count", "muhtasari", "hali", "jumla",
        }
    ),
    # One row always, so an empty day is a reported zero rather than silence.
    # Every status is counted explicitly rather than left for the model to
    # subtract; a figure it worked out itself is refused by validate_figures.
    sql="""
        SELECT COUNT(*) AS bills_raised,
               COUNT(*) FILTER (WHERE status = 'open') AS bills_open,
               COUNT(*) FILTER (WHERE status = 'partial') AS bills_part_paid,
               COUNT(*) FILTER (WHERE status = 'paid') AS bills_paid
        FROM bills
        WHERE created_at::date >= :start
          AND created_at::date <= :end
    """,
    params=frozenset({"start", "end"}),
    exposed_fields=frozenset(
        {"bills_raised", "bills_open", "bills_part_paid", "bills_paid"}
    ),
    numeric_fields=frozenset(
        {"bills_raised", "bills_open", "bills_part_paid", "bills_paid"}
    ),
    max_rows=1,
    example_question="How many bills were raised today, and by status?",
    swahili_example_question="Bili ngapi zimetolewa leo, na ziko hali gani?",
)


register(
    BILLING_COLLECTED,
    BILLING_UNPAID,
    BILLING_BILL_STATUS_SUMMARY,
)
