from __future__ import annotations

from app.assistant.live.contracts import MetricTier
from app.assistant.live.registry import MetricDefinition, register
from app.assistant.permissions import (
    DOCTOR,
    HOSPITAL_ADMIN,
    RECEPTIONIST,
    TRIAGE_NURSE,
    WARD_NURSE,
)

# Bed and ward figures.
#
# The bed and admission SQL below follows the shape already proven in
# admin-service/app/services/reports.py (bed_occupancy, operational_activity),
# against the same shared tenant schema. It is restated here rather than
# imported because that module is a synchronous Session API in another service,
# and because a metric's SQL must be fixed text declared beside its column
# allowlist for the registry test to check the two against each other.
#
# Every column selected is a count, an average, a bed state, or a ward name.
# None of it refers to an individual, so nothing on this page needs aliasing.

# Who may ask about beds. Bed pressure drives admission and discharge decisions,
# so it is deliberately wide: the people who move patients need it, not only
# administrators. It carries no patient data at all.
_BED_ROLES = frozenset({HOSPITAL_ADMIN, WARD_NURSE, DOCTOR, TRIAGE_NURSE, RECEPTIONIST})


BEDS_AVAILABILITY = MetricDefinition(
    metric_id="beds.availability",
    label="Bed availability by ward",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_BED_ROLES,
    triggers=frozenset(
        {
            "bed", "beds", "bedspace", "cot", "cots",
            "free", "empty", "vacant", "available", "availability",
            "occupied", "occupancy", "capacity", "space", "spaces",
            "ward", "wards",
            # Swahili surfaces through language.expand_query, but the direct
            # forms are kept so a term the map does not cover still routes.
            "kitanda", "vitanda", "wodi", "wazi", "nafasi",
        }
    ),
    # Anchored for the same reason admissions.current is: a hospital with no beds
    # recorded yet must answer zero rather than returning no rows, which would be
    # dropped from the prompt and reported as "I do not have that information".
    sql="""
        SELECT COALESCE(b.ward_name, 'none recorded') AS ward_name,
               COUNT(b.bed_id) FILTER (WHERE b.is_active) AS total_beds,
               COUNT(b.bed_id) FILTER (WHERE b.is_active AND b.is_available)
                   AS available_beds,
               COUNT(b.bed_id) FILTER (WHERE b.is_active AND NOT b.is_available)
                   AS occupied_beds
        FROM (SELECT 1) AS every_ward
        LEFT JOIN beds b ON TRUE
        GROUP BY b.ward_name
        ORDER BY 1
        LIMIT 40
    """,
    params=frozenset(),
    exposed_fields=frozenset({"ward_name", "total_beds", "available_beds", "occupied_beds"}),
    numeric_fields=frozenset({"total_beds", "available_beds", "occupied_beds"}),
    max_rows=40,
    example_question="How many beds are free in each ward?",
    swahili_example_question="Kuna vitanda vingapi wazi kila wodi?",
)


BEDS_AVAILABILITY_FOR_WARD = MetricDefinition(
    metric_id="beds.availability_for_ward",
    label="Bed availability for one ward",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_BED_ROLES,
    triggers=frozenset(
        {
            "bed", "beds", "free", "empty", "vacant", "available",
            "occupied", "ward", "kitanda", "vitanda", "wodi", "wazi",
        }
    ),
    # The ward name is bound, never concatenated. routing.py resolves it against
    # the ward names that actually exist in this tenant before it is bound, so a
    # value invented in a question cannot reach the query at all.
    sql="""
        SELECT ward_name AS ward_name,
               COUNT(*) FILTER (WHERE is_active) AS total_beds,
               COUNT(*) FILTER (WHERE is_active AND is_available) AS available_beds,
               COUNT(*) FILTER (WHERE is_active AND NOT is_available) AS occupied_beds
        FROM beds
        WHERE ward_name = :ward_name
        GROUP BY ward_name
        LIMIT 1
    """,
    params=frozenset({"ward_name"}),
    exposed_fields=frozenset({"ward_name", "total_beds", "available_beds", "occupied_beds"}),
    numeric_fields=frozenset({"total_beds", "available_beds", "occupied_beds"}),
    max_rows=1,
)


