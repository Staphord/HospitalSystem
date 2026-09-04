from __future__ import annotations

from app.assistant.live.contracts import MetricTier
from app.assistant.live.registry import MetricDefinition, register
from app.assistant.permissions import TENANT_STAFF_ROLES

# Queue depth and patient flow.
#
# Every literal status and type below is taken from the Postgres enums the
# tenant schema actually declares, not from what the words ought to be:
#
#   queue_status_enum  waiting, in_progress, completed, skipped
#   queue_type_enum    triage, doctor, lab, radiology, pharmacy, billing
#   visit_status_enum  registered, triaged, in_consultation, in_lab,
#                      in_pharmacy, admitted, discharged, completed, cancelled
#   visit_type_enum    outpatient, inpatient, emergency
#
# They are declared in services/visit-service/app/models/visit.py and enforced
# by the database. A wrong literal here would not raise; it would quietly count
# zero and read like a real answer, so these were checked against pg_enum in the
# running tenant database rather than inferred.
#
# Nothing on this page identifies anyone: queue type, priority, status, and
# counts only. Queue waits are measured from the timestamps on the queue row.

# Queue depth is the hospital's own operating load rather than anything
# sensitive, and it is the number that lets any department see where the
# pressure is, so every member of staff may ask for it.
_FLOW_ROLES = TENANT_STAFF_ROLES

# The queue a question is about, resolved deterministically from a fixed
# synonym map rather than from the model. The enum is fixed in the schema, so
# unlike a ward name this needs no database lookup.
QUEUE_TYPE_SYNONYMS: dict[str, str] = {
    "triage": "triage",
    "assessment": "triage",
    "doctor": "doctor",
    "doctors": "doctor",
    "consultation": "doctor",
    "clinician": "doctor",
    "lab": "lab",
    "labs": "lab",
    "laboratory": "lab",
    "radiology": "radiology",
    "imaging": "radiology",
    "xray": "radiology",
    "scan": "radiology",
    "pharmacy": "pharmacy",
    "dispensing": "pharmacy",
    "prescription": "pharmacy",
    "billing": "billing",
    "cashier": "billing",
    "payment": "billing",
    "payments": "billing",
}

_QUEUE_TRIGGERS = frozenset(
    {
        "queue", "queues", "waiting", "wait", "waits", "line", "lines",
        "backlog", "pending", "many", "count", "busy", "load",
        "triage", "doctor", "doctors", "consultation", "lab", "laboratory",
        "radiology", "imaging", "pharmacy", "billing", "cashier",
        # Swahili reaches these through language.expand_query (foleni, mstari,
        # daktari, maabara, dawa, malipo, uchunguzi); the English forms the map
        # produces are already above.
        "foleni", "mstari", "wanaosubiri", "subiri",
    }
)


QUEUE_WAITING_BY_TYPE = MetricDefinition(
    metric_id="queue.waiting_by_type",
    label="Patients waiting by queue",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_FLOW_ROLES,
    triggers=_QUEUE_TRIGGERS,
    # Deliberately not filtered by date. "Waiting" is a live state, not an event:
    # a patient still queued from last night is genuinely still queued, and a
    # date filter would quietly drop them. This matches the existing dashboard
    # count in admin-service, which also filters on status alone.
    #
    # The one-row anchor on the left of the join makes a quiet hospital answer
    # "nobody waiting" rather than nothing at all. A plain GROUP BY returns no
    # rows once every queue has emptied - overnight, most obviously - the figure
    # is then dropped from the prompt, and the answer becomes "I do not have that
    # information" when the true answer is that nobody is waiting anywhere.
    sql="""
        SELECT COALESCE(q.queue_type, 'none waiting') AS queue_type,
               COUNT(q.queue_id) FILTER (WHERE q.status = 'waiting') AS waiting_now,
               COUNT(q.queue_id) FILTER (WHERE q.status = 'in_progress')
                   AS being_seen_now
        FROM (SELECT 1) AS every_queue
        LEFT JOIN queues q ON q.status IN ('waiting', 'in_progress')
        GROUP BY q.queue_type
        ORDER BY 1
        LIMIT 10
    """,
    params=frozenset(),
    exposed_fields=frozenset({"queue_type", "waiting_now", "being_seen_now"}),
    numeric_fields=frozenset({"waiting_now", "being_seen_now"}),
    max_rows=10,
    example_question="How many patients are waiting in each queue?",
    swahili_example_question="Kuna wagonjwa wangapi wanaosubiri kila foleni?",
)


QUEUE_WAITING_FOR_TYPE = MetricDefinition(
    metric_id="queue.waiting_for_type",
    label="Patients waiting in one queue",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_FLOW_ROLES,
    triggers=_QUEUE_TRIGGERS,
    # An aggregate with no GROUP BY always returns exactly one row, so an empty
    # queue answers "none waiting" rather than falling through to "I do not have
    # that information". The difference matters: the second reads as a failure
    # when the true answer is a useful zero.
    sql="""
        SELECT CAST(:queue_type AS text) AS queue_type,
               COUNT(*) FILTER (WHERE status = 'waiting') AS waiting_now,
               COUNT(*) FILTER (WHERE status = 'in_progress') AS being_seen_now
        FROM queues
        WHERE queue_type = :queue_type
    """,
    params=frozenset({"queue_type"}),
    exposed_fields=frozenset({"queue_type", "waiting_now", "being_seen_now"}),
    numeric_fields=frozenset({"waiting_now", "being_seen_now"}),
    max_rows=1,
    example_question="How many patients are waiting for triage?",
    swahili_example_question="Kuna wagonjwa wangapi wanaosubiri triage?",
)


