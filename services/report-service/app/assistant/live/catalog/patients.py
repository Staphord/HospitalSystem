from __future__ import annotations

from app.assistant.live.contracts import MetricTier
from app.assistant.live.registry import MetricDefinition, register
from app.assistant.permissions import DOCTOR, HOSPITAL_ADMIN, RECEPTIONIST, WARD_NURSE

# Where one patient is, right now.
#
# This is the only metric in the catalog whose rows are about a person, and the
# only one that changes a published guarantee, so the constraints are worth
# stating rather than leaving to the tests:
#
#   1. It runs only when the question carries an explicit patient or visit
#      number. routing.extract_identifiers reads them, and route() skips every
#      PATIENT-tier metric when neither is present, so "where are my patients"
#      cannot reach one patient's row. There is no search here and no name
#      matching: a question has to already name the person it is about.
#
#   2. The model is never shown the name. full_name and patient_number are
#      selected, but execution.py replaces both with a per-request label from
#      aliases.py before any prompt text exists, and the server puts the real
#      name back after the answer has been sanitised and its figures checked.
#
#   3. Nothing clinical is read. No diagnosis, no complaint, no triage note, no
#      result, no reason for admission. registry.FORBIDDEN_SQL_COLUMNS names
#      them all and the registry test fails the build over any of them. What is
#      left is where the patient is and what they owe, which is what reception,
#      a ward nurse and the treating doctor ask about.
#
#   4. The date of birth is read but never exposed. It is bucketed into a band
#      inside the query; the column is absent from exposed_fields, so
#      MetricDefinition.project drops it before a row is built. See
#      registry.DERIVED_ONLY_COLUMNS.
#
# Values were read from the schema and from the writing services, not guessed:
#
#   visits.status        registered, triaged, in_consultation, in_lab,
#                        in_pharmacy, admitted, discharged, completed, cancelled
#   queues.status        waiting, in_progress, completed, skipped
#   admissions.status    active, discharged   (ward-service writes 'active',
#                        never 'admitted' - the first defect this work found)
#   bills.status         open, paid, partial  ("partial", never
#                        "partially_paid"; billing-service/app/api/v1/router.py)
#
# Money is grouped by currency, as every phase 4 billing metric is: this tenant
# holds both TZS and USD bills, and a single SUM across them is not money in any
# currency.

# Reception moves patients between queues, a ward nurse needs to know who is in
# which bed, and the treating doctor needs both. A cashier reads balances
# through the billing metrics without needing a person's location, and a
# pharmacist and a lab technician need neither, so none of the three is here.
_LOOKUP_ROLES = frozenset({RECEPTIONIST, HOSPITAL_ADMIN, DOCTOR, WARD_NURSE})

# Also read by live/names.py, which filters these out of a question before
# treating what is left as a patient's name - so an operational word can never
# resolve to a patient who happens to share it.
#
# Bare "patient", "patients" and "where" are stopwords in retrieval._tokenize,
# so they are not available to route on and are not listed here. What actually
# routes this metric is the identifier: routing scores a PATIENT-tier metric on
# the presence of a resolved patient or visit number, because a question naming
# a patient number is a question about that patient whatever else it says.
# These triggers only add to that score.
LOOKUP_TRIGGERS = frozenset(
    {
        "status", "located", "location", "now", "currently", "seen", "waiting",
        "queue", "admitted", "admission", "ward", "bed", "balance",
        "outstanding", "owes", "owe", "visit", "progress", "update",
        # Swahili: hali -> status/condition, yuko/wapi -> is/where,
        # kitanda -> bed, wodi -> ward, deni -> debt, foleni -> queue.
        "hali", "yuko", "wapi", "kitanda", "wodi", "deni", "foleni", "sasa",
    }
)

# The query, written once. Two metrics run it: one keyed by patient number, one
# by visit number, because the caller may name either and a single metric
# declaring both would bind NULL for whichever was missing and then answer
# confidently about nobody. That is the dropped-parameter defect this work has
# already met once, in a shape that would be far worse here.
#
# The two selectors are fixed module text substituted at import time. No value
# from a question, from retrieved content, or from a model response is ever
# concatenated into SQL; both identifiers are bound.
_BY_PATIENT_NUMBER = "p.patient_number = :patient_number"
_BY_VISIT_NUMBER = """EXISTS (
                  SELECT 1 FROM visits vk
                  WHERE vk.patient_id = p.id AND vk.visit_number = :visit_number
              )"""