ADMISSIONS_CURRENT = MetricDefinition(
    metric_id="admissions.current",
    label="Patients currently admitted",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_BED_ROLES,
    triggers=frozenset(
        {
            "admitted", "admission", "admissions", "inpatient", "inpatients",
            "currently", "current", "now", "many", "count",
            "amelazwa", "wamelazwa", "wagonjwa",
        }
    ),
    # An admission in progress carries status 'active', not 'admitted'. The
    # values are owned by ward-service: ADMISSION_ACTIVE and ADMISSION_DISCHARGED
    # in services/ward-service/app/services/ward.py. Guessing 'admitted' here
    # would have returned zero from a full ward, which is worse than an error
    # because it reads like a real answer.
    #
    # The one-row anchor on the left of the join makes an empty hospital answer
    # "nobody admitted" rather than nothing at all. A plain GROUP BY returns no
    # rows when every bed is free, no rows means the figure is dropped from the
    # prompt, and a nurse asking how many patients are admitted is then told the
    # assistant does not have that information - when the true and useful answer
    # is none.
    sql="""
        SELECT COALESCE(a.ward_name, 'none admitted') AS ward_name,
               COUNT(a.admission_id) AS admitted_patients
        FROM (SELECT 1) AS every_ward
        LEFT JOIN admissions a ON a.status = 'active'
        GROUP BY COALESCE(a.ward_name, 'none admitted')
        ORDER BY 1
        LIMIT 40
    """,
    params=frozenset(),
    exposed_fields=frozenset({"ward_name", "admitted_patients"}),
    numeric_fields=frozenset({"admitted_patients"}),
    max_rows=40,
    example_question="How many patients are admitted right now?",
    swahili_example_question="Kuna wagonjwa wangapi waliolazwa sasa?",
)


ADMISSIONS_IN_WINDOW = MetricDefinition(
    metric_id="admissions.in_window",
    label="Admissions and discharges",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_BED_ROLES,
    triggers=frozenset(
        {
            "admitted", "admission", "admissions", "discharge", "discharged",
            "discharges", "today", "week", "month", "yesterday",
            "kulazwa", "kuruhusiwa", "leo",
        }
    ),
    sql="""
        SELECT COUNT(*) FILTER (
                   WHERE admission_date::date >= :start
                     AND admission_date::date <= :end
               ) AS admitted_in_period,
               COUNT(*) FILTER (
                   WHERE status = 'discharged'
                     AND discharge_date IS NOT NULL
                     AND discharge_date::date >= :start
                     AND discharge_date::date <= :end
               ) AS discharged_in_period
        FROM admissions
    """,
    params=frozenset({"start", "end"}),
    exposed_fields=frozenset({"admitted_in_period", "discharged_in_period"}),
    numeric_fields=frozenset({"admitted_in_period", "discharged_in_period"}),
    max_rows=1,
    example_question="How many admissions and discharges were there today?",
    swahili_example_question="Leo kuna wagonjwa wangapi waliolazwa na kuruhusiwa?",
)


ADMISSIONS_LENGTH_OF_STAY = MetricDefinition(
    metric_id="admissions.average_length_of_stay",
    label="Average length of stay",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_BED_ROLES,
    triggers=frozenset(
        {
            "length", "stay", "los", "average", "mean", "long",
            "duration", "days", "muda", "wastani", "siku",
        }
    ),
    # ROUND to one decimal place in the database rather than in Python, so the
    # figure the model is shown is exactly the figure recorded as supplied. A
    # value rounded after the fact would not match the answer validator.
    sql="""
        SELECT ROUND(
                   AVG(EXTRACT(EPOCH FROM (discharge_date - admission_date)) / 86400.0)::numeric,
                   1
               ) AS average_length_of_stay_days,
               COUNT(*) AS discharges_counted
        FROM admissions
        WHERE status = 'discharged'
          AND discharge_date IS NOT NULL
          AND discharge_date::date >= :start
          AND discharge_date::date <= :end
    """,
    params=frozenset({"start", "end"}),
    exposed_fields=frozenset({"average_length_of_stay_days", "discharges_counted"}),
    numeric_fields=frozenset({"average_length_of_stay_days", "discharges_counted"}),
    max_rows=1,
    example_question="What is the average length of stay this month?",
    swahili_example_question="Wastani wa siku za kulazwa mwezi huu ni ngapi?",
)


register(
    BEDS_AVAILABILITY,
    BEDS_AVAILABILITY_FOR_WARD,
    ADMISSIONS_CURRENT,
    ADMISSIONS_IN_WINDOW,
    ADMISSIONS_LENGTH_OF_STAY,
)