QUEUE_AVERAGE_WAIT = MetricDefinition(
    metric_id="queue.average_wait",
    label="Average wait before being called",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_FLOW_ROLES,
    triggers=frozenset(
        {
            "wait", "waiting", "waits", "average", "mean", "long", "time",
            "minutes", "delay", "queue", "quickly", "slow",
            "muda", "wastani", "foleni", "dakika",
        }
    ),
    # Minutes rather than the seconds the admin report returns: a spoken answer
    # of "about 24 minutes" is usable, "1443 seconds" is not. Rounded in the
    # database so the figure the model is shown is exactly the figure recorded
    # as supplied, which is what the answer validator compares against.
    #
    # The one-row anchor on the left of the join is what makes a quiet morning
    # answer "nothing measured yet" instead of nothing at all. A plain GROUP BY
    # returns no rows before the first patient is called, no rows means the
    # figure is dropped from the prompt entirely, and the staff member is then
    # told the assistant does not have that information - when the true answer is
    # that nobody has waited for anything yet today. It was caught by offering
    # this question as a starting suggestion: a pharmacist clicked it on a day
    # with an empty queue and was told the figure did not exist.
    #
    # average_wait_minutes is NULL in that case, which format_value renders as
    # "not recorded" rather than inventing a zero-minute wait. patients_measured
    # is a true 0, and says why.
    sql="""
        SELECT COALESCE(q.queue_type, 'none measured') AS queue_type,
               ROUND(
                   AVG(
                       EXTRACT(EPOCH FROM (q.called_at - q.created_at)) / 60.0
                   )::numeric,
                   1
               ) AS average_wait_minutes,
               COUNT(q.queue_id) AS patients_measured
        FROM (SELECT 1) AS every_window
        LEFT JOIN queues q
               ON q.called_at IS NOT NULL
              AND q.created_at::date >= :start
              AND q.created_at::date <= :end
        GROUP BY q.queue_type
        ORDER BY 1
        LIMIT 10
    """,
    params=frozenset({"start", "end"}),
    exposed_fields=frozenset({"queue_type", "average_wait_minutes", "patients_measured"}),
    numeric_fields=frozenset({"average_wait_minutes", "patients_measured"}),
    max_rows=10,
    example_question="What is the average wait before a patient is called today?",
    swahili_example_question="Wastani wa muda wa kusubiri leo ni dakika ngapi?",
)


VISITS_IN_WINDOW = MetricDefinition(
    metric_id="visits.in_window",
    label="Visits registered",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_FLOW_ROLES,
    triggers=frozenset(
        {
            "visit", "visits", "registered", "registration", "attendance",
            "seen", "today", "week", "month", "many", "count", "patients",
            "ziara", "mahudhurio", "sajili", "usajili", "leo",
        }
    ),
    # visits.visit_date is a DATE column, so it compares directly against the
    # bound dates with no cast.
    sql="""
        SELECT COUNT(*) AS total_visits,
               COUNT(*) FILTER (WHERE visit_type = 'outpatient') AS outpatient_visits,
               COUNT(*) FILTER (WHERE visit_type = 'inpatient') AS inpatient_visits,
               COUNT(*) FILTER (WHERE visit_type = 'emergency') AS emergency_visits
        FROM visits
        WHERE visit_date >= :start
          AND visit_date <= :end
    """,
    params=frozenset({"start", "end"}),
    exposed_fields=frozenset(
        {
            "total_visits",
            "outpatient_visits",
            "inpatient_visits",
            "emergency_visits",
        }
    ),
    numeric_fields=frozenset(
        {
            "total_visits",
            "outpatient_visits",
            "inpatient_visits",
            "emergency_visits",
        }
    ),
    max_rows=1,
    example_question="How many visits were registered today?",
    swahili_example_question="Ziara ngapi zimesajiliwa leo?",
)


VISITS_BY_STATUS = MetricDefinition(
    metric_id="visits.by_status",
    label="Where visits have reached",
    tier=MetricTier.AGGREGATE,
    allowed_roles=_FLOW_ROLES,
    triggers=frozenset(
        {
            "status", "stage", "progress", "where", "still", "open",
            "outstanding", "completed", "cancelled", "discharged", "visits",
            "hatua", "hali", "ziara",
        }
    ),
    # Anchored for the same reason queue.average_wait is: on a day with no visits
    # yet, a plain GROUP BY returns no rows, the figure is dropped from the
    # prompt, and "no visits have been registered today" comes out as "I do not
    # have that information" - which reads as a broken assistant rather than a
    # quiet morning.
    sql="""
        SELECT COALESCE(v.status, 'none registered') AS visit_status,
               COUNT(v.visit_id) AS visits
        FROM (SELECT 1) AS every_window
        LEFT JOIN visits v
               ON v.visit_date >= :start
              AND v.visit_date <= :end
        GROUP BY v.status
        ORDER BY 1
        LIMIT 12
    """,
    params=frozenset({"start", "end"}),
    exposed_fields=frozenset({"visit_status", "visits"}),
    numeric_fields=frozenset({"visits"}),
    max_rows=12,
    example_question="What stage have today's visits reached?",
    swahili_example_question="Ziara za leo zimefika hatua gani?",
)


register(
    QUEUE_WAITING_BY_TYPE,
    QUEUE_WAITING_FOR_TYPE,
    QUEUE_AVERAGE_WAIT,
    VISITS_IN_WINDOW,
    VISITS_BY_STATUS,
)