_PATIENT_STATUS_SQL = """
        WITH target AS (
            SELECT p.id AS patient_id,
                   p.full_name AS full_name,
                   p.patient_number AS patient_number,
                   p.date_of_birth AS born_on
            FROM patients p
            WHERE p.is_active
              AND {selector}
            LIMIT 1
        )
        SELECT t.full_name,
               t.patient_number,
               CASE
                   WHEN t.born_on IS NULL THEN 'not recorded'
                   WHEN t.born_on > CURRENT_DATE - INTERVAL '1 year' THEN 'under 1'
                   WHEN t.born_on > CURRENT_DATE - INTERVAL '5 years' THEN '1 to 4'
                   WHEN t.born_on > CURRENT_DATE - INTERVAL '15 years' THEN '5 to 14'
                   WHEN t.born_on > CURRENT_DATE - INTERVAL '25 years' THEN '15 to 24'
                   WHEN t.born_on > CURRENT_DATE - INTERVAL '45 years' THEN '25 to 44'
                   WHEN t.born_on > CURRENT_DATE - INTERVAL '65 years' THEN '45 to 64'
                   ELSE '65 and over'
               END AS age_band,
               COALESCE(lv.visit_status, 'no visit recorded') AS visit_status,
               COALESCE(cq.queue_type, 'not in a queue') AS current_queue,
               cq.queue_position,
               COALESCE(adm.ward_name, 'not admitted') AS ward,
               COALESCE(adm.bed_number, 'not admitted') AS bed,
               COALESCE(bal.outstanding, 'nothing outstanding') AS outstanding_balance,
               COALESCE(bal.unpaid_bills, 0) AS unpaid_bills
        FROM target t
        LEFT JOIN LATERAL (
            SELECT v.status AS visit_status
            FROM visits v
            WHERE v.patient_id = t.patient_id
            ORDER BY v.visit_date DESC, v.created_at DESC
            LIMIT 1
        ) lv ON TRUE
        LEFT JOIN LATERAL (
            SELECT q.queue_type AS queue_type,
                   CASE
                       WHEN q.status = 'waiting' THEN (
                           SELECT COUNT(*) + 1
                           FROM queues ahead
                           WHERE ahead.queue_type = q.queue_type
                             AND ahead.status = 'waiting'
                             AND ahead.created_at < q.created_at
                       )
                   END AS queue_position
            FROM queues q
            JOIN visits qv ON qv.visit_id = q.visit_id
            WHERE qv.patient_id = t.patient_id
              AND q.status IN ('waiting', 'in_progress')
            ORDER BY q.created_at DESC
            LIMIT 1
        ) cq ON TRUE
        LEFT JOIN LATERAL (
            SELECT COALESCE(a.ward_name, b.ward_name) AS ward_name,
                   b.bed_number AS bed_number
            FROM admissions a
            LEFT JOIN beds b ON b.bed_id = a.bed_id
            WHERE a.patient_id = t.patient_id
              AND a.status = 'active'
            ORDER BY a.admission_date DESC
            LIMIT 1
        ) adm ON TRUE
        LEFT JOIN LATERAL (
            SELECT STRING_AGG(
                       per_currency.currency || ' ' || per_currency.owed,
                       ', ' ORDER BY per_currency.currency
                   ) AS outstanding,
                   SUM(per_currency.bills) AS unpaid_bills
            FROM (
                SELECT bl.currency AS currency,
                       COUNT(*) AS bills,
                       ROUND(
                           SUM(
                               bl.total_amount
                               - bl.discount_amount
                               - bl.paid_amount
                           ),
                           2
                       ) AS owed
                FROM bills bl
                WHERE bl.patient_id = t.patient_id
                  AND bl.status <> 'paid'
                GROUP BY bl.currency
            ) per_currency
        ) bal ON TRUE
"""

# Both metrics read and expose exactly the same shape. full_name and
# patient_number are here because execution.py needs a stable key and a display
# name to issue a label from; neither survives into a row, because both are
# replaced by aliases.ALIAS_FIELD before the projection leaves execution.
_EXPOSED = frozenset(
    {
        "full_name",
        "patient_number",
        "age_band",
        "visit_status",
        "current_queue",
        "queue_position",
        "ward",
        "bed",
        "outstanding_balance",
        "unpaid_bills",
    }
)

# queue_position and unpaid_bills are the only cells that are figures in their
# own right. The outstanding balance is a currency-qualified string
# ("TZS 45000.00, USD 12.00") rather than a number, because a total that does
# not say which currency it is in is not an answer. figures.supplied_figures
# scans every cell, so the amounts inside it are still figures the model may
# quote, and a figure it invents is still refused.
_NUMERIC = frozenset({"queue_position", "unpaid_bills"})

# The label a staff member sees in Sources, and the heading
# figures.render_block writes above the row.
#
# It stays a plain noun phrase. What tells the model which patient the row is
# about is MetricResult.subject, which render_block appends to this heading as
# "for PT-20260829-0003" - the identifier out of the staff member's own
# question. Putting it in the label instead would bake a per-request value into
# a module constant.
_LABEL = "Patient status"


PATIENT_STATUS = MetricDefinition(
    metric_id="patient.status",
    label=_LABEL,
    tier=MetricTier.PATIENT,
    allowed_roles=_LOOKUP_ROLES,
    triggers=LOOKUP_TRIGGERS,
    sql=_PATIENT_STATUS_SQL.format(selector=_BY_PATIENT_NUMBER),
    params=frozenset({"patient_number"}),
    exposed_fields=_EXPOSED,
    numeric_fields=_NUMERIC,
    max_rows=1,
    # Deliberately empty, for the reason the per-ward and per-drug metrics leave
    # it empty and more so: a starting suggestion would have to carry a real
    # patient number, and putting one person's number in front of every user of
    # the panel is precisely what this phase exists to prevent.
    example_question="",
    swahili_example_question="",
)


PATIENT_STATUS_BY_VISIT = MetricDefinition(
    metric_id="patient.status_by_visit",
    label=_LABEL,
    tier=MetricTier.PATIENT,
    allowed_roles=_LOOKUP_ROLES,
    triggers=LOOKUP_TRIGGERS,
    sql=_PATIENT_STATUS_SQL.format(selector=_BY_VISIT_NUMBER),
    params=frozenset({"visit_number"}),
    exposed_fields=_EXPOSED,
    numeric_fields=_NUMERIC,
    max_rows=1,
    example_question="",
    swahili_example_question="",
)


register(PATIENT_STATUS, PATIENT_STATUS_BY_VISIT)
